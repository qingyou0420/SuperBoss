"""WeCom browser OAuth routes."""

import secrets
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from superboss.core.actors import Actor
from superboss.core.config import Settings
from superboss.core.security import hash_token, new_csrf_token, utcnow
from superboss.infrastructure.wecom import WeComError
from superboss.modules.audit.schemas import AuditEventInput
from superboss.modules.audit.service import AuditService
from superboss.modules.auth.models import OAuthState
from superboss.modules.auth.repository import AuthRepository
from superboss.modules.auth.schemas import SessionPair
from superboss.modules.auth.service import AuthService, ForbiddenIdentity, InvalidSession
from superboss.modules.users.repository import UserRepository

router = APIRouter(prefix="/auth", tags=["auth"])
_OAUTH_STATE_COOKIE = "wecom_oauth_state"


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
        request.app.state.wecom_provider,
        request.app.state.settings,
    )


def _state_cookie(settings: Settings, state: str) -> str:
    if not settings.jwt_secret:
        raise HTTPException(500, "Authentication is not configured")
    now = datetime.now(UTC)
    return jwt.encode(
        {"state": state, "iat": now, "exp": now + timedelta(minutes=10)},
        settings.jwt_secret,
        algorithm="HS256",
    )


def _verify_state(settings: Settings, signed_state: str | None, state: str | None) -> None:
    if not signed_state or not state or not settings.jwt_secret:
        raise HTTPException(400, "Invalid OAuth state")
    try:
        payload = jwt.decode(signed_state, settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError as error:
        raise HTTPException(400, "Invalid OAuth state") from error
    if payload.get("state") != state:
        raise HTTPException(400, "Invalid OAuth state")


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
    response.set_cookie(
        "XSRF-TOKEN", new_csrf_token(), secure=True, httponly=False, samesite="lax", path="/"
    )


@router.get("/wecom/start")
async def wecom_start(
    request: Request, response: Response, session: AsyncSession = Depends(get_session)
) -> dict[str, str]:
    state = secrets.token_urlsafe(32)
    await AuthRepository(session).add_oauth_state(
        OAuthState(nonce_hash=hash_token(state), expires_at=utcnow() + timedelta(minutes=10))
    )
    response.set_cookie(
        _OAUTH_STATE_COOKIE,
        _state_cookie(request.app.state.settings, state),
        secure=True,
        httponly=True,
        samesite="lax",
        path="/api/v1/auth/wecom",
    )
    return {
        "state": state,
        "authorization_url": request.app.state.wecom_provider.authorization_url(state),
    }


@router.get("/wecom/callback", status_code=204)
async def wecom_callback(
    request: Request,
    response: Response,
    code: str | None = None,
    state: str | None = None,
    service: AuthService = Depends(get_service),
) -> None:
    response.delete_cookie(_OAUTH_STATE_COOKIE, path="/api/v1/auth/wecom")
    try:
        _verify_state(request.app.state.settings, request.cookies.get(_OAUTH_STATE_COOKIE), state)
        if (
            code is None
            or state is None
            or not await service.auth_repository.consume_oauth_state(hash_token(state), utcnow())
        ):
            raise HTTPException(400, "Invalid OAuth state")
        await service.session.commit()
    except HTTPException as error:
        result = JSONResponse({"detail": error.detail}, status_code=error.status_code)
        result.delete_cookie(_OAUTH_STATE_COOKIE, path="/api/v1/auth/wecom")
        return result  # type: ignore[return-value]
    try:
        completed = await service.complete_wecom_login(code, state)
    except (ForbiddenIdentity, WeComError):
        result = JSONResponse({"detail": "Identity is not authorized"}, status_code=403)
        result.delete_cookie(_OAUTH_STATE_COOKIE, path="/api/v1/auth/wecom")
        return result  # type: ignore[return-value]
    await service.session.commit()
    await AuditService(request.app.state.session_factory).record(
        AuditEventInput(
            actor=Actor(
                "user",
                completed.user.id,
                completed.user.role,
                frozenset(),
                frozenset(),
            ),
            action="auth.login",
            object_type="user",
            object_id=completed.user.id,
            project_id=None,
            outcome="SUCCESS",
            request_id=request.state.request_id,
            metadata={},
        )
    )
    _set_session_cookies(response, completed.pair)
    response.delete_cookie(_OAUTH_STATE_COOKIE, path="/api/v1/auth/wecom")


@router.post("/refresh", status_code=204)
async def refresh(
    request: Request, response: Response, service: AuthService = Depends(get_service)
) -> None:
    try:
        pair = await service.rotate_refresh_token(request.cookies.get("refresh_token", ""))
    except InvalidSession as error:
        raise HTTPException(401, "Invalid refresh token") from error
    _set_session_cookies(response, pair)


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


@router.get("/me")
async def me(request: Request, service: AuthService = Depends(get_service)) -> dict[str, str]:
    authorization = request.headers.get("Authorization", "")
    token = (
        authorization.removeprefix("Bearer ")
        if authorization.startswith("Bearer ")
        else request.cookies.get("access_token")
    )
    if token is None:
        raise HTTPException(401, "Authentication required")
    try:
        user = await service.authenticate_access_token(token)
    except InvalidSession as error:
        raise HTTPException(401, "Authentication required") from error
    return {"userid": user.wecom_userid, "role": str(user.role)}
