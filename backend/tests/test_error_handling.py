"""The NotImplementedError handler must work for HTTP requests and WebSocket connections."""

import pytest
from starlette.requests import Request
from starlette.websockets import WebSocket

from app.main import _not_implemented


async def _receive() -> dict:
    raise AssertionError("not used")


async def _send(message: dict) -> None:
    raise AssertionError("not used")


def _scope(scope_type: str) -> dict:
    scope = {
        "type": scope_type,
        "path": "/x",
        "headers": [],
        "query_string": b"",
        "server": ("test", 80),
        "scheme": "http" if scope_type == "http" else "ws",
    }
    if scope_type == "http":
        scope["method"] = "GET"
    return scope


@pytest.mark.parametrize(
    "connection",
    [
        Request(_scope("http"), _receive, _send),
        WebSocket(_scope("websocket"), _receive, _send),
    ],
    ids=["http", "websocket"],
)
async def test_not_implemented_handler_answers_501(connection: Request | WebSocket) -> None:
    response = await _not_implemented(connection, NotImplementedError())

    assert response.status_code == 501
