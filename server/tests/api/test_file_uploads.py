"""File upload request validation behavior."""

from pydantic import ValidationError
import pytest


def test_upload_rejects_size_larger_than_100_mib() -> None:
    """Removing the upper bound would accept an oversized direct upload."""
    from superboss.modules.files.schemas import UploadStart

    with pytest.raises(ValidationError):
        UploadStart(project_id="00000000-0000-0000-0000-000000000001", filename="x.pdf", size_bytes=100 * 1024 * 1024 + 1, sha256="0" * 64, category="资料", file_date="2026-08-09")


def test_upload_rejects_uppercase_sha256() -> None:
    """Relaxing the digest contract would accept non-canonical checksums."""
    from superboss.modules.files.schemas import UploadStart

    with pytest.raises(ValidationError):
        UploadStart(project_id="00000000-0000-0000-0000-000000000001", filename="x.pdf", size_bytes=1, sha256="A" * 64, category="资料", file_date="2026-08-09")
