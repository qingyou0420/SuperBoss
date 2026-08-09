"""File upload request validation behavior."""

import pytest
from pydantic import ValidationError


def test_upload_rejects_size_larger_than_100_mib() -> None:
    """Removing the upper bound would accept an oversized direct upload."""
    from superboss.modules.files.schemas import UploadStart

    with pytest.raises(ValidationError):
        UploadStart(
            project_id="00000000-0000-0000-0000-000000000001",
            filename="x.pdf",
            size_bytes=100 * 1024 * 1024 + 1,
            sha256="0" * 64,
            category="资料",
            file_date="2026-08-09",
        )


def test_upload_rejects_uppercase_sha256() -> None:
    """Relaxing the digest contract would accept non-canonical checksums."""
    from superboss.modules.files.schemas import UploadStart

    with pytest.raises(ValidationError):
        UploadStart(
            project_id="00000000-0000-0000-0000-000000000001",
            filename="x.pdf",
            size_bytes=1,
            sha256="A" * 64,
            category="资料",
            file_date="2026-08-09",
        )


@pytest.mark.parametrize("value", [" ", "a\r\nb", "\x00name"])
def test_upload_rejects_control_or_blank_file_metadata(value: str) -> None:
    """Dropping text hygiene would permit headers/keys with control characters."""
    from superboss.modules.files.schemas import UploadStart

    with pytest.raises(ValidationError):
        UploadStart(
            project_id="00000000-0000-0000-0000-000000000001",
            filename=value,
            size_bytes=1,
            sha256="0" * 64,
            category="资料",
            file_date="2026-08-09",
        )


@pytest.mark.parametrize("part", [0, 10_001])
def test_completed_part_rejects_s3_outside_range(part: int) -> None:
    from superboss.modules.files.schemas import PartComplete
    with pytest.raises(ValidationError): PartComplete(part_number=part, etag="etag")


@pytest.mark.parametrize("etag", [" ", "x\r\ny", "x\x00y"])
def test_completed_part_rejects_unsafe_etag(etag: str) -> None:
    from superboss.modules.files.schemas import PartComplete
    with pytest.raises(ValidationError): PartComplete(part_number=1, etag=etag)
