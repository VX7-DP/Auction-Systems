// Keeps one auction page in sync with the server: a snapshot over REST plus live
// events over the WebSocket, applied strictly in seq order (CLAUDE.md section 4,
// "Live update order"). Redis Pub/Sub doesn't redeliver missed messages, so a seq gap
// or a reconnect is repaired by refetching the snapshot.
//
// Plain TypeScript with no React, so the ordering rules are unit-tested directly.
import { ApiError } from '../api/client.ts'
import type {
  AuctionEvent,
  AuctionSnapshot,
  AuctionStatus,
  RecentBid,
} from '../api/contract.ts'
import type { SocketLike } from '../api/transport.ts'
import {
  EXPECT_SEQ_TIMEOUT_MS,
  FALLBACK_POLL_MS,
  HISTORY_LIMIT,
  RECONNECT_BASE_MS,
  RECONNECT_MAX_MS,
} from '../config.ts'

export interface BidRow {
  seq: number
  amount: number
  bidderName: string
  isBot: boolean
  createdAt: string
  /** Placed by this browser (known from our own accepted bids). */
  mine: boolean
  /** Arrived after the page first loaded, so the UI can highlight it. */
  fresh: boolean
}

export interface AuctionView {
  id: string
  title: string
  startPrice: number
  endAt: string
  status: AuctionStatus
  seq: number
  highAmount: number | null
  highBidderId: string | null
  highBidderName: string | null
  highIsBot: boolean | null
  /** Newest first. */
  bids: BidRow[]
  winner: { id: string | null; name: string | null; amount: number | null } | null
}

export interface OutbidNotice {
  /** Changes with every new notice, so a dismissed one never hides the next. */
  id: number
  byName: string | null
  byIsBot: boolean | null
  amount: number | null
}

/** 'closed' means the feed stopped on purpose because the auction ended. */
export type Connection = 'connecting' | 'live' | 'reconnecting' | 'closed'

export interface FeedState {
  phase: 'loading' | 'ready' | 'not_found'
  view: AuctionView | null
  connection: Connection
  outbid: OutbidNotice | null
  /** Why the first load hasn't worked yet; it keeps retrying. */
  loadError: string | null
}

export interface AuctionFeedOptions {
  auctionId: string
  socketUrl: string
  getAuction(id: string): Promise<AuctionSnapshot>
  openSocket(url: string): SocketLike
  /** The signed-in bidder, read at the moment of each change, for the outbid notice. */
  getBidderId(): string | null
  onChange(state: FeedState): void
  isVisible?(): boolean
}

export interface AuctionFeed {
  start(): void
  stop(): void
  /** Fetch a fresh snapshot now (or right after the one in flight). */
  resync(): void
  /** Our bid was accepted with this seq: resync if its event doesn't show up soon. */
  expectSeq(seq: number): void
  markMine(seq: number): void
  /** The browser is back online or visible: reconnect now instead of waiting. */
  wake(): void
  getState(): FeedState
}

const BUFFER_LIMIT = 500
const CLOSE_NOT_FOUND = 4404
const LOAD_RETRY_BASE_MS = 1_000
const GAP_RETRY_BASE_MS = 250
const RETRY_MAX_MS = 5_000

