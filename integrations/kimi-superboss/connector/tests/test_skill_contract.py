from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath
from typing import Final
from uuid import UUID

import pytest

INTEGRATION_ROOT = Path(__file__).resolve().parents[2]
SKILL_PATH = INTEGRATION_ROOT / "SKILL.md"

BEHAVIOR_RUBRIC: Final = {
    "finish_document_first": "Finish document work before offering synchronization.",
    "strict_manifest": "Produce the exact local envelope, K3 object, and attachment fields.",
    "stable_idempotency_key": "Generate one key and keep it stable for that submission.",
    "complete_preview": "Preview project, attachments, three result counts, risks, and key.",
    "fresh_confirmation": "Obtain explicit approval after the final preview and before submit.",
    "approved_cli_only": "Use only pair, submit, status, and retry through superboss.",
    "exit_3": "Pair again or inspect device revocation.",
    "exit_4": "Create a new manifest and idempotency key.",
    "exit_6": "Offer the exact superboss retry command.",
    "scanning_language": "Describe SCANNING as pending safety scan, not completion.",
    "secret_boundary": "Keep credentials out of chat, files, manifests, commands, and logs.",
}

ROOT_FIELDS = {
    "idempotency_key",
    "project_id",
    "local_task_id",
    "external_document_reference",
    "base_sha256",
    "k3_result",
    "attachments",
}
K3_FIELDS = {
    "model_label",
    "processed_at",
    "modification_details",
    "knowledge_points",
    "risks",
    "suggested_title",
    "suggested_tags",
}
ATTACHMENT_FIELDS = {"kind", "path", "content_type"}
EXPECTED_RUBRIC_KEYS = {
    "finish_document_first",
    "strict_manifest",
    "stable_idempotency_key",
    "complete_preview",
    "fresh_confirmation",
    "approved_cli_only",
    "exit_3",
    "exit_4",
    "exit_6",
    "scanning_language",
    "secret_boundary",
}


def _skill_text() -> str:
    if not SKILL_PATH.is_file():
        pytest.fail(
            "Task 11 Stage 3 RED: integrations/kimi-superboss/SKILL.md is missing",
            pytrace=False,
        )
    return SKILL_PATH.read_text(encoding="utf-8")


def _frontmatter(text: str) -> tuple[dict[str, str], str]:
    match = re.match(r"\A---\r?\n(?P<header>.*?)\r?\n---\r?\n", text, re.DOTALL)
    assert match is not None, "SKILL.md must begin with YAML frontmatter"
    metadata: dict[str, str] = {}
    for raw_line in match.group("header").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        assert ":" in line, "frontmatter entries must be key/value YAML scalars"
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        assert key and value, "frontmatter keys and values must be non-empty"
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        assert key not in metadata, "frontmatter keys must be unique"
        metadata[key] = value
    return metadata, text[match.end() :]


def _sections(body: str) -> tuple[list[str], dict[str, str]]:
    matches = list(re.finditer(r"(?m)^##\s+(.+?)\s*$", body))
    headings: list[str] = []
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        heading = match.group(1).strip().lower()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        headings.append(heading)
        sections[heading] = body[match.end() : end]
    return headings, sections


def _concept_section(
    headings: list[str],
    sections: dict[str, str],
    concept: str,
) -> tuple[int, str]:
    matches = [heading for heading in headings if concept in heading]
    assert len(matches) == 1, f"expected one level-two {concept!r} section"
    heading = matches[0]
    return headings.index(heading), sections[heading]


def _code_blocks(body: str) -> list[tuple[str, str]]:
    return [
        (match.group("language").lower(), match.group("content"))
        for match in re.finditer(
            r"```(?P<language>[A-Za-z0-9_-]*)\s*\r?\n(?P<content>.*?)```",
            body,
            re.DOTALL,
        )
    ]


def _manifest_example(body: str) -> dict[str, object]:
    candidates: list[dict[str, object]] = []
    for language, content in _code_blocks(body):
        if language != "json":
            continue
        try:
            document = json.loads(content)
        except json.JSONDecodeError:
            continue
        if isinstance(document, dict) and set(document) == ROOT_FIELDS:
            candidates.append(document)
    assert len(candidates) == 1, "the compact example must contain one exact local manifest JSON"
    return candidates[0]


def _command_lines(body: str) -> list[str]:
    commands: list[str] = []
    for _language, content in _code_blocks(body):
        for raw_line in content.splitlines():
            line = raw_line.strip()
            for prompt in ("$ ", "PS> ", "> "):
                if line.startswith(prompt):
                    line = line[len(prompt) :].strip()
            if line.lower().startswith("superboss "):
                commands.append(line)
    return commands


def test_forward_behavior_rubric_is_complete_and_binary() -> None:
    assert set(BEHAVIOR_RUBRIC) == EXPECTED_RUBRIC_KEYS
    assert all(description.endswith(".") for description in BEHAVIOR_RUBRIC.values())


