"""Strict request contracts for minimal K3 result packages."""

import importlib
import json
from copy import deepcopy
from datetime import UTC, datetime
from types import ModuleType
from uuid import uuid4

import pytest
from pydantic import ValidationError


def schemas_contract() -> ModuleType:
    """Load the wished-for schema API lazily so RED identifies the absent feature."""
    try:
        return importlib.import_module("superboss.modules.imports.schemas")
    except ModuleNotFoundError as error:
        pytest.fail(f"Task 10 imports schemas are not implemented ({error.name})")


def valid_payload() -> dict[str, object]:
    return {
        "project_id": str(uuid4()),
        "local_task_id": "kimi-local-001",
        "external_document_reference": "external-doc-一号",
        "base_sha256": "a" * 64,
        "k3_result": {
            "model_label": "K3 工作台",
            "processed_at": "2026-08-09T12:34:56+08:00",
            "modification_details": ["第一处修改\n保留文档换行"],
            "knowledge_points": ["知识点一"],
            "risks": ["待 OWNER 确认"],
            "suggested_title": "建议标题",
            "suggested_tags": ["合同", "复核"],
        },
        "attachments": [
            {
                "kind": "ORIGINAL",
                "filename": "原稿.pdf",
                "size_bytes": 1,
                "sha256": "a" * 64,
                "content_type": "application/pdf",
            },
            {
                "kind": "K3_RAW",
                "filename": "k3-result.json",
                "size_bytes": 128,
                "sha256": "b" * 64,
                "content_type": "application/json",
            },
        ],
    }


def test_manifest_accepts_unicode_document_newlines_and_exposes_only_exact_fields() -> None:
    """Dropping exact fields or silently accepting future fields would drift the connector contract."""
    schemas = schemas_contract()
    manifest = schemas.ImportJobCreate.model_validate(valid_payload())

    assert set(schemas.K3Result.model_fields) == {
        "model_label",
        "processed_at",
        "modification_details",
        "knowledge_points",
        "risks",
        "suggested_title",
        "suggested_tags",
    }
    assert set(schemas.ImportJobCreate.model_fields) == {
        "project_id",
        "local_task_id",
        "external_document_reference",
        "base_sha256",
        "k3_result",
        "attachments",
    }
    assert set(schemas.AttachmentDeclaration.model_fields) == {
        "kind",
        "filename",
        "size_bytes",
        "sha256",
        "content_type",
    }
    assert {kind.value for kind in schemas.AttachmentKind} == {
        "ORIGINAL",
        "REVISED",
        "K3_RAW",
    }
    assert manifest.k3_result.modification_details == ["第一处修改\n保留文档换行"]
    assert manifest.k3_result.processed_at.utcoffset() is not None


@pytest.mark.parametrize("location", ["job", "k3_result", "attachment"])
def test_every_manifest_model_forbids_extra_fields(location: str) -> None:
    """Ignoring unknown input would make idempotency fingerprints omit client semantics."""
    schemas = schemas_contract()
    payload = valid_payload()
    if location == "job":
        payload["unexpected"] = "value"
    elif location == "k3_result":
        assert isinstance(payload["k3_result"], dict)
        payload["k3_result"]["unexpected"] = "value"
    else:
        assert isinstance(payload["attachments"], list)
        payload["attachments"][0]["unexpected"] = "value"

    with pytest.raises(ValidationError):
        schemas.ImportJobCreate.model_validate(payload)


@pytest.mark.parametrize(
    ("target", "value"),
    [
        ("model_label", "K3\x00hidden"),
        ("local_task_id", "task\x01hidden"),
        ("external_document_reference", "doc\x7fhidden"),
        ("modification_detail", "line\x0bhidden"),
        ("suggested_tag", "tag\x00hidden"),
        ("filename", "safe\rname.pdf"),
    ],
)
def test_manifest_rejects_nul_and_unsafe_control_text(target: str, value: str) -> None:
    """Control characters must not reach storage keys, logs, headers, or canonical JSON."""
    schemas = schemas_contract()
    payload = valid_payload()
    k3_result = payload["k3_result"]
    attachments = payload["attachments"]
    assert isinstance(k3_result, dict) and isinstance(attachments, list)
    if target == "model_label":
        k3_result["model_label"] = value
    elif target == "local_task_id":
        payload["local_task_id"] = value
    elif target == "external_document_reference":
        payload["external_document_reference"] = value
    elif target == "modification_detail":
        k3_result["modification_details"] = [value]
    elif target == "suggested_tag":
        k3_result["suggested_tags"] = [value]
    else:
        attachments[0]["filename"] = value

    with pytest.raises(ValidationError):
        schemas.ImportJobCreate.model_validate(payload)


