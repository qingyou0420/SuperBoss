"""Project HTTP schemas."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, StrictBool, field_validator

from superboss.modules.projects.models import ProjectStatus


class ProjectCreate(BaseModel):
    name: str
    is_test: StrictBool = False

    @field_validator("name")
    @classmethod
    def canonical_name(cls, value: str) -> str:
        normalized = value.strip()
        if not 1 <= len(normalized) <= 255:
            raise ValueError("Project name must contain 1 to 255 non-whitespace characters")
        return normalized


class ProjectRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    is_test: bool
    status: ProjectStatus
