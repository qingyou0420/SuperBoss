"""Production compose publishes only local HTTPS and pins external images."""

import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PATH = ROOT / "docker-compose.yml"
DIGEST = re.compile(r"^[^:@\s]+(?:/[^:@\s]+)*@sha256:[0-9a-f]{64}$")


def _compose() -> dict[str, Any]:
    document = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def test_only_localhost_https_is_published() -> None:
    services = _compose()["services"]
    published: list[str] = []
    for service in services.values():
        published.extend(str(item) for item in service.get("ports", []))
    assert published == ["127.0.0.1:443:443"]


def test_external_images_are_digest_pinned() -> None:
    for service in _compose()["services"].values():
        image = service.get("image")
        if not image or str(image).startswith("superboss-"):
            continue
        assert DIGEST.match(str(image).split()[0])
