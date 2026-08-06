"""Dependencias HTTP compartilhadas pelos routers da API."""

from __future__ import annotations

from collections.abc import Callable, Generator, Sequence

from fastapi import Cookie, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import decode_access_token
from app.db.base import SessionLocal
from app.db.models import User
from app.services.session_service import active_session


security_scheme = HTTPBearer(auto_error=False)


def get_db() -> Generator[Session, None, None]:
    """Fornece uma sessao curta e garante seu fechamento ao fim da request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security_scheme),
    session_token: str | None = Cookie(None),
    db: Session = Depends(get_db),
) -> User:
    token = credentials.credentials if credentials else session_token
    if not token:
        if not settings.api_auth_required:
            fallback_admin = db.query(User).filter(User.role == "admin").first()
            if fallback_admin:
                return fallback_admin
            return User(id=0, username="anonymous", role="admin", is_active=True)
        raise HTTPException(status_code=401, detail="Token de autorizacao ausente.")

    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Token invalido ou expirado.")

    user_id = payload.get("user_id")
    session_id = payload.get("sid")
    if not user_id or not session_id:
        raise HTTPException(status_code=401, detail="Token invalido.")

    if not active_session(db, str(session_id), int(user_id)):
        raise HTTPException(status_code=401, detail="Sessao expirada ou revogada.")

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail="Usuario nao encontrado.")
    if not user.is_active:
        raise HTTPException(status_code=401, detail="Usuario inativo.")
    if user.must_change_password and request.url.path not in {"/api/auth/me", "/api/auth/logout"}:
        raise HTTPException(status_code=403, detail="Troca de senha obrigatoria.")
    return user


def require_role(roles: Sequence[str]) -> Callable[..., User]:
    """Cria uma dependencia que restringe a rota aos papeis informados."""
    allowed_roles = frozenset(roles)

    def dependency(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in allowed_roles and current_user.role != "dev":
            raise HTTPException(status_code=403, detail="Acesso negado: permissao insuficiente.")
        return current_user

    return dependency
