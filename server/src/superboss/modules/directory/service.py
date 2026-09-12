"""Directory import, listing, and communication records."""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from superboss.core.actors import Actor, require_owner
from superboss.core.errors import DomainError, ForbiddenError, NotFoundError
from superboss.core.security import utcnow
from superboss.modules.directory.models import (
    DirectoryCommunication,
    DirectoryConflict,
    DirectoryEntry,
    DirectoryImportBatch,
    DirectoryProjectLink,
    DirectorySourceRow,
)
from superboss.modules.directory.schemas import CommunicationCreate, ConvertProjectCreate
from superboss.modules.directory.xlsx import as_date, read_sheet_rows
from superboss.modules.projects.models import Project
from superboss.modules.users.models import Role

_PHONE = re.compile(r"\d")


def mask_phone(phone: str) -> str:
    digits = "".join(_PHONE.findall(phone))
    if len(digits) < 7:
        return "" if not phone else "****"
    return f"{digits[:3]}****{digits[-4:]}"


def _norm_key(value: str) -> str:
    return re.sub(r"\s+", "", value.replace("（", "(").replace("）", ")"))


def _first(record: dict[str, Any], *names: str) -> str:
    normalized = {_norm_key(str(key)): value for key, value in record.items()}
    for name in names:
        value = record.get(name)
        if value is None:
            value = normalized.get(_norm_key(name))
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _int(value: str) -> int | None:
    if not value:
        return None
    match = re.search(r"\d+", value.replace(",", ""))
    if match is None:
        return None
    return int(match.group())


def identity_key(name: str, district: str, street: str, community: str) -> str:
    packed = f"{name}|{district}|{street}|{community}"
    return re.sub(r"\s+", "", packed).lower()


def map_row(record: dict[str, Any], filename: str, district_fallback: str) -> dict[str, Any]:
    name = _first(record, "物业小区名称")
    district = _first(record, "县区") or district_fallback
    street = _first(record, "小区所在街道 （乡镇）", "小区所在街道")
    community = _first(record, "小区所在社区（村）", "小区所在社区")
    return {
        "name": name[:255],
        "district": district[:64],
        "street": street[:64],
        "community": community[:64],
        "property_company": _first(record, "物业企业全称")[:255],
        "households": _int(_first(record, "总户数（户）")),
        "floor_area": _first(
            record, "总建筑面积（平方）", "总建筑面积\n（平方）", "总建筑面积 (平方)"
        )[:64],
        "delivered_on": as_date(_first(record, "交房年月")),
        "estate_type": _first(record, "小区类型", "小区类型（多层\\高层）", "小区类型(多层\\高层)")[
            :64
        ],
        "manager_name": _first(
            record, "小区物业项目  经理姓名", "小区物业项目\n负责人姓名", "小区物业项目负责人姓名"
        )[:64],
        "phone": _first(record, "联系手机")[:32],
        "extra": {key: value for key, value in record.items() if key != "_row"},
        "source_filename": filename[:255],
        "source_sheet": "Sheet1",
        "source_row": int(record["_row"]),
        "identity_key": identity_key(name, district, street, community),
    }


def _fill_blank(current: str, incoming: str) -> str:
    return current if current.strip() else incoming


def _apply_fill_blank(existing: DirectoryEntry, mapped: dict[str, Any]) -> None:
    existing.district = _fill_blank(existing.district, mapped["district"])
    existing.street = _fill_blank(existing.street, mapped["street"])
    existing.community = _fill_blank(existing.community, mapped["community"])
    existing.property_company = _fill_blank(existing.property_company, mapped["property_company"])
    if existing.households is None:
        existing.households = mapped["households"]
    existing.floor_area = _fill_blank(existing.floor_area, mapped["floor_area"])
    if existing.delivered_on is None:
        existing.delivered_on = mapped["delivered_on"]
    existing.estate_type = _fill_blank(existing.estate_type, mapped["estate_type"])
    existing.manager_name = _fill_blank(existing.manager_name, mapped["manager_name"])
    existing.phone = _fill_blank(existing.phone, mapped["phone"])
    extra = dict(existing.extra or {})
    for key, value in mapped["extra"].items():
        if key not in extra and value not in {"", None}:
            extra[key] = value
    existing.extra = extra


