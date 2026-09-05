"""Finance API routes."""

from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from superboss.core.actors import Actor, get_actor
from superboss.modules.audit.service import AuditService
from superboss.modules.finance.schemas import (
    FinanceAdjustmentCreate,
    FinanceEntryCreate,
    FinanceEntryRead,
    FinanceSummary,
)
from superboss.modules.finance.service import FinanceService

router = APIRouter(prefix="/finance", tags=["finance"])
_MONTH = r"^\d{4}-(0[1-9]|1[0-2])$"


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    session = request.app.state.session_factory()
    try:
        yield session
    except Exception:
        await session.rollback()
        raise
    else:
        await session.commit()
    finally:
        await session.close()


def get_service(request: Request, session: AsyncSession = Depends(get_session)) -> FinanceService:
    return FinanceService(session, AuditService(request.app.state.session_factory))


@router.get("/entries", response_model=list[FinanceEntryRead])
async def list_entries(
    actor: Actor = Depends(get_actor),
    service: FinanceService = Depends(get_service),
    month: str | None = Query(default=None, pattern=_MONTH),
    project_id: UUID | None = None,
) -> list[FinanceEntryRead]:
    return await service.list_entries(actor, month=month, project_id=project_id)


@router.get("/export")
async def export_csv(
    actor: Actor = Depends(get_actor),
    service: FinanceService = Depends(get_service),
    month: str | None = Query(default=None, pattern=_MONTH),
) -> PlainTextResponse:
    entries = await service.list_entries(actor, month=month)
    rows = ["occurred_on,kind,scope,project_name,category,amount_yuan,visibility"]
    for item in entries:
        yuan = f"{item.amount_cents / 100:.2f}"
        project = item.project_name or ""
        rows.append(
            f"{item.occurred_on.isoformat()},{item.kind.value},{item.scope.value},"
            f"{project},{item.category},{yuan},{item.visibility.value}"
        )
    return PlainTextResponse("\n".join(rows) + "\n", media_type="text/csv")


@router.get("/alerts")
async def alerts(
    actor: Actor = Depends(get_actor),
    service: FinanceService = Depends(get_service),
) -> list[dict[str, str]]:
    return await service.cost_alerts(actor)


@router.get("/summary", response_model=FinanceSummary, response_model_exclude_none=True)
async def summary(
    actor: Actor = Depends(get_actor),
    service: FinanceService = Depends(get_service),
    month: str | None = Query(default=None, pattern=_MONTH),
) -> FinanceSummary:
    return await service.summary(actor, month)


@router.post("/entries", response_model=FinanceEntryRead, status_code=status.HTTP_201_CREATED)
async def create_entry(
    request: Request,
    command: FinanceEntryCreate,
    actor: Actor = Depends(get_actor),
    service: FinanceService = Depends(get_service),
) -> FinanceEntryRead:
    return await service.create_entry(actor, command, UUID(request.state.request_id))


@router.post("/entries/{entry_id}/adjustments", response_model=FinanceEntryRead)
async def adjust_entry(
    request: Request,
    entry_id: UUID,
    command: FinanceAdjustmentCreate,
    actor: Actor = Depends(get_actor),
    service: FinanceService = Depends(get_service),
) -> FinanceEntryRead:
    return await service.adjust_entry(actor, entry_id, command, UUID(request.state.request_id))
