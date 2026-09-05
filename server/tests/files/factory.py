from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from superboss.modules.files.models import File, FileState, Folder, FolderVisibility


async def add_folder(
    session: AsyncSession,
    owner_id: UUID,
    *,
    name: str = "项目",
    visibility: FolderVisibility = FolderVisibility.ALL,
) -> Folder:
    folder = Folder(name=name, visibility=visibility, created_by=owner_id, parent_id=None)
    session.add(folder)
    await session.flush()
    return folder


def make_file(*, folder_id: UUID, uploader_id: UUID, **overrides: object) -> File:
    values: dict[str, object] = {
        "folder_id": folder_id,
        "filename": "x.pdf",
        "object_key": f"folders/{folder_id}/{uuid4()}/x.pdf",
        "size_bytes": 1,
        "sha256": "0" * 64,
        "state": FileState.CLEAN,
        "uploader_id": uploader_id,
        "content_type": "application/pdf",
    }
    values.update(overrides)
    return File(**values)
