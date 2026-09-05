"""FastAPI application factory."""

import secrets
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from superboss.api.router import api_router
from superboss.core.config import Settings, get_settings
from superboss.core.errors import DomainError, UnauthenticatedError
from superboss.core.llm import LLMClient, llm_from_settings
from superboss.infrastructure.s3 import Boto3ObjectStorage
from superboss.modules.agent.tasks import enqueue_memory_extract as celery_enqueue_memory_extract
from superboss.modules.files.storage import ObjectStorage
from superboss.modules.files.tasks import enqueue_file_scan as celery_enqueue_file_scan


def create_app(
    settings: Settings | None = None,
    *,
    object_storage: ObjectStorage | None = None,
    enqueue_file_scan: Callable[[UUID, UUID], Awaitable[None] | None] | None = None,
    llm_client: LLMClient | None = None,
    enqueue_memory_extract: Callable[[UUID], Awaitable[None] | None] | None = None,
) -> FastAPI:
    active_settings = settings or get_settings()
    engine = create_async_engine(active_settings.database_url, pool_pre_ping=True)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        del app
        try:
            yield
        finally:
            await engine.dispose()

    app = FastAPI(title="SuperBoss API", version="1.0.0", lifespan=lifespan)
    app.state.settings = active_settings
    app.state.engine = engine
    app.state.readiness_checker = None
    app.state.session_factory = async_sessionmaker(engine, expire_on_commit=False)
    app.state.object_storage = object_storage or Boto3ObjectStorage(
        active_settings.s3_bucket,
        active_settings.s3_endpoint_url,
        active_settings.s3_access_key_id,
        active_settings.s3_secret_access_key,
        public_endpoint_url=active_settings.s3_public_endpoint_url,
    )
    app.state.enqueue_file_scan = enqueue_file_scan or celery_enqueue_file_scan
    app.state.llm_client = llm_client or llm_from_settings(active_settings)
    app.state.enqueue_memory_extract = enqueue_memory_extract or celery_enqueue_memory_extract

    def request_id(request: Request) -> str:
        candidate = request.headers.get("X-Request-ID", "")
        try:
            return str(UUID(candidate))
        except (TypeError, ValueError, AttributeError):
            pass
        return str(uuid4())

    def error_response(
        request: Request,
        code: str,
        message: str,
        status_code: int,
        *,
        headers: dict[str, str] | None = None,
    ) -> JSONResponse:
        request_id = request.state.request_id
        response_headers = {"X-Request-ID": request_id, **(headers or {})}
        return JSONResponse(
            {"error": {"code": code, "message": message, "request_id": request_id}},
            status_code=status_code,
            headers=response_headers,
        )

    def finalize_response(request: Request, response: Response) -> Response:
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.exception_handler(DomainError)
    async def handle_domain_error(request: Request, error: DomainError) -> JSONResponse:
        headers = None
        if (
            isinstance(error, UnauthenticatedError)
            and request.url.path == "/api/v1/auth/me"
            and request.cookies.get("refresh_token")
        ):
            headers = {"X-SuperBoss-Refreshable": "1"}
        return error_response(
            request, error.code, error.message, error.status_code, headers=headers
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, error: RequestValidationError
    ) -> JSONResponse:
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
            has_browser_credentials = bool(
                request.cookies.get("access_token") or request.cookies.get("refresh_token")
            )
            if has_browser_credentials or request.url.path.startswith("/api/v1/auth/"):
                csrf_cookie = request.cookies.get("XSRF-TOKEN")
                csrf_header = request.headers.get("X-CSRF-Token")
                if (
                    not csrf_cookie
                    or not csrf_header
                    or not secrets.compare_digest(csrf_cookie, csrf_header)
                ):
                    return error_response(
                        request, "CSRF_VALIDATION_FAILED", "CSRF validation failed", 403
                    )
        return finalize_response(request, await call_next(request))

    app.include_router(api_router, prefix="/api/v1")
    return app


app = create_app()
