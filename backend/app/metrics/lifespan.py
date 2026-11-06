from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.metrics import recorder


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Counts from zero on every app start, so the numbers mean "since this instance
    started" (and each test app starts clean)."""
    recorder.reset()
    yield
