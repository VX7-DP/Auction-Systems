import asyncio
from collections.abc import Iterator
from contextlib import AsyncExitStack

import httpx
import pytest
from fastapi import FastAPI
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import create_async_engine

from app.resources import get_db_engine, get_health_db_engine, get_redis

# Nothing listens on port 1, so connections are refused at once.
DEAD_POSTGRES_URL = "postgresql+asyncpg://nobody:nothing@127.0.0.1:1/nothing"
DEAD_REDIS_URL = "redis://127.0.0.1:1/0"


@pytest.fixture
def overrides(app: FastAPI) -> Iterator[dict]:
    yield app.dependency_overrides
    app.dependency_overrides.clear()


async def test_health_reports_both_up(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "postgres": "up", "redis": "up"}


async def test_health_reports_redis_down(client: httpx.AsyncClient, overrides: dict) -> None:
    dead_redis = Redis.from_url(DEAD_REDIS_URL)
    overrides[get_redis] = lambda: dead_redis
    try:
        response = await client.get("/api/health")
    finally:
        await dead_redis.aclose()

    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "postgres": "up", "redis": "down"}


async def test_health_reports_postgres_down(client: httpx.AsyncClient, overrides: dict) -> None:
    dead_engine = create_async_engine(DEAD_POSTGRES_URL)
    overrides[get_health_db_engine] = lambda: dead_engine
    try:
        response = await client.get("/api/health")
    finally:
        await dead_engine.dispose()

    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "postgres": "down", "redis": "up"}


async def test_health_reports_postgres_up_while_the_app_pool_is_full(
    client: httpx.AsyncClient,
) -> None:
    """Busy is not down: bids holding every pooled connection must not fail the check."""
    engine = get_db_engine()
    async with AsyncExitStack() as held:
        while True:  # check out connections until the pool is exhausted
            try:
                async with asyncio.timeout(0.5):
                    await held.enter_async_context(engine.connect())
            except TimeoutError:
                break

        response = await client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "postgres": "up", "redis": "up"}
