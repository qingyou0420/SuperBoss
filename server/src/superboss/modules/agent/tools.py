"""Read-only tools execute immediately; propose_* tools only create cards."""

import json
from dataclasses import dataclass, field
from typing import Any, cast
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from superboss.core.actors import Actor
from superboss.modules.agent.models import AgentCard, CardKind, CardStatus
from superboss.modules.agent.schemas import CARD_MODELS
from superboss.modules.files.service import FileService
from superboss.modules.files.storage import ObjectStorage
from superboss.modules.finance.service import FinanceService
from superboss.modules.projects.service import ProjectService

_PROPOSE = {
    "propose_finance_entry": CardKind.FINANCE_ENTRY,
    "propose_finance_adjust": CardKind.FINANCE_ADJUST,
    "propose_project": CardKind.PROJECT_CREATE,
    "propose_project_update": CardKind.PROJECT_UPDATE,
    "propose_milestones": CardKind.MILESTONE_CHANGE,
    "propose_file_move": CardKind.FILE_MOVE,
    "propose_memory": CardKind.MEMORY,
    "propose_knowledge_ingest": CardKind.KNOWLEDGE_INGEST,
}


def _function(
    name: str, description: str, properties: dict[str, Any], required: list[str]
) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": properties,
                "required": required,
            },
        },
    }


