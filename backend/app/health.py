"""GET /api/health: reports whether Postgres and Redis answer."""

import asyncio
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response, status
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.contract import Health
from app.resources import get_redis, get_resources

CHECK_TIMEOUT_SECONDS = 2.0

router = APIRouter(prefix="/api", tags=["health"])


def get_db_engine() -> AsyncEngine:
    return get_resources().db_engine


async def _postgres_up(engine: AsyncEngine) -> bool:
    try:
        async with asyncio.timeout(CHECK_TIMEOUT_SECONDS), engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception:
        return False
    return True


async def _redis_up(redis: Redis) -> bool:
    try:
        async with asyncio.timeout(CHECK_TIMEOUT_SECONDS):
            await redis.ping()
    except Exception:
        return False
    return True


def _state(up: bool) -> Literal["up", "down"]:
    return "up" if up else "down"


@router.get("/health", responses={503: {"model": Health}})
async def health(
    response: Response,
    engine: Annotated[AsyncEngine, Depends(get_db_engine)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> Health:
    postgres_up, redis_up = await asyncio.gather(_postgres_up(engine), _redis_up(redis))
    if not (postgres_up and redis_up):
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return Health(
        status="ok" if postgres_up and redis_up else "degraded",
        postgres=_state(postgres_up),
        redis=_state(redis_up),
    )
