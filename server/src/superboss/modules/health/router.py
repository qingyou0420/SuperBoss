from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from superboss.modules.health.readiness import (
    ReadinessChecker,
    build_default_readiness_checker,
)

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def ready(request: Request) -> JSONResponse:
    checker: ReadinessChecker | None = getattr(request.app.state, "readiness_checker", None)
    if checker is None:
        checker = build_default_readiness_checker(
            settings=request.app.state.settings,
            engine=request.app.state.engine,
        )
    result = await checker.check()
    return JSONResponse(
        {
            "status": result.status,
            "dependencies": result.dependencies,
        },
        status_code=200 if result.status == "ok" else 503,
    )