TOOLS: list[dict[str, Any]] = [
    _function(
        "list_projects",
        "列出全部项目、阶段、进度、约定服务费和当前节点。",
        {},
        [],
    ),
    _function(
        "list_directory",
        "搜索业务地图名录。不合并同名来源行。",
        {"query": {"type": "string"}, "district": {"type": "string"}},
        [],
    ),
    _function(
        "record_communication",
        "把老板已确认的沟通直接写入名录，不走卡片。",
        {
            "entry_id": {"type": "string"},
            "occurred_on": {"type": "string"},
            "contact_name": {"type": "string"},
            "contact_role": {"type": "string"},
            "demand": {"type": "string"},
            "result": {"type": "string"},
            "next_step": {"type": "string"},
            "status": {
                "type": "string",
                "enum": ["LEARNING", "CONTACTING", "COMMISSIONED", "PAUSED", "DROPPED"],
            },
        },
        ["entry_id", "occurred_on", "contact_name", "contact_role", "demand", "result"],
    ),
    _function(
        "preview_schedule_shift",
        "试算从某节点起顺延或提前若干天，不写库。",
        {
            "project_id": {"type": "string"},
            "node_id": {"type": "string"},
            "days": {"type": "integer"},
        },
        ["project_id", "node_id", "days"],
    ),
    _function(
        "apply_schedule_shift",
        "老板已与客户确认后，从某节点起调整后续排期并记录原因。",
        {
            "project_id": {"type": "string"},
            "node_id": {"type": "string"},
            "days": {"type": "integer"},
            "reason": {"type": "string"},
        },
        ["project_id", "node_id", "days", "reason"],
    ),
    _function(
        "complete_project_node",
        "确认当前阶段完成。必须提供已上传文件的 file_id，不能用任意文字代替。",
        {
            "project_id": {"type": "string"},
            "node_id": {"type": "string"},
            "evidence": {"type": "array", "items": {"type": "string"}},
        },
        ["project_id", "node_id"],
    ),
    _function(
        "convert_directory_to_project",
        "已确定委托后，从名录建立会务项目并保留原沟通链。",
        {
            "entry_id": {"type": "string"},
            "name": {"type": "string"},
            "starts_on": {"type": "string"},
            "service_fee_cents": {"type": "integer"},
        },
        ["entry_id"],
    ),
    _function(
        "import_finance_batch",
        "把已核对的账本行直接入账。同一 batch_key 重传不重复记账。",
        {
            "batch_key": {"type": "string"},
            "rows": {"type": "array", "items": {"type": "object"}},
        },
        ["batch_key", "rows"],
    ),
    _function(
        "import_finance_from_file",
        "读取老板提交的账本附件并入账。支持 xlsx；异常行逐条返回，已成功行重试不重复。",
        {
            "file_id": {"type": "string"},
            "batch_key": {"type": "string"},
        },
        ["file_id"],
    ),
    _function(
        "get_finance_overview",
        "经营总览。没有期初余额时不把净流入当成银行可用余额。",
        {},
        [],
    ),
    _function(
        "get_project_rewards",
        "按确认服务费和直接成本计算节余奖金、抽成和毛利。",
        {"project_id": {"type": "string"}},
        ["project_id"],
    ),
    _function(
        "record_knowledge",
        "按老板指令写入知识链。publish 为 true 时直接发布。",
        {
            "title": {"type": "string"},
            "body_md": {"type": "string"},
            "stage_title": {"type": "string"},
            "change_reason": {"type": "string"},
            "project_id": {"type": "string"},
            "is_canonical": {"type": "boolean"},
            "publish": {"type": "boolean"},
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        ["title"],
    ),
    _function(
        "get_finance_summary",
        "按月汇总财务。STAFF 不可见公司与收入，但老板可见全部。",
        {"month": {"type": "string", "description": "YYYY-MM，缺省为当月"}},
        [],
    ),
    _function("list_folders", "列出网盘目录及可见性。", {}, []),
    _function(
        "list_files",
        "列出一个目录下的文件。",
        {"folder_id": {"type": "string"}},
        ["folder_id"],
    ),
    _function(
        "search_knowledge",
        "搜索已发布知识文档和知识点。",
        {"query": {"type": "string"}},
        ["query"],
    ),
    _function(
        "recall_memory",
        "按关键词召回长期记忆。",
        {"query": {"type": "string"}},
        ["query"],
    ),
    _function(
        "propose_finance_entry",
        "提出一张财务入账卡片，不直接写库。",
        {
            "kind": {"type": "string", "enum": ["COST", "INCOME"]},
            "scope": {"type": "string", "enum": ["COMPANY", "PROJECT"]},
            "project_id": {"type": "string"},
            "amount_cents": {"type": "integer"},
            "occurred_on": {"type": "string"},
            "category": {"type": "string"},
            "memo": {"type": "string"},
            "visibility": {"type": "string", "enum": ["ALL", "MANAGEMENT", "OWNER_ONLY"]},
        },
        ["kind", "scope", "amount_cents", "occurred_on", "category"],
    ),
    _function(
        "propose_finance_adjust",
        "提出财务调整卡片。",
        {
            "entry_id": {"type": "string"},
            "field": {
                "type": "string",
                "enum": ["amount_cents", "occurred_on", "category", "memo", "visibility"],
            },
            "new_value": {"type": "string"},
            "reason": {"type": "string"},
        },
        ["entry_id", "field", "new_value", "reason"],
    ),
    _function(
        "propose_project",
        "提出新建项目卡片。",
        {
            "name": {"type": "string"},
            "description": {"type": "string"},
            "stage": {
                "type": "string",
                "enum": ["PLANNING", "ACTIVE", "DELIVERING", "REVIEW", "ARCHIVED"],
            },
        },
        ["name"],
    ),
    _function(
        "propose_project_update",
        "提出项目字段变更卡片。",
        {
            "project_id": {"type": "string"},
            "name": {"type": "string"},
            "description": {"type": "string"},
            "stage": {
                "type": "string",
                "enum": ["PLANNING", "ACTIVE", "DELIVERING", "REVIEW", "ARCHIVED"],
            },
            "progress_percent": {"type": "integer"},
        },
        ["project_id"],
    ),
    _function(
        "propose_milestones",
        "提出里程碑增删改卡片。",
        {
            "project_id": {"type": "string"},
            "add": {"type": "array", "items": {"type": "object"}},
            "update": {"type": "array", "items": {"type": "object"}},
            "remove": {"type": "array", "items": {"type": "string"}},
        },
        ["project_id"],
    ),
    _function(
        "propose_file_move",
        "提出文件移动或重命名卡片。",
        {
            "file_id": {"type": "string"},
            "target_folder_id": {"type": "string"},
            "new_name": {"type": "string"},
        },
        ["file_id", "target_folder_id"],
    ),
    _function(
        "propose_knowledge_ingest",
        "提出知识入库卡片，列出知识点，不直接写库。",
        {
            "source_file_id": {"type": "string"},
            "target_doc_id": {"type": "string"},
            "new_doc_title": {"type": "string"},
            "points": {"type": "array", "items": {"type": "object"}},
        },
        ["points"],
    ),
    _function(
        "propose_memory",
        "提出长期记忆卡片，供老板确认后写入。",
        {
            "kind": {
                "type": "string",
                "enum": ["FACT", "PREFERENCE", "DECISION", "PROJECT_NOTE"],
            },
            "content": {"type": "string"},
            "importance": {"type": "integer"},
            "pinned": {"type": "boolean"},
        },
        ["kind", "content"],
    ),
]


@dataclass
class ToolContext:
    session: AsyncSession
    actor: Actor
    storage: ObjectStorage | None
    conversation_id: UUID
    recall: Any
    pending_cards: list[AgentCard] = field(default_factory=list)


async def execute_tool(context: ToolContext, name: str, arguments: str) -> str:
    try:
        payload = json.loads(arguments) if arguments else {}
        if not isinstance(payload, dict):
            raise TypeError("arguments must be an object")
    except (json.JSONDecodeError, TypeError, ValueError) as error:
        return json.dumps({"error": str(error)}, ensure_ascii=False)
    try:
        result = await _run(context, name, payload)
    except Exception as error:  # noqa: BLE001 -- tool errors are returned to the model
        return json.dumps(
            {"error": getattr(error, "code", type(error).__name__)}, ensure_ascii=False
        )
    return json.dumps(result, ensure_ascii=False, default=str)


async def _run(context: ToolContext, name: str, payload: dict[str, Any]) -> object:
    if name in _PROPOSE:
        kind = _PROPOSE[name]
        parsed = CARD_MODELS[kind].model_validate(payload)
        card = AgentCard(
            conversation_id=context.conversation_id,
            kind=kind,
            payload=parsed.model_dump(mode="json"),
            status=CardStatus.PROPOSED,
        )
        context.session.add(card)
        await context.session.flush()
        context.pending_cards.append(card)
        return {"card_id": str(card.id), "kind": kind.value, "status": "PROPOSED"}
    if name == "list_projects":
        projects = await ProjectService(context.session).list(context.actor)
        return [
            {
                "id": str(item.id),
                "name": item.name,
                "stage": item.stage.value,
                "progress_percent": item.progress_percent,
                "service_fee_cents": item.service_fee_cents,
                "nodes": [
                    {
                        "id": str(node.id),
                        "title": node.title,
                        "status": node.status.value,
                        "planned_end": node.planned_end,
                    }
                    for node in item.nodes
                ],
            }
            for item in projects
        ]
    if name == "list_directory":
        from superboss.modules.directory.service import DirectoryService, to_read

        query = payload.get("query") if isinstance(payload.get("query"), str) else None
        district = payload.get("district") if isinstance(payload.get("district"), str) else None
        rows, total = await DirectoryService(context.session).list_entries(
            context.actor, query=query, district=district, limit=20
        )
        return {
            "total": total,
            "items": [to_read(item, context.actor) for item in rows],
        }
    if name == "record_communication":
        from datetime import date as date_cls

        from superboss.modules.directory.models import CommunicationStatus
        from superboss.modules.directory.schemas import CommunicationCreate
        from superboss.modules.directory.service import DirectoryService

        command = CommunicationCreate(
            occurred_on=date_cls.fromisoformat(str(payload["occurred_on"])),
            contact_name=str(payload["contact_name"]),
            contact_role=str(payload["contact_role"]),
            demand=str(payload["demand"]),
            result=str(payload["result"]),
            next_step=str(payload.get("next_step") or ""),
            status=cast(CommunicationStatus, payload.get("status") or "LEARNING"),
        )
        row = await DirectoryService(context.session).add_communication(
            context.actor, UUID(str(payload["entry_id"])), command
        )
        return {"id": str(row.id), "written": True}
    if name == "preview_schedule_shift":
        service = ProjectService(context.session)
        project = await service.get(context.actor, UUID(str(payload["project_id"])))
        return service.preview_shift(project, UUID(str(payload["node_id"])), int(payload["days"]))
    if name == "apply_schedule_shift":
        project = await ProjectService(context.session).apply_shift(
            context.actor,
            UUID(str(payload["project_id"])),
            UUID(str(payload["node_id"])),
            int(payload["days"]),
            str(payload.get("reason") or ""),
        )
        return {"project_id": str(project.id), "written": True, "due_on": project.due_on}
    if name == "complete_project_node":
        raw_evidence = payload.get("evidence")
        evidence: list[object] = raw_evidence if isinstance(raw_evidence, list) else []
        project = await ProjectService(context.session).complete_node(
            context.actor,
            UUID(str(payload["project_id"])),
            UUID(str(payload["node_id"])),
            [str(item) for item in evidence],
        )
        return {
            "project_id": str(project.id),
            "progress_percent": project.progress_percent,
            "written": True,
        }
    if name == "get_finance_summary":
        month = payload.get("month")
        summary = await FinanceService(context.session).summary(
            context.actor, month if isinstance(month, str) else None
        )
        return summary.model_dump(mode="json", exclude_none=True)
    if name == "list_folders":
        folders = await FileService(context.session, context.storage).list_folders(context.actor)
        return [
            {
                "id": str(item.id),
                "name": item.name,
                "parent_id": str(item.parent_id) if item.parent_id else None,
                "visibility": item.visibility.value,
            }
            for item in folders
        ]
    if name == "list_files":
        folder_id = UUID(str(payload["folder_id"]))
        files = await FileService(context.session, context.storage).list_files(
            context.actor, folder_id
        )
        return [
            {
                "id": str(item.id),
                "filename": item.filename,
                "state": item.state.value,
                "folder_id": str(item.folder_id),
            }
            for item in files
        ]
    if name == "convert_directory_to_project":
        from datetime import date as date_cls

        from superboss.modules.directory.schemas import ConvertProjectCreate
        from superboss.modules.directory.service import DirectoryService

        starts = payload.get("starts_on")
        project = await DirectoryService(context.session).convert_to_project(
            context.actor,
            UUID(str(payload["entry_id"])),
            ConvertProjectCreate(
                name=str(payload.get("name") or ""),
                starts_on=date_cls.fromisoformat(str(starts)) if starts else None,
                service_fee_cents=payload.get("service_fee_cents"),
            ),
        )
        return {"project_id": str(project.id), "name": project.name, "written": True}
    if name == "import_finance_batch":
        from superboss.modules.finance.schemas import FinanceImportCommand

        result = await FinanceService(context.session).import_batch(
            context.actor, FinanceImportCommand.model_validate(payload)
        )
        return {
            "batch_key": result["batch_key"],
            "inserted": result["inserted"],
            "skipped": result["skipped"],
            "unresolved": result["unresolved"],
            "replayed": result["replayed"],
            "written": True,
        }
    if name == "import_finance_from_file":
        from hashlib import sha256

        from superboss.modules.files.models import File, FileState

        file = await context.session.get(File, UUID(str(payload["file_id"])))
        if file is None:
            return {"error": "FILE_NOT_FOUND"}
        if file.state is not FileState.CLEAN:
            return {"error": "FILE_NOT_CLEAN"}
        if context.storage is None:
            return {"error": "STORAGE_UNAVAILABLE"}
        chunks: list[bytes] = []
        async for chunk in context.storage.stream(file.object_key):
            chunks.append(chunk)
        payload_bytes = b"".join(chunks)
        key = str(payload.get("batch_key") or sha256(payload_bytes).hexdigest()[:32])
        result = await FinanceService(context.session).import_from_file(
            context.actor,
            payload_bytes,
            batch_key=key,
            filename=file.filename,
            source_file_id=file.id,
        )
        return {
            "batch_key": result["batch_key"],
            "inserted": result["inserted"],
            "skipped": result["skipped"],
            "unresolved": result["unresolved"],
            "parse_unresolved": result.get("parse_unresolved") or [],
            "replayed": result["replayed"],
            "written": True,
        }
    if name == "get_finance_overview":
        return await FinanceService(context.session).overview(context.actor)
    if name == "get_project_rewards":
        return await FinanceService(context.session).rewards_for(
            context.actor, UUID(str(payload["project_id"]))
        )
    if name == "record_knowledge":
        from superboss.modules.knowledge.models import KnowledgeStatus
        from superboss.modules.knowledge.schemas import KnowledgeDocCreate
        from superboss.modules.knowledge.service import KnowledgeService

        knowledge_service = KnowledgeService(context.session)
        raw_tags = payload.get("tags")
        tags: list[object] = raw_tags if isinstance(raw_tags, list) else []
        doc = await knowledge_service.create(
            context.actor,
            KnowledgeDocCreate(
                title=str(payload["title"]),
                body_md=str(payload.get("body_md") or ""),
                tags=[str(item) for item in tags][:20],
                project_id=UUID(str(payload["project_id"])) if payload.get("project_id") else None,
                stage_title=str(payload.get("stage_title") or ""),
                change_reason=str(payload.get("change_reason") or ""),
                is_canonical=bool(payload.get("is_canonical")),
            ),
        )
        if payload.get("publish"):
            from superboss.modules.knowledge.schemas import KnowledgeDocUpdate

            doc = await knowledge_service.update(
                context.actor, doc.id, KnowledgeDocUpdate(status=KnowledgeStatus.PUBLISHED)
            )
        return {"id": str(doc.id), "status": doc.status.value, "written": True}
    if name == "search_knowledge":
        from superboss.modules.knowledge.service import KnowledgeService

        return await KnowledgeService(context.session).search(
            context.actor, str(payload.get("query") or "")
        )
    if name == "recall_memory":
        return await context.recall(str(payload.get("query") or ""))
    return {"error": "UNKNOWN_TOOL"}
