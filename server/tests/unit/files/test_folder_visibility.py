"""Folder visibility is role-based, not project membership."""

from uuid import uuid4

from superboss.core.actors import Actor
from superboss.modules.files.models import FolderVisibility
from superboss.modules.files.service import folder_is_visible
from superboss.modules.users.models import Role


def test_owner_sees_every_visibility() -> None:
    actor = Actor(uuid4(), Role.OWNER)
    assert folder_is_visible(actor, FolderVisibility.ALL)
    assert folder_is_visible(actor, FolderVisibility.MANAGEMENT)
    assert folder_is_visible(actor, FolderVisibility.OWNER_ONLY)


def test_manager_sees_company_and_shared_not_owner_private() -> None:
    actor = Actor(uuid4(), Role.MANAGER)
    assert folder_is_visible(actor, FolderVisibility.ALL)
    assert folder_is_visible(actor, FolderVisibility.MANAGEMENT)
    assert not folder_is_visible(actor, FolderVisibility.OWNER_ONLY)


def test_staff_sees_only_all_visibility() -> None:
    actor = Actor(uuid4(), Role.STAFF)
    assert folder_is_visible(actor, FolderVisibility.ALL)
    assert not folder_is_visible(actor, FolderVisibility.MANAGEMENT)
    assert not folder_is_visible(actor, FolderVisibility.OWNER_ONLY)
