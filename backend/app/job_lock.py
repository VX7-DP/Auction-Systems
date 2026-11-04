"""The Redis lock that keeps a background job on one instance (CLAUDE.md section 4).

Shared by every background job: the close sweeper and the simulated bidders.

``SET lock:job:{name} <token> NX PX <ttl>``, renewed while held. Renew and release
only touch the key if it still holds this instance's token. Rolling deploys briefly
run two instances; the lock keeps the job on one of them.

Two ways to use it: ``run_while_holding`` runs a long-lived job only while the lock is
held (the bots), and ``hold()`` takes or renews it in one step for a job that checks
before each pass (the close sweeper).

The lock also tracks a local lease deadline: ``held()`` turns false once the TTL
could have run out since the last renewal, even if this process stalled and never
heard back from Redis. Jobs check it before each action.
"""

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable

from redis.asyncio import Redis

from app.contract import job_lock_key

logger = logging.getLogger(__name__)

_RENEW = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('PEXPIRE', KEYS[1], ARGV[2])
end
return 0
"""

_HOLD = """
local holder = redis.call('GET', KEYS[1])
if holder == ARGV[1] then
    redis.call('PEXPIRE', KEYS[1], ARGV[2])
    return 1
end
if not holder then
    redis.call('SET', KEYS[1], ARGV[1], 'PX', ARGV[2])
    return 1
end
return 0
"""

_RELEASE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""


class JobLock:
    def __init__(self, redis: Redis, name: str, *, ttl_seconds: float) -> None:
        self.redis = redis
        self.key = job_lock_key(name)
        self.token = uuid.uuid4().hex
        self.ttl_ms = max(1, int(ttl_seconds * 1000))
        self._valid_until = 0.0

    def held(self) -> bool:
        return time.monotonic() < self._valid_until

    async def acquire(self) -> bool:
        started = time.monotonic()
        if await self.redis.set(self.key, self.token, nx=True, px=self.ttl_ms):
            self._valid_until = started + self.ttl_ms / 1000
            return True
        return False

    async def renew(self) -> bool:
        started = time.monotonic()
        if await self.redis.eval(_RENEW, 1, self.key, self.token, self.ttl_ms):
            self._valid_until = started + self.ttl_ms / 1000
            return True
        self._valid_until = 0.0
        return False

    async def hold(self) -> bool:
        """Takes the lock if it is free, or renews it if this instance holds it."""
        started = time.monotonic()
        if await self.redis.eval(_HOLD, 1, self.key, self.token, self.ttl_ms):
            self._valid_until = started + self.ttl_ms / 1000
            return True
        self._valid_until = 0.0
        return False

    async def release(self) -> None:
        self._valid_until = 0.0
        await self.redis.eval(_RELEASE, 1, self.key, self.token)


async def run_while_holding(
    lock: JobLock,
    job: Callable[[], Awaitable[None]],
    *,
    renew_every: float,
    retry_every: float,
) -> None:
    """Run ``job`` only while ``lock`` is held, until cancelled.

    Waits for the lock, starts the job, renews every ``renew_every`` seconds, and
    cancels the job the moment a renewal fails. Then it goes back to waiting.
    """
    while True:
        try:
            acquired = await lock.acquire()
        except Exception:
            logger.warning("Could not reach Redis for lock %s", lock.key, exc_info=True)
            acquired = False
        if not acquired:
            await asyncio.sleep(retry_every)
            continue

        logger.info("Took lock %s; starting the job", lock.key)
        job_task = asyncio.create_task(job())
        try:
            while not job_task.done():
                await asyncio.wait({job_task}, timeout=renew_every)
                if job_task.done():
                    break
                try:
                    renewed = await lock.renew()
                except Exception:
                    logger.warning("Could not renew lock %s", lock.key, exc_info=True)
                    renewed = False
                if not renewed:
                    logger.warning("Lost lock %s; stopping the job", lock.key)
                    break
        finally:
            job_task.cancel()
            results = await asyncio.gather(job_task, return_exceptions=True)
            try:
                await lock.release()
            except Exception:
                logger.warning("Could not release lock %s", lock.key, exc_info=True)
        if isinstance(results[0], Exception):
            logger.error("Job under lock %s failed", lock.key, exc_info=results[0])
        await asyncio.sleep(retry_every)
