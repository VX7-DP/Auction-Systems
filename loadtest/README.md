# Load test

Bidders over HTTP and viewers over WebSockets on one live auction, run with [k6](https://grafana.com/docs/k6/latest/) 2.2 (pinned as `grafana/k6:2.2.0` in `compose.yml`). The test measures speed and also checks the guarantees: afterwards it reads Postgres and fails if a confirmed bid is missing or bids are out of order.

## Run it

You need Docker with Docker Compose and Python 3.11+. No packages are needed. From the repo root:

```bash
python3 loadtest/run.py
```

That command:

1. Starts the stack with `loadtest/compose.yml` on top. The override turns the simulated bidders off, so only load-test traffic is counted.
2. Creates a fresh auction through the admin API.
3. Reads `GET /api/metrics`, runs k6 (`bid_and_watch.js`) inside the Compose network, then reads the metrics again.
4. Checks what Postgres saved.
5. Writes `loadtest/results/<UTC time>-<commit>.json` and prints a summary. It exits 1 if a check fails.

| Option | Default | Meaning |
| --- | --- | --- |
| `--viewers` | 200 | Open auction pages (WebSocket only) |
| `--bidders` | 50 | Signed-in bidders; each also holds a WebSocket |
| `--bid-interval-ms` | 1000 | Mean pause between one bidder's bids (randomised 0.5x to 1.5x) |
| `--duration` | 60 | Seconds of bidding. Viewers stay 5 more seconds |
| `--start-price` | 10000 | Opening price, in minor units |
| `--label` | | Free text saved in the results file |
| `--no-up` / `--no-build` / `--down` | | Skip starting the stack / skip rebuilding images / stop the stack afterwards |

The stack's ports and admin password follow the same environment variables as `docker-compose.yml` (`BACKEND_PORT`, `ADMIN_PASSWORD`).

## What the bidders and viewers do

- **Bidders** sign in as guests, open the auction's WebSocket to learn the current price, and bid it plus a small random raise (up to 1%). Many bidders race for the same price, so some bids are rejected as `too_low`. That's the contention this system is built for.
- **Viewers**, and the bidders' own sockets, follow the auction the way the front end does. They read the snapshot, apply events in `seq` order, and refetch the snapshot when they see a gap.

## The results file

| Field | What it is |
| --- | --- |
| `client.bids` | Bids sent, accepted, rejected by reason, errors, and bids per second over the bidding time |
| `client.bid_latency_ms` | Bid response time as k6 measured it (request sent to response received) |
| `client.bid_to_own_update_ms` | From sending a bid to that bidder's own WebSocket showing it. This is the live-update delay. Updates dropped for arriving out of order (below) have no sample |
| `client.websockets` | Connections, messages delivered, resyncs, events out of order, unexpected closes, and how many viewers ended on the final state |
| `server` | `GET /api/metrics` after minus before: the server's own count. Its latency is the server's recent window (10,000 calls), so a short run on a busy server mixes in earlier bids |
| `postgres` | What the source of truth saved for the auction |
| `checks`, `passed` | Pass or fail for each guarantee (below) |
| `machine`, `commit`, `parameters` | Where and what ran. All parts share the one machine: the app, Postgres, Redis and k6 |
| `k6_summary` | k6's full summary, for anything not pulled out above |

Checks:

- **Every confirmed bid is saved.** Postgres holds at least as many bids as k6 was told were accepted.
- **The server's count matches Postgres.** The server's accepted count for the run equals the bids saved.
- **Saved bids are in the right order.** By `seq`, every bid is higher than the one before.
- **The auction's high bid is the top saved bid.** Its amount and its `seq` both match.
- **Every viewer ends on the right high bid, with nothing out of order.** After the bidders stop, each viewer shows the final `seq` and high bid. No socket ever received an event older than one it already had.
- **At least one bid was accepted.**

### Resyncs are expected

When two bids are saved at the same moment, the later `seq` can finish first. The server then drops the earlier event rather than send it out of order, and every page refetches the snapshot (CLAUDE.md section 4). The results count these as `resyncs`. Each one sends a snapshot request from every open page at once, so expect them to show up in the tail latency.

## Publishing numbers

A results file describes the machine it ran on. Quote only numbers from a results file, together with that file's `machine` and `parameters`. Commit the file the README or case study quotes; other runs don't need committing.
