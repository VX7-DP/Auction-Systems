"""Connection pools shared by the whole process: the Postgres engine and the Redis client.

``main.create_app`` opens them when the app starts and closes them when it stops.
Request handlers get them through the FastAPI dependencies below. Plain functions
whose signature is fixed by the contract, such as ``app.bidding.service.place_bid``,
call ``get_resources()`` instead.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import Settings


@dataclass(frozen=True)
class Resources:
    settings: Settings
    db_engine: AsyncEngine
    db_sessionmaker: async_sessionmaker[AsyncSession]
    # decode_responses=True: commands return str, not bytes.
    redis: Redis


_current: Resources | None = None


@asynccontextmanager
async def open_resources(settings: Settings) -> AsyncIterator[Resources]:
    """Create the pools, make them current for the app's lifetime, then close them."""
    global _current
    if _current is not None:
        raise RuntimeError("Resources are already open in this process.")
    db_engine = create_async_engine(settings.database_url)
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    _current = Resources(
        settings=settings,
        db_engine=db_engine,
        db_sessionmaker=async_sessionmaker(db_engine, expire_on_commit=False),
        redis=redis,
    )
    try:
        yield _current
    finally:
        _current = None
        await redis.aclose()
        await db_engine.dispose()


def get_resources() -> Resources:
    if _current is None:
        raise RuntimeError("Resources are not open: call this inside the running app.")
    return _current


# --- FastAPI dependencies -----------------------------------------------------------


async def get_db_session() -> AsyncIterator[AsyncSession]:
    async with get_resources().db_sessionmaker() as session:
        yield session


def get_redis() -> Redis:
    return get_resources().redis