def test_skill_has_canonical_minimal_frontmatter_and_bounded_length() -> None:
    text = _skill_text()
    metadata, body = _frontmatter(text)

    assert set(metadata) == {"name", "description"}
    assert re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", metadata["name"])
    assert metadata["name"] == SKILL_PATH.parent.name == "kimi-superboss"
    description = metadata["description"].lower()
    assert "kimi" in description and "superboss" in description
    assert any(term in description for term in ("sync", "submit", "result"))
    assert body.strip()
    assert len(text.splitlines()) < 500


def test_skill_contains_ordered_positive_workflow_and_one_compact_example() -> None:
    _metadata, body = _frontmatter(_skill_text())
    headings, sections = _sections(body)
    workflow_index, workflow = _concept_section(headings, sections, "workflow")
    manifest_index, _manifest = _concept_section(headings, sections, "manifest")
    preview_index, _preview = _concept_section(headings, sections, "preview")
    confirmation_index, _confirmation = _concept_section(headings, sections, "confirmation")
    example_index, example = _concept_section(headings, sections, "example")

    assert workflow_index < manifest_index < preview_index < confirmation_index < example_index
    steps = re.findall(r"(?m)^\d+\.\s+(.+)$", workflow)
    assert len(steps) >= 6
    assert re.search(r"\b(finish|complete)\b", steps[0], re.IGNORECASE)
    assert re.search(r"\b(document|work)\b", steps[0], re.IGNORECASE)
    preview_step = next(index for index, step in enumerate(steps) if "preview" in step.lower())
    confirmation_step = next(index for index, step in enumerate(steps) if "confirm" in step.lower())
    submit_step = next(index for index, step in enumerate(steps) if "submit" in step.lower())
    assert preview_step < confirmation_step < submit_step
    assert len(example.splitlines()) <= 100


def test_skill_manifest_example_has_exact_task10_k3_and_local_attachment_shape() -> None:
    _metadata, body = _frontmatter(_skill_text())
    manifest = _manifest_example(body)

    idempotency_key = manifest["idempotency_key"]
    assert isinstance(idempotency_key, str)
    key_match = re.fullmatch(r"kimi-(?P<uuid>[0-9a-fA-F-]{36})", idempotency_key)
    assert key_match is not None
    parsed_key = UUID(key_match.group("uuid"))
    assert parsed_key.version in {4, 7}
    k3_result = manifest["k3_result"]
    assert isinstance(k3_result, dict)
    assert set(k3_result) == K3_FIELDS
    attachments = manifest["attachments"]
    assert isinstance(attachments, list) and 1 <= len(attachments) <= 3
    kinds: list[str] = []
    for attachment in attachments:
        assert isinstance(attachment, dict)
        assert set(attachment) == ATTACHMENT_FIELDS
        kinds.append(str(attachment["kind"]))
        relative_path = str(attachment["path"])
        parsed_path = PurePosixPath(relative_path.replace("\\", "/"))
        assert relative_path and not parsed_path.is_absolute()
        assert ".." not in parsed_path.parts
        assert not re.match(r"^[A-Za-z]:", relative_path)
        assert not relative_path.startswith(("/", "\\"))
    assert len(kinds) == len(set(kinds))
    assert kinds.count("K3_RAW") == 1

    headings, sections = _sections(body)
    _index, manifest_guidance = _concept_section(headings, sections, "manifest")
    lowered = manifest_guidance.lower()
    assert "relative" in lowered and "path" in lowered
    assert "idempotency_key" in lowered
    assert re.search(r"\b(stable|unchanged|reuse)\b", lowered)
    assert re.search(r"\b(once|generated)\b", lowered)
    assert "uuid" in lowered
    assert re.search(r"\b(exit 4|new submission)\b", lowered)


def test_skill_relates_base_digest_to_original_attachment() -> None:
    _metadata, body = _frontmatter(_skill_text())
    manifest = _manifest_example(body)
    attachments = manifest["attachments"]
    assert isinstance(attachments, list)
    kinds = {str(attachment["kind"]) for attachment in attachments}
    assert manifest["base_sha256"] is None
    assert "ORIGINAL" not in kinds

    headings, sections = _sections(body)
    _index, guidance = _concept_section(headings, sections, "manifest")
    lowered = guidance.lower()
    assert "base_sha256" in lowered and "original" in lowered
    assert re.search(r"\b(not null|non-null)\b", lowered)
    assert re.search(r"\b(exactly one|one)\b.{0,50}\boriginal\b", lowered)
    assert re.search(
        r"\bnull\b.{0,50}(?:\boptional\b.{0,30}\boriginal\b|\boriginal\b.{0,30}\boptional\b)",
        lowered,
    )


