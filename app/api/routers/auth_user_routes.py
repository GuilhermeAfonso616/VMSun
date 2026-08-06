"""Rotas HTTP de autenticacao, perfil e administracao de usuarios."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user, get_db, require_role
from app.core.config import settings
from app.core.security import decode_access_token
from app.db.models import User, UserSession
from app.services.audit_service import log_audit
from app.services.user_service import (
    VALID_USER_ROLES,
    UserServiceError,
    authenticate_user,
    create_initial_admin,
    create_user_account,
    delete_user_account,
    installation_setup_required,
    unlock_user_account,
    update_own_profile,
    update_user_account,
)
from app.services.session_service import (
    create_login_session,
    create_user_session,
    get_active_user_sessions,
    revoke_session,
)


router = APIRouter(tags=["authentication", "users"])


class UserLogin(BaseModel):
    username: str
    password: str


class InitialSetup(BaseModel):
    username: str
    password: str
    name: str | None = None


class UserCreate(BaseModel):
    username: str
    password: str
    name: str | None = None
    role: str = "operator"
    is_active: bool = True
    max_active_sessions: int | None = None


class UserUpdate(BaseModel):
    password: str | None = None
    name: str | None = None
    role: str | None = None
    is_active: bool | None = None
    max_active_sessions: int | None = None


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    name: str | None = None
    role: str
    is_active: bool
    must_change_password: bool = False
    lockout_until: datetime | None = None
    max_active_sessions: int | None = None


class UserSessionOut(BaseModel):
    id: str
    created_at: datetime
    expires_at: datetime
    last_seen_at: datetime | None = None
    ip_address: str | None = None
    user_agent: str | None = None
    is_current: bool = False


class ProfileUpdate(BaseModel):
    name: str | None = None
    current_password: str | None = None
    new_password: str | None = None


def _translate_service_error(exc: UserServiceError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


def _set_session_cookie(response: Response, token: str, request: Request) -> None:
    secure = settings.session_cookie_secure
    if secure is None:
        secure = request.url.scheme == "https"
    response.set_cookie(
        "session_token",
        token,
        max_age=max(60, settings.session_ttl_seconds),
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )


def _request_session_id(request: Request) -> str | None:
    authorization = request.headers.get("authorization", "")
    token = authorization[7:] if authorization.lower().startswith("bearer ") else request.cookies.get("session_token")
    payload = decode_access_token(token or "") or {}
    session_id = payload.get("sid")
    return str(session_id) if session_id else None


@router.post("/auth/login")
def login(payload: UserLogin, request: Request, response: Response, db: Session = Depends(get_db)):
    try:
        user = authenticate_user(
            db,
            username=payload.username,
            password=payload.password,
            ip_address=request.client.host if request.client else None,
        )
    except UserServiceError as exc:
        raise _translate_service_error(exc) from exc

    previous_session_id = _request_session_id(request)
    if previous_session_id:
        previous_session = db.query(UserSession).filter(
            UserSession.id == previous_session_id,
            UserSession.user_id == user.id,
            UserSession.revoked_at.is_(None),
        ).first()
        if previous_session:
            revoke_session(db, previous_session.id)

    _, token, session_context = create_login_session(
        db,
        user,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    _set_session_cookie(response, token, request)
    concurrent_notice = None
    if session_context.revoked_sessions:
        concurrent_notice = (
            f"{session_context.revoked_sessions} sessao(oes) antiga(s) foram encerradas "
            "para respeitar o limite configurado."
        )
    elif session_context.other_active_sessions:
        concurrent_notice = (
            f"Este usuario ja estava conectado em "
            f"{session_context.other_active_sessions} outro(s) dispositivo(s)."
        )
    if concurrent_notice:
        log_audit(
            db,
            "concurrent_login",
            user,
            concurrent_notice,
            ip_address=request.client.host if request.client else None,
        )
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {"username": user.username, "name": user.name, "role": user.role},
        "must_change_password": bool(user.must_change_password),
        "other_active_sessions": session_context.other_active_sessions,
        "sessions_revoked": session_context.revoked_sessions,
        "concurrent_session_notice": concurrent_notice,
    }


@router.get("/auth/setup/status")
def setup_status(db: Session = Depends(get_db)):
    return {"setup_required": installation_setup_required(db)}


@router.post("/auth/setup", status_code=201)
def setup_initial_admin(
    payload: InitialSetup,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    try:
        user = create_initial_admin(
            db,
            username=payload.username,
            password=payload.password,
            name=payload.name,
            ip_address=request.client.host if request.client else None,
        )
    except UserServiceError as exc:
        raise _translate_service_error(exc) from exc
    _, token = create_user_session(
        db,
        user,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    _set_session_cookie(response, token, request)
    return {"ok": True, "user": {"username": user.username, "name": user.name, "role": user.role}}


@router.post("/auth/logout")
def api_logout(
    request: Request,
    response: Response,
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    authorization = request.headers.get("authorization", "")
    token = authorization[7:] if authorization.lower().startswith("bearer ") else request.cookies.get("session_token")
    payload = decode_access_token(token or "") or {}
    revoke_session(db, payload.get("sid"))
    response.delete_cookie("session_token", path="/")
    return {"ok": True}


@router.get("/auth/me", response_model=UserOut)
def get_me(current_user: User = Depends(get_current_user)):
    return current_user


@router.get("/users", response_model=list[UserOut])
def list_users(
    current_user: User = Depends(require_role(["admin"])),
    db: Session = Depends(get_db),
):
    return db.query(User).all()


@router.post("/users", response_model=UserOut)
def create_user(
    payload: UserCreate,
    request: Request,
    current_user: User = Depends(require_role(["admin"])),
    db: Session = Depends(get_db),
):
    try:
        return create_user_account(
            db,
            username=payload.username,
            password=payload.password,
            name=payload.name,
            role=payload.role,
            is_active=payload.is_active,
            max_active_sessions=payload.max_active_sessions,
            actor=current_user,
            ip_address=request.client.host if request.client else None,
        )
    except UserServiceError as exc:
        raise _translate_service_error(exc) from exc


@router.put("/users/{user_id}", response_model=UserOut)
def update_user(
    user_id: int,
    payload: UserUpdate,
    request: Request,
    current_user: User = Depends(require_role(["admin"])),
    db: Session = Depends(get_db),
):
    try:
        update_fields = {
            "password": payload.password,
            "name": payload.name,
            "role": payload.role,
            "is_active": payload.is_active,
        }
        if "max_active_sessions" in payload.model_fields_set:
            update_fields["max_active_sessions"] = payload.max_active_sessions
        return update_user_account(
            db,
            user_id=user_id,
            actor=current_user,
            ip_address=request.client.host if request.client else None,
            **update_fields,
        )
    except UserServiceError as exc:
        raise _translate_service_error(exc) from exc


@router.delete("/users/{user_id}")
def delete_user(
    user_id: int,
    request: Request,
    current_user: User = Depends(require_role(["admin"])),
    db: Session = Depends(get_db),
):
    try:
        delete_user_account(
            db,
            user_id=user_id,
            actor=current_user,
            ip_address=request.client.host if request.client else None,
        )
    except UserServiceError as exc:
        raise _translate_service_error(exc) from exc
    return {"ok": True, "detail": "Usuario excluido com sucesso."}


@router.post("/users/{user_id}/unlock")
def unlock_user(
    user_id: int,
    request: Request,
    current_user: User = Depends(require_role(["admin"])),
    db: Session = Depends(get_db),
):
    try:
        user = unlock_user_account(
            db,
            user_id=user_id,
            actor=current_user,
            ip_address=request.client.host if request.client else None,
        )
    except UserServiceError as exc:
        raise _translate_service_error(exc) from exc
    return {"ok": True, "detail": f"Usuario {user.username} desbloqueado com sucesso."}


@router.get("/users/{user_id}/sessions", response_model=list[UserSessionOut])
def list_user_sessions(
    user_id: int,
    request: Request,
    _current_user: User = Depends(require_role(["admin"])),
    db: Session = Depends(get_db),
):
    if not db.get(User, user_id):
        raise HTTPException(status_code=404, detail="Usuario nao encontrado.")
    current_session_id = _request_session_id(request)
    return [
        UserSessionOut(
            id=session.id,
            created_at=session.created_at,
            expires_at=session.expires_at,
            last_seen_at=session.last_seen_at,
            ip_address=session.ip_address,
            user_agent=session.user_agent,
            is_current=session.id == current_session_id,
        )
        for session in get_active_user_sessions(db, user_id)
    ]


@router.delete("/users/{user_id}/sessions/{session_id}")
def close_user_session(
    user_id: int,
    session_id: str,
    request: Request,
    current_user: User = Depends(require_role(["admin"])),
    db: Session = Depends(get_db),
):
    session = db.query(UserSession).filter(
        UserSession.id == session_id,
        UserSession.user_id == user_id,
    ).first()
    if not session or session.revoked_at is not None:
        raise HTTPException(status_code=404, detail="Sessao ativa nao encontrada.")
    revoke_session(db, session.id)
    target = db.get(User, user_id)
    log_audit(
        db,
        "user_session_revoked",
        current_user,
        f"Encerrou sessao de {target.username if target else user_id}: {session.id[:8]}",
        ip_address=request.client.host if request.client else None,
    )
    return {"ok": True, "current_session_revoked": session.id == _request_session_id(request)}


@router.put("/auth/me", response_model=UserOut)
def update_my_profile(
    payload: ProfileUpdate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        return update_own_profile(
            db,
            actor=current_user,
            name=payload.name,
            current_password=payload.current_password,
            new_password=payload.new_password,
            ip_address=request.client.host if request.client else None,
        )
    except UserServiceError as exc:
        raise _translate_service_error(exc) from exc
