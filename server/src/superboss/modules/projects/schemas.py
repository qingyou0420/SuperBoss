"""Project HTTP schemas."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from superboss.modules.projects.models import ProjectStatus


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    is_test: bool = False


class ProjectRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    is_test: bool
    status: ProjectStatus
