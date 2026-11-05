// Load test: bidders over HTTP and viewers over WebSockets, on one live auction.
//
// Written for k6 2.2 (grafana/k6:2.2.0, pinned in loadtest/compose.yml). Run it with
// loadtest/run.py, which starts the stack, creates the auction, runs this script,
// then checks Postgres and writes a results file. See loadtest/README.md.
//
// Settings, from environment variables:
//   BASE_URL         the API, default http://backend:8000 (inside Compose)
//   AUCTION_ID       the auction to bid on (run.py creates it)
//   START_PRICE      its opening price in minor units, default 10000
//   VIEWERS          open auction pages (WebSockets only), default 200
//   BIDDERS          signed-in bidders (HTTP bids, plus a WebSocket each), default 50
//   BID_INTERVAL_MS  mean pause between one bidder's bids, default 1000 (jittered 0.5x-1.5x)
//   DURATION_S       how long bidders bid, default 60
//
// The summary goes to stdout as JSON between SUMMARY_BEGIN and SUMMARY_END lines.

import http from "k6/http";
import { Counter, Rate, Trend } from "k6/metrics";
import { WebSocket } from "k6/websockets";

const BASE_URL = (__ENV.BASE_URL || "http://backend:8000").replace(/\/$/, "");
const WS_URL = BASE_URL.replace(/^http/, "ws");
const AUCTION_ID = __ENV.AUCTION_ID;
const START_PRICE = parseInt(__ENV.START_PRICE || "10000", 10);
const VIEWERS = parseInt(__ENV.VIEWERS || "200", 10);
const BIDDERS = parseInt(__ENV.BIDDERS || "50", 10);
const BID_INTERVAL_MS = parseInt(__ENV.BID_INTERVAL_MS || "1000", 10);
const DURATION_S = parseInt(__ENV.DURATION_S || "60", 10);
// Viewers stay this long after the bidders stop, so the last updates reach them.
const DRAIN_S = 5;
const REASONS = ["too_low", "too_high", "closed", "paused", "not_found", "duplicate_bid_id"];

if (!AUCTION_ID) {
  throw new Error("Set AUCTION_ID (loadtest/run.py creates the auction and sets it).");
}

const bidLatency = new Trend("bid_latency", true);
const bidToUpdate = new Trend("bid_to_update", true);
const bidsAccepted = new Counter("bids_accepted");
const bidsRejected = new Counter("bids_rejected");
const bidsError = new Counter("bids_error");

const wsConnect = new Trend("ws_connect", true);
const wsMessages = new Counter("ws_messages");
const wsResyncs = new Counter("ws_resyncs");
const wsOutOfOrder = new Counter("ws_out_of_order");
const wsUnexpectedCloses = new Counter("ws_unexpected_closes");
const wsBehindAtEnd = new Counter("ws_behind_at_end");
const viewerEndedRight = new Rate("viewer_ended_on_final_state");

// A threshold on a tagged sub-metric is the way to make k6 report it in the summary.
// These always pass: they exist to break the rejections down by reason.
const reasonThresholds = {};
for (const reason of REASONS) {
  reasonThresholds[`bids_rejected{reason:${reason}}`] = ["count>=0"];
}

export const options = {
  scenarios: {
    viewers: {
      executor: "per-vu-iterations",
      exec: "viewer",
      vus: VIEWERS,
      iterations: 1,
      maxDuration: `${DURATION_S + DRAIN_S + 60}s`,
    },
    bidders: {
      executor: "per-vu-iterations",
      exec: "bidder",
      vus: BIDDERS,
      iterations: 1,
      maxDuration: `${DURATION_S + DRAIN_S + 60}s`,
    },
  },
  thresholds: reasonThresholds,
  summaryTrendStats: ["min", "med", "avg", "p(95)", "p(99)", "max", "count"],
};

function snapshot() {
  const response = http.get(`${BASE_URL}/api/auctions/${AUCTION_ID}`, {
    tags: { name: "snapshot" },
  });
  if (response.status !== 200) {
    throw new Error(`snapshot answered ${response.status}: ${response.body}`);
  }
  return response.json();
}

