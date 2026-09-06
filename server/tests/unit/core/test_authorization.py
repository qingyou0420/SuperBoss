"""Project authorization policy tests."""

from uuid import uuid4

import pytest

from superboss.core.actors import Actor, require_owner
from superboss.core.errors import ForbiddenError
from superboss.modules.projects.schemas import ProjectCreate
from superboss.modules.projects.service import ProjectService
from superboss.modules.users.models import Role


@pytest.fixture
def staff_actor() -> Actor:
    return Actor(uuid4(), Role.STAFF)


def test_staff_cannot_use_owner_policy(staff_actor: Actor) -> None:
    """Changing the role check must not allow STAFF project creation."""
    with pytest.raises(ForbiddenError):
        require_owner(staff_actor)


def test_manager_cannot_use_owner_policy() -> None:
    with pytest.raises(ForbiddenError):
        require_owner(Actor(uuid4(), Role.MANAGER))


@pytest.mark.asyncio
async def test_missing_role_cannot_reach_project_queries() -> None:
    actor = Actor(uuid4(), None)
    service = ProjectService(None)  # type: ignore[arg-type]
    with pytest.raises(ForbiddenError):
        await service.list(actor)
    with pytest.raises(ForbiddenError):
        await service.get(actor, uuid4())
    with pytest.raises(ForbiddenError):
        await service.create(actor, ProjectCreate(name="Denied"))
