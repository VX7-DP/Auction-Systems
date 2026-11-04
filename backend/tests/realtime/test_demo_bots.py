"""Demo mode: labeled simulated bidders, behind the job lock, bidding through place_bid."""

import asyncio
import random
from collections.abc import Callable

from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import Settings
from app.contract import auction_key, job_lock_key
from app.demo.bots import BOTS, SimulatedBidders, ceiling_for
from app.demo.job_lock import JobLock, run_while_holding
from app.demo.lifespan import BOTS_JOB
from app.main import create_app
from tests.realtime.conftest import FakeEngine, RecordingPlaceBid, in_hours, wait_until

ROSTER = {(bot.bidder_id, bot.name) for bot in BOTS}


def bots(place_bid: RecordingPlaceBid, **kwargs) -> SimulatedBidders:
    kwargs.setdefault("bid_probability", 1.0)
    return SimulatedBidders(place_bid=place_bid, rng=random.Random(7), **kwargs)


async def test_every_bot_bid_is_labeled_and_from_the_roster(
    app: FastAPI, engine: FakeEngine
) -> None:
    for title in ("Lamp", "Clock", "Vase"):
        await engine.create_auction(title=title)
    place_bid = RecordingPlaceBid()

    for _ in range(5):
        await bots(place_bid).bid_once()

    assert len(place_bid.calls) == 15
    assert all(call["is_bot"] is True for call in place_bid.calls)
    assert {(call["bidder_id"], call["bidder_name"]) for call in place_bid.calls} <= ROSTER
    assert all(bot.name.endswith("(bot)") for bot in BOTS)
    assert len({call["bid_id"] for call in place_bid.calls}) == 15


async def test_bots_bid_above_the_live_high_and_within_the_ceiling(
    app: FastAPI, engine: FakeEngine
) -> None:
    fresh = await engine.create_auction(title="Fresh", start_price=1_000)
    contested = await engine.create_auction(title="Contested", start_price=1_000)
    await engine.bid(contested, 1_500, bidder_name="Asha")
    place_bid = RecordingPlaceBid()

    for _ in range(20):
        await bots(place_bid).bid_once()

    amounts = {fresh: set(), contested: set()}
    for call in place_bid.calls:
        amounts[call["auction_id"]].add(call["amount"])
    assert min(amounts[fresh]) > 1_000
    assert min(amounts[contested]) > 1_500
    assert max(amounts[fresh]) <= ceiling_for(fresh, 1_000)
    assert max(amounts[contested]) <= ceiling_for(contested, 1_000)


async def test_a_bot_never_outbids_itself(app: FastAPI, engine: FakeEngine) -> None:
    auction_id = await engine.create_auction()
    leader = BOTS[0]
    await engine.bid(auction_id, 1_500, bidder_id=leader.bidder_id, bidder_name=leader.name)
    place_bid = RecordingPlaceBid()
    runner = bots(place_bid)

    for _ in range(100):
        await runner.bid_once()

    bidders = {call["bidder_id"] for call in place_bid.calls}
    # Every other bot gets a turn; the leader never bids against itself.
    assert bidders == {bot.bidder_id for bot in BOTS[1:]}


async def test_bots_skip_auctions_that_are_not_running_or_at_their_ceiling(
    app: FastAPI, engine: FakeEngine, redis: Redis
) -> None:
    await engine.create_auction(title="Closed", status="closed", end_at=in_hours(-1))
    await engine.create_auction(title="Paused", status="paused")
    await engine.create_auction(title="Expired, not swept yet", end_at=in_hours(-0.01))
    closed_in_redis = await engine.create_auction(title="Closed in Redis only")
    await redis.hset(auction_key(closed_in_redis), "status", "closed")
    topped_out = await engine.create_auction(title="Topped out", start_price=1_000)
    await engine.bid(topped_out, ceiling_for(topped_out, 1_000))
    place_bid = RecordingPlaceBid()

    for _ in range(10):
        await bots(place_bid).bid_once()

    assert place_bid.calls == []


async def test_bots_bid_on_an_auction_redis_has_not_loaded(
    app: FastAPI, engine: FakeEngine
) -> None:
    """After a Redis restart the hash is missing; place_bid reloads it (NOT_LOADED)."""
    auction_id = await engine.create_auction(start_price=2_000, load_redis=False)
    place_bid = RecordingPlaceBid()

    await bots(place_bid).bid_once()

    assert [call["auction_id"] for call in place_bid.calls] == [auction_id]
    assert place_bid.calls[0]["amount"] > 2_000


async def test_bots_do_not_bid_without_a_valid_lease(app: FastAPI, engine: FakeEngine) -> None:
    await engine.create_auction()
    place_bid = RecordingPlaceBid()

    await bots(place_bid, may_bid=lambda: False).bid_once()

    assert place_bid.calls == []


async def test_a_failing_place_bid_does_not_stop_the_bots(app: FastAPI, engine: FakeEngine) -> None:
    await engine.create_auction(title="Lamp")
    await engine.create_auction(title="Clock")
    place_bid = RecordingPlaceBid(error=RuntimeError("Postgres is down"))

    await bots(place_bid).bid_once()
    await bots(place_bid).bid_once()

    assert len(place_bid.calls) == 4


async def test_only_the_lock_holder_bids_and_another_instance_takes_over(
    app: FastAPI, engine: FakeEngine, redis: Redis
) -> None:
    await engine.create_auction()
    calls = {"first": RecordingPlaceBid(), "second": RecordingPlaceBid()}

    def instance(name: str) -> asyncio.Task[None]:
        lock = JobLock(redis, BOTS_JOB, ttl_seconds=1)
        runner = bots(calls[name], may_bid=lock.held, tick_seconds=0.02)
        return asyncio.create_task(
            run_while_holding(lock, runner.run_forever, renew_every=0.05, retry_every=0.05)
        )

    first = instance("first")
    await wait_until(lambda: len(calls["first"].calls) >= 3)
    second = instance("second")
    await asyncio.sleep(0.3)
    assert calls["second"].calls == []

    first.cancel()
    await asyncio.gather(first, return_exceptions=True)
    stopped_at = len(calls["first"].calls)
    await wait_until(lambda: len(calls["second"].calls) >= 3)

    second.cancel()
    await asyncio.gather(second, return_exceptions=True)
    assert len(calls["first"].calls) == stopped_at


async def test_bots_stay_off_when_background_jobs_are_off(app: FastAPI, redis: Redis) -> None:
    assert app.state.demo_bots_task is None
    assert await redis.get(job_lock_key(BOTS_JOB)) is None


async def test_bots_start_behind_the_lock_when_background_jobs_are_on(
    make_settings: Callable[..., Settings], db_engine: AsyncEngine, redis: Redis
) -> None:
    application = create_app(make_settings(run_background_jobs=True))
    async with LifespanManager(application):
        await wait_until(lambda: application.state.demo_bots_task is not None)
        async with asyncio.timeout(5):
            while True:
                if await redis.get(job_lock_key(BOTS_JOB)) is not None:
                    break
                await asyncio.sleep(0.01)

    # Released on shutdown, so the next instance takes over at once.
    assert await redis.get(job_lock_key(BOTS_JOB)) is None
