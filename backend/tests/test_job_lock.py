"""The Redis lock that keeps background jobs on one instance (SET NX PX, renewed)."""

import asyncio

from redis.asyncio import Redis

from app.contract import job_lock_key
from app.job_lock import JobLock, run_while_holding
from tests.realtime.conftest import wait_until


async def test_only_one_holder_at_a_time(redis: Redis) -> None:
    first, second = JobLock(redis, "job", ttl_seconds=5), JobLock(redis, "job", ttl_seconds=5)

    assert await first.acquire()
    assert not await second.acquire()
    assert await redis.get(job_lock_key("job")) == first.token
    assert first.held() and not second.held()


async def test_renew_fails_once_the_key_is_gone_or_taken(redis: Redis) -> None:
    lock = JobLock(redis, "job", ttl_seconds=5)
    assert await lock.acquire()
    assert await lock.renew()

    await redis.set(job_lock_key("job"), "someone-else")

    assert not await lock.renew()
    assert not lock.held()

    await redis.delete(job_lock_key("job"))
    assert not await lock.renew()


async def test_release_only_deletes_its_own_token(redis: Redis) -> None:
    lock = JobLock(redis, "job", ttl_seconds=5)
    assert await lock.acquire()
    await redis.set(job_lock_key("job"), "someone-else")

    await lock.release()

    assert await redis.get(job_lock_key("job")) == "someone-else"


async def test_an_expired_lock_can_be_taken_over(redis: Redis) -> None:
    first, second = JobLock(redis, "job", ttl_seconds=0.1), JobLock(redis, "job", ttl_seconds=5)
    assert await first.acquire()

    await asyncio.sleep(0.2)

    assert not first.held()
    assert await second.acquire()


async def test_the_job_runs_only_on_the_holder_and_moves_when_it_stops(redis: Redis) -> None:
    running: set[str] = set()

    def job(name: str):
        async def run() -> None:
            running.add(name)
            try:
                await asyncio.Event().wait()
            finally:
                running.discard(name)

        return run

    def runner(name: str) -> asyncio.Task[None]:
        lock = JobLock(redis, "job", ttl_seconds=1)
        return asyncio.create_task(
            run_while_holding(lock, job(name), renew_every=0.05, retry_every=0.05)
        )

    first = runner("first")
    await wait_until(lambda: running == {"first"})
    second = runner("second")
    await asyncio.sleep(0.3)
    assert running == {"first"}

    first.cancel()
    await asyncio.gather(first, return_exceptions=True)
    await wait_until(lambda: running == {"second"})

    second.cancel()
    await asyncio.gather(second, return_exceptions=True)
    assert running == set()
    assert await redis.get(job_lock_key("job")) is None


async def test_the_job_stops_when_the_lock_is_lost(redis: Redis) -> None:
    running = asyncio.Event()
    stopped = asyncio.Event()

    async def job() -> None:
        running.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    lock = JobLock(redis, "job", ttl_seconds=5)
    task = asyncio.create_task(run_while_holding(lock, job, renew_every=0.05, retry_every=10))
    try:
        await asyncio.wait_for(running.wait(), 5)
        # Another instance holds it now, e.g. after this one stalled past the TTL.
        await redis.set(job_lock_key("job"), "someone-else")

        await asyncio.wait_for(stopped.wait(), 5)
        assert not lock.held()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_hold_takes_a_free_lock_and_renews_its_own(redis: Redis) -> None:
    """What the close sweeper does before each pass."""
    lock = JobLock(redis, "job", ttl_seconds=5)

    assert await lock.hold()
    assert await redis.get(job_lock_key("job")) == lock.token
    await redis.pexpire(job_lock_key("job"), 100)

    assert await lock.hold()
    assert await redis.pttl(job_lock_key("job")) > 1_000
    assert lock.held()


async def test_hold_refuses_a_lock_another_instance_holds(redis: Redis) -> None:
    first, second = JobLock(redis, "job", ttl_seconds=5), JobLock(redis, "job", ttl_seconds=5)
    assert await first.hold()

    assert not await second.hold()
    assert not second.held()
    assert await redis.get(job_lock_key("job")) == first.token

    await first.release()
    assert await second.hold()