def test_skill_preview_and_confirmation_are_complete_and_fresh() -> None:
    _metadata, body = _frontmatter(_skill_text())
    headings, sections = _sections(body)
    preview_index, preview = _concept_section(headings, sections, "preview")
    confirmation_index, confirmation = _concept_section(headings, sections, "confirmation")

    assert preview_index < confirmation_index
    lowered_preview = preview.lower()
    for concept in ("project", "attachment", "risk", "idempotency"):
        assert concept in lowered_preview
    assert re.search(r"modification.{0,30}count|count.{0,30}modification", lowered_preview)
    assert re.search(r"knowledge.{0,30}count|count.{0,30}knowledge", lowered_preview)
    assert re.search(r"risk.{0,30}count|count.{0,30}risk", lowered_preview)

    lowered_confirmation = confirmation.lower()
    for concept in ("explicit", "preview", "submit"):
        assert concept in lowered_confirmation
    assert re.search(r"\b(after|following)\b", lowered_confirmation)
    assert re.search(r"\b(immediately|directly)\b", lowered_confirmation)
    assert re.search(r"\b(silence|no response)\b", lowered_confirmation)
    assert re.search(r"\b(earlier|prior|previous)\b", lowered_confirmation)
    assert re.search(r"\b(does not|do not|is not|isn't)\b", lowered_confirmation)
    assert body.find("superboss submit") > body.lower().find("## confirmation")


def test_skill_executable_examples_use_only_the_exact_cli_surface() -> None:
    _metadata, body = _frontmatter(_skill_text())
    commands = _command_lines(body)
    assert commands, "the Skill must show the approved CLI surface"
    patterns = {
        "pair": re.compile(r'^superboss pair --server \S+ --code \S+ --name (?:"[^"]+"|\S+)$'),
        "submit": re.compile(r"^superboss submit --server \S+ --manifest \S+$"),
        "status": re.compile(r"^superboss status --server \S+ --job-id \S+$"),
        "retry": re.compile(r"^superboss retry --server \S+$"),
    }
    seen: set[str] = set()
    for command in commands:
        parts = command.split()
        assert len(parts) >= 2 and parts[0] == "superboss"
        name = parts[1]
        assert name in patterns and patterns[name].fullmatch(command)
        seen.add(name)
    assert seen == set(patterns)

    executable_blocks = "\n".join(content for _language, content in _code_blocks(body)).lower()
    forbidden = (
        "curl ",
        "invoke-webrequest",
        "requests.",
        "/api/v1/",
        "authorization:",
        "bearer ",
        "$env:",
        "setx ",
        "--insecure",
        "verify_tls",
    )
    assert all(term not in executable_blocks for term in forbidden)


def test_skill_status_recovery_and_evidence_contract_is_structured() -> None:
    _metadata, body = _frontmatter(_skill_text())
    headings, sections = _sections(body)
    _status_index, status = _concept_section(headings, sections, "status")
    _recovery_index, recovery = _concept_section(headings, sections, "recovery")

    lowered_status = status.lower()
    for concept in ("scanning", "pending", "safety", "job id", "status", "evidence"):
        assert concept in lowered_status
    assert re.search(r"\b(not|never|before)\b.{0,40}\b(complete|archive)", lowered_status)

    rows: dict[int, str] = {}
    for code in (2, 3, 4, 5, 6):
        match = re.search(rf"(?m)^\|\s*{code}\s*\|(?P<action>.+?)\|\s*$", recovery)
        assert match is not None, f"recovery table must contain exit {code}"
        rows[code] = match.group("action").lower()
    assert re.search(r"\b(correct|fix)\b", rows[2])
    assert any(term in rows[2] for term in ("input", "manifest", "state"))
    assert "pair" in rows[3] and any(term in rows[3] for term in ("revoke", "device"))
    assert all(term in rows[4] for term in ("new", "manifest", "key"))
    assert "uuid" in rows[4]
    assert any(term in rows[5] for term in ("resolve", "inspect", "rejected", "server"))
    assert "superboss retry" in rows[6]


def test_skill_keeps_secrets_out_of_every_transfer_surface() -> None:
    _metadata, body = _frontmatter(_skill_text())
    headings, sections = _sections(body)
    _index, security = _concept_section(headings, sections, "security")
    lowered = security.lower()
    for concept in ("credential", "manifest", "chat", "command", "file", "log"):
        assert concept in lowered
    assert "connector" in lowered and "evidence" in lowered
    for concept in ("access", "refresh", "pairing code", "owner", "terminal", "exception"):
        assert concept in lowered

    manifest = _manifest_example(body)
    serialized = json.dumps(manifest, ensure_ascii=False).lower()
    forbidden_fields = (
        "access_token",
        "refresh_token",
        "credential",
        "password",
        "authorization",
        "cookie",
        "presigned",
    )
    assert all(field not in serialized for field in forbidden_fields)
