"""Local browser authentication routes."""

from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from superboss.core.actors import Actor
from superboss.core.errors import AuthenticationFailedError, UnauthenticatedError
from superboss.core.security import new_csrf_token
from superboss.modules.audit.models import AuditLog
from superboss.modules.audit.schemas import AuditEventInput
from superboss.modules.auth.repository import AuthRepository
from superboss.modules.auth.schemas import (
    AuthUserRead,
    LoginCommand,
    PasswordChangeCommand,
    SessionPair,
)
from superboss.modules.auth.service import AuthService, CompletedLogin, InvalidSession, LoginFailure
from superboss.modules.users.repository import UserRepository

router = APIRouter(prefix="/auth", tags=["auth"])
_SYSTEM_ACTOR_ID = UUID(int=0)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    session = request.app.state.session_factory()
    try:
        yield session
    except Exception:
        await session.rollback()
        raise
    else:
        await session.commit()
    finally:
        await session.close()


def get_service(request: Request, session: AsyncSession = Depends(get_session)) -> AuthService:
    return AuthService(
        session,
        AuthRepository(session),
        UserRepository(session),
        request.app.state.settings,
    )


def _set_csrf_cookie(response: Response) -> None:
    response.set_cookie(
        "XSRF-TOKEN", new_csrf_token(), secure=True, httponly=False, samesite="lax", path="/"
    )


def _set_session_cookies(response: Response, pair: SessionPair) -> None:
    response.set_cookie(
        "access_token", pair.access_token, secure=True, httponly=True, samesite="lax", path="/"
    )
    response.set_cookie(
        "refresh_token",
        pair.refresh_token,
        secure=True,
        httponly=True,
        samesite="lax",
        path="/api/v1/auth",
    )
    _set_csrf_cookie(response)


def _actor(completed: CompletedLogin) -> Actor:
    return Actor("user", completed.user.id, completed.user.role, frozenset(), frozenset())


async def _stage_auth_audit(session: AsyncSession, event: AuditEventInput) -> None:
    session.add(
        AuditLog(
            actor_kind=event.actor.kind,
            actor_id=event.actor.subject_id,
            action=event.action,
            object_type=event.object_type,
            object_id=event.object_id,
            project_id=event.project_id,
            outcome=event.outcome,
            metadata_json={
                **event.metadata,
                "actor_role": (
                    event.actor.role.value if event.actor.role is not None else None
                ),
            },
            request_id=event.request_id,
            event_key=event.event_key,
        )
    )
    await session.flush()


def _failure_actor(failure: LoginFailure) -> tuple[Actor, UUID | None]:
    user = failure.user
    return (
        Actor(
            "user" if user is not None else "system",
            user.id if user is not None else _SYSTEM_ACTOR_ID,
            user.role if user is not None else None,
            frozenset(),
            frozenset(),
        ),
        user.id if user is not None else None,
    )


async def _record_login(
    request: Request,
    session: AsyncSession,
    *,
    completed: CompletedLogin | None,
    failure: LoginFailure | None,
) -> None:
    if completed is not None:
        actor = _actor(completed)
        object_id: UUID | None = completed.user.id
        outcome = "SUCCESS"
        metadata: dict[str, object] = {}
    else:
        assert failure is not None
        actor, object_id = _failure_actor(failure)
        outcome = "DENIED"
        metadata = {"reason": failure.reason}
    await _stage_auth_audit(
        session,
        AuditEventInput(
            actor=actor,
            action="auth.login",
            object_type="user",
            object_id=object_id,
            project_id=None,
            outcome=outcome,
            request_id=UUID(request.state.request_id),
            metadata=metadata,
        )
    )


@router.get("/csrf", status_code=204)
async def csrf(response: Response) -> None:
    _set_csrf_cookie(response)


@router.post("/login", status_code=204)
async def login(
    request: Request,
    response: Response,
    command: LoginCommand,
    service: AuthService = Depends(get_service),
) -> None:
    try:
        completed = await service.login(command.username, command.password)
    except LoginFailure as failure:
        await _record_login(
            request, service.session, completed=None, failure=failure
        )
        await service.session.commit()
        raise AuthenticationFailedError() from failure
    await _record_login(request, service.session, completed=completed, failure=None)
    await service.session.commit()
    _set_session_cookies(response, completed.pair)


@router.post("/refresh", status_code=204)
async def refresh(
    request: Request, response: Response, service: AuthService = Depends(get_service)
) -> None:
    try:
        pair = await service.rotate_refresh_token(request.cookies.get("refresh_token", ""))
    except InvalidSession as error:
        raise UnauthenticatedError() from error
    _set_session_cookies(response, pair)


@router.post("/password/change", status_code=204)
async def change_password(
    request: Request,
    response: Response,
    command: PasswordChangeCommand,
    service: AuthService = Depends(get_service),
) -> None:
    token = request.cookies.get("access_token")
    if token is None:
        raise UnauthenticatedError()
    try:
        current = await service.authenticate_access_token(token)
        completed = await service.change_password(
            current.id, command.current_password, command.new_password
        )
    except InvalidSession as error:
        raise UnauthenticatedError() from error
    except LoginFailure as failure:
        actor, object_id = _failure_actor(failure)
        await _stage_auth_audit(
            service.session,
            AuditEventInput(
                actor=actor,
                action="auth.password.change",
                object_type="user",
                object_id=object_id,
                outcome="DENIED",
                request_id=UUID(request.state.request_id),
                metadata={"reason": failure.reason},
            ),
        )
        await service.session.commit()
        raise AuthenticationFailedError() from failure
    await _stage_auth_audit(
        service.session,
        AuditEventInput(
            actor=_actor(completed),
            action="auth.password.change",
            object_type="user",
            object_id=completed.user.id,
            outcome="SUCCESS",
            request_id=UUID(request.state.request_id),
            metadata={},
        )
    )
    await service.session.commit()
    _set_session_cookies(response, completed.pair)


@router.post("/logout", status_code=204)
async def logout(
    request: Request, response: Response, service: AuthService = Depends(get_service)
) -> None:
    authorization = request.headers.get("Authorization", "")
    access_token = (
        authorization.removeprefix("Bearer ")
        if authorization.startswith("Bearer ")
        else request.cookies.get("access_token")
    )
    await service.logout(access_token, request.cookies.get("refresh_token"))
    response.delete_cookie("access_token", path="/")
    response.delete_cookie("refresh_token", path="/api/v1/auth")
    response.delete_cookie("XSRF-TOKEN", path="/")


@router.get("/me", response_model=AuthUserRead)
async def me(request: Request, service: AuthService = Depends(get_service)) -> AuthUserRead:
    authorization = request.headers.get("Authorization", "")
    token = (
        authorization.removeprefix("Bearer ")
        if authorization.startswith("Bearer ")
        else request.cookies.get("access_token")
    )
    if token is None:
        raise UnauthenticatedError()
    try:
        user = await service.authenticate_access_token(token)
    except InvalidSession as error:
        raise UnauthenticatedError() from error
    return AuthUserRead.model_validate(user)
