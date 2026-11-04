# Stage 1 review

This review checks the merged Stage 1 code on `main` (up to #14) against CLAUDE.md sections 4 and 10. Every confirmed finding has a failing test. Following section 11, no fix is applied until the owner approves the tests.

Severity order: lost or misordered bid > wrong live state > crash > minor.

## Confirmed

### 1. A bid saved after the close loses to it, so the wrong winner is announced

- **Severity:** lost or misordered bid.
- **Where:** `backend/app/bidding/store.py:272` (`record_close`) and `store.py:146` (the high-bid `UPDATE … WHERE high_seq < :seq`).
- **Broken claim:** "no confirmed bid is lost and bids are never ordered wrongly."
- **Scenario:**
  1. Ben bids 120 and gets seq 1.
  2. Just before `end_at`, Redis accepts Asha's 150 as seq 2. Her save hasn't locked the auction row yet.
  3. The sweeper's CLOSE takes seq 3. `record_close` commits `status = closed`, `high_seq = 3` and the winner Ben/120, then publishes `auction_closed` with Ben/120.
  4. Asha's save then passes the epoch fence, because closing doesn't change `load_epoch`. It inserts her bid row, but its high-bid `UPDATE` matches nothing, since 3 is not less than 2.
  5. She is told **accepted**. Her `auction_update` (seq 2, `status: open`) is published after the close, and the hub drops it.
- **Result:** Asha holds a confirmed bid of 150, but the announced winner and Postgres `high_*` both say Ben at 120. Redis says Asha, so a snapshot contradicts the close event. This window is widest during the end-of-auction rush the demo is meant to show off.
- **Test:** `backend/tests/bidding/test_close.py::test_a_bid_saved_after_the_close_never_loses_to_it`. It fails with `assert [120] == [150]`: the close announced 120 although 150 was accepted.

### 2. A `bid_id` reused from another auction pauses the target auction

- **Severity:** wrong live state. Any signed-in guest can also pause any auction this way.
- **Where:** `backend/app/bidding/service.py:121` catches `store.BidConflict` (`store.py:144`) as an ordinary write failure. It retries `insert_attempts` times and then calls `_pause`. The pause publishes the Redis hash, which by then holds the refused bid as the high bid.
- **Broken claim:** only a failed Postgres write pauses an auction. Nothing is published that wasn't committed.
- **Scenario:**
  1. Bid X is accepted on auction A.
  2. The same `bid_id` X is posted to auction B. B's bid memory has never seen X, so DECIDE accepts it and writes it into B's `high_*`.
  3. Postgres refuses the insert, because X already belongs to A, and raises `BidConflict`.
  4. B is paused, and the pause event shows X as B's high bid.
- **Test:** `backend/tests/bidding/test_idempotent_retry.py::test_a_bid_id_from_another_auction_does_not_pause_or_show_on_this_one`. It fails with `assert 'paused' == 'open'`. The log shows `BidConflict` retried twice.

### 3. A failed bid is shown as the high bid while the auction is paused

- **Severity:** wrong live state.
- **Where:** `backend/app/bidding/service.py:132` (`_pause` → `publish_current_state`) and the snapshot in `backend/app/auctions/router.py:149`. DECIDE has already written the unsaved bid into the hash's `high_*`.
- **Broken claim:** "only after the commit does the app … publish the update." Section 4 accepts that a seq can be visible before Postgres records it. It doesn't accept announcing a bid that is already known to have failed as the leader.
- **Scenario:**
  1. Ben's 120 is saved.
  2. Asha's 150 can't be saved, and she is told "error".
  3. The paused event, and every snapshot until the reload, show Asha/150 as the high bid. Ben gets "you've been outbid" from a bid that doesn't exist.
  4. On unpause, the high drops back to 120 with no notice.
- **Test:** `backend/tests/bidding/test_write_failure.py::test_pause_event_keeps_the_last_saved_high_bid`. It fails with `(150, Asha) == (120, Ben)`.

### 4. The front end ignores WebSocket close codes

- **Severity:** minor.
- **Where:** `frontend/src/live/auctionFeed.ts:365` (`onclose` never reads `code`).
- **Broken claim:** section 10 says "4404: don't reconnect."
- **Scenario:** the socket closes with 4404 before the snapshot request answers, so the feed reconnects after `RECONNECT_BASE_MS`. If that snapshot fails with a network error rather than a 404, it keeps reconnecting with backoff indefinitely.
- **Test:** `frontend/src/live/auctionFeed.test.ts`, "a 4404 close does not reconnect, even before the snapshot answers". It fails because a second socket opens.

## Checked, not confirmed

None of these produced a failing interleaving:

- **Lua CAS and seq ceiling.** Each script runs atomically. DECIDE keeps one seq spare, so PAUSE always has room. CLOSE asks for a reservation at the ceiling. LOAD resumes at the highest of `high_seq`, Redis's seq and the old ceiling. The ceiling only goes up (`GREATEST` in `raise_seq_ceiling` and `read_for_load`).
- **`load_epoch` fence.** A reload's `FOR UPDATE` waits for a write already past the fence, and a later write sees the new epoch and is fenced. No Redis call happens inside a Postgres transaction, so the two stores can't deadlock each other. Racing loads are ordered by LOAD's `STALE` check.
- **`NOT_LOADED`.** The reload lock serializes reloads. A second epoch bump can only happen if the lock's TTL runs out mid-reload, and that is still safe.
- **Idempotent `bid_id` across a Redis restart.** Every LOAD rebuilds the bid memory from Postgres, so a repeat returns the original seq and amount. Rejected bids aren't remembered: a repeat is decided again, which has no side effects.
- **Deadline.** `now_ms >= end_at_ms` rejects a bid at exactly `end_at`. A paused auction past `end_at` rejects bids as `closed`, is reopened by the sweeper, and is then closed.
- **Job lock.** Bots check `lock.held()` before each bid and are cancelled when a renewal fails. A sweeper pass that outlives the TTL can overlap with another instance, but `record_close` and reloads are idempotent.
- **Real-time.** Close codes 4404, 1012, 1013 and 1008 are sent as section 10 says. A client is cut off when its 101st message is pending. Each (re)subscription closes every socket with 1012. Older seqs are dropped in the hub. The client resyncs on a gap and on every reconnect.
- **Errors.** `place_bid` turns any exception into `status: error`, and the endpoint answers 503.

Two risks are not bugs today:

- A reload that holds the auction row for longer than `insert_attempts × insert_timeout_ms` makes concurrent bids time out and pause the auction instead of being fenced. That is degraded service, not a lost bid. It's worth watching in the Stage 3 load test.
- LOAD passes every bid of the auction as script arguments, which grows without limit on long auctions. Section 4 says "recent IDs".

## Fixes

The owner approved the four failing tests on 4 Nov and chose option (a) for fix 2.

1. **Fence the close.** `record_close` now bumps `load_epoch` as well as checking it. A bid decided before the close but not yet saved is fenced, reloaded and rejected as `closed`. A save already holding the row lock commits first, and the close then returns it as the winner. After a close, the sweeper reloads the auction so Redis matches Postgres.
   - Tradeoff: a bid that Redis accepted before `end_at` but that hadn't reached Postgres is rejected as `closed`.
2. **`BidConflict` is a refusal, not a write failure.** It isn't retried and doesn't pause the auction. The auction is reloaded, which drops the refused decision from Redis, and the bid is rejected with the new reason `duplicate_bid_id` (section 10).
3. **A pause shows the last confirmed bid.** The Redis hash gains `confirmed_*` fields (section 10):
   - LOAD sets them from Postgres.
   - A new `CONFIRM` script raises them after each commit, and publishes the bid's `auction_update` in the same call, so the bid path makes no extra round trip.
   - PAUSE copies them into `high_*`, or clears `high_*` if there are none.
   - If `CONFIRM` fails after a commit, a later pause can show an older confirmed bid, but never an unsaved one.
4. **Front end:** a 4404 close stops the feed as "not found" and doesn't reconnect.

`test_redis_restart.py` pins the exact Redis hash, so its expected value now includes the `confirmed_*` fields.