@pytest.mark.parametrize(
    "target",
    [
        "model_label",
        "local_task_id",
        "external_document_reference",
        "modification_detail",
        "suggested_title",
        "suggested_tag",
        "filename",
    ],
)
def test_every_text_surface_has_a_finite_upper_bound(target: str) -> None:
    """An attacker-controlled manifest must not create unbounded rows or responses."""
    schemas = schemas_contract()
    payload = valid_payload()
    k3_result = payload["k3_result"]
    attachments = payload["attachments"]
    assert isinstance(k3_result, dict) and isinstance(attachments, list)
    oversized = "界" * 100_000
    if target == "model_label":
        k3_result["model_label"] = oversized
    elif target == "local_task_id":
        payload["local_task_id"] = oversized
    elif target == "external_document_reference":
        payload["external_document_reference"] = oversized
    elif target == "modification_detail":
        k3_result["modification_details"] = [oversized]
    elif target == "suggested_title":
        k3_result["suggested_title"] = oversized
    elif target == "suggested_tag":
        k3_result["suggested_tags"] = [oversized]
    else:
        attachments[0]["filename"] = oversized

    with pytest.raises(ValidationError):
        schemas.ImportJobCreate.model_validate(payload)


@pytest.mark.parametrize(
    "target",
    [
        "model_label",
        "local_task_id",
        "external_document_reference",
        "modification_detail",
        "knowledge_point",
        "risk",
        "suggested_title",
        "suggested_tag",
        "filename",
    ],
)
def test_present_text_values_must_not_be_blank(target: str) -> None:
    """Whitespace-only semantics would create unverifiable but apparently valid packages."""
    schemas = schemas_contract()
    payload = valid_payload()
    k3_result = payload["k3_result"]
    attachments = payload["attachments"]
    assert isinstance(k3_result, dict) and isinstance(attachments, list)
    if target == "model_label":
        k3_result["model_label"] = " \t "
    elif target == "local_task_id":
        payload["local_task_id"] = " \t "
    elif target == "external_document_reference":
        payload["external_document_reference"] = " \t "
    elif target == "modification_detail":
        k3_result["modification_details"] = [" \n "]
    elif target == "knowledge_point":
        k3_result["knowledge_points"] = [" \n "]
    elif target == "risk":
        k3_result["risks"] = [" \n "]
    elif target == "suggested_title":
        k3_result["suggested_title"] = " \t "
    elif target == "suggested_tag":
        k3_result["suggested_tags"] = [" \t "]
    else:
        attachments[0]["filename"] = " \t "

    with pytest.raises(ValidationError):
        schemas.ImportJobCreate.model_validate(payload)


def test_k3_lists_are_bounded_and_suggested_tags_are_unique() -> None:
    """Unbounded or duplicate K3 collections would defeat canonical manifest bounds."""
    schemas = schemas_contract()
    duplicate_tags = valid_payload()
    k3_result = duplicate_tags["k3_result"]
    assert isinstance(k3_result, dict)
    k3_result["suggested_tags"] = ["合同", "合同"]
    with pytest.raises(ValidationError):
        schemas.ImportJobCreate.model_validate(duplicate_tags)

    for field in ("modification_details", "knowledge_points", "risks", "suggested_tags"):
        payload = valid_payload()
        k3_result = payload["k3_result"]
        assert isinstance(k3_result, dict)
        k3_result[field] = [f"item-{index}" for index in range(10_000)]
        with pytest.raises(ValidationError):
            schemas.ImportJobCreate.model_validate(payload)


