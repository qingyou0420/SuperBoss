"""FastAPI application factory."""

import secrets
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from superboss.api.router import api_router
from superboss.core.config import Settings, get_settings
from superboss.infrastructure.wecom import build_wecom_provider


def create_app(settings: Settings | None = None) -> FastAPI:
    active_settings = settings or get_settings()
    app = FastAPI(title="SuperBoss API", version="1.0.0")
    app.state.settings = active_settings
    engine = create_async_engine(active_settings.database_url, pool_pre_ping=True)
    app.state.session_factory = async_sessionmaker(engine, expire_on_commit=False)
    app.state.wecom_provider = build_wecom_provider(active_settings)

    @app.middleware("http")
    async def enforce_browser_csrf(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and not request.headers.get(
            "Authorization"
        ):
            csrf_cookie = request.cookies.get("XSRF-TOKEN")
            csrf_header = request.headers.get("X-CSRF-Token")
            if (
                not csrf_cookie
                or not csrf_header
                or not secrets.compare_digest(csrf_cookie, csrf_header)
            ):
                return JSONResponse({"detail": "CSRF validation failed"}, status_code=403)
        return await call_next(request)

    app.include_router(api_router, prefix="/api/v1")
    return app


app = create_app()
