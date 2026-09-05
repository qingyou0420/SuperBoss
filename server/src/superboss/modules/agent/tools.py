"""Read-only tools execute immediately; propose_* tools only create cards."""

import json
from dataclasses import dataclass, field
from typing import Any
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
    _function("list_projects", "列出全部项目、阶段、进度和里程碑。", {}, []),
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
                "milestones": [
                    {"id": str(point.id), "title": point.title, "due_on": point.due_on}
                    for point in item.milestones
                ],
            }
            for item in projects
        ]
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
    if name == "search_knowledge":
        from superboss.modules.knowledge.service import KnowledgeService

        return await KnowledgeService(context.session).search(
            context.actor, str(payload.get("query") or "")
        )
    if name == "recall_memory":
        return await context.recall(str(payload.get("query") or ""))
    return {"error": "UNKNOWN_TOOL"}
