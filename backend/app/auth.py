"""Guest sign-in and the admin check.

Guests pick a display name. Their bidder id and name live in a signed session
cookie (Starlette's SessionMiddleware, set up in main.py). The admin page sends one
shared password, from the ADMIN_PASSWORD setting, in an X-Admin-Password header.
"""

import secrets
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import ValidationError

from app.config import Settings
from app.contract import Bidder, SessionCreate
from app.resources import get_resources

router = APIRouter(prefix="/api/session", tags=["session"])


def _settings() -> Settings:
    return get_resources().settings


async def current_bidder(request: Request) -> Bidder:
    """FastAPI dependency: the signed-in guest, or 401."""
    try:
        return Bidder.model_validate(request.session)
    except ValidationError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in first.") from None


async def require_admin(
    settings: Annotated[Settings, Depends(_settings)],
    x_admin_password: Annotated[str | None, Header()] = None,
) -> None:
    """FastAPI dependency: 401 unless X-Admin-Password matches ADMIN_PASSWORD."""
    expected = settings.admin_password.get_secret_value().encode()
    given = (x_admin_password or "").encode()
    if not secrets.compare_digest(given, expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong admin password.")


@router.post("")
async def sign_in(body: SessionCreate, request: Request) -> Bidder:
    """Start a guest session. Signing in again keeps the bidder id and changes the name."""
    try:
        bidder_id = uuid.UUID(request.session.get("bidder_id", ""))
    except ValueError:
        bidder_id = uuid.uuid4()
    bidder = Bidder(bidder_id=bidder_id, display_name=body.display_name)
    request.session.clear()
    request.session.update(bidder.model_dump(mode="json"))
    return bidder


@router.get("")
async def read_session(bidder: Annotated[Bidder, Depends(current_bidder)]) -> Bidder:
    return bidder
