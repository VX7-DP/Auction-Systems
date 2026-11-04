# CLAUDE.md — Distributed Auction System

Single source of truth for the auction project, revised 4 Nov 2026. It replaces the old .NET/Azure plan; if an older doc (`decisions.md`, `auction-roadmap.md`, `auction-checklist.md`) disagrees with it, **this file wins**.

## Git rules

- Never commit or push unless the user explicitly allows it for that change.
- Commit author and committer must always be `VX7-DP <vx7cse@gmail.com>`. Set it before committing:
  `git config user.name "VX7-DP" && git config user.email "vx7cse@gmail.com"`
- Branch names must describe the task, in the form `<type>/<short-kebab-description>`, e.g. `feat/bid-placement-api`, `fix/auction-close-race`, `chore/add-claude-md`. Types: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`. Never use auto-generated names like `claude/<random-words>`; rename such a branch before pushing.
- Never add `Co-Authored-By`, `Claude-Session`, or any mention of Claude, Sonnet, Opus, Anthropic, or other AI attribution to commit messages, PR titles/descriptions, or code.

## 1. What the project is

A real-time auction system where many bidders race to update one auction's high bid. **Guarantee: no confirmed bid is lost and bids are never ordered wrongly, even during write spikes.** Section 4 says how.

## 2. Goal

- **Goal:** a live demo for potential clients (India, US, Europe).
- **Audience:** a potential client, often non-technical, asking: can he build my thing, deliver on time, and will it hold up with real users?
- **Definition of done:** shipped, live and usable by a stranger. Engineering depth is proof, not the product.
- **Claims the demo must back with evidence:** every confirmed bid is saved, bids land in the right order, and it stays fast under load. The case study shows the test or number behind each claim.

## 3. Tech stack

| Layer | Choice | Notes |
| --- | --- | --- |
| Backend language | Python | Replaces the earlier C#/.NET plan. |
| Backend framework | FastAPI | Async, with WebSockets. |
| Frontend | React + TypeScript | Angular background helps. |
| Hot path | Redis + Lua CAS | One Redis instance. Needs Lua scripting, Pub/Sub and `TIME` inside scripts (Redis 7+ or Valkey). Persistence is optional: Redis is rebuilt from Postgres. |
| Durable store | PostgreSQL | Source of truth. Every confirmed bid is committed here first. |
| Real-time fan-out | WebSockets + Redis Pub/Sub | Same layout as the old SignalR + Redis backplane, but reconnect and resync are now our code (sections 4 and 11). |
| Packaging | Docker / Docker Compose | Everything containerized from day one. |
| CI | GitHub Actions | Kept from the old plan: tests and image build on every push. |

**Why this stack:** it's the fastest path to a shipped demo, Python fits AI client work, and React is asked for more often than Angular. Each language has one job: Python on the server, TypeScript in the browser.

**What it gives up:** the earlier .NET work, plus SignalR's built-in reconnect and backplane, which are now ours to build.

## 4. Architecture decisions

**Carried forward (settled):**

- Redis makes the bid accept/reject decision. Postgres is not part of that decision.
- Bid ordering is enforced by an atomic Lua compare-and-set (CAS) script in one Redis instance. This limit is deliberate.
- "Which database" and "how writes reach Postgres" are separate questions.
- Live updates reach viewers on every server instance through Redis Pub/Sub.
- Vercel won't host the backend. Its Functions now accept WebSockets, FastAPI included, but connections close at the function's maximum duration and there's no always-on process for in-app jobs.

**Durability (decided 4 Nov): synchronous dual-write.** A bid is confirmed only after Postgres commits it, so Postgres is on the confirmation path, not the decision path.

1. The Lua CAS checks the bid: auction open, before `end_at`, higher than the current high (or the start price), `bid_id` not seen before. It updates Redis and returns the auction's next sequence number (`seq`).
2. The app inserts the bid into Postgres, with a statement timeout.
3. Only after the commit does the app confirm to the bidder and publish the update.

- **Tradeoff:** Postgres latency is added to every bid response, and a Postgres outage stops bidding (it fails closed).
- **Insert fails after Redis accepted:** retry it, which is safe because the keys are the same. If it still fails, pause that auction (the script rejects bids while paused) and return an error. When writes work again, reload the auction from Postgres and unpause.
- **Redis restart:** when an auction's key is missing, the script returns `NOT_LOADED`. The app reloads that auction from Postgres under a lock, then retries the bid.
- **Every load is fenced (decided 4 Nov).** Loading an auction into Redis first bumps `auctions.load_epoch`, and the Redis hash carries that epoch. A bid write locks the auction row, then checks that the epoch it was decided under is still current before inserting, so a write decided against an older load can never commit after a reload. A write already past that check holds the row, so the reload waits for it. No timing assumption is involved.
- **Seqs are reserved before Redis uses them (decided 4 Nov).** Redis shows a seq before Postgres records it: a pause, or a snapshot of a bid still being saved. So every load reserves a block of seqs in `auctions.seq_ceiling`, and Redis never hands out a seq above the ceiling it was given; it asks for the next block when it runs out. When Redis has lost an auction, the reload resumes at the old ceiling, above every seq Redis could have shown, so a seq is never reused. Viewers see one gap and resync.
- **Why this option:** it's the only one that keeps both "no confirmed bid is lost" and "runs on any host" true.
- **Upgrade path, not now:** Redis Streams plus a projector. It needs a self-hosted Redis that fsyncs every write, because ElastiCache doesn't support AOF.

**Other decisions (4 Nov):**

- **Bid rows:** unique on `(auction_id, seq)` and on `bid_id`. Write with `INSERT … ON CONFLICT DO NOTHING`, then `UPDATE auctions … WHERE id = :id AND high_seq < :seq`. Repeats are harmless, arrival order doesn't matter, and the hot row never conflicts.
- **`xmin` optimistic concurrency:** kept for admin edits to auctions only, not the bid path. SQLAlchemy can map `xmin` as a server-side version counter.
- **Idempotent bids:** the client sends a `bid_id` (UUID) with every bid. The Lua script remembers recent IDs per auction and returns the original result for a repeat.
- **Auction close:** the Lua script rejects bids once Redis `TIME` reaches `end_at`, so there's one clock and no drift between instances. Closing is an idempotent script ("close if open and past `end_at`"), run by a sweeper on startup and every few seconds.
- **Background jobs** (simulated bidders, close sweeper) run only on the instance holding that job's Redis lock (`SET NX PX`, renewed). Rolling deploys briefly run two instances; the lock prevents double bidders and double closes.
- **Live update order:** `seq` goes up by one on every state change (an accepted bid, a pause or unpause, the close), and every event carries it. The one jump is after Redis loses an auction (see "Seqs are reserved" above). A client that sees a gap, or reconnects, fetches a snapshot over REST, because Redis Pub/Sub doesn't redeliver missed messages.

## 5. Build rules (host-agnostic)

The build must run on any host that meets the requirements in section 8.

1. Docker for every service.
2. All configuration through environment variables (database URL, Redis URL, secrets).
3. No reliance on local disk or in-memory shared state. Redis holds nothing that can't be rebuilt from Postgres.
4. Redis Pub/Sub for WebSocket fan-out, with `seq` on every event.
5. A health check endpoint that checks Postgres and Redis.
6. Background jobs run inside the app for now, guarded by a Redis lock. Moving them to a separate worker later is an option.
7. WebSocket heartbeats run more often than the host's idle timeout (an AWS ALB defaults to 60 seconds).

## 6. What the demo must include

In priority order:

1. Live bidding with real-time updates and an outbid notification.
2. Demo mode with simulated bidders, so a lone visitor sees live action. Bots are labeled as bots and use the same bid path as people.
3. Clean front end: auction list, auction page, countdown timer, bid history.
4. Simple sign-in and an admin page to create auctions.
5. Deployed live at `auction.summittech.in`.
6. A public metrics view and a load-test results page, showing real numbers only.
7. A readme and a case study written in business terms (results, not jargon).

**Positioning:** present it as a real-time engine pattern (flash sales, ticket drops, live bidding, live dashboards), not only an auction.

## 7. Stages and done-signals

Each stage ends on a done-signal, not a date. Targets assume solo work runs 1.5–2x estimates.

1. **Core loop, local (target 4 Nov).** Items 1–3 from section 6, running in Docker Compose. Done when two browsers and the simulated bidders bid on one auction, outbid notices fire, and the auction closes on time. A Redis restart mid-auction must lose no confirmed bid and keep the right high bid.
2. **Live (target 5 Nov).** Items 4–5: sign-in, admin page and deployment at `auction.summittech.in` over HTTPS. Needs the host decision in section 8. Done when a stranger on a phone opens the link, signs in and bids.
3. **Proof.** Items 6–7: load test, results page, public metrics, readme and case study. Done when the case study is published with real load-test numbers.

Later, only if a client or the case study needs it: OpenTelemetry + Grafana, the Streams projector, a separate worker, scale-out on AWS ECS.

## 8. Deployment

Stage 2 needs a host, and the owner decides before starting it. Any host must provide containers, long-lived WebSockets, an always-on process for in-app jobs, Redis with Lua and Pub/Sub (persistence optional), and Postgres with backups.

| Option | Monthly cost (checked 4 Nov 2026) | Tradeoffs |
| --- | --- | --- |
| One VM running the Compose stack (e.g. Lightsail, 2 GB) | $12, public IPv4 included | Fastest path to Stage 2, same setup as local. You own TLS, patching and Postgres backups. |
| Render, paid | Web service from $7, Postgres from $6, Key Value priced separately | Managed and always on. Confirm its Key Value runs Lua scripts. |
| AWS managed: ECS Fargate + ALB + RDS + ElastiCache | About $60 floor in us-east-1 before traffic (breakdown below) | The scale-up story for clients. About a day of setup on its own (estimate). |
| Render, free | $0 | Not for the backend: it sleeps after 15 idle minutes, free Postgres expires after 30 days and is deleted 14 days later, and free Key Value doesn't persist. |

**AWS managed floor, us-east-1:**

| Item | Price |
| --- | --- |
| RDS PostgreSQL, db.t4g.micro | About $11.68 + storage |
| ElastiCache, cache.t4g.micro | $0.0128/hr on Valkey (about $9.34), $0.016/hr on Redis OSS |
| Fargate task, 0.25 vCPU / 0.5 GB | About $9 ($0.04048 per vCPU-hour, $0.004445 per GB-hour) |
| Application Load Balancer | $16.43 + capacity units |
| Public IPv4 addresses | $3.65 each; the ALB holds one per Availability Zone |

- The ALB supports WebSockets. Keep heartbeats under its 60-second idle timeout, or raise it.
- Set a billing alert on day one.
- Accounts opened on or after 15 Jul 2025 get $100 in credits, plus up to $100 more. The Free plan closes the account at 6 months or when credits run out, so pick the Paid plan for a public demo.
- The old 12-month free RDS offer covered accounts opened before 15 Jul 2025, so it has run out for everyone by now.

**Front end:** serve the built React app from the same host as the API. Same origin means no CORS setup and simple cookie sign-in. Vercel Hobby is out: it's for non-commercial use only, and its definition of commercial covers any financial gain for anyone involved.

**On a VM,** Postgres is only as safe as its disk plus your backups. RDS adds managed backups.

## 9. Domains

- `summittech.in` becomes the main client-facing site. For now the existing portfolio (nearly done; still needs the auction case study) serves this role.
- Portfolio moves to a subdomain later, with 301 redirects, once a dedicated client site exists.
- Auction lives at `auction.summittech.in`.

## 10. Interface contract (starting point)

Parallel sessions build against this. Change it here first, then in code. Amounts are integers in minor units (paise or cents), and times are ISO 8601 UTC strings. All REST paths sit under `/api`; the front end owns every other path.

**REST:**

- `POST /api/session {display_name}` → sets a signed session cookie and returns `{bidder_id, display_name}`.
- `GET /api/session` → `{bidder_id, display_name}`, or 401.
- `GET /api/auctions` → `[{id, title, status, high_amount, end_at}]`.
- `GET /api/auctions/{id}` → snapshot: `{id, title, start_price, end_at, status, seq, high_amount, high_bidder_id, high_bidder_name, high_is_bot, recent_bids: [{seq, amount, bidder_name, is_bot, created_at}]}`.
- `POST /api/auctions/{id}/bids {bid_id, amount}` → `{bid_id, status: accepted | rejected | error, reason?, seq?}`, where `reason` is `too_low`, `closed`, `paused` or `not_found`.
- `POST /api/admin/auctions {title, start_price, end_at}` with an `X-Admin-Password` header → the new auction.
- `GET /api/health` → Postgres and Redis status.

**WebSocket** at `/ws/auctions/{id}`, server to client only. Each message is JSON with a `type` field:

- `auction_update {auction_id, seq, high_amount, high_bidder_id, high_bidder_name, is_bot, end_at, status}`. The client shows "you've been outbid" when the high bidder changes from the current user to someone else.
- `auction_closed {auction_id, seq, winner_id, winner_name, amount}`.
- Clients ignore a `type` they don't know. Keepalive is uvicorn's protocol-level ping every 20 seconds; there is no heartbeat message.
- **Close codes** the server sends, and what the client does:
  - `4404`: no such auction. Don't reconnect.
  - `1012`: the server lost, regained or is closing its Redis subscription, or is restarting, so events may have been missed. Reconnect and resync from the snapshot.
  - `1013`: the server isn't subscribed to Redis yet. Reconnect with backoff.
  - `1008`: the client fell more than 100 messages behind. Reconnect and resync. FastAPI also sends `1008` when the id isn't a UUID; the snapshot then answers 422, so stop.
  - Any other close, or a dropped connection: reconnect with backoff and resync.

**Postgres:**

- `auctions`: id, title, start_price, end_at, status (open, paused or closed), high_amount, high_bidder_id, high_bidder_name, high_seq, load_epoch (bumped on every load into Redis; see section 4), seq_ceiling (the highest seq Redis may hand out; see section 4).
- `bids`: bid_id (UUID, primary key), auction_id, seq, bidder_id, bidder_name, amount, is_bot, created_at. Unique on `(auction_id, seq)`.

**Redis:**

- Hash `auction:{id}` with `status`, `end_at_ms`, `start_price`, `high_amount`, `high_bidder_id`, `high_bidder_name`, `high_is_bot`, `seq`, `epoch` (the `load_epoch` it was loaded under) and `ceiling` (the highest seq it may hand out).
- Hash encodings: every value is a string. `status` is `open`, `paused` or `closed`. Number fields are base-10 integers: amounts in minor units, and `end_at_ms` in milliseconds since the Unix epoch. `high_bidder_id` is a lowercase UUID, `high_bidder_name` is plain text, and `high_is_bot` is `1` or `0`. The `high_*` fields are absent until the first accepted bid.
- Pub/Sub channel `auction:{id}:events`, carrying the WebSocket messages above as JSON.
- Locks `lock:job:{name}`.

**Code interfaces (session 0 creates the stubs):**

- `app.bidding.service.place_bid(auction_id, bid_id, bidder_id, bidder_name, amount, is_bot=False) -> BidResult`, used by the bids endpoint and the simulated bidders.
- `app.auth.current_bidder` and `app.auth.require_admin`, both FastAPI dependencies.

## 11. Decisions: defaults and open items

Defaults are in effect unless the owner changes them.

- **Hot-path code** (Lua CAS, write path, close logic): the failing tests come first and the owner approves them before implementation, then reviews every line.
- **Real-time library.** Default: plain FastAPI WebSockets, push only, since bids go over REST. That's little plumbing and keeps load testing simple. Alternative: python-socketio with its Redis manager, which adds rooms and reconnect but ties clients and load tests to the Socket.IO protocol.
- **Other libraries:** redis-py's asyncio client; SQLAlchemy 2.0 async over asyncpg, with plain SQL for the bid write; Alembic for migrations.
- **Sign-in:** guests pick a display name, kept in a signed cookie. The admin page sits behind one password from an environment variable.
- **Observability:** not in Stages 1–2. The public metrics view uses app-level counters (bids accepted and rejected, bid latency percentiles). OpenTelemetry + Grafana come later.
- **Load-test tool:** open until Stage 3. It must drive WebSocket viewers as well as HTTP bids.
- **Host:** open until Stage 2 (section 8).

## 12. Retired or superseded

- C#/.NET 10, Minimal APIs, EF Core, SignalR, xUnit/Testcontainers (as the .NET toolset), NBomber.
- Azure Container Apps as the default host.
- The P0–P6 / M0–M7 / 16-sprint plan and the rule against working ahead of sprints, replaced by section 7.
- Deployment scheduled late (old S15).
- Holding the durability fork open until a failing test forced it: decided 4 Nov as sync dual-write.
- `xmin` optimistic concurrency on the bid path: now admin edits only.
- "Postgres is never on the bid's latency path": it is now on the confirmation path.
- A reload waiting out the insert timeout to catch in-flight inserts: replaced 4 Nov by the `load_epoch` fence (section 4).

## 13. Working preferences that still apply

- "Demo works = done." Polish beyond what the demo needs counts as procrastination.
- Pain-driven: write the failing test first, then fix. Start with Stage 1's Redis-restart test.
- Proposals open with tradeoffs, not benefits.
- Flag version or API uncertainty (FastAPI, redis-py, SQLAlchemy, React) instead of guessing.
- Don't quietly reinvent settled decisions (sections 3–5).
- Adversarial review of plans and docs is welcome.
- Expect solo work to run 1.5–2x estimates.

## 14. Timeline

- Stage 1 target 4 Nov 2026, Stage 2 target 5 Nov 2026, then harden.

## 15. Claude Code sessions

Run session 0 alone, then sessions 1–4 in parallel, then 5, then 6. Each session starts from `main` after the earlier PRs are merged. Every session follows the Git rules, works test-first, treats section 10 as fixed and stays inside the paths it owns.

Shared files belong to session 0: `backend/app/main.py`, `backend/app/config.py`, `backend/app/auth.py`, `backend/pyproject.toml`, `backend/uv.lock`, `backend/tests/conftest.py`, `docker-compose.yml` and `.github/`. A later session that must change one says so in its PR. In cloud sessions, PostgreSQL 16 and Redis 7 are installed but stopped (`service postgresql start`, `service redis-server start`), and Docker Compose is available.

| Session | Branch | Owns | Task | Done when |
| --- | --- | --- | --- | --- |
| 0. Scaffold | `chore/project-scaffold` | The layout and the shared files | Layout: `backend/` (app in `backend/app/`, tests in `backend/tests/`), `frontend/`, `deploy/`, `loadtest/`, `docs/`. FastAPI app with one stub router per module and the section 10 code interfaces, settings from env, guest sign-in and admin check, SQLAlchemy models and the first Alembic migration, every expected backend dependency managed with uv, a Vite + React + TypeScript skeleton, `docker-compose.yml` with postgres, redis, backend and frontend, and GitHub Actions for lint and tests. | `docker compose up` starts everything, `GET /api/health` reports Postgres and Redis up, and CI is green. |
| 1. Bid engine | `feat/bid-engine` | `backend/app/bidding/`, `backend/app/admin/`, `backend/tests/bidding/` | Lua CAS, sync dual-write, pause on a failed insert, reload on `NOT_LOADED`, idempotent `bid_id`, publish after commit, close script and sweeper behind the job lock, `POST /api/auctions/{id}/bids` and `POST /api/admin/auctions`. | Tests pass for concurrent ordering, a Redis restart, an idempotent retry and closing at the deadline. |
| 2. Real-time and demo mode | `feat/realtime-demo-mode` | `backend/app/realtime/`, `backend/app/auctions/`, `backend/app/demo/`, `backend/tests/realtime/` | `/ws/auctions/{id}`, one Pub/Sub listener per instance, `GET /api/auctions`, `GET /api/auctions/{id}`, and labeled simulated bidders behind the job lock that call `place_bid`. | Two WebSocket clients see the same updates in `seq` order, and a client that misses one resyncs from the snapshot. |
| 3. Front end | `feat/frontend-ui` | `frontend/` | Auction list, auction page (countdown from the server's `end_at`, bid box, history, outbid notice, bot labels), sign-in, admin form, and reconnect plus resync on `seq` gaps. Mock the contract until the backend lands. | Every screen works against the mocked contract, and the build and tests pass. |
| 4. Deploy prep | `chore/vm-deploy` | `deploy/`, `docs/deploy.md` | For the one-VM option in section 8: production Compose override, Caddy serving the front end and proxying `/api` and `/ws` with HTTPS on `auction.summittech.in`, `.env.example`, a nightly Postgres backup, and a runbook for first deploy, redeploy and restore. | The production stack starts locally with Caddy's internal certificate, and a WebSocket upgrade works through it. |
| 5. Integration | `feat/stage1-integration` | Wiring across modules | Connect the merged pieces and add an end-to-end test of the Stage 1 done-signal, with two WebSocket clients standing in for browsers. | The Stage 1 done-signal passes in Compose. |
| 6. Proof prep | `feat/load-test-metrics` | `loadtest/`, `backend/app/metrics/`, `README.md`, `docs/case-study.md` | Load test with HTTP bids and WebSocket viewers (the tool choice opens with tradeoffs), app-level metrics counters, and a README plus case-study skeleton in business terms. | The load test runs against Compose and writes a results file. |

## Sources (checked 4 Nov 2026)

- [Amazon Lightsail bundles](https://docs.aws.amazon.com/lightsail/latest/userguide/amazon-lightsail-bundles.html)
- [AWS Free Tier for RDS](https://aws.amazon.com/rds/free/)
- [AWS Free Tier credits and 6-month Free plan](https://aws.amazon.com/about-aws/whats-new/2025/07/aws-free-tier-credits-month-free-plan/)
- [AWS Free Tier limits (StormIT)](https://www.stormit.cloud/blog/aws-free-tier-limits/)
- [ElastiCache API reference: AOF not supported](https://docs.aws.amazon.com/AmazonElastiCache/latest/APIReference/API_Snapshot.html)
- [ElastiCache pricing (Upstash)](https://upstash.com/blog/aws-elasticache-pricing-explained-2026-full-cost-breakdown)
- [RDS db.t4g.micro pricing (Aiven)](https://aiven.io/tools/instances/db.t4g.micro)
- [ALB and public IPv4 pricing (CloudBurn)](https://cloudburn.io/blog/aws-elastic-load-balancing-pricing)
- [Fargate pricing (DEV)](https://dev.to/dspv/what-does-aws-fargate-actually-cost-per-environment-5538)
- [ALB WebSockets and idle timeout (Sockudo)](https://sockudo.io/docs/deployment/cloud-platforms)
- [Render free tier](https://render.com/docs/free)
- [Render pricing (Temps)](https://temps.sh/blog/render-pricing-2026)
- [Vercel WebSockets](https://vercel.com/docs/functions/websockets)
- [Vercel fair use guidelines](https://vercel.com/docs/limits/fair-use-guidelines)
- [SQLAlchemy version counters](https://sqlalchemy.org/docs/orm/versioning.html)
- [Redis script replication](https://medium.com/@jerryk2/redis-transactions-and-lua-script-a9fcf4e1f5f2)
- [Claude Code cloud environments](https://code.claude.com/docs/en/cloud-environments)
