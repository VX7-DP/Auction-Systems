"""Shared test fixtures. Shared file owned by session 0.

Tests run against a real Postgres and Redis:
- Postgres database ``auction_test``: created if missing, rebuilt from the Alembic
  migrations once per run, and emptied before each test that uses it.
- Redis logical database 15: flushed before each test that uses it.

The defaults match ``docker compose up -d --wait postgres redis`` and the CI service
containers. Point the tests elsewhere with TEST_DATABASE_URL and TEST_REDIS_URL.

To change settings for one test module, override the ``settings`` fixture there:

    @pytest.fixture
    def settings(make_settings):
        return make_settings(run_background_jobs=True)
"""

import asyncio
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

import asyncpg
import httpx
import pytest
from alembic import command
from alembic.config import Config
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from redis.asyncio import Redis
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from app.config import Settings, async_database_url
from app.main import create_app
from app.models import Base

# Normalised like DATABASE_URL, so a plain postgresql:// URL works here too.
TEST_DATABASE_URL = async_database_url(
    os.environ.get(
        "TEST_DATABASE_URL", "postgresql+asyncpg://auction:auction@localhost:5432/auction_test"
    )
)
TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/15")
ADMIN_PASSWORD = "test-admin-password"
BACKEND_DIR = Path(__file__).resolve().parents[1]


def alembic_config() -> Config:
    """Alembic config aimed at the test database."""
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL.replace("%", "%%"))
    config.attributes["configure_logger"] = False
    return config


async def _recreate_schema() -> None:
    url = make_url(TEST_DATABASE_URL).set(drivername="postgresql")
    server = await asyncpg.connect(url.set(database="postgres").render_as_string(False))
    try:
        if not await server.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", url.database):
            await server.execute(f'CREATE DATABASE "{url.database}"')
    finally:
        await server.close()
    database = await asyncpg.connect(url.render_as_string(False))
    try:
        await database.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    finally:
        await database.close()


@pytest.fixture(scope="session")
def migrated_database() -> str:
    """URL of the test database, rebuilt from the migrations once per test run."""
    database = make_url(TEST_DATABASE_URL).database or ""
    if not database.endswith("_test"):
        raise pytest.UsageError(
            f"TEST_DATABASE_URL names database {database!r}. The tests drop and rebuild "
            "its schema, so its name must end in '_test'."
        )
    asyncio.run(_recreate_schema())
    command.upgrade(alembic_config(), "head")
    return TEST_DATABASE_URL


@pytest.fixture
def make_settings() -> Callable[..., Settings]:
    """Builds test Settings. Background jobs are off unless a test turns them on."""

    def make(**overrides: Any) -> Settings:
        values: dict[str, Any] = {
            "database_url": TEST_DATABASE_URL,
            "redis_url": TEST_REDIS_URL,
            "session_secret": "test-session-secret-not-for-production",
            "admin_password": ADMIN_PASSWORD,
            "cookie_secure": False,
            "run_background_jobs": False,
        }
        values.update(overrides)
        return Settings(_env_file=None, **values)  # type: ignore[call-arg]

    return make


@pytest.fixture
def settings(make_settings: Callable[..., Settings]) -> Settings:
    return make_settings()


@pytest.fixture
async def db_engine(migrated_database: str) -> AsyncIterator[AsyncEngine]:
    """Engine on the test database, with every table emptied first."""
    engine = create_async_engine(migrated_database)
    async with engine.begin() as connection:
        tables = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
        await connection.execute(text(f"TRUNCATE {tables}"))
    yield engine
    await engine.dispose()


@pytest.fixture
async def db_session(db_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        yield session


@pytest.fixture
async def redis() -> AsyncIterator[Redis]:
    """Client on the test Redis database, flushed first."""
    client = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    await client.flushdb()
    yield client
    await client.aclose()


@pytest.fixture
async def app(settings: Settings, db_engine: AsyncEngine, redis: Redis) -> AsyncIterator[FastAPI]:
    """The app with its lifespan running, on an empty database and Redis."""
    application = create_app(settings)
    async with LifespanManager(application):
        yield application


@pytest.fixture
async def client_factory(app: FastAPI) -> AsyncIterator[Callable[[], httpx.AsyncClient]]:
    """Makes HTTP clients for the app, each with its own cookie jar (one per bidder)."""
    clients: list[httpx.AsyncClient] = []

    def make() -> httpx.AsyncClient:
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        clients.append(client)
        return client

    yield make
    for client in clients:
        await client.aclose()


@pytest.fixture
def client(client_factory: Callable[[], httpx.AsyncClient]) -> httpx.AsyncClient:
    return client_factory()


@pytest.fixture
def bidder_client(
    client_factory: Callable[[], httpx.AsyncClient],
) -> Callable[[str], Awaitable[httpx.AsyncClient]]:
    """``await bidder_client("Asha")`` returns a client signed in as a new guest."""

    async def make(display_name: str) -> httpx.AsyncClient:
        client = client_factory()
        response = await client.post("/api/session", json={"display_name": display_name})
        response.raise_for_status()
        return client

    return make


@pytest.fixture
def admin_headers() -> dict[str, str]:
    return {"X-Admin-Password": ADMIN_PASSWORD}
