"""Guards the CLAUDE.md section 10 routes. These stay true once the stubs are filled in."""

from collections.abc import Callable

import httpx
import pytest
from fastapi import FastAPI

from app.config import Settings
from app.main import create_app

SECTION_10_HTTP_ROUTES = {
    ("POST", "/api/session"),
    ("GET", "/api/session"),
    ("GET", "/api/auctions"),
    ("GET", "/api/auctions/{auction_id}"),
    ("POST", "/api/auctions/{auction_id}/bids"),
    ("POST", "/api/admin/auctions"),
    ("GET", "/api/health"),
    ("GET", "/api/metrics"),
}
HTTP_METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}


@pytest.fixture
def api(make_settings: Callable[..., Settings]) -> FastAPI:
    return create_app(make_settings())


def _http_routes(api: FastAPI) -> set[tuple[str, str]]:
    return {
        (method.upper(), path)
        for path, operations in api.openapi()["paths"].items()
        for method in operations
        if method in HTTP_METHODS
    }


def test_every_section_10_http_route_exists(api: FastAPI) -> None:
    assert SECTION_10_HTTP_ROUTES <= _http_routes(api)


def test_section_10_websocket_route_exists(api: FastAPI) -> None:
    assert api.url_path_for("auction_events", auction_id="abc") == "/ws/auctions/abc"


def test_http_routes_live_under_api(api: FastAPI) -> None:
    """The front end owns every path outside /api and /ws."""
    for _, path in _http_routes(api):
        assert path.startswith("/api/"), path


async def test_api_docs_live_under_api(api: FastAPI) -> None:
    transport = httpx.ASGITransport(app=api)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/api/docs")).status_code == 200
        assert (await client.get("/api/openapi.json")).status_code == 200
        assert (await client.get("/docs")).status_code == 404
        assert (await client.get("/openapi.json")).status_code == 404
