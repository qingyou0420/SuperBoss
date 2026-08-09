"""FastAPI application factory."""

import asyncio
import logging
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from superboss.api.router import api_router
from superboss.core.actors import get_actor
from superboss.core.config import Settings, get_settings
from superboss.core.errors import DomainError, UnauthenticatedError
from superboss.infrastructure.s3 import Boto3ObjectStorage
from superboss.infrastructure.wecom import build_wecom_provider
from superboss.modules.files.service import FileLifecycleService
from superboss.modules.files.storage import ObjectStorage
from superboss.modules.files.tasks import enqueue_file_scan as celery_enqueue_file_scan

logger = logging.getLogger(__name__)

def create_app(
    settings: Settings | None = None,
    *,
    object_storage: ObjectStorage | None = None,
    enqueue_file_scan: Callable[[UUID, UUID], Awaitable[None] | None] | None = None,
) -> FastAPI:
    active_settings = settings or get_settings()
    engine = create_async_engine(active_settings.database_url, pool_pre_ping=True)
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        stop = asyncio.Event()
        task: asyncio.Task[None] | None = None
        if active_settings.lifecycle_reconcile_interval_seconds > 0:
            async def maintain() -> None:
                while not stop.is_set():
                    try:
                        await FileLifecycleService(app.state.session_factory, app.state.object_storage, app.state.enqueue_file_scan).reconcile(active_settings.lifecycle_reconcile_batch_size)
                    except Exception as error:  # noqa: BLE001
                        logger.warning("file lifecycle maintenance failed: %s", type(error).__name__)
                    try:
                        await asyncio.wait_for(stop.wait(), active_settings.lifecycle_reconcile_interval_seconds)
                    except TimeoutError:
                        continue
            task = asyncio.create_task(maintain())
        app.state.lifecycle_maintenance_task = task
        try:
            yield
        finally:
            stop.set()
            if task is not None:
                try:
                    await asyncio.wait_for(task, timeout=5)
                except TimeoutError:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            await engine.dispose()

    app = FastAPI(title="SuperBoss API", version="1.0.0", lifespan=lifespan)
    app.state.settings = active_settings
    app.state.engine = engine
    app.state.session_factory = async_sessionmaker(engine, expire_on_commit=False)
    app.state.wecom_provider = build_wecom_provider(active_settings)
    app.state.object_storage = object_storage or Boto3ObjectStorage(active_settings.s3_bucket, active_settings.s3_endpoint_url, active_settings.s3_access_key_id, active_settings.s3_secret_access_key)
    app.state.enqueue_file_scan = enqueue_file_scan or celery_enqueue_file_scan

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

    def is_authenticated_write_path(path: str) -> bool:
        return path in {
            "/api/v1/projects",
            "/api/v1/files",
            "/api/v1/device/import-jobs",
        } or path.startswith(
            (
                "/api/v1/projects/",
                "/api/v1/files/",
                "/api/v1/owner/devices",
                "/api/v1/device/import-jobs/",
            )
        )

    @app.exception_handler(DomainError)
    async def handle_domain_error(request: Request, error: DomainError) -> JSONResponse:
        return error_response(request, error.code, error.message, error.status_code)

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
            if request.method == "POST" and request.url.path in {
                "/api/v1/device-auth/pair",
                "/api/v1/device-auth/refresh",
            }:
                return finalize_response(request, await call_next(request))
            authorization = request.headers.get("Authorization")
            has_browser_credentials = bool(
                request.cookies.get("access_token") or request.cookies.get("refresh_token")
            )
            if authorization is not None and not has_browser_credentials:
                try:
                    await get_actor(request)
                except UnauthenticatedError:
                    return error_response(
                        request, "AUTHENTICATION_REQUIRED", "Authentication required", 401
                    )
                return finalize_response(request, await call_next(request))
            if not has_browser_credentials and is_authenticated_write_path(request.url.path):
                return finalize_response(request, await call_next(request))
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
