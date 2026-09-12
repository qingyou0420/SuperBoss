"""Knowledge routes."""

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from superboss.core.actors import Actor, get_actor
from superboss.core.db import get_session
from superboss.core.errors import ForbiddenError, NotFoundError
from superboss.modules.files.service import FileService
from superboss.modules.knowledge.models import KnowledgeDoc, KnowledgeStatus
from superboss.modules.knowledge.schemas import (
    KnowledgeDocCreate,
    KnowledgeDocRead,
    KnowledgeDocUpdate,
    KnowledgePointRead,
    KnowledgePointsReview,
)
from superboss.modules.knowledge.service import (
    KnowledgeService,
    published_revision,
    staff_visible_revision,
)
from superboss.modules.users.models import Role

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


def get_service(session: AsyncSession = Depends(get_session)) -> KnowledgeService:
    return KnowledgeService(session)


def _as_int(value: object) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0


def _point_from_snapshot(item: dict[str, object], fallback_id: UUID) -> KnowledgePointRead:
    raw_id = item.get("id") or fallback_id
    try:
        point_id = UUID(str(raw_id))
    except (TypeError, ValueError):
        point_id = fallback_id
    raw_source = item.get("source_file_id")
    source_file_id = None
    if raw_source:
        try:
            source_file_id = UUID(str(raw_source))
        except (TypeError, ValueError):
            source_file_id = None
    return KnowledgePointRead(
        id=point_id,
        title=str(item.get("title") or ""),
        body_md=str(item.get("body_md") or ""),
        sort_order=_as_int(item.get("sort_order") or 0),
        source_file_id=source_file_id,
    )


def _staff_may_read(revision: object) -> bool:
    review = getattr(revision, "points_review", "OK") or "OK"
    return review != "NEEDS_REVIEW"


def _to_read(actor: Actor, doc: KnowledgeDoc) -> KnowledgeDocRead:
    payload = KnowledgeDocRead.model_validate(doc)
    if actor.role == Role.OWNER:
        return payload
    visible = [item for item in payload.revisions if item.released and _staff_may_read(item)]
    published = next(
        (item for item in payload.revisions if item.id == payload.published_revision_id),
        None,
    )
    if published is None or not _staff_may_read(published):
        published = max(visible, key=lambda item: item.version) if visible else None
    if published is not None:
        payload.body_md = published.body_md
        payload.change_reason = published.change_reason
        payload.stage_title = published.stage_title
        payload.is_canonical = published.is_canonical
        payload.source_file_id = published.source_file_id
        payload.points = [
            _point_from_snapshot(item, payload.id)
            for item in published.points_json
            if isinstance(item, dict)
        ]
        payload.revisions = visible
    else:
        payload.body_md = ""
        payload.change_reason = ""
        payload.stage_title = ""
        payload.source_file_id = None
        payload.points = []
        payload.revisions = []
    return payload


def _collect_source_ids(source_file_id: UUID | None, points: object) -> set[UUID]:
    allowed: set[UUID] = set()
    if source_file_id is not None:
        allowed.add(source_file_id)
    rows = points if isinstance(points, list) else []
    for item in rows:
        raw = item.get("source_file_id") if isinstance(item, dict) else None
        if raw:
            try:
                allowed.add(UUID(str(raw)))
            except (TypeError, ValueError):
                continue
    return allowed


def _published_source_ids(doc: KnowledgeDoc) -> set[UUID]:
    allowed: set[UUID] = set()
    revisions = list(doc.revisions) if "revisions" in doc.__dict__ else []
    released = [item for item in revisions if item.released and _staff_may_read(item)]
    if not released:
        published = staff_visible_revision(doc)
        if published is not None:
            released = [published]
    for revision in released:
        allowed.update(_collect_source_ids(revision.source_file_id, revision.points_json))
    return allowed


