"""OWNER-managed STAFF whitelist policy and transactional mutations."""

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from superboss.core.actors import Actor
from superboss.core.errors import DomainError, NotFoundError, OwnerRequiredError
from superboss.core.security import utcnow
from superboss.modules.audit.models import AuditLog
from superboss.modules.audit.schemas import AuditEventInput
from superboss.modules.audit.service import AuditService
from superboss.modules.auth.passwords import hash_password, new_temporary_password
from superboss.modules.projects.models import Project
from superboss.modules.users.models import Role, User, UserStatus
from superboss.modules.users.repository import UserRepository
from superboss.modules.users.schemas import ProjectAssignments, StaffCreate, StaffUpdate


class OwnerProtectedError(DomainError):
    def __init__(self) -> None:
        super().__init__("OWNER_PROTECTED", "The OWNER account is protected", 409)


class DuplicateUserError(DomainError):
    def __init__(self) -> None:
        super().__init__("USERNAME_CONFLICT", "Username already exists", 409)


class UserNotFoundError(DomainError):
    def __init__(self) -> None:
        super().__init__("USER_NOT_FOUND", "User not found", 404)


class UnknownProjectError(NotFoundError):
    """Keep project existence failures on the frozen generic 404 contract."""


@dataclass(frozen=True)
class OwnerUserView:
    id: UUID
    username: str
    display_name: str
    role: Role
    status: UserStatus
    last_login_at: datetime | None
    projects: tuple[Project, ...]


@dataclass(frozen=True)
class StaffCredentialResult:
    user: OwnerUserView
    temporary_password: str = field(repr=False)


@dataclass(frozen=True)
class PasswordResetResult:
    temporary_password: str = field(repr=False)


