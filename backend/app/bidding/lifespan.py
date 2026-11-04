from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Runs for the app's lifetime, after the shared resources are open.

    Start the close sweeper here, behind its job lock, only when
    get_resources().settings.run_background_jobs is true.
    """
    yield