def _identity_payload(
    existing: DirectoryEntry,
    mapped: dict[str, Any],
    source: DirectorySourceRow,
    batch_id: UUID,
    candidates: list[DirectoryEntry],
    incoming_id: UUID | None = None,
) -> dict[str, Any]:
    return {
        "incoming_name": mapped["name"],
        "existing_name": existing.name,
        "row": mapped["source_row"],
        "incoming_entry_id": str(incoming_id) if incoming_id else None,
        "existing_entry_id": str(existing.id),
        "incoming_households": mapped["households"],
        "existing_households": existing.households,
        "incoming_floor_area": mapped["floor_area"],
        "existing_floor_area": existing.floor_area,
        "incoming_property_company": mapped["property_company"],
        "existing_property_company": existing.property_company,
        "candidate_entry_ids": [str(item.id) for item in candidates],
        "source_row_id": str(source.id),
        "batch_id": str(batch_id),
    }


def _scale_differs(existing: DirectoryEntry, mapped: dict[str, Any]) -> bool:
    incoming_households = mapped.get("households")
    if (
        existing.households is not None
        and incoming_households is not None
        and existing.households != incoming_households
    ):
        return True
    existing_area = _norm_key(existing.floor_area or "")
    incoming_area = _norm_key(str(mapped.get("floor_area") or ""))
    return bool(existing_area and incoming_area and existing_area != incoming_area)


