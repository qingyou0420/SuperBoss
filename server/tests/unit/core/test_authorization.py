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
    return Actor(
        kind="user",
        subject_id=uuid4(),
        role=Role.STAFF,
        project_ids=frozenset({assigned_project_id}),
        scopes=frozenset(),
    )


def test_staff_cannot_use_owner_policy(staff_actor: Actor) -> None:
    """Changing the role check must not allow STAFF project creation."""
    with pytest.raises(ForbiddenError):
        require_owner(staff_actor)


def test_staff_can_only_access_assigned_projects(
    staff_actor: Actor, assigned_project_id: UUID
) -> None:
    """Dropping membership enforcement would expose an unassigned project."""
    require_project_access(staff_actor, assigned_project_id)
    with pytest.raises(ForbiddenError):
        require_project_access(staff_actor, uuid4())


def test_owner_can_access_any_project_without_materialized_membership() -> None:
    """Replacing role access with memberships would deny an OWNER's project."""
    require_project_access(
        Actor("user", uuid4(), Role.OWNER, frozenset(), frozenset()), uuid4()
    )


@pytest.mark.parametrize(
    ("kind", "role"),
    [
        ("user", None),
        ("device", Role.STAFF),
        ("device", None),
        ("system", None),
        ("device", Role.OWNER),
        ("system", Role.OWNER),
        ("system", Role.STAFF),
    ],
)
def test_only_user_staff_membership_can_access_assigned_project(
    assigned_project_id: UUID, kind: str, role: Role | None
) -> None:
    """Removing the complete actor-shape check grants synthetic actors project access."""
    actor = Actor(kind, uuid4(), role, frozenset({assigned_project_id}), frozenset())  # type: ignore[arg-type]
    with pytest.raises(ForbiddenError):
        require_project_access(actor, assigned_project_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "role"),
    [
        ("user", None),
        ("device", None),
        ("device", Role.OWNER),
        ("device", Role.STAFF),
        ("system", None),
        ("system", Role.OWNER),
        ("system", Role.STAFF),
    ],
)
async def test_invalid_actor_cannot_reach_project_queries(
    kind: str, role: Role | None
) -> None:
    """Dropping service-boundary checks lets device/system IDs query user projects."""
    actor = Actor(kind, uuid4(), role, frozenset(), frozenset())  # type: ignore[arg-type]
    service = ProjectService(None)  # type: ignore[arg-type]
    with pytest.raises(ForbiddenError):
        await service.list(actor)
    with pytest.raises(ForbiddenError):
        await service.get(actor, uuid4())
    with pytest.raises(ForbiddenError):
        await service.create(actor, ProjectCreate(name="Denied"))
