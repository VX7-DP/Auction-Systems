from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Runs for the app's lifetime, after the shared resources are open.

    Start this instance's one Pub/Sub listener here. It is per instance, not a
    locked background job.
    """
    yield
