"""Project authorization policy tests."""

from uuid import UUID, uuid4

import pytest

from superboss.core.actors import Actor, require_owner, require_project_access
from superboss.core.errors import ForbiddenError
from superboss.modules.projects.schemas import ProjectCreate
from superboss.modules.projects.service import ProjectService
from superboss.modules.users.models import Role


@pytest.fixture
def assigned_project_id() -> UUID:
    return uuid4()


@pytest.fixture
def staff_actor(assigned_project_id: UUID) -> Actor:
    return Actor(uuid4(), Role.STAFF, frozenset({assigned_project_id}))


def test_staff_cannot_use_owner_policy(staff_actor: Actor) -> None:
    """Changing the role check must not allow STAFF project creation."""
    with pytest.raises(ForbiddenError):
        require_owner(staff_actor)


def test_manager_cannot_use_owner_policy() -> None:
    with pytest.raises(ForbiddenError):
        require_owner(Actor(uuid4(), Role.MANAGER))


def test_owner_and_manager_can_access_any_project(assigned_project_id: UUID) -> None:
    foreign = uuid4()
    require_project_access(Actor(uuid4(), Role.OWNER), foreign)
    require_project_access(Actor(uuid4(), Role.MANAGER), foreign)
    require_project_access(
        Actor(uuid4(), Role.STAFF, frozenset({assigned_project_id})),
        assigned_project_id,
    )
    with pytest.raises(ForbiddenError):
        require_project_access(
            Actor(uuid4(), Role.STAFF, frozenset({assigned_project_id})),
            foreign,
        )


def test_missing_role_cannot_access_assigned_project(assigned_project_id: UUID) -> None:
    actor = Actor(uuid4(), None, frozenset({assigned_project_id}))
    with pytest.raises(ForbiddenError):
        require_project_access(actor, assigned_project_id)


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
