"""The Stage 1 done-signal (CLAUDE.md section 7), end to end against the running stack.

Two guests, each with the auction page open over a WebSocket like a browser tab, bid on
one auction alongside the simulated bidders. Outbid notices fire, Redis restarts
mid-auction without losing a confirmed bid or the high bid, and the auction closes on
time. Nothing is faked: the real bid engine, fan-out, bots and close sweeper run.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from tests.e2e.clients import Attempt, Guest, Viewer
from tests.e2e.conftest import Saved, Stack

AUCTION_SECONDS = 60
# Minor units. The bots stop at 2-4x the start price and raise it by 2-10% a bid, so
# this leaves them well over a dozen bids: enough to stay active for the whole auction.
START_PRICE = 500_000
STEP = 1_000
BOT_WAIT_SECONDS = 25
RECOVERY_SECONDS = 30
# The close sweeper runs every second.
CLOSE_GRACE_SECONDS = 5


def assert_saved(saved: Saved, confirmed: list[Attempt], viewers: list[Viewer]) -> None:
    """Every bid confirmed to anyone is in Postgres as confirmed, in the right order."""
    by_id = {bid["bid_id"]: bid for bid in saved.bids}
    for attempt in confirmed:
        row = by_id.get(attempt.bid_id)
        assert row is not None, f"confirmed bid {attempt} is not saved"
        assert (row["seq"], row["amount"], row["bidder_id"]) == (
            attempt.seq,
            attempt.amount,
            attempt.bidder_id,
        ), (attempt, row)

    by_seq = {bid["seq"]: bid for bid in saved.bids}
    for viewer in viewers:
        for seen in viewer.bids:
            row = by_seq.get(seen.seq)
            assert row is not None, f"bid announced at seq {seen.seq} is not saved: {seen}"
            assert (row["amount"], row["bidder_id"], row["bidder_name"], row["is_bot"]) == (
                seen.amount,
                seen.bidder_id,
                seen.bidder_name,
                seen.is_bot,
            ), (seen, row)

    amounts = [bid["amount"] for bid in saved.bids]
    assert amounts == sorted(set(amounts)), f"a lower bid landed after a higher one: {amounts}"
    last = saved.bids[-1]
    auction = saved.auction
    assert (auction["high_amount"], auction["high_bidder_id"]) == (
        last["amount"],
        last["bidder_id"],
    )
    assert auction["high_seq"] >= last["seq"]


def high_at(saved: Saved, seq: int) -> int | None:
    """The high bid as of ``seq``: the saved bid with the largest seq up to it."""
    amounts = [bid["amount"] for bid in saved.bids if bid["seq"] <= seq]
    return amounts[-1] if amounts else None


@pytest.mark.timeout(AUCTION_SECONDS + 120)
async def test_two_guests_and_the_bots_through_a_redis_restart_to_the_close(
    stack: Stack,
) -> None:
    end_at = (datetime.now(UTC) + timedelta(seconds=AUCTION_SECONDS)).replace(microsecond=0)
    auction = await stack.create_auction(
        title=f"End-to-end {uuid.uuid4().hex[:8]}", start_price=START_PRICE, end_at=end_at
    )
    end_at = datetime.fromisoformat(auction["end_at"])
    alice = await stack.guest("Alice", auction)
    bob = await stack.guest("Bob", auction)
    guests: list[Guest] = [alice, bob]
    viewers = [guest.viewer for guest in guests]
    for viewer in viewers:
        await viewer.wait_until(lambda v=viewer: v.live, within=10, what="the page to go live")
    confirmed: list[Attempt] = []

    # 1. Two people outbid each other, and the one outbid is told.
    first = await alice.bid_above_high(STEP)
    second = await bob.bid_above_high(STEP)
    confirmed += [first, second]
    assert second.seq > first.seq
    await alice.viewer.wait_until(
        lambda: alice.viewer.seq >= second.seq, within=10, what="Bob's bid on Alice's page"
    )
    assert any(first.seq < notice.seq <= second.seq for notice in alice.viewer.outbids), (
        alice.viewer.report()
    )

    # 2. A simulated bidder outbids Bob, and Bob is told it was a bot.
    await bob.viewer.wait_until(
        lambda: any(notice.seq > second.seq for notice in bob.viewer.outbids),
        within=BOT_WAIT_SECONDS,
        what="a simulated bidder to outbid Bob (are RUN_BACKGROUND_JOBS and DEMO_BOTS_ENABLED on?)",
    )
    notice = next(notice for notice in bob.viewer.outbids if notice.seq > second.seq)
    assert notice.by_is_bot is True and (notice.by_name or "").endswith("(bot)"), notice

    # 3. Redis restarts mid-auction while both guests keep bidding.
    before = await stack.saved(auction["id"])
    synced = [viewer.synced for viewer in viewers]
    stop = asyncio.Event()
    loops = [asyncio.create_task(guest.keep_bidding(stop, STEP)) for guest in guests]
    await asyncio.sleep(0.5)
    await stack.restart_redis()
    await asyncio.sleep(1.0)
    stop.set()
    during: list[Attempt] = []
    for guest, loop in zip(guests, loops, strict=True):
        for attempt in await loop:
            # A bid whose outcome is unknown is retried with its bid_id, as the page does.
            during.append(await guest.settle(attempt))
    assert all(attempt.status == "accepted" or attempt.reason == "too_low" for attempt in during), (
        during
    )
    confirmed += [attempt for attempt in during if attempt.status == "accepted"]

    # Every page is told to reconnect, and resyncs from the snapshot.
    for viewer, count in zip(viewers, synced, strict=True):
        await viewer.wait_until(
            lambda v=viewer, c=count: v.synced > c and v.live,
            within=RECOVERY_SECONDS,
            what="the page to reconnect and resync after the Redis restart",
        )

    after = await stack.saved(auction["id"])
    assert_saved(after, confirmed, viewers)
    assert [bid for bid in after.bids if bid["seq"] <= before.bids[-1]["seq"]] == before.bids
    # The auction was rebuilt in Redis from Postgres: a new load epoch...
    assert after.auction["load_epoch"] > before.auction["load_epoch"]
    for viewer in viewers:
        assert viewer.state is not None
        assert viewer.state["high_amount"] == high_at(after, viewer.seq) or (
            viewer.seq > after.bids[-1]["seq"]  # a bot bid landed after the read above
        ), viewer.report()
    # ...holding the right high bid: matching it is too low, beating it is accepted.
    high = after.bids[-1]
    matching = await alice.bid(high["amount"])
    assert (matching.status, matching.reason) == ("rejected", "too_low"), matching
    beating = await alice.bid_above_high(STEP)
    confirmed.append(beating)
    assert beating.seq is not None and beating.seq > high["seq"]

    # 4. The auction closes on time.
    await asyncio.sleep((end_at - datetime.now(UTC)).total_seconds() + 0.5)
    late = await bob.bid(beating.amount * 2)
    assert (late.status, late.reason) == ("rejected", "closed"), late
    for viewer in viewers:
        await viewer.wait_until(
            lambda v=viewer: v.closed is not None,
            within=max(0.1, (end_at - datetime.now(UTC)).total_seconds() + CLOSE_GRACE_SECONDS),
            what="the auction to close",
        )

    final = await stack.saved(auction["id"])
    assert final.auction["status"] == "closed"
    assert_saved(final, confirmed, viewers)
    winner = final.bids[-1]
    for viewer in viewers:
        closed = viewer.closed
        assert closed is not None and viewer.closed_at is not None
        assert (closed["winner_id"], closed["winner_name"], closed["amount"]) == (
            winner["bidder_id"],
            winner["bidder_name"],
            winner["amount"],
        )
        assert closed["seq"] > winner["seq"]
        assert (viewer.closed_at - end_at).total_seconds() <= CLOSE_GRACE_SECONDS
    # The bots came back after the restart and bid through the rebuilt state.
    assert any(bid["is_bot"] and bid["seq"] > beating.seq for bid in final.bids), final.bids

    # Each page applied events strictly in seq order, never saw the seq go backwards,
    # and both pages ended in the same state.
    for viewer in viewers:
        seqs = [event["seq"] for event in viewer.applied]
        assert seqs == sorted(set(seqs)), viewer.report()
        assert viewer.stale_snapshots == 0, viewer.report()
    assert alice.viewer.state == bob.viewer.state
