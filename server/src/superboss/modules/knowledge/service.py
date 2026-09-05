"""Knowledge application service."""

from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from superboss.core.actors import Actor, require_owner, require_project_actor
from superboss.core.errors import NotFoundError
from superboss.modules.knowledge.models import KnowledgeDoc, KnowledgePoint, KnowledgeStatus
from superboss.modules.knowledge.schemas import (
    KnowledgeDocCreate,
    KnowledgeDocUpdate,
    KnowledgeIngestCard,
)
from superboss.modules.users.models import Role


class KnowledgeService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _visible(self, actor: Actor):
        statement = select(KnowledgeDoc).options(selectinload(KnowledgeDoc.points))
        if actor.role != Role.OWNER:
            statement = statement.where(KnowledgeDoc.status == KnowledgeStatus.PUBLISHED)
        return statement

    async def list_docs(self, actor: Actor, query: str | None = None) -> list[KnowledgeDoc]:
        require_project_actor(actor)
        statement = self._visible(actor).order_by(KnowledgeDoc.updated_at.desc())
        needle = (query or "").strip()
        if needle:
            pattern = f"%{needle[:80]}%"
            statement = statement.where(
                or_(KnowledgeDoc.title.ilike(pattern), KnowledgeDoc.body_md.ilike(pattern))
            )
        return list((await self.session.scalars(statement)).all())

    async def get(self, actor: Actor, doc_id: UUID) -> KnowledgeDoc:
        require_project_actor(actor)
        doc = await self.session.scalar(self._visible(actor).where(KnowledgeDoc.id == doc_id))
        if doc is None:
            raise NotFoundError("KNOWLEDGE_NOT_FOUND", "Document not found")
        return doc

    async def create(self, actor: Actor, command: KnowledgeDocCreate) -> KnowledgeDoc:
        require_owner(actor)
        doc = KnowledgeDoc(
            title=command.title,
            body_md=command.body_md,
            tags=command.tags,
            created_by=actor.subject_id,
        )
        for index, point in enumerate(command.points):
            doc.points.append(
                KnowledgePoint(title=point.title, body_md=point.body_md, sort_order=index)
            )
        self.session.add(doc)
        await self.session.flush()
        return doc

    async def update(self, actor: Actor, doc_id: UUID, command: KnowledgeDocUpdate) -> KnowledgeDoc:
        require_owner(actor)
        doc = await self.session.get(KnowledgeDoc, doc_id)
        if doc is None:
            raise NotFoundError("KNOWLEDGE_NOT_FOUND", "Document not found")
        if command.title is not None:
            doc.title = command.title
        if command.body_md is not None:
            doc.body_md = command.body_md
        if command.tags is not None:
            doc.tags = command.tags
        if command.status is not None:
            doc.status = command.status
        await self.session.flush()
        return doc

    async def ingest(self, actor: Actor, command: KnowledgeIngestCard) -> KnowledgeDoc:
        require_owner(actor)
        doc: KnowledgeDoc | None = None
        if command.target_doc_id is not None:
            doc = await self.session.get(KnowledgeDoc, command.target_doc_id)
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
        await self.session.flush()
        return doc

    async def search(self, actor: Actor, query: str) -> list[dict[str, str]]:
        docs = await self.list_docs(actor, query)
        hits: list[dict[str, str]] = []
        for doc in docs:
            hits.append({"id": str(doc.id), "title": doc.title, "kind": "doc"})
            for point in doc.points:
                if query.lower() in (point.title + point.body_md).lower():
                    hits.append(
                        {
                            "id": str(point.id),
                            "title": point.title,
                            "kind": "point",
                            "body": point.body_md[:400],
                        }
                    )
        return hits[:20]
