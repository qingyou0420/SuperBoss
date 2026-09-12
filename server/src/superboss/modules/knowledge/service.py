"""Knowledge application service."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Select, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload
from sqlalchemy.orm.attributes import set_committed_value

from superboss.core.actors import Actor, require_owner, require_project_actor
from superboss.core.errors import DomainError, NotFoundError
from superboss.modules.audit.service import write_audit
from superboss.modules.knowledge.models import (
    KnowledgeDoc,
    KnowledgePoint,
    KnowledgeRevision,
    KnowledgeRevisionPollution,
    KnowledgeRevisionReviewEvent,
    KnowledgeStatus,
)
from superboss.modules.knowledge.schemas import (
    KnowledgeDocCreate,
    KnowledgeDocUpdate,
    KnowledgeIngestCard,
)
from superboss.modules.users.models import Role


def _points_payload(doc: KnowledgeDoc) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    points = list(doc.points) if "points" in doc.__dict__ else []
    for point in points:
        rows.append(
            {
                "id": str(point.id) if point.id else "",
                "title": point.title,
                "body_md": point.body_md,
                "source_file_id": str(point.source_file_id) if point.source_file_id else None,
                "sort_order": point.sort_order,
            }
        )
    return rows


def published_revision(doc: KnowledgeDoc) -> KnowledgeRevision | None:
    if doc.published_revision_id is None:
        return None
    revisions = list(doc.revisions) if "revisions" in doc.__dict__ else []
    return next((item for item in revisions if item.id == doc.published_revision_id), None)


def _review_status(revision: KnowledgeRevision | None) -> str:
    if revision is None:
        return "OK"
    return revision.points_review or "OK"


def staff_visible_revision(doc: KnowledgeDoc) -> KnowledgeRevision | None:
    published = published_revision(doc)
    if published is not None and _review_status(published) != "NEEDS_REVIEW":
        return published
    revisions = list(doc.revisions) if "revisions" in doc.__dict__ else []
    released = [
        item for item in revisions if item.released and _review_status(item) != "NEEDS_REVIEW"
    ]
    if not released:
        return None
    return max(released, key=lambda item: item.version)


def _staff_readable_revision_id() -> object:
    published_ok = (
        select(KnowledgeRevision.id)
        .where(
            KnowledgeRevision.id == KnowledgeDoc.published_revision_id,
            KnowledgeRevision.points_review != "NEEDS_REVIEW",
        )
        .correlate(KnowledgeDoc)
        .scalar_subquery()
    )
    fallback = (
        select(KnowledgeRevision.id)
        .where(
            KnowledgeRevision.doc_id == KnowledgeDoc.id,
            KnowledgeRevision.released.is_(True),
            KnowledgeRevision.points_review != "NEEDS_REVIEW",
        )
        .order_by(KnowledgeRevision.version.desc())
        .limit(1)
        .correlate(KnowledgeDoc)
        .scalar_subquery()
    )
    return func.coalesce(published_ok, fallback)


class KnowledgeService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _visible(self, actor: Actor) -> Select[tuple[KnowledgeDoc]]:
        statement = select(KnowledgeDoc).options(
            selectinload(KnowledgeDoc.points),
            selectinload(KnowledgeDoc.revisions),
        )
        if actor.role != Role.OWNER:
            statement = statement.where(KnowledgeDoc.status == KnowledgeStatus.PUBLISHED)
        return statement

    async def _next_version(self, doc_id: UUID) -> int:
        latest = await self.session.scalar(
            select(KnowledgeRevision.version)
            .where(KnowledgeRevision.doc_id == doc_id)
            .order_by(KnowledgeRevision.version.desc())
            .limit(1)
        )
        return int(latest or 0) + 1

    async def _loaded_doc(self, doc_id: UUID) -> KnowledgeDoc | None:
        doc = await self.session.scalar(
            select(KnowledgeDoc)
            .options(selectinload(KnowledgeDoc.points), selectinload(KnowledgeDoc.revisions))
            .where(KnowledgeDoc.id == doc_id)
        )
        return doc if isinstance(doc, KnowledgeDoc) else None

    async def _snapshot(self, actor: Actor, doc: KnowledgeDoc) -> KnowledgeRevision:
        if "points" not in doc.__dict__:
            await self.session.refresh(doc, attribute_names=["points"])
        if "revisions" not in doc.__dict__:
            await self.session.refresh(doc, attribute_names=["revisions"])
        revision = KnowledgeRevision(
            doc_id=doc.id,
            version=await self._next_version(doc.id),
            body_md=doc.body_md,
            change_reason=doc.change_reason,
            stage_title=doc.stage_title,
            is_canonical=doc.is_canonical,
            source_file_id=doc.source_file_id,
            points_json=_points_payload(doc),
            released=False,
            points_review="OK",
            created_by=actor.subject_id,
        )
        self.session.add(revision)
        await self.session.flush()
        doc.draft_revision_id = revision.id
        doc.revisions.append(revision)
        return revision

    async def list_docs(
        self,
        actor: Actor,
        query: str | None = None,
        *,
        stage_title: str | None = None,
        project_id: UUID | None = None,
    ) -> list[KnowledgeDoc]:
        require_project_actor(actor)
        statement = self._visible(actor).order_by(KnowledgeDoc.updated_at.desc())
        needle = (query or "").strip()
        stage = (stage_title or "").strip()[:255]
        visible = aliased(KnowledgeRevision)
        use_visible = actor.role != Role.OWNER
        if use_visible:
            statement = statement.outerjoin(visible, visible.id == _staff_readable_revision_id())
        if needle:
            pattern = f"%{needle[:80]}%"
            if use_visible:
                statement = statement.where(
                    or_(
                        KnowledgeDoc.title.ilike(pattern),
                        visible.body_md.ilike(pattern),
                        visible.stage_title.ilike(pattern),
                        visible.change_reason.ilike(pattern),
                    )
                )
            else:
                statement = statement.where(
                    or_(
                        KnowledgeDoc.title.ilike(pattern),
                        KnowledgeDoc.body_md.ilike(pattern),
                        KnowledgeDoc.stage_title.ilike(pattern),
                        KnowledgeDoc.change_reason.ilike(pattern),
                    )
                )
        if stage:
            if use_visible:
                statement = statement.where(visible.stage_title == stage)
            else:
                statement = statement.where(KnowledgeDoc.stage_title == stage)
        if project_id is not None:
            statement = statement.where(KnowledgeDoc.project_id == project_id)
        return list((await self.session.scalars(statement)).unique().all())

    async def get(self, actor: Actor, doc_id: UUID) -> KnowledgeDoc:
        require_project_actor(actor)
        doc = await self.session.scalar(self._visible(actor).where(KnowledgeDoc.id == doc_id))
        if not isinstance(doc, KnowledgeDoc):
            raise NotFoundError("KNOWLEDGE_NOT_FOUND", "Document not found")
        return doc

    async def create(self, actor: Actor, command: KnowledgeDocCreate) -> KnowledgeDoc:
        require_owner(actor)
        doc = KnowledgeDoc(
            title=command.title,
            body_md=command.body_md,
            tags=command.tags,
            project_id=command.project_id,
            stage_title=command.stage_title,
            change_reason=command.change_reason,
            is_canonical=command.is_canonical,
            created_by=actor.subject_id,
        )
        for index, point in enumerate(command.points):
            doc.points.append(
                KnowledgePoint(title=point.title, body_md=point.body_md, sort_order=index)
            )
        self.session.add(doc)
        await self.session.flush()
        loaded = list(doc.points) if "points" in doc.__dict__ else []
        set_committed_value(doc, "points", loaded)
        set_committed_value(doc, "revisions", [])
        await self._snapshot(actor, doc)
        return doc

    async def update(self, actor: Actor, doc_id: UUID, command: KnowledgeDocUpdate) -> KnowledgeDoc:
        require_owner(actor)
        doc = await self.session.get(KnowledgeDoc, doc_id)
        if doc is None:
            raise NotFoundError("KNOWLEDGE_NOT_FOUND", "Document not found")
        content_changed = False
        if command.title is not None:
            doc.title = command.title
        if command.body_md is not None and command.body_md != doc.body_md:
            doc.body_md = command.body_md
            content_changed = True
        if command.tags is not None:
            doc.tags = command.tags
        if "project_id" in command.model_fields_set:
            doc.project_id = command.project_id
        if command.stage_title is not None:
            doc.stage_title = command.stage_title.strip()[:255]
            content_changed = True
        if command.change_reason is not None:
            doc.change_reason = command.change_reason.strip()[:4000]
            content_changed = True
        if command.is_canonical is not None:
            doc.is_canonical = command.is_canonical
            content_changed = True
        if content_changed:
            if "revisions" not in doc.__dict__:
                await self.session.refresh(doc, attribute_names=["revisions"])
            await self._snapshot(actor, doc)
        if command.status is not None:
            doc.status = command.status
            if command.status is KnowledgeStatus.PUBLISHED:
                if doc.draft_revision_id is None:
                    if "revisions" not in doc.__dict__:
                        await self.session.refresh(doc, attribute_names=["revisions"])
                    await self._snapshot(actor, doc)
                doc.published_revision_id = doc.draft_revision_id
                if "revisions" not in doc.__dict__:
                    await self.session.refresh(doc, attribute_names=["revisions"])
                current = next(
                    (item for item in doc.revisions if item.id == doc.published_revision_id),
                    None,
                )
                if current is not None:
                    current.released = True
        await self.session.flush()
        return await self.get(actor, doc.id)

    async def ingest(self, actor: Actor, command: KnowledgeIngestCard) -> KnowledgeDoc:
        require_owner(actor)
        doc: KnowledgeDoc | None = None
        if command.target_doc_id is not None:
            doc = await self._loaded_doc(command.target_doc_id)
            if doc is None:
                raise NotFoundError("KNOWLEDGE_NOT_FOUND", "Document not found")
        else:
            title = command.new_doc_title or (
                command.points[0].title if command.points else "知识点"
            )
            doc = KnowledgeDoc(
                title=title[:255],
                tags=command.tags,
                created_by=actor.subject_id,
                source_file_id=command.source_file_id,
            )
            self.session.add(doc)
            await self.session.flush()
            set_committed_value(doc, "points", [])
            set_committed_value(doc, "revisions", [])
        if command.source_file_id is not None and doc.source_file_id is None:
            doc.source_file_id = command.source_file_id
        start = len(doc.points)
        for index, point in enumerate(command.points):
            doc.points.append(
                KnowledgePoint(
                    title=point.title,
                    body_md=point.body_md,
                    source_file_id=command.source_file_id,
                    sort_order=start + index,
                )
            )
            if point.body_md:
                doc.body_md = (doc.body_md + "\n\n" + point.body_md).strip()
        if not doc.change_reason:
            doc.change_reason = "导入知识点"
        await self.session.flush()
        await self._snapshot(actor, doc)
        await self.session.flush()
        loaded = await self.get(actor, doc.id)
        return loaded

    async def _locked_pollution(self, revision_id: UUID) -> KnowledgeRevisionPollution | None:
        recorded = await self.session.scalar(
            select(KnowledgeRevisionPollution)
            .where(KnowledgeRevisionPollution.revision_id == revision_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return recorded if isinstance(recorded, KnowledgeRevisionPollution) else None

    async def _preserve_original_points(
        self,
        revision_id: UUID,
        original: list[dict[str, object]],
        reviewed_by: UUID,
    ) -> KnowledgeRevisionPollution | None:
        pollution = KnowledgeRevisionPollution(
            revision_id=revision_id,
            reason="OWNER_CLEAR",
            original_points_json=original,
            reviewed_by=reviewed_by,
        )
        try:
            async with self.session.begin_nested():
                self.session.add(pollution)
                await self.session.flush()
            return pollution
        except IntegrityError:
            recorded = await self._locked_pollution(revision_id)
            if recorded is None:
                raise
            return recorded

    async def review_points(
        self,
        actor: Actor,
        doc_id: UUID,
        revision_id: UUID,
        action: str,
        request_id: UUID | None = None,
    ) -> KnowledgeDoc:
        require_owner(actor)
        doc = await self.get(actor, doc_id)
        revision = await self.session.scalar(
            select(KnowledgeRevision)
            .where(
                KnowledgeRevision.id == revision_id,
                KnowledgeRevision.doc_id == doc_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if revision is None:
            raise NotFoundError("KNOWLEDGE_REVISION_NOT_FOUND", "Revision not found")
        previous_status = revision.points_review or "OK"
        original = list(revision.points_json or [])
        reviewed_at = datetime.now(UTC)
        if action == "confirm":
            already_confirmed = previous_status == "OK" and revision.points_reviewed_at is not None
            revision.points_review = "OK"
            outcome = "ALREADY_CONFIRMED" if already_confirmed else "CONFIRMED"
            event_points: list[dict[str, object]] = []
        elif action == "clear":
            recorded = await self._locked_pollution(revision.id)
            if recorded is None:
                recorded = await self._preserve_original_points(
                    revision.id, original, actor.subject_id
                )
            elif not recorded.original_points_json and original:
                recorded.original_points_json = original
            if recorded is not None:
                recorded.reviewed_by = actor.subject_id
            outcome = "ALREADY_CLEARED" if previous_status == "CLEARED" else "CLEARED"
            revision.points_json = []
            revision.points_review = "CLEARED"
            preserved = list(recorded.original_points_json or []) if recorded is not None else []
            event_points = original or preserved
        else:
            raise DomainError("KNOWLEDGE_REVIEW_ACTION", "Unknown points review action", 422)
        revision.points_reviewed_by = actor.subject_id
        revision.points_reviewed_at = reviewed_at
        self.session.add(
            KnowledgeRevisionReviewEvent(
                revision_id=revision.id,
                doc_id=doc.id,
                action=action,
                outcome=outcome,
                actor_id=actor.subject_id,
                previous_status=previous_status,
                new_status=revision.points_review,
                original_points_json=event_points,
            )
        )
        await write_audit(
            self.session,
            actor_kind="user",
            actor_id=actor.subject_id,
            action=f"knowledge.revision.points_review.{action}",
            object_type="knowledge_revision",
            object_id=revision.id,
            project_id=doc.project_id,
            outcome=outcome,
            request_id=request_id,
            metadata={
                "doc_id": str(doc.id),
                "revision_id": str(revision.id),
                "version": revision.version,
                "action": action,
                "previous_status": previous_status,
                "new_status": revision.points_review,
                "repeat": outcome.startswith("ALREADY"),
            },
        )
        await self.session.flush()
        return await self.get(actor, doc.id)

    async def search(self, actor: Actor, query: str) -> list[dict[str, str]]:
        docs = await self.list_docs(actor, query)
        hits: list[dict[str, str]] = []
        needle = query.lower()
        for doc in docs:
            hits.append({"id": str(doc.id), "title": doc.title, "kind": "doc"})
            points: list[dict[str, object]] = []
            if actor.role == Role.OWNER:
                points = _points_payload(doc)
            else:
                published = staff_visible_revision(doc)
                if published is not None:
                    points = list(published.points_json or [])
            for point in points:
                title = str(point.get("title") or "")
                body = str(point.get("body_md") or "")
                if needle in (title + body).lower():
                    hits.append(
                        {
                            "id": str(point.get("id") or doc.id),
                            "title": title,
                            "kind": "point",
                            "body": body[:400],
                        }
                    )
        return hits[:20]
