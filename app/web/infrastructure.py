"""Infraestrutura compartilhada pelas rotas HTML.

Mantem autenticacao, sessao e configuracao de templates fora dos routers de
dominio. Assim, cada router pode ser importado e testado sem depender do
arquivo monolitico ``web_routes``.
"""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi import Cookie, Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.core.build_info import build_info_payload, web_version_text
from app.core.security import decode_access_token
from app.core.timezone import format_brazil_datetime
from app.db.base import SessionLocal
from app.db.models import User
from app.services.session_service import active_session
from app.web.presentation_filters import (
    event_type_label,
    lifecycle_label,
    severity_label,
    status_label,
)


def resolve_templates_dir() -> Path:
    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        executable_dir = Path(sys.executable).resolve().parent
        candidates.append(executable_dir / "templates")
        bundled_dir = getattr(sys, "_MEIPASS", None)
        if bundled_dir:
            candidates.append(Path(bundled_dir) / "templates")

    candidates.append(Path.cwd() / "templates")
    candidates.append(Path(__file__).resolve().parents[2] / "templates")
    for path in candidates:
        if path.exists() and path.is_dir():
            return path
    return candidates[0]


TEMPLATES_DIR = resolve_templates_dir()
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.filters["br_datetime"] = format_brazil_datetime
templates.env.filters["event_label"] = event_type_label
templates.env.filters["status_label"] = status_label
templates.env.filters["severity_label"] = severity_label
templates.env.filters["lifecycle_label"] = lifecycle_label
templates.env.globals["analitico_build"] = build_info_payload()
templates.env.globals["analitico_web_version"] = web_version_text()


def get_scoped_db() -> Session:
    """Cria uma sessao para fluxos web que controlam seu proprio ciclo de vida.

    Nao e um generator (nao serve para ``Depends()``): o chamador e responsavel
    por fechar a sessao. Nome distinto de ``app.api.dependencies.get_db``
    (esse sim um generator para ``Depends()``) para evitar confusao entre os
    dois contratos.
    """
    return SessionLocal()


def get_web_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_web_user(
    request: Request,
    session_token: str | None = Cookie(None),
    db: Session = Depends(get_web_db),
) -> User | None:
    if not session_token:
        return None
    payload = decode_access_token(session_token)
    if not payload:
        return None
    user_id = payload.get("user_id")
    session_id = payload.get("sid")
    if not user_id or not session_id:
        return None
    session = active_session(db, str(session_id), int(user_id))
    if not session:
        return None
    request.state.session_id = session.id
    request.state.session_created_at = session.created_at
    user = db.query(User).filter(User.id == user_id).first()
    if not user or not user.is_active:
        return None
    return user


from app.services.role_permissions_service import is_action_allowed, is_nav_item_visible

templates.env.globals["is_nav_visible"] = is_nav_item_visible
templates.env.globals["is_action_allowed"] = is_action_allowed


def require_web_auth(roles: list[str] | None = None):
    def dependency(
        request: Request,
        current_user: User | None = Depends(get_web_user),
    ) -> User:
        if not current_user:
            raise HTTPException(
                status_code=307,
                detail="Redirect to login",
                headers={"Location": "/login"},
            )
        if current_user.must_change_password and request.url.path != "/perfil":
            raise HTTPException(
                status_code=303,
                detail="Troca de senha obrigatoria.",
                headers={"Location": "/perfil?password_change=required"},
            )
        if roles and current_user.role not in roles and current_user.role != "dev":
            raise HTTPException(
                status_code=303,
                detail="Acesso negado: permissao insuficiente.",
                headers={"Location": "/"},
            )

        preview_role = request.cookies.get("dev_preview_role") if current_user.role == "dev" else None
        effective_role = preview_role if preview_role in ["operator", "supervisor", "admin", "dev"] else current_user.role

        request.state.user = current_user
        request.state.effective_role = effective_role
        request.state.dev_preview_mode = bool(preview_role and current_user.role == "dev")
        return current_user

    return dependency
