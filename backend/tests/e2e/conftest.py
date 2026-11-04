"""End-to-end tests: they drive a running stack over HTTP and WebSockets, as browsers do.

Skipped unless E2E_BASE_URL is set. Against the Compose stack:

    docker compose up -d --build --wait
    cd backend && E2E_BASE_URL=http://localhost:5173 uv run pytest tests/e2e

Settings, from the environment:
- E2E_BASE_URL: where a browser would go. Through the Vite dev server (:5173), the
  front end's /api and /ws proxy is covered too.
- E2E_DATABASE_URL: the stack's Postgres, read to check what was saved. Defaults to the
  one docker-compose.yml publishes.
- E2E_ADMIN_PASSWORD: defaults to docker-compose.yml's "admin".
- E2E_RESTART_REDIS: a command that restarts the stack's Redis, run from the repo root.
  Defaults to "docker compose restart redis".

The tests restart Redis, so point them only at a local or CI stack.
"""

import asyncio
import os
import shlex
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import asyncpg
import httpx
import pytest
from sqlalchemy import make_url

from tests.e2e.clients import REQUEST_TIMEOUT_SECONDS, Guest, Viewer

BASE_URL = os.environ.get("E2E_BASE_URL", "")
DATABASE_URL = os.environ.get(
    "E2E_DATABASE_URL", "postgresql://auction:auction@localhost:5432/auction"
)
ADMIN_PASSWORD = os.environ.get("E2E_ADMIN_PASSWORD", "admin")
RESTART_REDIS = os.environ.get("E2E_RESTART_REDIS", "docker compose restart redis")
REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def _needs_a_running_stack() -> None:
    if not BASE_URL:
        pytest.skip("set E2E_BASE_URL to run the end-to-end tests against a running stack")


@dataclass(frozen=True)
class Saved:
    """What Postgres, the source of truth, holds for one auction."""

    auction: dict[str, Any]
    # Its bids in seq order, with ids as strings.
    bids: list[dict[str, Any]]


@dataclass
class Stack:
    base_url: str
    _clients: list[httpx.AsyncClient] = field(default_factory=list)
    _viewers: list[Viewer] = field(default_factory=list)

    def http(self) -> httpx.AsyncClient:
        """A client with its own cookie jar, like a separate browser."""
        client = httpx.AsyncClient(base_url=self.base_url, timeout=REQUEST_TIMEOUT_SECONDS)
        self._clients.append(client)
        return client

    async def create_auction(self, *, title: str, start_price: int, end_at: datetime) -> dict:
        response = await self.http().post(
            "/api/admin/auctions",
            headers={"X-Admin-Password": ADMIN_PASSWORD},
            json={"title": title, "start_price": start_price, "end_at": end_at.isoformat()},
        )
        assert response.status_code == 200, response.text
        return response.json()

    async def guest(self, name: str, auction: dict) -> Guest:
        """Signs in a new guest and opens the auction page in their browser."""
        http = self.http()
        response = await http.post("/api/session", json={"display_name": name})
        assert response.status_code == 200, response.text
        bidder_id = response.json()["bidder_id"]
        viewer = Viewer(self.base_url, auction["id"], me=bidder_id)
        self._viewers.append(viewer)
        viewer.start()
        return Guest(http, name, bidder_id, auction["id"], auction["start_price"], viewer)

    async def restart_redis(self) -> None:
        process = await asyncio.create_subprocess_exec(
            *shlex.split(RESTART_REDIS),
            cwd=REPO_ROOT,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        output, _ = await process.communicate()
        assert process.returncode == 0, f"{RESTART_REDIS!r} failed:\n{output.decode()}"

    async def saved(self, auction_id: str) -> Saved:
        url = make_url(DATABASE_URL).set(drivername="postgresql")
        connection = await asyncpg.connect(url.render_as_string(hide_password=False))
        try:
            auction = await connection.fetchrow(
                "SELECT status, high_amount, high_bidder_id::text, high_bidder_name, high_seq,"
                " load_epoch FROM auctions WHERE id = $1",
                auction_id,
            )
            bids = await connection.fetch(
                "SELECT bid_id::text, seq, bidder_id::text, bidder_name, amount, is_bot"
                " FROM bids WHERE auction_id = $1 ORDER BY seq",
                auction_id,
            )
        finally:
            await connection.close()
        assert auction is not None, f"auction {auction_id} is not in Postgres"
        return Saved(dict(auction), [dict(bid) for bid in bids])

    async def close(self) -> None:
        for viewer in self._viewers:
            await viewer.stop()
        for client in self._clients:
            await client.aclose()


@pytest.fixture
async def stack() -> AsyncIterator[Stack]:
    running = Stack(BASE_URL.rstrip("/"))
    try:
        yield running
    finally:
        await running.close()
