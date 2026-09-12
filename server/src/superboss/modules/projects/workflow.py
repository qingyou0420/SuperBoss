"""Default assembly-meeting template and date shifting."""

from datetime import date, timedelta
from typing import Any

DEFAULT_STAGES: tuple[tuple[str, int, bool, tuple[str, ...], str, tuple[str, ...]], ...] = (
    (
        "筹备与资料确认",
        3,
        False,
        ("核对本小区议题和委托资料", "确认本项目联系人及沟通方式"),
        "筹备工作安排.pdf",
        ("document",),
    ),
    (
        "候选人报名",
        5,
        False,
        ("准备报名表与候选人资料清单", "核对报名截止时间及受理方式"),
        "候选人报名通知.pdf",
        ("document",),
    ),
    (
        "候选人名单公示",
        3,
        True,
        ("使用经确认的候选人名单", "提前确认公示位置与照片留存方式"),
        "候选人名单公示.pdf",
        ("document", "photo"),
    ),
    (
        "业主大会公告",
        3,
        True,
        ("核对公告中的议题、时间与地点", "确认投票材料与现场准备事项"),
        "业主大会召开公告.pdf",
        ("document", "photo"),
    ),
    (
        "投票与现场执行",
        2,
        True,
        ("按最新日程准备投票材料", "确认现场物料与资料保管方式"),
        "投票执行安排.pdf",
        ("document", "photo"),
    ),
    (
        "开箱与结果统计",
        2,
        False,
        ("准备经确认的统计表及记录材料", "核对结果材料与原始记录"),
        "开箱及结果统计表.pdf",
        ("document",),
    ),
    (
        "结果公示与备案",
        5,
        True,
        ("核对结果公示定稿与相关材料", "按本项目要求整理备案资料"),
        "表决结果公示.pdf",
        ("document", "photo"),
    ),
)


def add_days(value: date, days: int) -> date:
    return value + timedelta(days=days)


def plan_from_stages(start: date, stages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cursor = start
    planned: list[dict[str, Any]] = []
    for item in stages:
        duration = max(int(item.get("duration_days") or 1), 1)
        end = add_days(cursor, duration - 1)
        planned.append(
            {
                "title": item["title"],
                "planned_start": cursor,
                "planned_end": end,
                "photo_required": bool(item.get("photo_required")),
                "preparation": list(item.get("preparation") or []),
                "document_name": str(item.get("document_name") or ""),
                "required_materials": list(item.get("required_materials") or []),
                "duration_days": duration,
            }
        )
        cursor = add_days(end, 1)
    return planned


def default_stage_dicts() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for title, duration, photo, prep, document, materials in DEFAULT_STAGES:
        rows.append(
            {
                "title": title,
                "duration_days": duration,
                "photo_required": photo,
                "preparation": list(prep),
                "document_name": document,
                "required_materials": list(materials),
            }
        )
    return rows


def plan_from_start(start: date) -> list[dict[str, Any]]:
    return plan_from_stages(start, default_stage_dicts())


def _status_value(node: dict[str, Any]) -> str:
    status = node.get("status")
    value = getattr(status, "value", status)
    return str(value or "")


def _is_immutable(node: dict[str, Any]) -> bool:
    return _status_value(node) == "DONE"


def reflow_from_anchor(
    nodes: list[dict[str, Any]], anchor_index: int, new_start: date
) -> list[dict[str, Any]]:
    """Keep earlier and DONE dates; rebuild later OPEN dates from the anchor."""
    result: list[dict[str, Any]] = []
    cursor: date | None = None
    for index, node in enumerate(nodes):
        row = dict(node)
        if _is_immutable(node):
            result.append(row)
            end = row.get("planned_end")
            if index >= anchor_index and isinstance(end, date):
                nxt = add_days(end, 1)
                if cursor is None or nxt > cursor:
                    cursor = nxt
            continue
        duration = max(int(node.get("duration_days") or 1), 1)
        if index < anchor_index:
            result.append(row)
            continue
        if index == anchor_index:
            cursor = new_start
        elif cursor is None:
            start = node.get("planned_start")
            cursor = start if isinstance(start, date) else new_start
        end = add_days(cursor, duration - 1)
        row["planned_start"] = cursor
        row["planned_end"] = end
        result.append(row)
        cursor = add_days(end, 1)
    return result