class OwnerUserService:
    def __init__(self, repository: UserRepository, audit_service: AuditService | None) -> None:
        self.repository = repository
        self.audit_service = audit_service

    async def _record(
        self, actor: Actor, action: str, outcome: str, request_id: UUID, user_id: UUID | None = None, *, reason: str | None = None
    ) -> None:
        if self.audit_service is None:
            return
        metadata: dict[str, object] = {} if reason is None else {"reason": reason}
        await self.audit_service.record(
            AuditEventInput(
                actor=actor, action=action, object_type="user", object_id=user_id,
                outcome=outcome, request_id=request_id, metadata=metadata,
            )
        )

    async def _require_owner(self, actor: Actor, action: str, request_id: UUID, user_id: UUID | None = None) -> None:
        if actor.kind == "user" and actor.role == Role.OWNER:
            return
        await self._record(actor, action, "DENIED", request_id, user_id, reason="OWNER_REQUIRED")
        raise OwnerRequiredError()

    async def _staff_for_update(self, actor: Actor, action: str, user_id: UUID, request_id: UUID) -> User:
        await self._require_owner(actor, action, request_id, user_id)
        user = await self.repository.by_id_for_update(user_id)
        if user is None:
            await self._record(actor, action, "DENIED", request_id, user_id, reason="USER_NOT_FOUND")
            raise UserNotFoundError()
        if user.role == Role.OWNER:
            await self._record(actor, action, "DENIED", request_id, user_id, reason="OWNER_PROTECTED")
            raise OwnerProtectedError()
        return user

    async def _view(self, user: User) -> OwnerUserView:
        return OwnerUserView(
            id=user.id, username=user.username, display_name=user.display_name,
            role=user.role, status=user.status, last_login_at=user.last_login_at,
            projects=tuple(await self.repository.projects_for_user(user.id)),
        )

    async def list_users(self, actor: Actor, request_id: UUID) -> list[OwnerUserView]:
        await self._require_owner(actor, "user.list", request_id)
        return [await self._view(user) for user in await self.repository.list_all()]

    async def create_staff(
        self, actor: Actor, command: StaffCreate, request_id: UUID
    ) -> StaffCredentialResult:
        await self._require_owner(actor, "user.create", request_id)
        project_ids = sorted(command.project_ids)
        projects = await self.repository.projects_for_update(project_ids)
        if len(projects) != len(project_ids):
            await self._record(actor, "user.create", "DENIED", request_id, reason="PROJECT_NOT_FOUND")
            raise UnknownProjectError()
        temporary_password = new_temporary_password()
        user = User(
            username=command.username,
            display_name=command.display_name,
            password_hash=hash_password(temporary_password),
            must_change_password=True,
            password_changed_at=utcnow(),
            role=Role.STAFF,
            status=UserStatus.ACTIVE,
        )
        try:
            await self.repository.add(user)
            await self.repository.replace_project_memberships(user.id, project_ids)
        except IntegrityError as error:
            await self.repository.session.rollback()
            await self._record(actor, "user.create", "DENIED", request_id, reason="USERNAME_CONFLICT")
            raise DuplicateUserError() from error
        return StaffCredentialResult(
            OwnerUserView(
                user.id,
                user.username,
                user.display_name,
                user.role,
                user.status,
                user.last_login_at,
                tuple(projects),
            ),
            temporary_password,
        )

    async def update_staff(self, actor: Actor, user_id: UUID, command: StaffUpdate, request_id: UUID) -> OwnerUserView:
        user = await self._staff_for_update(actor, "user.update", user_id, request_id)
        values = command.model_dump(exclude_none=True)
        if not values:
            await self._record(actor, "user.update", "DENIED", request_id, user_id, reason="EMPTY_UPDATE")
            raise DomainError("VALIDATION_ERROR", "Request validation failed", 422)
        if "display_name" in values:
            user.display_name = values["display_name"]
        if values.get("status") == UserStatus.DISABLED:
            user.status = UserStatus.DISABLED
            await self.repository.revoke_browser_sessions(user.id, utcnow())
        elif values.get("status") == UserStatus.ACTIVE:
            user.status = UserStatus.ACTIVE
        await self.repository.session.flush()
        return await self._view(user)

    async def replace_projects(self, actor: Actor, user_id: UUID, command: ProjectAssignments, request_id: UUID) -> OwnerUserView:
        user = await self._staff_for_update(actor, "user.projects.replace", user_id, request_id)
        project_ids = sorted(command.project_ids)
        projects = await self.repository.projects_for_update(project_ids)
        if len(projects) != len(project_ids):
            await self._record(actor, "user.projects.replace", "DENIED", request_id, user_id, reason="PROJECT_NOT_FOUND")
            raise UnknownProjectError()
        await self.repository.replace_project_memberships(user.id, project_ids)
        return OwnerUserView(user.id, user.username, user.display_name, user.role, user.status, user.last_login_at, tuple(projects))

    async def reset_staff_password(
        self, actor: Actor, user_id: UUID, request_id: UUID
    ) -> PasswordResetResult:
        user = await self._staff_for_update(actor, "user.password.reset", user_id, request_id)
        temporary_password = new_temporary_password()
        now = utcnow()
        user.password_hash = hash_password(temporary_password)
        user.password_changed_at = now
        user.must_change_password = True
        user.failed_login_count = 0
        user.locked_until = None
        await self.repository.revoke_browser_sessions(user.id, now)
        await self.repository.session.flush()
        return PasswordResetResult(temporary_password)

    async def commit_and_record_success(self, actor: Actor, action: str, request_id: UUID, user_id: UUID | None = None) -> None:
        """Commit the business mutation and SUCCESS evidence atomically."""
        self.repository.session.add(
            AuditLog(
                actor_kind=actor.kind,
                actor_id=actor.subject_id,
                action=action,
                object_type="user",
                object_id=user_id,
                outcome="SUCCESS",
                metadata_json={
                    "actor_role": actor.role.value if actor.role is not None else None
                },
                request_id=request_id,
            )
        )
        await self.repository.session.commit()
