"""File upload state-machine behavior."""

from uuid import uuid4

import pytest


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("../secret.pdf", "secret.pdf"), ("..\\secret.pdf", "secret.pdf"), ("\x00", "file")],
)
def test_object_key_segment_never_preserves_path_or_control(raw: str, expected: str) -> None:
    """A raw filename must never become a key path component."""
    from superboss.modules.files.service import FileService

    assert FileService._segment(raw, "file") == expected


@pytest.mark.asyncio
async def test_download_requires_clean_state() -> None:
    """Changing the state gate would expose quarantined material."""
    from superboss.modules.files.models import File, FileState
    from superboss.modules.files.service import FileNotReadyError, FileService

    service = FileService(None, None)
    file = File(id=uuid4(), project_id=uuid4(), filename="report.pdf", category="资料", object_key="x", size_bytes=1, sha256="0" * 64, state=FileState.QUARANTINED, uploader_id=uuid4())
    with pytest.raises(FileNotReadyError):
        await service.ensure_downloadable(file)
