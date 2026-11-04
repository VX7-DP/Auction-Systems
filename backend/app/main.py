"""The FastAPI app. Shared file owned by session 0.

Run it with ``uvicorn app.main:create_app --factory``.

Feature modules plug in without editing this file. Each package in MODULES exports:
- ``router``: an APIRouter, served as is, so it declares its full paths.
- ``lifespan`` (optional): ``lifespan(app)``, an async context manager entered after
  the shared resources open and exited before they close. Background jobs start here.
"""

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from types import ModuleType

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.sessions import SessionMiddleware

from app import admin, auctions, auth, bidding, demo, health, metrics, realtime
from app.config import Settings, get_settings
from app.resources import open_resources

MODULES: tuple[ModuleType, ...] = (bidding, admin, auctions, realtime, demo, metrics)

SESSION_COOKIE = "auction_session"
SESSION_MAX_AGE_SECONDS = 30 * 24 * 60 * 60


async def _not_implemented(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse({"detail": "Not implemented yet."}, status_code=501)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with open_resources(settings), AsyncExitStack() as module_lifespans:
            for module in MODULES:
                module_lifespan = getattr(module, "lifespan", None)
                if module_lifespan is not None:
                    await module_lifespans.enter_async_context(module_lifespan(app))
            yield

    # The front end owns every path outside /api and /ws, so the API docs live under /api.
    app = FastAPI(
        title="Auction API",
        lifespan=lifespan,
        docs_url="/api/docs",
        swagger_ui_oauth2_redirect_url="/api/docs/oauth2-redirect",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret.get_secret_value(),
        session_cookie=SESSION_COOKIE,
        max_age=SESSION_MAX_AGE_SECONDS,
        same_site="lax",
        https_only=settings.cookie_secure,
    )
    app.add_exception_handler(NotImplementedError, _not_implemented)

    app.include_router(health.router)
    app.include_router(auth.router)
    for module in MODULES:
        app.include_router(module.router)
    return app
