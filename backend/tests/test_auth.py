from collections.abc import Awaitable, Callable
from typing import Annotated
from uuid import UUID

import httpx
import pytest
from fastapi import Depends, FastAPI

from app.auth import require_admin
from app.main import SESSION_COOKIE


async def test_sign_in_returns_bidder_and_session_reads_it_back(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/session", json={"display_name": "  Asha  "})

    assert response.status_code == 200
    bidder = response.json()
    assert bidder["display_name"] == "Asha"
    assert UUID(bidder["bidder_id"])
    assert (await client.get("/api/session")).json() == bidder


async def test_session_cookie_is_http_only_and_same_site_lax(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/session", json={"display_name": "Asha"})

    cookie = response.headers["set-cookie"].lower()
    assert cookie.startswith(f"{SESSION_COOKIE}=")
    assert "httponly" in cookie
    assert "samesite=lax" in cookie


async def test_signing_in_again_keeps_bidder_id_and_changes_name(
    client: httpx.AsyncClient,
) -> None:
    first = (await client.post("/api/session", json={"display_name": "Asha"})).json()
    second = (await client.post("/api/session", json={"display_name": "Asha K"})).json()

    assert second == {"bidder_id": first["bidder_id"], "display_name": "Asha K"}


async def test_each_guest_gets_their_own_bidder_id(
    bidder_client: Callable[[str], Awaitable[httpx.AsyncClient]],
) -> None:
    asha = await (await bidder_client("Asha")).get("/api/session")
    ben = await (await bidder_client("Ben")).get("/api/session")

    assert asha.json()["bidder_id"] != ben.json()["bidder_id"]


async def test_session_without_cookie_is_401(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/session")).status_code == 401


async def test_session_with_forged_cookie_is_401(client: httpx.AsyncClient) -> None:
    client.cookies.set(SESSION_COOKIE, "eyJiaWRkZXJfaWQiOiAieCJ9.forged.signature")

    assert (await client.get("/api/session")).status_code == 401


@pytest.mark.parametrize("display_name", ["", "   ", "x" * 41])
async def test_sign_in_rejects_bad_display_name(
    client: httpx.AsyncClient, display_name: str
) -> None:
    response = await client.post("/api/session", json={"display_name": display_name})

    assert response.status_code == 422


@pytest.mark.parametrize("headers", [{}, {"X-Admin-Password": "wrong"}])
async def test_admin_endpoint_rejects_missing_or_wrong_password(
    client: httpx.AsyncClient, headers: dict[str, str]
) -> None:
    response = await client.post("/api/admin/auctions", headers=headers, json={})

    assert response.status_code == 401


async def test_require_admin_accepts_the_admin_password(
    app: FastAPI, client: httpx.AsyncClient, admin_headers: dict[str, str]
) -> None:
    @app.get("/api/test-only/admin")
    async def admin_only(_: Annotated[None, Depends(require_admin)]) -> dict[str, bool]:
        return {"ok": True}

    response = await client.get("/api/test-only/admin", headers=admin_headers)

    assert response.status_code == 200