// Follows one auction's events the way the front end does (CLAUDE.md sections 4 and
// 10): open the socket, read the snapshot, then apply events in seq order. An event
// more than one past the last applied seq is a gap, and a gap means a refetch of the
// snapshot (a resync). Under concurrent bids this is expected now and then: a later
// seq can commit first, and the server drops the earlier one rather than send it out
// of order. Calls onState after the snapshot, each applied update and each resync.
function follow({ closeAfterMs, onState, onOpen, onDone }) {
  const opened = Date.now();
  const socket = new WebSocket(`${WS_URL}/ws/auctions/${AUCTION_ID}`);
  const state = { seq: null, high: null, highBidder: null, outOfOrder: 0, closedByUs: false };
  const early = [];

  function fromSnapshot(snap) {
    state.seq = snap.seq;
    state.high = snap.high_amount;
    state.highBidder = snap.high_bidder_id;
  }

  socket.onopen = () => {
    wsConnect.add(Date.now() - opened);
    fromSnapshot(snapshot());
    if (onOpen) onOpen(state);
    for (const event of early) apply(event);
    early.length = 0;
    setTimeout(() => {
      state.closedByUs = true;
      socket.close();
    }, closeAfterMs);
  };

  function apply(event) {
    if (event.seq <= state.seq) return; // already in the snapshot
    if (event.seq > state.seq + 1) {
      wsResyncs.add(1);
      const snap = snapshot();
      if (snap.seq >= event.seq) {
        fromSnapshot(snap);
        if (onState) onState(state, null);
        return;
      }
      // The snapshot is older than this event; the event is the newer state.
    }
    state.seq = event.seq;
    if (event.type === "auction_update") {
      state.high = event.high_amount;
      state.highBidder = event.high_bidder_id;
      if (onState) onState(state, event);
    }
  }

  let lastSeen = -1;
  socket.onmessage = (message) => {
    wsMessages.add(1);
    const event = JSON.parse(message.data);
    if (event.seq <= lastSeen) {
      state.outOfOrder += 1;
      wsOutOfOrder.add(1);
    }
    lastSeen = Math.max(lastSeen, event.seq);
    if (state.seq === null) early.push(event);
    else apply(event);
  };

  socket.onclose = (event) => {
    if (!state.closedByUs) {
      wsUnexpectedCloses.add(1, { code: String(event.code) });
    }
    if (onDone) onDone(state);
  };

  socket.onerror = () => {
    // onclose follows and counts it.
  };
}

export function viewer() {
  follow({
    closeAfterMs: (DURATION_S + DRAIN_S) * 1000,
    onDone: (state) => {
      // The bidders stopped DRAIN_S ago: the page should show the final state.
      const final = snapshot();
      const behind = state.seq === null ? final.seq : Math.max(0, final.seq - state.seq);
      wsBehindAtEnd.add(behind);
      viewerEndedRight.add(
        behind === 0 && state.high === final.high_amount && state.outOfOrder === 0,
      );
    },
  });
}

function uuid4() {
  // Bid ids only need to be unique, not unguessable.
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    return (c === "x" ? r : (r & 0x3) | 0x8).toString(16);
  });
}

export function bidder() {
  const signIn = http.post(
    `${BASE_URL}/api/session`,
    JSON.stringify({ display_name: `Load bidder ${__VU}` }),
    { headers: { "Content-Type": "application/json" }, tags: { name: "sign_in" } },
  );
  if (signIn.status !== 200) {
    throw new Error(`sign-in answered ${signIn.status}: ${signIn.body}`);
  }
  const me = signIn.json("bidder_id");

  let high = null;
  // Amount -> when this bidder sent it, to time how long until its own socket shows it.
  const sentAt = {};
  const stopAt = Date.now() + DURATION_S * 1000;

  function nextAmount() {
    const base = high === null ? START_PRICE : high;
    // A small raise: well inside the max-raise rule, so most rejections are races.
    return base + 1 + Math.floor(Math.random() * Math.max(100, Math.floor(base / 100)));
  }

  function placeBid() {
    if (Date.now() >= stopAt) return;
    const amount = nextAmount();
    const sent = Date.now();
    sentAt[amount] = sent;
    http
      .asyncRequest(
        "POST",
        `${BASE_URL}/api/auctions/${AUCTION_ID}/bids`,
        JSON.stringify({ bid_id: uuid4(), amount }),
        { headers: { "Content-Type": "application/json" }, tags: { name: "bid" } },
      )
      .then((response) => {
        bidLatency.add(response.timings.duration);
        let body = null;
        try {
          body = response.json();
        } catch (_) {
          body = null;
        }
        if (response.status === 200 && body && body.status === "accepted") {
          bidsAccepted.add(1);
        } else if (response.status === 200 && body && body.status === "rejected") {
          bidsRejected.add(1, { reason: body.reason });
          delete sentAt[amount];
        } else {
          bidsError.add(1, { status: String(response.status) });
          delete sentAt[amount];
        }
      });
    const pause = BID_INTERVAL_MS * (0.5 + Math.random());
    setTimeout(placeBid, pause);
  }

  follow({
    closeAfterMs: (DURATION_S + DRAIN_S) * 1000,
    onOpen: (state) => {
      high = state.high;
      // Spread the first bids out instead of all bidders firing at once.
      setTimeout(placeBid, Math.random() * BID_INTERVAL_MS);
    },
    onState: (state, event) => {
      high = state.high;
      if (event && event.high_bidder_id === me && sentAt[event.high_amount] !== undefined) {
        bidToUpdate.add(Date.now() - sentAt[event.high_amount]);
        delete sentAt[event.high_amount];
      }
    },
  });
}

export function handleSummary(data) {
  return {
    stdout: `\nSUMMARY_BEGIN\n${JSON.stringify(data)}\nSUMMARY_END\n`,
  };
}
