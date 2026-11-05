# Case study: a live auction that never loses a bid

> **Draft.** Sections marked _TBD_ wait for numbers from a load test on the live site. Every number in this document comes from a test or a recorded run, and each one names its source.

## The problem

When hundreds of people bid on the same item in the same second, two things usually go wrong:

- **Bids get lost.** A bidder is told "you're winning", then the bid isn't there when the auction ends.
- **Bids get shuffled.** Two near-simultaneous bids are recorded in the wrong order, so the wrong person wins.

Both cost money and trust. The same problem shows up in flash sales, ticket drops and any live dashboard where many people act on one thing at once.

## What was built

A live auction site. People sign in with a display name, watch the price move in real time, bid, and get an instant "you've been outbid" notice. A countdown shows when the auction ends, and simulated bidders keep a demo auction busy even for a lone visitor. They're labelled as bots.

- Live at: `auction.summittech.in` (_TBD: not deployed yet_)
- Source: this repository

## The promises, and the proof

| Promise | Proof | Result |
| --- | --- | --- |
| Once a bidder is told "your bid is in", it is saved for good | A test restarts the live-price store in the middle of an auction and checks that every confirmed bid survives. It runs on every code change | Passes on every change. Load test: _TBD_ confirmed bids, _TBD_ saved |
| Bids land in the order they were made | 100 bids sent at the same instant must be accepted in strictly rising order. After each load test, every saved bid is checked against the one before it | Passes on every change. Load test: _TBD_ out-of-order bids |
| Everyone watching ends up seeing the same, correct price | In the load test, every viewer must end on the final price | Load test: _TBD_ of _TBD_ viewers |
| It stays fast with many people at once | Load test with simulated viewers and bidders on one auction | See below |

## Load test results

_TBD: run `python3 loadtest/run.py` against the live site's setup, commit the results file, and fill this table from it._

| Measure | Result |
| --- | --- |
| People watching at once | _TBD_ |
| People bidding at once | _TBD_ |
| Bids per second | _TBD_ |
| Bid response time, typical (p50) | _TBD_ |
| Bid response time, 95% of bids under (p95) | _TBD_ |
| Bid response time, 99% of bids under (p99) | _TBD_ |
| Time until a new bid shows on screen (p50 / p95) | _TBD_ |
| Confirmed bids lost | _TBD_ |
| Bids saved out of order | _TBD_ |
| Server | _TBD: machine size and monthly cost_ |
| Results file | _TBD: `loadtest/results/…json`_ |

## How it works, in short

- **A fast in-memory store makes each decision.** Every bid is checked and ordered in one step, so two bids can never both win the same price.
- **The database confirms it.** A bid only counts once the database has saved it. The bidder is told "accepted" after that, never before.
- **Everyone sees it live.** Each confirmed bid is pushed to every open page. If a page misses an update, it fetches a fresh copy and catches up.

## Tradeoffs made on purpose

- **Safety over a few milliseconds.** Waiting for the database adds its save time to every bid response. That's the price of "a confirmed bid is never lost". Measured cost: _TBD_.
- **It stops rather than guesses.** If the database is down, bidding pauses instead of taking bids it can't save. The auction reopens on its own once the database is back.
- **One live-price store.** All bids for an auction go through one in-memory store. That keeps ordering simple and correct. The limit on how much one store can take: _TBD from the load test_.
- **Metrics per server.** The public numbers are counted by each server since it last started. That's simple and enough for one server; several servers would need a shared counter.

## Timeline

| Milestone | Date |
| --- | --- |
| Core loop working locally: live bidding, outbid notices, closing on time, surviving a restart of the live-price store | 4 Nov 2026 |
| Live on `auction.summittech.in` | _TBD_ |
| Load test on the live site and this case study published | _TBD_ |

## Where it goes next

- More servers behind a load balancer. Live updates already reach viewers connected to any server.
- Managed cloud hosting (AWS) for clients who need it.
- Dashboards and tracing, if a client needs them.

Each of these is a known next step, not something built or measured yet.