class DirectoryService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _require_viewer(self, actor: Actor) -> None:
        if actor.role not in {Role.OWNER, Role.STAFF}:
            raise ForbiddenError()

    async def import_workbook(self, actor: Actor, filename: str, payload: bytes) -> dict[str, Any]:
        require_owner(actor)
        district_fallback = "芗城区" if "芗城" in filename else ""
        rows = read_sheet_rows(payload, header_row=2)
        if not rows:
            raise DomainError("DIRECTORY_EMPTY_IMPORT", "Workbook has no usable rows", 422)
        batch = DirectoryImportBatch(filename=filename[:255], created_by=actor.subject_id)
        self.session.add(batch)
        await self.session.flush()
        inserted = 0
        updated = 0
        skipped = 0
        conflicts: list[dict[str, Any]] = []
        named = 0
        batch_by_key: dict[str, list[DirectoryEntry]] = {}
        for record in rows:
            mapped = map_row(record, filename, district_fallback)
            source = DirectorySourceRow(
                batch_id=batch.id,
                filename=mapped["source_filename"],
                sheet=mapped["source_sheet"],
                row_number=mapped["source_row"],
                identity_key=mapped["identity_key"],
                payload={
                    "name": mapped["name"],
                    "district": mapped["district"],
                    "street": mapped["street"],
                    "community": mapped["community"],
                    "property_company": mapped["property_company"],
                    "households": mapped["households"],
                    "floor_area": mapped["floor_area"],
                    "delivered_on": mapped["delivered_on"].isoformat()
                    if mapped["delivered_on"] is not None
                    else None,
                    "estate_type": mapped["estate_type"],
                    "manager_name": mapped["manager_name"],
                    "phone": mapped["phone"],
                    "identity_key": mapped["identity_key"],
                    "source_filename": mapped["source_filename"],
                    "source_sheet": mapped["source_sheet"],
                    "source_row": mapped["source_row"],
                    "extra": mapped["extra"],
                },
            )
            self.session.add(source)
            if not mapped["name"]:
                skipped += 1
                continue
            named += 1
            stored = list(
                (
                    await self.session.scalars(
                        select(DirectoryEntry).where(
                            DirectoryEntry.identity_key == mapped["identity_key"]
                        )
                    )
                ).all()
            )
            seen_ids = {item.id for item in stored}
            for item in batch_by_key.get(mapped["identity_key"], []):
                if item.id not in seen_ids:
                    stored.append(item)
            occupant = await self.session.scalar(
                select(DirectoryEntry).where(
                    DirectoryEntry.source_filename == mapped["source_filename"],
                    DirectoryEntry.source_sheet == mapped["source_sheet"],
                    DirectoryEntry.source_row == mapped["source_row"],
                    DirectoryEntry.identity_key != mapped["identity_key"],
                )
            )
            if occupant is not None:
                conflict = DirectoryConflict(
                    batch_id=batch.id,
                    source_row_id=source.id,
                    existing_entry_id=occupant.id,
                    reason="ROW_IDENTITY_CONFLICT",
                    payload={
                        "incoming_name": mapped["name"],
                        "existing_name": occupant.name,
                        "row": mapped["source_row"],
                    },
                )
                self.session.add(conflict)
                conflicts.append(
                    {
                        "reason": "ROW_IDENTITY_CONFLICT",
                        "row": mapped["source_row"],
                        "existing_name": occupant.name,
                        "incoming_name": mapped["name"],
                    }
                )
            in_batch = batch_by_key.get(mapped["identity_key"], [])
            compatible = [item for item in stored if not _scale_differs(item, mapped)]
            same_batch_conflict = in_batch and not any(
                not _scale_differs(item, mapped) for item in in_batch
            )
            if same_batch_conflict:
                entry = DirectoryEntry(**mapped)
                self.session.add(entry)
                await self.session.flush()
                source.entry_id = entry.id
                inserted += 1
                batch_by_key.setdefault(mapped["identity_key"], []).append(entry)
                conflict_payload = _identity_payload(
                    in_batch[0], mapped, source, batch.id, stored, entry.id
                )
                conflict_payload["provisional_entry_id"] = str(entry.id)
                self.session.add(
                    DirectoryConflict(
                        batch_id=batch.id,
                        source_row_id=source.id,
                        existing_entry_id=in_batch[0].id,
                        reason="IDENTITY_SCALE_MISMATCH",
                        payload=conflict_payload,
                        status="OPEN",
                    )
                )
                conflicts.append({"reason": "IDENTITY_SCALE_MISMATCH", **conflict_payload})
                continue
            if len(stored) == 1 and compatible == stored:
                match = stored[0]
                _apply_fill_blank(match, mapped)
                source.entry_id = match.id
                updated += 1
                batch_by_key.setdefault(mapped["identity_key"], []).append(match)
                continue
            if stored:
                reason = "IDENTITY_AMBIGUOUS" if len(stored) > 1 else "IDENTITY_SCALE_MISMATCH"
                skipped += 1
                for existing in stored:
                    conflict_payload = _identity_payload(existing, mapped, source, batch.id, stored)
                    self.session.add(
                        DirectoryConflict(
                            batch_id=batch.id,
                            source_row_id=source.id,
                            existing_entry_id=existing.id,
                            reason=reason,
                            payload=conflict_payload,
                            status="OPEN",
                        )
                    )
                    conflicts.append({"reason": reason, **conflict_payload})
                continue
            entry = DirectoryEntry(**mapped)
            self.session.add(entry)
            await self.session.flush()
            source.entry_id = entry.id
            inserted += 1
            batch_by_key.setdefault(mapped["identity_key"], []).append(entry)
        if named == 0:
            raise DomainError("DIRECTORY_EMPTY_IMPORT", "Workbook has no named communities", 422)
        await self.session.flush()
        return {
            "inserted": inserted,
            "updated": updated,
            "skipped": skipped,
            "conflicts": conflicts,
        }

    async def list_entries(
        self,
        actor: Actor,
        *,
        query: str | None = None,
        district: str | None = None,
        street: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[DirectoryEntry], int]:
        self._require_viewer(actor)
        statement = select(DirectoryEntry)
        count_statement = select(func.count()).select_from(DirectoryEntry)
        if district:
            statement = statement.where(DirectoryEntry.district == district)
            count_statement = count_statement.where(DirectoryEntry.district == district)
        if street:
            statement = statement.where(DirectoryEntry.street == street)
            count_statement = count_statement.where(DirectoryEntry.street == street)
        needle = (query or "").strip()[:80]
        if needle:
            pattern = f"%{needle}%"
            match = or_(
                DirectoryEntry.name.ilike(pattern),
                DirectoryEntry.property_company.ilike(pattern),
                DirectoryEntry.community.ilike(pattern),
                DirectoryEntry.manager_name.ilike(pattern),
            )
            statement = statement.where(match)
            count_statement = count_statement.where(match)
        total = int(await self.session.scalar(count_statement) or 0)
        rows = (
            await self.session.scalars(
                statement.order_by(
                    DirectoryEntry.district,
                    DirectoryEntry.street,
                    DirectoryEntry.name,
                    DirectoryEntry.source_row,
                )
                .offset(offset)
                .limit(limit)
            )
        ).all()
        return list(rows), total

    async def get_entry(self, actor: Actor, entry_id: UUID) -> DirectoryEntry:
        self._require_viewer(actor)
        entry = await self.session.get(DirectoryEntry, entry_id)
        if entry is None:
            raise NotFoundError("DIRECTORY_NOT_FOUND", "Directory entry not found")
        return entry

    async def add_communication(
        self, actor: Actor, entry_id: UUID, command: CommunicationCreate
    ) -> DirectoryCommunication:
        require_owner(actor)
        entry = await self.get_entry(actor, entry_id)
        if not command.contact_name or not command.contact_role or not command.demand:
            raise DomainError("VALIDATION_ERROR", "Communication is incomplete", 422)
        row = DirectoryCommunication(
            entry_id=entry.id,
            occurred_on=command.occurred_on,
            contact_name=command.contact_name,
            contact_role=command.contact_role,
            demand=command.demand,
            result=command.result,
            next_step=command.next_step,
            status=command.status,
            created_by=actor.subject_id,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def list_communications(
        self, actor: Actor, entry_id: UUID
    ) -> list[DirectoryCommunication]:
        await self.get_entry(actor, entry_id)
        rows = (
            await self.session.scalars(
                select(DirectoryCommunication)
                .where(DirectoryCommunication.entry_id == entry_id)
                .order_by(DirectoryCommunication.occurred_on.desc())
            )
        ).all()
        return list(rows)

    async def convert_to_project(
        self, actor: Actor, entry_id: UUID, command: ConvertProjectCreate
    ) -> Project:
        require_owner(actor)
        from superboss.core.security import utcnow
        from superboss.modules.projects.schemas import ProjectCreate
        from superboss.modules.projects.service import ProjectService

        entry = await self.get_entry(actor, entry_id)
        notes = await self.list_communications(actor, entry_id)
        latest = notes[0] if notes else None
        description_parts = [
            f"{entry.district} {entry.street} {entry.community} {entry.name}".strip(),
        ]
        if latest is not None:
            description_parts.append(f"{latest.demand} {latest.result}".strip())
        service = ProjectService(self.session)
        name = (command.name or f"{entry.name}会务")[:255]
        if await service._name_taken(name):
            name = f"{name}-{utcnow().date().isoformat()}"[:255]
        if entry.project_id is not None and not command.new_engagement:
            existing = await service.get(actor, entry.project_id)
            return existing
        if entry.project_id is not None and command.new_engagement:
            current = await self.session.scalar(
                select(DirectoryProjectLink).where(
                    DirectoryProjectLink.entry_id == entry.id,
                    DirectoryProjectLink.is_current.is_(True),
                )
            )
            if current is not None:
                current.is_current = False
        project = await service.create(
            actor,
            ProjectCreate(
                name=name,
                description="；".join(part for part in description_parts if part)[:4000],
                starts_on=command.starts_on or utcnow().date(),
                service_fee_cents=command.service_fee_cents,
                lead_user_id=command.lead_user_id,
            ),
        )
        entry.project_id = project.id
        self.session.add(
            DirectoryProjectLink(entry_id=entry.id, project_id=project.id, is_current=True)
        )
        await self.session.flush()
        return project

    async def facets(self, actor: Actor) -> dict[str, object]:
        self._require_viewer(actor)
        rows = (
            await self.session.execute(
                select(DirectoryEntry.district, DirectoryEntry.street).order_by(
                    DirectoryEntry.district, DirectoryEntry.street
                )
            )
        ).all()
        districts = sorted({item[0] for item in rows if item[0]}, key=lambda value: value)
        streets = sorted({item[1] for item in rows if item[1]}, key=lambda value: value)
        by_district: dict[str, list[str]] = {}
        for district, street in rows:
            if not district or not street:
                continue
            bucket = by_district.setdefault(district, [])
            if street not in bucket:
                bucket.append(street)
        return {"districts": districts, "streets": streets, "streets_by_district": by_district}

    async def list_conflicts(
        self,
        actor: Actor,
        *,
        status: str | None = "OPEN",
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[DirectoryConflict], int]:
        require_owner(actor)
        statement = select(DirectoryConflict)
        count_statement = select(func.count()).select_from(DirectoryConflict)
        if status:
            statement = statement.where(DirectoryConflict.status == status)
            count_statement = count_statement.where(DirectoryConflict.status == status)
        total = int(await self.session.scalar(count_statement) or 0)
        rows = (
            await self.session.scalars(
                statement.order_by(DirectoryConflict.created_at.desc(), DirectoryConflict.id.desc())
                .offset(offset)
                .limit(limit)
            )
        ).all()
        return list(rows), total

    def _mapped_from_source(self, source: DirectorySourceRow) -> dict[str, Any]:
        payload = dict(source.payload or {})
        raw_extra = payload.get("extra")
        extra: dict[str, Any] = raw_extra if isinstance(raw_extra, dict) else payload
        delivered = payload.get("delivered_on")
        return {
            "name": str(payload.get("name") or extra.get("物业小区名称") or "")[:255],
            "district": str(payload.get("district") or extra.get("县区") or "")[:64],
            "street": str(
                payload.get("street")
                or extra.get("小区所在街道 （乡镇）")
                or extra.get("小区所在街道")
                or ""
            )[:64],
            "community": str(
                payload.get("community")
                or extra.get("小区所在社区（村）")
                or extra.get("小区所在社区")
                or ""
            )[:64],
            "property_company": str(
                payload.get("property_company") or extra.get("物业企业全称") or ""
            )[:255],
            "households": payload.get("households")
            if isinstance(payload.get("households"), int)
            else _int(str(extra.get("总户数（户）") or "")),
            "floor_area": str(
                payload.get("floor_area")
                or extra.get("总建筑面积（平方）")
                or extra.get("总建筑面积")
                or ""
            )[:64],
            "delivered_on": as_date(str(delivered)) if delivered else None,
            "estate_type": str(payload.get("estate_type") or "")[:64],
            "manager_name": str(payload.get("manager_name") or "")[:64],
            "phone": str(payload.get("phone") or extra.get("联系手机") or "")[:32],
            "extra": extra if isinstance(extra, dict) else {},
            "source_filename": source.filename[:255],
            "source_sheet": source.sheet,
            "source_row": source.row_number,
            "identity_key": source.identity_key
            or identity_key(
                str(payload.get("name") or ""),
                str(payload.get("district") or ""),
                str(payload.get("street") or ""),
                str(payload.get("community") or ""),
            ),
        }

    async def _close_sibling_conflicts(
        self,
        source_id: UUID,
        keep_id: UUID,
        resolved_entry_id: UUID | None,
        actor: Actor,
    ) -> None:
        await self.session.execute(
            update(DirectoryConflict)
            .where(
                DirectoryConflict.source_row_id == source_id,
                DirectoryConflict.id != keep_id,
                DirectoryConflict.status == "OPEN",
            )
            .values(
                status="SUPERSEDED",
                resolved_entry_id=resolved_entry_id,
                resolved_by=actor.subject_id,
                resolved_at=utcnow(),
            )
        )

    async def resolve_conflict(
        self, actor: Actor, conflict_id: UUID, action: str
    ) -> DirectoryConflict:
        require_owner(actor)
        conflict = await self.session.get(DirectoryConflict, conflict_id)
        if conflict is None:
            raise NotFoundError("DIRECTORY_CONFLICT_NOT_FOUND", "Conflict not found")
        source = None
        if conflict.source_row_id is not None:
            source = await self.session.scalar(
                select(DirectorySourceRow)
                .where(DirectorySourceRow.id == conflict.source_row_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        else:
            conflict = await self.session.scalar(
                select(DirectoryConflict)
                .where(DirectoryConflict.id == conflict_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if conflict is None:
                raise NotFoundError("DIRECTORY_CONFLICT_NOT_FOUND", "Conflict not found")
        await self.session.refresh(conflict)
        if source is not None:
            await self.session.refresh(source)
        if conflict.status != "OPEN":
            return conflict
        payload = dict(conflict.payload or {})
        provisional_id: UUID | None = None
        raw_provisional = payload.get("provisional_entry_id") or payload.get("incoming_entry_id")
        if raw_provisional:
            try:
                provisional_id = UUID(str(raw_provisional))
            except (TypeError, ValueError):
                provisional_id = None
        if (
            source is not None
            and source.entry_id is not None
            and (provisional_id is None or source.entry_id != provisional_id)
        ):
            conflict.status = "SUPERSEDED"
            conflict.resolved_entry_id = source.entry_id
            conflict.resolved_by = actor.subject_id
            conflict.resolved_at = utcnow()
            await self.session.flush()
            return conflict
        if action == "link":
            if conflict.existing_entry_id is None:
                raise DomainError(
                    "DIRECTORY_CONFLICT_NO_ENTRY", "Conflict has no existing entry", 422
                )
            existing = await self.session.get(DirectoryEntry, conflict.existing_entry_id)
            if existing is None:
                raise NotFoundError("DIRECTORY_ENTRY_NOT_FOUND", "Directory entry not found")
            if source is not None:
                _apply_fill_blank(existing, self._mapped_from_source(source))
                source.entry_id = existing.id
            conflict.status = "LINKED"
            conflict.resolved_entry_id = existing.id
        elif action == "split":
            if source is None:
                raise DomainError("DIRECTORY_CONFLICT_NO_SOURCE", "Conflict has no source row", 422)
            mapped = self._mapped_from_source(source)
            if source.entry_id is None:
                extra = dict(mapped)
                extra.pop("identity_key", None)
                entry = DirectoryEntry(**mapped)
                self.session.add(entry)
                await self.session.flush()
                source.entry_id = entry.id
            conflict.status = "SPLIT"
            conflict.resolved_entry_id = source.entry_id
        else:
            raise DomainError("DIRECTORY_CONFLICT_ACTION", "Unknown conflict action", 422)
        conflict.resolved_by = actor.subject_id
        conflict.resolved_at = utcnow()
        if source is not None:
            await self._close_sibling_conflicts(
                source.id, conflict.id, conflict.resolved_entry_id, actor
            )
        await self.session.flush()
        return conflict

    async def list_projects(self, actor: Actor, entry_id: UUID) -> list[DirectoryProjectLink]:
        await self.get_entry(actor, entry_id)
        rows = (
            await self.session.scalars(
                select(DirectoryProjectLink)
                .where(DirectoryProjectLink.entry_id == entry_id)
                .order_by(DirectoryProjectLink.created_at.desc())
            )
        ).all()
        return list(rows)


def to_read(entry: DirectoryEntry, actor: Actor) -> dict[str, object]:
    payload = {
        "id": entry.id,
        "name": entry.name,
        "district": entry.district,
        "street": entry.street,
        "community": entry.community,
        "property_company": entry.property_company,
        "households": entry.households,
        "floor_area": entry.floor_area,
        "delivered_on": entry.delivered_on,
        "estate_type": entry.estate_type,
        "manager_name": entry.manager_name,
        "phone_masked": mask_phone(entry.phone),
        "phone": entry.phone if actor.role == Role.OWNER else None,
        "source_filename": entry.source_filename,
        "source_sheet": entry.source_sheet,
        "source_row": entry.source_row,
        "extra": entry.extra if actor.role == Role.OWNER else {},
        "project_id": entry.project_id,
    }
    return payload
