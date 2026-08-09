"""FastAPI application factory."""

import secrets
from collections.abc import Awaitable, Callable
from uuid import UUID, uuid4

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from superboss.api.router import api_router
from superboss.core.config import Settings, get_settings
from superboss.core.errors import DomainError
from superboss.core.security import TokenError, decode_access_token
from superboss.infrastructure.wecom import build_wecom_provider
from superboss.modules.auth.repository import AuthRepository
from superboss.modules.auth.service import AuthService, InvalidSession
from superboss.modules.users.repository import UserRepository


def create_app(settings: Settings | None = None) -> FastAPI:
    active_settings = settings or get_settings()
    app = FastAPI(title="SuperBoss API", version="1.0.0")
    app.state.settings = active_settings
    engine = create_async_engine(active_settings.database_url, pool_pre_ping=True)
    app.state.session_factory = async_sessionmaker(engine, expire_on_commit=False)
    app.state.wecom_provider = build_wecom_provider(active_settings)

    def request_id(request: Request) -> str:
        candidate = request.headers.get("X-Request-ID", "")
        try:
            return str(UUID(candidate))
        except (TypeError, ValueError, AttributeError):
            pass
        return str(uuid4())

    def error_response(request: Request, code: str, message: str, status_code: int) -> JSONResponse:
        request_id = request.state.request_id
        return JSONResponse(
            {"error": {"code": code, "message": message, "request_id": request_id}},
            status_code=status_code,
            headers={"X-Request-ID": request_id},
        )

    def finalize_response(request: Request, response: Response) -> Response:
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.exception_handler(DomainError)
    async def handle_domain_error(request: Request, error: DomainError) -> JSONResponse:
        return error_response(request, error.code, error.message, error.status_code)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
        del error
        return error_response(request, "VALIDATION_ERROR", "Request validation failed", 422)

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, error: Exception) -> JSONResponse:
        del error
        return error_response(request, "REQUEST_FAILED", "Request failed", 500)

    @app.middleware("http")
    async def enforce_browser_csrf(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request.state.request_id = request_id(request)
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            authorization = request.headers.get("Authorization")
            has_browser_credentials = bool(
                request.cookies.get("access_token") or request.cookies.get("refresh_token")
            )
            if authorization is not None and not has_browser_credentials:
                if not authorization.startswith("Bearer "):
                    return error_response(
                        request, "AUTHENTICATION_REQUIRED", "Authentication required", 401
                    )
                try:
                    decode_access_token(active_settings, authorization.removeprefix("Bearer "))
                    session = app.state.session_factory()
                    try:
                        await AuthService(session, AuthRepository(session), UserRepository(session), None, active_settings).authenticate_access_token(authorization.removeprefix("Bearer "))
                    finally:
                        await session.close()
                except (TokenError, InvalidSession):
                    return error_response(
                        request, "AUTHENTICATION_REQUIRED", "Authentication required", 401
                    )
                return finalize_response(request, await call_next(request))
            if not has_browser_credentials and request.url.path.startswith("/api/v1/projects"):
                return finalize_response(request, await call_next(request))
            csrf_cookie = request.cookies.get("XSRF-TOKEN")
            csrf_header = request.headers.get("X-CSRF-Token")
            if (
                not csrf_cookie
                or not csrf_header
                or not secrets.compare_digest(csrf_cookie, csrf_header)
            ):
                return error_response(request, "CSRF_VALIDATION_FAILED", "CSRF validation failed", 403)
        return finalize_response(request, await call_next(request))

    app.include_router(api_router, prefix="/api/v1")
    return app


app = create_app()
