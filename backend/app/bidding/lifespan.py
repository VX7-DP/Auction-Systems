import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.bidding.settings import load_bidding_settings
from app.bidding.sweeper import run_sweeper
from app.resources import get_resources


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Reads the bidding settings, and starts the close sweeper (behind its job lock)
    when background jobs are on."""
    settings = load_bidding_settings()
    sweeper = None
    if get_resources().settings.run_background_jobs:
        sweeper = asyncio.create_task(run_sweeper(settings), name="close-sweeper")
    try:
        yield
    finally:
        if sweeper is not None:
            sweeper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await sweeper
