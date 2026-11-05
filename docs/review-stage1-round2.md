# Stage 1 review, round 2

This review covers `main` at 43098c9 (#16), checked against CLAUDE.md sections 4 and 10. Round 1 (`docs/review-stage1.md`) didn't see the fixes it led to (#15) or the front-end redesign (#16). Every confirmed finding has a failing test. Following section 11, the hot-path fixes (B1 to B3) wait until the owner approves their tests. The front-end fixes (F1 to F6) are done.

Severity order: lost or misordered bid > wrong live state > crash > minor.

## Confirmed: back end (awaiting approval)

### B1. A bid decided as an auction reopens never becomes its high bid

- **Severity:** lost or misordered bid.
- **Where:** `backend/app/bidding/loader.py`: `reload_auction` → `load_into_redis` → `_announce_reopened`. `load_into_redis` discards the seq LOAD returns. `_announce_reopened` then reads `seq` back from Redis, records it with `record_seq`, and publishes the hash as it is at that moment (`publish_current_state`).
- **Broken claim:** "no confirmed bid is lost and bids are never ordered wrongly", and "only after the commit does the app … publish the update".
- **Scenario:**
  1. Asha's 120 is saved (seq 1). Kenji's 150 can't be saved, so the auction pauses (seq 3).
  2. The sweeper reloads the auction, and LOAD reopens it at seq 4.
  3. Before `_announce_reopened` reads `seq`, Ben's 200 is decided at seq 5.
  4. `record_seq` sets `high_seq = 5`. The reopen event goes out at seq 5 showing Ben's 200, which isn't saved yet.
  5. Ben's save inserts his bid. Its `UPDATE auctions … WHERE high_seq < 5` matches nothing, so `auctions.high_*` stays on Asha's 120.
- **Result:** if no later bid arrives, `record_close` names Asha the winner, although Ben's higher bid is saved. After any reload, Redis takes its floor from Postgres (120), so a bid between 120 and 200 would be accepted after Ben's.
- **Test:** `backend/tests/bidding/test_write_failure.py::test_a_bid_decided_as_the_auction_reopens_becomes_the_high_bid`. It fails with `assert (120, 'Asha') == (200, 'Ben')`.
- **Proposed fix:** `load_into_redis` returns the reopen seq from LOAD's result. `_announce_reopened` records that seq and publishes an update built from what LOAD wrote (the Postgres state), not a later read of the hash.

### B2. A snapshot taken during the close can name a bid that lost

- **Severity:** wrong live state. The wrong winner is shown, and the page never corrects it.
- **Where:** `backend/app/auctions/router.py` (`get_auction`, and `list_auctions` for the amount), during `backend/app/bidding/sweeper.py:close_auction`.
- **Broken claim:** the winner shown is the highest committed bid.
- **Scenario:**
  1. Ben's 120 is saved (seq 1). Just before `end_at`, Asha's 150 is decided (seq 2), but her save hasn't reached the fence.
  2. CLOSE marks Redis `closed` (seq 3). `record_close` fences Asha off and announces Ben at 120.
  3. Until the sweeper's reload, the Redis hash says `closed` with `high_*` = Asha 150, and the snapshot reads it. Every viewer resyncs at exactly this moment, because Asha's seq 2 is never published.
  4. The page's `applySnapshot` sees `closed`, shows Asha as the winner, stops listening, and drops the buffered `auction_closed` event that named Ben.
- **Result:** some pages show the wrong winner for good. If the reload after the close fails, Redis keeps the wrong high bid until it restarts, because the sweeper skips closed auctions.
- **Test:** `backend/tests/bidding/test_close.py::test_a_snapshot_during_the_close_never_names_a_bid_that_lost`. It fails with a closed snapshot naming Asha at 150 while the close announced Ben at 120.
- **Proposed fix:** when Redis says `closed`, the snapshot answers from Postgres. That is final once `record_close` commits. Before then it still shows the pre-close state: `open`, past `end_at`. `list_auctions` keeps Redis's status but takes the amount from Postgres.
  - Tradeoff: for those few ms the snapshot's seq is Postgres's, which can be lower than a seq the page already shows. Pages keep their newer view and pick up the close event.

### B3. A bid confirmed after a pause doesn't lead while paused

- **Severity:** wrong live state.
- **Where:** the `CONFIRM` script in `backend/app/bidding/scripts.py`.
- **Broken claim:** section 10: "while `status` is `paused`, `high_*` equals `confirmed_*`".
- **Scenario:**
  1. Ben's 120 is saved. Asha's 150 commits, but her CONFIRM hasn't run yet.
  2. Kenji's 200 can't be saved. PAUSE copies `confirmed_*`, still Ben's, into `high_*`.
  3. Asha's CONFIRM then raises `confirmed_*` to her bid but leaves `high_*` on Ben.
- **Result:** while paused, viewers see Ben's lower bid as the leader. Asha can get "you've been outbid" by a bid lower than hers.
- **Test:** `backend/tests/bidding/test_write_failure.py::test_a_bid_saved_just_before_a_pause_leads_while_paused`. It fails with `('120', <Ben>) == ('150', <Asha>)`.
- **Proposed fix:** when CONFIRM records a bid while the auction is paused, it sets `high_*` to that bid as well.

## Confirmed: front end (fixed)

| | Bug | Fix | Test (`frontend/src/…`) |
| --- | --- | --- | --- |
| F1 | Pause and reopen events were added to the history as new bids, so a page that had seen a bid still being saved got a duplicate row. | Only a change while `open` counts as a bid. | `live/auctionFeed.test.ts`: "a pause or reopen is not a new bid" |
| F2 | A bid still being saved when the snapshot was read never reached the history: its event arrives at a seq the page already shows and was dropped. | Such an event, with a newer seq than the history and an amount the history lacks, triggers a resync. | "a bid still being saved when the snapshot was read still reaches the history" |
| F3 | `auction_closed` with no winner kept the old amount, and a winning bot lost its label when the page had shown someone else (the B2 case). | Use the event's amount, and take the bot flag from the winning bid's row. | "auction_closed with no winner…", "…keeps a winning bot labelled…" |
| F4 | With no bids, a bid at or below the opening price was sent, and the reply read "Someone bid higher first". | Checked before sending, with a matching hint. | `pages/AuctionPage.test.tsx`: "a first bid must beat the opening price…" |
| F5 | `/auctions/%E0%A4%A` threw in `decodeURIComponent`, and the whole app went blank. | Routed to not found. | `lib/router.test.ts` |
| F6 | An auction that ended with no bids showed "Winning bid" and "Be the first." | It shows "Opening price" and "No bids were placed." | `pages/AuctionPage.test.tsx`: "an auction that ended without bids…" |

Each test failed before its fix.

## Checked, not confirmed

- **Close fence (#15 fix 1).** A save past the fence holds the row, so `record_close` waits and names it the winner. A later save is fenced, reloaded and rejected as `closed`.
- **`BidConflict` (#15 fix 2).** It isn't retried and doesn't pause. A same-auction conflict can't happen, because every LOAD rebuilds the bid memory from Postgres.
- **Seq ceiling.** DECIDE keeps a spare for PAUSE. LOAD's reopen seq fits under the new ceiling, because the block is at least 2. Racing reloads only raise the ceiling.
- **Idempotent retries during a pause.** A retry of the failed bid is saved at its original seq, and the reload reopens it from Postgres.
- **Admin create racing a first bid.** The bid's reload takes a newer epoch, and the admin's LOAD is `STALE`.

## Risks, not fixed

- **`bid_id` reuse as griefing.** A guest can post their own `bid_id` from another auction with a huge amount. Redis accepts it until the conflict reload a few ms later, and meanwhile every other bid on that auction is rejected as `too_low`. Repeating it jams the auction. Stopping it needs a contract decision, for example checking which auction a `bid_id` belongs to before DECIDE.
- **`_pause` re-reads the hash** after PAUSE (`service.py`). It could publish a later, unsaved bid only if Postgres recovered and a reload ran inside that sub-ms window.
- **Write spikes on one auction.** Saves queue on the auction row lock. Past the 1 s statement timeout they fail twice and pause the auction. This is for the Stage 3 load test.
- **`deploy/restore.sh`** brings back the dump's `seq_ceiling`. After a restore, seqs resume below ones that open tabs already saw, so those tabs ignore events until the seq catches up, or until they reload. Raising every `seq_ceiling` by a large constant after `pg_restore` would avoid it.

## Fixes

- F1 to F6: done (4b2df44, 55db2c4, fca59ba, 896730a).
- B1 to B3: the failing tests are committed (32953f6, 1f1dba4, 57aab7c). The fixes wait for the owner's approval of those tests.