@pytest.mark.parametrize(
    "processed_at",
    ["2026-08-09T12:34:56", datetime(2026, 8, 9, 12, 34, 56)],  # noqa: DTZ001
)
def test_processed_at_requires_timezone_awareness(processed_at: object) -> None:
    """Persisting a naive Kimi timestamp would make UTC ordering ambiguous."""
    schemas = schemas_contract()
    payload = valid_payload()
    k3_result = payload["k3_result"]
    assert isinstance(k3_result, dict)
    k3_result["processed_at"] = processed_at

    with pytest.raises(ValidationError):
        schemas.ImportJobCreate.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("base_sha256", "A" * 64),
        ("base_sha256", "a" * 63),
        ("attachment_sha256", "g" * 64),
        ("attachment_sha256", "b" * 65),
    ],
)
def test_hashes_are_exact_lowercase_sha256(field: str, value: str) -> None:
    """Accepting ambiguous digest syntax would split semantic idempotency."""
    schemas = schemas_contract()
    payload = valid_payload()
    attachments = payload["attachments"]
    assert isinstance(attachments, list)
    if field == "base_sha256":
        payload[field] = value
    else:
        attachments[0]["sha256"] = value

    with pytest.raises(ValidationError):
        schemas.ImportJobCreate.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("size_bytes", 0),
        ("size_bytes", 100 * 1024 * 1024 + 1),
        ("content_type", "not a mime"),
        ("content_type", "text/plain\r\nX-Injected: yes"),
        ("content_type", "a" * 256),
    ],
)
def test_attachment_limits_match_the_existing_upload_contract(field: str, value: object) -> None:
    """Import declarations must fail before creating Task 7 storage state."""
    schemas = schemas_contract()
    payload = valid_payload()
    attachments = payload["attachments"]
    assert isinstance(attachments, list)
    attachments[0][field] = value

    with pytest.raises(ValidationError):
        schemas.ImportJobCreate.model_validate(payload)


@pytest.mark.parametrize(
    "case",
    [
        "empty",
        "too_many",
        "missing_raw",
        "duplicate_raw",
        "duplicate_original",
        "base_without_original",
        "unknown_kind",
    ],
)
def test_attachment_shape_requires_one_to_three_unique_kinds_and_exactly_one_raw(
    case: str,
) -> None:
    """Ambiguous attachment roles would make base verification and replay unsafe."""
    schemas = schemas_contract()
    payload = valid_payload()
    attachments = payload["attachments"]
    assert isinstance(attachments, list)
    raw = deepcopy(attachments[1])
    original = deepcopy(attachments[0])
    revised = deepcopy(original)
    revised.update(
        kind="REVISED",
        filename="revised.pdf",
        sha256="c" * 64,
    )
    if case == "empty":
        payload["attachments"] = []
    elif case == "too_many":
        payload["attachments"] = [original, revised, raw, deepcopy(raw)]
    elif case == "missing_raw":
        payload["attachments"] = [original, revised]
    elif case == "duplicate_raw":
        payload["attachments"] = [raw, deepcopy(raw)]
    elif case == "duplicate_original":
        payload["attachments"] = [original, deepcopy(original), raw]
    elif case == "base_without_original":
        payload["attachments"] = [revised, raw]
    else:
        raw["kind"] = "MODEL_OUTPUT"
        payload["attachments"] = [original, raw]

    with pytest.raises(ValidationError):
        schemas.ImportJobCreate.model_validate(payload)


def test_original_is_optional_only_when_no_base_sha256_is_declared() -> None:
    """A package without a base may contain only K3 raw or K3 raw plus revised content."""
    schemas = schemas_contract()
    payload = valid_payload()
    payload["base_sha256"] = None
    attachments = payload["attachments"]
    assert isinstance(attachments, list)
    payload["attachments"] = [attachments[1]]

    manifest = schemas.ImportJobCreate.model_validate(payload)

    assert [attachment.kind.value for attachment in manifest.attachments] == ["K3_RAW"]
    assert manifest.k3_result.processed_at.astimezone(UTC).isoformat() == (
        "2026-08-09T04:34:56+00:00"
    )


def test_manifest_size_guard_accounts_for_postgresql_jsonb_text_spacing() -> None:
    """A compact-fit manifest must not reach a stricter JSONB text constraint after uploads."""
    schemas = schemas_contract()
    payload = valid_payload()
    k3_result = payload["k3_result"]
    assert isinstance(k3_result, dict)
    maximum_item = "\u754c" * schemas.K3_TEXT_MAX_CHARS
    k3_result["modification_details"] = [maximum_item for _ in range(5)]

    compact_without_filler = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    filler_length = schemas.MANIFEST_MAX_UTF8_BYTES - len(compact_without_filler) - 3
    assert 1 <= filler_length <= schemas.K3_TEXT_MAX_CHARS
    k3_result["modification_details"].append("x" * filler_length)
    compact = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    postgres_style = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(", ", ": "),
    ).encode("utf-8")
    assert len(compact) == schemas.MANIFEST_MAX_UTF8_BYTES
    assert len(postgres_style) > schemas.MANIFEST_MAX_UTF8_BYTES

    with pytest.raises(ValidationError):
        schemas.ImportJobCreate.model_validate(payload)