@router.get("", response_model=list[KnowledgeDocRead])
async def list_docs(
    actor: Actor = Depends(get_actor),
    service: KnowledgeService = Depends(get_service),
    q: str | None = Query(default=None, max_length=80),
    stage: str | None = Query(default=None, max_length=255),
    project_id: UUID | None = None,
) -> list[KnowledgeDocRead]:
    docs = await service.list_docs(actor, q, stage_title=stage, project_id=project_id)
    return [_to_read(actor, item) for item in docs]


@router.get("/{doc_id}", response_model=KnowledgeDocRead)
async def get_doc(
    doc_id: UUID,
    actor: Actor = Depends(get_actor),
    service: KnowledgeService = Depends(get_service),
) -> KnowledgeDocRead:
    return _to_read(actor, await service.get(actor, doc_id))


@router.post("", response_model=KnowledgeDocRead, status_code=status.HTTP_201_CREATED)
async def create_doc(
    command: KnowledgeDocCreate,
    actor: Actor = Depends(get_actor),
    service: KnowledgeService = Depends(get_service),
) -> KnowledgeDocRead:
    return _to_read(actor, await service.create(actor, command))


@router.patch("/{doc_id}", response_model=KnowledgeDocRead)
async def update_doc(
    doc_id: UUID,
    command: KnowledgeDocUpdate,
    actor: Actor = Depends(get_actor),
    service: KnowledgeService = Depends(get_service),
) -> KnowledgeDocRead:
    return _to_read(actor, await service.update(actor, doc_id, command))


@router.post("/{doc_id}/revisions/{revision_id}/points-review", response_model=KnowledgeDocRead)
async def review_revision_points(
    request: Request,
    doc_id: UUID,
    revision_id: UUID,
    command: KnowledgePointsReview,
    actor: Actor = Depends(get_actor),
    service: KnowledgeService = Depends(get_service),
) -> KnowledgeDocRead:
    return _to_read(
        actor,
        await service.review_points(
            actor,
            doc_id,
            revision_id,
            command.action,
            UUID(request.state.request_id),
        ),
    )


@router.get("/{doc_id}/source-download")
async def download_source(
    request: Request,
    doc_id: UUID,
    file_id: UUID | None = Query(default=None),
    actor: Actor = Depends(get_actor),
    service: KnowledgeService = Depends(get_service),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    doc = await service.get(actor, doc_id)
    if doc.status is not KnowledgeStatus.PUBLISHED and actor.role != Role.OWNER:
        raise NotFoundError("KNOWLEDGE_NOT_FOUND", "Document not found")
    if actor.role == Role.OWNER:
        allowed = {item for item in (doc.source_file_id,) if item is not None}
        for point in doc.points:
            if point.source_file_id is not None:
                allowed.add(point.source_file_id)
        for revision in doc.revisions:
            if revision.source_file_id is not None:
                allowed.add(revision.source_file_id)
            for item in revision.points_json or []:
                raw = item.get("source_file_id") if isinstance(item, dict) else None
                if raw:
                    try:
                        allowed.add(UUID(str(raw)))
                    except (TypeError, ValueError):
                        continue
    else:
        allowed = _published_source_ids(doc)
    current_published = (
        published_revision(doc) if actor.role == Role.OWNER else staff_visible_revision(doc)
    )
    target = file_id or (current_published.source_file_id if current_published else None)
    if actor.role == Role.OWNER:
        target = file_id or doc.source_file_id
    if target is None:
        raise NotFoundError("FILE_NOT_FOUND", "File not found")
    if actor.role == Role.OWNER:
        files = FileService(
            session, request.app.state.object_storage, request.app.state.enqueue_file_scan
        )
        url = await files.presign_download(actor, target)
        return {"url": url}
    if actor.role != Role.STAFF:
        raise ForbiddenError("FOLDER_FORBIDDEN", "You cannot access this folder")
    files = FileService(
        session, request.app.state.object_storage, request.app.state.enqueue_file_scan
    )
    url = await files.presign_published_knowledge_source(actor, target, allowed)
    return {"url": url}