export function createAuctionFeed(options: AuctionFeedOptions): AuctionFeed {
  const isVisible = options.isVisible ?? (() => document.visibilityState !== 'hidden')
  const mine = new Set<number>()
  let state: FeedState = { phase: 'loading', view: null, connection: 'connecting', outbid: null, loadError: null }
  let stopped = false
  let finished = false // the auction closed or doesn't exist: no more socket
  let socket: SocketLike | null = null
  let reconnectAttempts = 0
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null
  let pollTimer: ReturnType<typeof setInterval> | null = null
  let syncing = false // a snapshot request is in flight (or waiting to retry)
  let syncAgain = false // ask for another snapshot once this one lands
  let syncRetryTimer: ReturnType<typeof setTimeout> | null = null
  let syncFailures = 0
  let gapRetries = 0
  let buffer: AuctionEvent[] = []
  let outbidCount = 0
  const expectTimers = new Set<ReturnType<typeof setTimeout>>()

  function emit(patch: Partial<FeedState>) {
    if (stopped) return
    state = { ...state, ...patch }
    options.onChange(state)
  }

  // --- snapshot ----------------------------------------------------------------

  function requestResync() {
    if (stopped || finished) return
    if (syncing) {
      syncAgain = true
      return
    }
    if (syncRetryTimer) {
      clearTimeout(syncRetryTimer)
      syncRetryTimer = null
    }
    void runSync()
  }

  /** A gap was seen: resync now, unless one is in flight or a backoff is pending. */
  function resyncForGap() {
    if (syncing || syncRetryTimer) return
    void runSync()
  }

  async function runSync() {
    syncing = true
    syncAgain = false
    let snapshot: AuctionSnapshot
    try {
      snapshot = await options.getAuction(options.auctionId)
    } catch (error) {
      if (stopped) return
      if (error instanceof ApiError && (error.status === 404 || error.status === 422)) {
        syncing = false
        finish()
        emit({ phase: 'not_found', connection: 'closed' })
        return
      }
      syncFailures += 1
      if (!state.view) {
        emit({ loadError: error instanceof Error ? error.message : 'Something went wrong.' })
      }
      // Stay in "syncing" so live events keep buffering until a snapshot lands.
      syncRetryTimer = setTimeout(() => {
        syncRetryTimer = null
        void runSync()
      }, backoff(LOAD_RETRY_BASE_MS, syncFailures - 1))
      return
    }
    if (stopped) return
    syncFailures = 0
    applySnapshot(snapshot)
    if (syncAgain && !finished) {
      void runSync()
      return
    }
    syncing = false
    drainBuffer()
  }

  function applySnapshot(snapshot: AuctionSnapshot) {
    const previous = state.view
    if (previous && snapshot.seq < previous.seq) {
      // Older than what we already show: keep ours, it's newer.
      emit({ phase: 'ready', loadError: null })
      return
    }
    const view = viewFromSnapshot(snapshot, previous)
    emit({
      phase: 'ready',
      loadError: null,
      view,
      outbid: previous && snapshot.seq > previous.seq ? outbidNotice(previous, view) : state.outbid,
    })
    if (view.status === 'closed') closeForGood()
  }

  function viewFromSnapshot(snapshot: AuctionSnapshot, previous: AuctionView | null): AuctionView {
    const rows = new Map<number, BidRow>()
    for (const row of previous?.bids ?? []) rows.set(row.seq, row)
    for (const bid of snapshot.recent_bids) {
      // Bids in the first snapshot are history; later ones are news to this viewer.
      rows.set(bid.seq, rowFromBid(bid, rows.get(bid.seq)?.fresh ?? previous !== null))
    }
    return {
      id: snapshot.id,
      title: snapshot.title,
      startPrice: snapshot.start_price,
      endAt: snapshot.end_at,
      status: snapshot.status,
      seq: snapshot.seq,
      highAmount: snapshot.high_amount,
      highBidderId: snapshot.high_bidder_id,
      highBidderName: snapshot.high_bidder_name,
      highIsBot: snapshot.high_is_bot,
      bids: sortRows([...rows.values()]),
      winner:
        snapshot.status === 'closed'
          ? { id: snapshot.high_bidder_id, name: snapshot.high_bidder_name, amount: snapshot.high_amount }
          : null,
    }
  }

  function rowFromBid(bid: RecentBid, fresh: boolean): BidRow {
    return {
      seq: bid.seq,
      amount: bid.amount,
      bidderName: bid.bidder_name,
      isBot: bid.is_bot,
      createdAt: bid.created_at,
      mine: mine.has(bid.seq),
      fresh,
    }
  }

  // --- events ------------------------------------------------------------------

  function onMessage(data: unknown) {
    const event = parseEvent(data, options.auctionId)
    if (!event) return
    if (syncing || !state.view) {
      if (buffer.length < BUFFER_LIMIT) buffer.push(event)
      return
    }
    const seq = state.view.seq
    if (event.seq <= seq) return // duplicate or already in the snapshot
    if (event.seq === seq + 1) {
      applyEvent(event)
      drainBuffer()
      return
    }
    buffer.push(event)
    resyncForGap()
  }

  /** Apply buffered events that follow on from the view; resync if a gap remains. */
  function drainBuffer() {
    if (!state.view || buffer.length === 0) {
      gapRetries = 0
      return
    }
    buffer.sort((a, b) => a.seq - b.seq)
    const remaining: AuctionEvent[] = []
    for (const event of buffer) {
      const seq = state.view.seq
      if (event.seq <= seq) continue
      if (event.seq === seq + 1) applyEvent(event)
      else remaining.push(event)
    }
    buffer = remaining
    if (buffer.length === 0 || finished) {
      buffer = []
      gapRetries = 0
      return
    }
    // The snapshot didn't reach the buffered events yet: try again shortly.
    if (!syncRetryTimer && !syncing) {
      syncRetryTimer = setTimeout(() => {
        syncRetryTimer = null
        void runSync()
      }, backoff(GAP_RETRY_BASE_MS, gapRetries))
      gapRetries += 1
    }
  }

  function applyEvent(event: AuctionEvent) {
    const previous = state.view!
    let view: AuctionView
    if (event.type === 'auction_update') {
      const isBid = event.high_amount !== null && event.high_amount !== previous.highAmount
      const bids = isBid
        ? sortRows([
            {
              seq: event.seq,
              amount: event.high_amount!,
              bidderName: event.high_bidder_name ?? '',
              isBot: event.is_bot ?? false,
              // The event has no bid time; the next snapshot brings the server's.
              createdAt: new Date().toISOString(),
              mine: mine.has(event.seq),
              fresh: true,
            },
            ...previous.bids,
          ])
        : previous.bids
      view = {
        ...previous,
        seq: event.seq,
        status: event.status,
        endAt: event.end_at,
        highAmount: event.high_amount,
        highBidderId: event.high_bidder_id,
        highBidderName: event.high_bidder_name,
        highIsBot: event.is_bot,
        bids,
        winner:
          event.status === 'closed'
            ? { id: event.high_bidder_id, name: event.high_bidder_name, amount: event.high_amount }
            : null,
      }
    } else {
      view = {
        ...previous,
        seq: event.seq,
        status: 'closed',
        highAmount: event.amount ?? previous.highAmount,
        highBidderId: event.winner_id,
        highBidderName: event.winner_name,
        highIsBot: event.winner_id === previous.highBidderId ? previous.highIsBot : null,
        winner: { id: event.winner_id, name: event.winner_name, amount: event.amount },
      }
    }
    emit({ view, outbid: outbidNotice(previous, view) })
    if (view.status === 'closed') closeForGood()
  }

  /** "You've been outbid": the high bidder changed from me to someone else. */
  function outbidNotice(previous: AuctionView, next: AuctionView): OutbidNotice | null {
    const me = options.getBidderId()
    if (me && previous.highBidderId === me && next.highBidderId !== null && next.highBidderId !== me) {
      outbidCount += 1
      return { id: outbidCount, byName: next.highBidderName, byIsBot: next.highIsBot, amount: next.highAmount }
    }
    return state.outbid
  }

  // --- socket ------------------------------------------------------------------

  function connect() {
    if (stopped || finished) return
    const current = options.openSocket(options.socketUrl)
    socket = current
    current.onopen = () => {
      if (socket !== current) return
      reconnectAttempts = 0
      stopPolling()
      emit({ connection: 'live' })
      // Anything published before our subscription went live is only in a fresh snapshot.
      requestResync()
    }
    current.onmessage = (message) => {
      if (socket === current) onMessage(message.data)
    }
    current.onclose = (event) => {
      if (socket !== current) return
      socket = null
      if (stopped || finished) return
      if (event.code === CLOSE_NOT_FOUND) {
        // No such auction (CLAUDE.md section 10): don't reconnect.
        finish()
        emit({ phase: 'not_found', connection: 'closed' })
        return
      }
      emit({ connection: 'reconnecting' })
      startPolling()
      reconnectTimer = setTimeout(() => {
        reconnectTimer = null
        connect()
      }, jitter(backoff(RECONNECT_BASE_MS, reconnectAttempts, RECONNECT_MAX_MS)))
      reconnectAttempts += 1
    }
    current.onerror = () => {} // a close event follows
  }

  function startPolling() {
    if (pollTimer) return
    pollTimer = setInterval(() => {
      if (isVisible()) requestResync()
    }, FALLBACK_POLL_MS)
  }

  function stopPolling() {
    if (pollTimer) clearInterval(pollTimer)
    pollTimer = null
  }

  function detachSocket() {
    const current = socket
    socket = null
    if (current) {
      current.onopen = current.onmessage = current.onclose = current.onerror = null
      current.close(1000)
    }
  }

  /** The auction ended (or never existed): no more live updates will come. */
  function closeForGood() {
    finish()
    emit({ connection: 'closed' })
  }

  function finish() {
    finished = true
    detachSocket()
    stopPolling()
    if (reconnectTimer) clearTimeout(reconnectTimer)
    reconnectTimer = null
    buffer = []
  }

  return {
    start() {
      options.onChange(state)
      connect()
      requestResync()
    },
    stop() {
      stopped = true
      finish()
      if (syncRetryTimer) clearTimeout(syncRetryTimer)
      for (const timer of expectTimers) clearTimeout(timer)
      expectTimers.clear()
    },
    resync: requestResync,
    expectSeq(seq) {
      const timer = setTimeout(() => {
        expectTimers.delete(timer)
        if (state.view && state.view.seq < seq) requestResync()
      }, EXPECT_SEQ_TIMEOUT_MS)
      expectTimers.add(timer)
    },
    markMine(seq) {
      mine.add(seq)
      const view = state.view
      if (view?.bids.some((row) => row.seq === seq)) {
        emit({ view: { ...view, bids: view.bids.map((row) => (row.seq === seq ? { ...row, mine: true } : row)) } })
      }
    },
    wake() {
      if (stopped || finished || state.connection === 'live') return
      if (reconnectTimer) clearTimeout(reconnectTimer)
      reconnectTimer = null
      reconnectAttempts = 0
      detachSocket()
      connect()
    },
    getState: () => state,
  }
}

function sortRows(rows: BidRow[]): BidRow[] {
  return rows.sort((a, b) => b.seq - a.seq).slice(0, HISTORY_LIMIT)
}

function backoff(base: number, attempt: number, max = RETRY_MAX_MS): number {
  return Math.min(max, base * 2 ** attempt)
}

function jitter(ms: number): number {
  return ms * (1 + Math.random() * 0.3)
}

/** A section 10 event for this auction, or null for anything else (heartbeats included). */
function parseEvent(data: unknown, auctionId: string): AuctionEvent | null {
  if (typeof data !== 'string') return null
  let message: unknown
  try {
    message = JSON.parse(data)
  } catch {
    return null
  }
  if (typeof message !== 'object' || message === null) return null
  const event = message as Partial<AuctionEvent>
  if (event.type !== 'auction_update' && event.type !== 'auction_closed') return null
  if (typeof event.seq !== 'number' || event.auction_id !== auctionId) return null
  return event as AuctionEvent
}
