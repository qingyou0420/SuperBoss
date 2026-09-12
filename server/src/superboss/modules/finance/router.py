"""Finance API routes."""

from uuid import UUID

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile, status
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from superboss.core.actors import Actor, get_actor
from superboss.core.db import get_session
from superboss.modules.audit.service import AuditService
from superboss.modules.finance.schemas import (
    CompanyMonthCostRead,
    CompanyMonthCostWrite,
    FinanceAdjustmentCreate,
    FinanceEntryCreate,
    FinanceEntryRead,
    FinanceImportCommand,
    FinanceImportResolve,
    FinanceImportRowListRead,
    FinanceImportRowRead,
    FinancePayCommand,
    FinanceSummary,
)
from superboss.modules.finance.service import FinanceService

router = APIRouter(prefix="/finance", tags=["finance"])
_MONTH = r"^\d{4}-(0[1-9]|1[0-2])$"


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


@router.get("/overview")
async def overview(
    actor: Actor = Depends(get_actor),
    service: FinanceService = Depends(get_service),
) -> dict[str, object]:
    return await service.overview(actor)


@router.get("/rewards/{project_id}")
async def project_rewards(
    project_id: UUID,
    actor: Actor = Depends(get_actor),
    service: FinanceService = Depends(get_service),
) -> dict[str, object]:
    return await service.rewards_for(actor, project_id)


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


@router.post("/import")
async def import_entries(
    request: Request,
    command: FinanceImportCommand,
    actor: Actor = Depends(get_actor),
    service: FinanceService = Depends(get_service),
) -> dict[str, object]:
    return await service.import_batch(actor, command, UUID(request.state.request_id))


@router.post("/imports")
async def import_workbook(
    request: Request,
    actor: Actor = Depends(get_actor),
    service: FinanceService = Depends(get_service),
    file: UploadFile = File(...),
    batch_key: str = Query(default="", max_length=64),
) -> dict[str, object]:
    from hashlib import sha256

    payload = await file.read()
    key = batch_key.strip() or sha256(payload).hexdigest()[:32]
    return await service.import_from_file(
        actor,
        payload,
        batch_key=key,
        filename=file.filename or "",
        request_id=UUID(request.state.request_id),
    )


@router.get("/import-rows", response_model=FinanceImportRowListRead)
async def list_import_rows(
    actor: Actor = Depends(get_actor),
    service: FinanceService = Depends(get_service),
    status: str | None = Query(default="UNRESOLVED", max_length=16),
    offset: int = Query(default=0, ge=0, le=100_000),
    limit: int = Query(default=100, ge=1, le=500),
) -> FinanceImportRowListRead:
    rows, total = await service.list_import_rows(
        actor, status=status or None, offset=offset, limit=limit
    )
    return FinanceImportRowListRead(
        items=[FinanceImportRowRead.model_validate(item) for item in rows],
        total=total,
    )


@router.post(
    "/imports/{batch_key}/rows/{row_index}/resolve",
    response_model=FinanceImportRowRead,
)
async def resolve_import_row(
    request: Request,
    batch_key: str,
    row_index: int,
    command: FinanceImportResolve,
    actor: Actor = Depends(get_actor),
    service: FinanceService = Depends(get_service),
) -> FinanceImportRowRead:
    return await service.resolve_import_row(
        actor,
        batch_key,
        row_index,
        command.action,
        command.entry_id,
        UUID(request.state.request_id),
    )


@router.post("/entries/{entry_id}/pay", response_model=FinanceEntryRead)
async def mark_paid(
    request: Request,
    entry_id: UUID,
    command: FinancePayCommand,
    actor: Actor = Depends(get_actor),
    service: FinanceService = Depends(get_service),
) -> FinanceEntryRead:
    return await service.mark_paid(actor, entry_id, command, UUID(request.state.request_id))


@router.get("/month-costs", response_model=list[CompanyMonthCostRead])
async def list_month_costs(
    actor: Actor = Depends(get_actor),
    service: FinanceService = Depends(get_service),
) -> list[CompanyMonthCostRead]:
    return await service.list_month_costs(actor)


@router.put("/month-costs/{month}", response_model=CompanyMonthCostRead)
async def upsert_month_cost(
    month: str,
    command: CompanyMonthCostWrite,
    actor: Actor = Depends(get_actor),
    service: FinanceService = Depends(get_service),
) -> CompanyMonthCostRead:
    return await service.upsert_month_cost(actor, month, command)
