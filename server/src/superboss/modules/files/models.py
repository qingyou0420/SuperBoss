"""File upload persistence state."""
from datetime import date, datetime
from enum import StrEnum
from uuid import UUID, uuid4
from sqlalchemy import Date, DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from superboss.core.db import Base

class FileState(StrEnum):
    UPLOADING="UPLOADING"; QUARANTINED="QUARANTINED"; SCANNING="SCANNING"; CLEAN="CLEAN"; INFECTED="INFECTED"; FAILED="FAILED"

class File(Base):
    __tablename__="files"
    id: Mapped[UUID]=mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    project_id: Mapped[UUID]=mapped_column(PG_UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    filename: Mapped[str]=mapped_column(String(1024), nullable=False)
    category: Mapped[str]=mapped_column(String(255), nullable=False)
    file_date: Mapped[date]=mapped_column(Date, nullable=False)
    object_key: Mapped[str]=mapped_column(String(2048), unique=True, nullable=False)
    size_bytes: Mapped[int]=mapped_column(Integer, nullable=False)
    sha256: Mapped[str]=mapped_column(String(64), nullable=False)
    state: Mapped[FileState]=mapped_column(Enum(FileState, name="file_state", native_enum=False), default=FileState.UPLOADING, nullable=False)
    uploader_id: Mapped[UUID]=mapped_column(PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    scan_result: Mapped[str|None]=mapped_column(Text, nullable=True)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

class Upload(Base):
    __tablename__="uploads"; __table_args__=(UniqueConstraint("project_id","uploader_id","idempotency_key",name="uq_upload_idempotency"),)
    id: Mapped[UUID]=mapped_column(PG_UUID(as_uuid=True),primary_key=True,default=uuid4)
    file_id: Mapped[UUID]=mapped_column(PG_UUID(as_uuid=True),ForeignKey("files.id",ondelete="CASCADE"),unique=True,nullable=False)
    project_id: Mapped[UUID]=mapped_column(PG_UUID(as_uuid=True),nullable=False)
    uploader_id: Mapped[UUID]=mapped_column(PG_UUID(as_uuid=True),nullable=False)
    idempotency_key: Mapped[str]=mapped_column(String(255),nullable=False)
    multipart_id: Mapped[str]=mapped_column(String(1024),nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now(),nullable=False)
