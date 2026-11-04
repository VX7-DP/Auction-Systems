import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.demo.bots import SimulatedBidders
from app.demo.job_lock import JobLock, run_while_holding
from app.demo.settings import DemoSettings
from app.resources import get_resources

BOTS_JOB = "simulated_bidders"
LOCK_TTL_SECONDS = 10.0
LOCK_RENEW_SECONDS = 3.0


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Runs the simulated bidders, on whichever instance holds their job lock.

    Off unless RUN_BACKGROUND_JOBS and DEMO_BOTS_ENABLED are both true.
    """
    app.state.demo_bots_task = None
    resources = get_resources()
    demo = DemoSettings()
    if not (resources.settings.run_background_jobs and demo.bots_enabled):
        yield
        return

    lock = JobLock(resources.redis, BOTS_JOB, ttl_seconds=LOCK_TTL_SECONDS)
    bots = SimulatedBidders(tick_seconds=demo.bot_tick_seconds, may_bid=lock.held)
    task = asyncio.create_task(
        run_while_holding(
            lock, bots.run_forever, renew_every=LOCK_RENEW_SECONDS, retry_every=LOCK_RENEW_SECONDS
        ),
        name="demo-bots",
    )
    app.state.demo_bots_task = task
    try:
        yield
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
