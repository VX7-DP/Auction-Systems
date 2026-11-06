# Auction-Systems

A real-time auction system where many bidders race to raise one auction's high bid. **The guarantee: no confirmed bid is lost, and bids are never ordered wrongly, even during write spikes.**

The same engine fits any "many people, one hot item, live updates" problem: flash sales, ticket drops, live bidding, live dashboards.

## How it works

- **Redis decides.** An atomic Lua compare-and-set script in Redis accepts or rejects each bid. It checks that the auction is open, the deadline hasn't passed, the bid beats the current high without raising it by more than one step allows, and the bid isn't a duplicate. It then gives the bid the next sequence number.
- **Postgres confirms.** A bid counts as confirmed only after PostgreSQL has committed it. Postgres is the source of truth, and Redis can always be rebuilt from it.
- **Everyone sees it live.** Updates reach every viewer over WebSockets, fanned out through Redis Pub/Sub. Each event carries its sequence number, so a client that misses one refetches a snapshot.

## Bid rules

- **Beat the current price.** A bid must be higher than the current high bid, or than the opening price before anyone has bid.
- **One raise at a time.** A single bid can at most double the price, or add ₹1,000 if that is more. At ₹5,000, the most one bid can reach is ₹10,000. This stops one bidder from ending the contest with an absurd jump.
- **Changing the limits.** They are `BIDDING_MAX_RAISE_PERCENT` (default 100) and `BIDDING_MIN_MAX_RAISE` (in paise or cents, default 100000). The front end mirrors them in `frontend/src/config.ts` so it can warn before sending, so change both together. The server's check is the one that counts.

## Stack

| Part | Technology |
| --- | --- |
| API | Python 3.13, FastAPI, SQLAlchemy (async), Alembic, managed with uv |
| Front end | React + TypeScript, built with Vite |
| Hot path | Redis 7 (Lua scripts, Pub/Sub) |
| Source of truth | PostgreSQL 16 |
| Packaging and CI | Docker Compose, GitHub Actions |

## Status

| Area | State |
| --- | --- |
| Project scaffold, health check, guest sign-in, admin check | Done |
| Bid engine, real-time updates, demo bidders, front end | Done. CI runs an end-to-end test on the full stack: two bidders and the simulated bidders, a Redis restart mid-auction, then the close |
| Deployment to `auction.summittech.in` | Production setup ready ([docs/deploy.md](docs/deploy.md)); not live yet |
| Load tests, public metrics, case study | Planned |

## Run it locally

You need Docker with Docker Compose.

```bash
docker compose up --build
```

- App: http://localhost:5173
- API docs: http://localhost:8000/api/docs
- Health: http://localhost:8000/api/health
- The admin password is `admin` (local only).
- On Windows, if Docker says a port is "not available" or "forbidden", see the port note in [docs/development.md](docs/development.md#run-the-whole-stack).

## Tests

```bash
docker compose up -d --wait postgres redis
cd backend && uv sync && uv run pytest
```

```bash
cd frontend && npm ci && npm test
```

The end-to-end test drives the whole stack, two bidders and the simulated bidders through a Redis restart to the close. CI runs it on every push; to run it locally, see [docs/development.md](docs/development.md#end-to-end-test).

Lint, migrations, and how to add code are covered in [docs/development.md](docs/development.md).

## Repository layout

```
backend/    FastAPI app (app/), Alembic migrations (migrations/), tests (tests/)
frontend/   React + TypeScript app
deploy/     Production setup for the one-VM deploy
loadtest/   Load tests (planned)
docs/       Developer and project docs
```

## Docs

- [CLAUDE.md](CLAUDE.md): the project spec, with decisions, the interface contract and the build plan.
- [docs/development.md](docs/development.md): running, testing and extending the code.
- [docs/deploy.md](docs/deploy.md): deploying to one VM, with backups and restore.
