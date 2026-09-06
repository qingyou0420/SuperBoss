"""Live three-round 霜月 acceptance against a running API.

Only use against a disposable database. Confirming cards writes real finance
entries, milestones, and possibly a 星野合作 project. Pass --dry-run to send
messages without confirming cards.

Requires E2E_OWNER_USERNAME / E2E_OWNER_PASSWORD (or SUPERBOSS_OWNER_*).
Does not print the password. Writes a JSON report to stdout.
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any

import httpx

TURNS = (
    "列一下项目",
    "给星野加个 10 月 20 日交付节点",
    "这个月房租 8000",
)
RECALL = "昨天那个合作项目"
OFFLINE = "霜月暂时离线"


def _required(name: str, *aliases: str) -> str:
    for key in (name, *aliases):
        value = os.environ.get(key, "")
        if value:
            return value
    raise SystemExit(f"{name} is required")


def _client() -> httpx.Client:
    base = os.environ.get("SUPERBOSS_API_URL", "http://127.0.0.1:8000")
    insecure = os.environ.get("E2E_ALLOW_LOCAL_SELF_SIGNED", "").lower() == "true"
    return httpx.Client(
        base_url=base,
        timeout=180.0,
        follow_redirects=True,
        verify=not insecure,
        trust_env=False,
    )


def _login(client: httpx.Client, username: str, password: str) -> None:
    csrf = client.get("/api/v1/auth/csrf")
    csrf.raise_for_status()
    token = client.cookies.get("XSRF-TOKEN")
    if not token:
        raise SystemExit("login did not receive XSRF-TOKEN")
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
        headers={"X-CSRF-Token": token, "Origin": str(client.base_url)},
    )
    if response.status_code != 204:
        detail = response.text[:200].replace("\n", " ")
        raise SystemExit(f"login failed: {response.status_code} {detail}")


def _csrf(client: httpx.Client) -> dict[str, str]:
    token = client.cookies.get("XSRF-TOKEN")
    if not token:
        raise SystemExit("missing XSRF-TOKEN")
    return {"X-CSRF-Token": token}


def _create_conversation(client: httpx.Client) -> str:
    response = client.post(
        "/api/v1/agent/conversations",
        json={"title": "三轮验收"},
        headers=_csrf(client),
    )
    response.raise_for_status()
    return str(response.json()["id"])


def _send(client: httpx.Client, conversation_id: str, content: str) -> dict[str, Any]:
    response = client.post(
        f"/api/v1/agent/conversations/{conversation_id}/messages",
        json={"content": content},
        headers=_csrf(client),
    )
    response.raise_for_status()
    return response.json()


def _confirm_proposed(client: httpx.Client, cards: list[dict[str, Any]]) -> list[str]:
    committed: list[str] = []
    for card in cards:
        if card.get("status") != "PROPOSED":
            continue
        response = client.post(
            f"/api/v1/agent/cards/{card['id']}/confirm",
            headers=_csrf(client),
        )
        if response.status_code == 200:
            committed.append(str(response.json().get("kind")))
    return committed


def _wait_memories(client: httpx.Client, timeout: float = 90.0) -> list[dict[str, Any]]:
    deadline = time.monotonic() + timeout
    latest: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        response = client.get("/api/v1/agent/memories")
        if response.status_code == 200:
            latest = list(response.json())
            if latest:
                return latest
        time.sleep(3)
    return latest


def main() -> int:
    username = _required("E2E_OWNER_USERNAME", "SUPERBOSS_OWNER_USERNAME")
    password = _required("E2E_OWNER_PASSWORD", "SUPERBOSS_OWNER_PASSWORD")
    dry_run = "--dry-run" in sys.argv
    report: dict[str, Any] = {
        "turns": [],
        "recall": None,
        "memories": [],
        "dry_run": dry_run,
    }
    failed = False

    with _client() as client:
        _login(client, username, password)
        listed = client.get("/api/v1/projects")
        listed.raise_for_status()
        names = {str(item.get("name") or "") for item in listed.json()}
        if not any("星野" in name for name in names):
            created = client.post(
                "/api/v1/projects",
                json={"name": "星野合作"},
                headers=_csrf(client),
            )
            created.raise_for_status()
        conversation_id = _create_conversation(client)
        report["conversation_id"] = conversation_id
        for prompt in TURNS:
            turn = _send(client, conversation_id, prompt)
            message = turn.get("message") or {}
            content = str(message.get("content") or "")
            offline = bool(turn.get("offline"))
            cards = list(turn.get("cards") or [])
            committed = [] if dry_run else _confirm_proposed(client, cards)
            entry = {
                "prompt": prompt,
                "offline": offline,
                "card_kinds": [card.get("kind") for card in cards],
                "committed": committed,
                "content_preview": content[:240],
            }
            report["turns"].append(entry)
            if offline or OFFLINE in content:
                failed = True

        time.sleep(15)
        recall_id = _create_conversation(client)
        report["recall_conversation_id"] = recall_id
        memories = _wait_memories(client, timeout=120.0)
        report["memories"] = [item.get("content") for item in memories[:12]]
        recall = _send(client, recall_id, RECALL)
        recall_content = str((recall.get("message") or {}).get("content") or "")
        report["recall"] = {
            "offline": bool(recall.get("offline")),
            "content_preview": recall_content[:400],
            "mentions_xingye": "星野" in recall_content,
        }
        if recall.get("offline") or OFFLINE in recall_content:
            failed = True
        if "星野" not in recall_content and not any("星野" in str(item) for item in report["memories"]):
            failed = True

    report["ok"] = not failed
    json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
