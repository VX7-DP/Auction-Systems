# Front end

React + TypeScript, built with Vite. The interface contract it speaks is CLAUDE.md section 10.

## Scripts

```bash
npm ci
npm run dev        # proxies /api and /ws to http://localhost:8000 (override with API_PROXY_TARGET)
npm run dev:mock   # no backend needed: an in-memory mock backend with simulated bidders
npm run lint       # Oxlint
npm test           # Vitest
npm run build      # type-check, then build into dist/
```

`npm run dev:mock` seeds a few auctions (one ends about a minute after start) and runs simulated bidders. In the browser console, `mockBackend` can pause an auction (`mockBackend.pause(id)`), drop live connections (`mockBackend.disconnectSockets()`) or lose the next live event (`mockBackend.dropNextEvent(id)`), to watch the page recover.

## Pages

| Path | Page |
| --- | --- |
| `/` | Auction list. Refreshes every 5 seconds while the tab is visible. |
| `/auctions/{id}` | Auction page: current bid, countdown, bid box, history, outbid notice. |
| `/sign-in?next=/path` | Pick a display name, then return to `next` (same-site paths only). |
| `/admin` | Create an auction (needs the admin password). |

These are client-side routes, so the production web server must serve `index.html` for any path that isn't a file, `/api` or `/ws`.

## How live updates stay correct

`src/live/auctionFeed.ts` keeps an auction page in sync and is unit-tested on its own.

- Every event carries `seq`. The page applies events strictly in order and ignores any it already has.
- When a socket opens (the first time and after every reconnect), the page fetches a fresh snapshot, because anything published before the subscription only shows up there. Events that arrive meanwhile are buffered and replayed after it.
- A `seq` gap means a message was lost (Redis Pub/Sub doesn't redeliver), so the page refetches the snapshot.
- Dropped connections reconnect with backoff. While disconnected, the page polls the snapshot every 5 seconds, so it still updates on networks that block WebSockets.
- Each bid has a `bid_id`. It is reused only to retry a bid whose outcome is unknown (network failure, timeout, 5xx), so a retry can't place a bid twice.
- The countdown is the server's `end_at` minus the browser clock. The server decides when bidding stops; at zero the page shows "Closing…" until the close event arrives.

## Where code goes

```
src/api/        contract types, REST client, transport (the swappable network boundary)
src/live/       auction feed (seq ordering, resync, reconnect) and bid placement hooks
src/pages/      one component per page, plus the bid box
src/components/ shared UI
src/session/    guest sign-in state
src/lib/        money, time, ids, router
src/mock/       in-memory backend for tests and dev:mock (never in the production build)
```

## Settings

- `VITE_CURRENCY` (build time): the display currency for all amounts, default `INR`. The contract carries amounts in minor units only, with no currency.
