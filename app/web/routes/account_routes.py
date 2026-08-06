"""Páginas web de sessão, perfil e administração de usuários."""

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from app.core.logging import log_ignored_exception
from app.core.security import decode_access_token
from app.db.models import User
from app.services.session_service import revoke_session
from app.services.user_service import installation_setup_required, record_user_logout
from app.web.infrastructure import get_scoped_db, get_web_user, require_web_auth, templates


router = APIRouter()


@router.get("/login")
def web_login(
    request: Request,
    current_user: User | None = Depends(get_web_user),
):
    if current_user:
        return RedirectResponse(url="/monitor")
    db = get_scoped_db()
    try:
        if installation_setup_required(db):
            return RedirectResponse(url="/setup")
    finally:
        db.close()
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"request": request},
    )


@router.get("/setup")
def web_setup(request: Request):
    db = get_scoped_db()
    try:
        if not installation_setup_required(db):
            return RedirectResponse(url="/login")
    finally:
        db.close()
    return templates.TemplateResponse(
        request=request,
        name="setup.html",
        context={"request": request},
    )


@router.get("/logout")
def web_logout(
    request: Request,
    current_user: User | None = Depends(get_web_user),
):
    if current_user:
        db = get_scoped_db()
        try:
            ip_address = request.client.host if request.client else None
            record_user_logout(
                db,
                user=current_user,
                ip_address=ip_address,
            )
            token_payload = decode_access_token(request.cookies.get("session_token", "")) or {}
            revoke_session(db, token_payload.get("sid"))
        except Exception:
            # O logout do navegador não deve ser bloqueado por falha de auditoria, mas
            # uma sessao que nao foi revogada no servidor precisa aparecer no log.
            log_ignored_exception("account.logout_cleanup", level=logging.WARNING)
        finally:
            db.close()
    response = RedirectResponse(url="/login")
    response.delete_cookie("session_token", path="/")
    return response


@router.get("/perfil")
def web_profile_page(
    request: Request,
    current_user: User = Depends(
        require_web_auth(["admin", "supervisor", "operator", "viewer", "dev"])
    ),
):
    return templates.TemplateResponse(
        request=request,
        name="perfil.html",
        context={"request": request},
    )


@router.get("/usuarios")
@router.get("/users")
def get_users_page(
    request: Request,
    current_user: User = Depends(require_web_auth(["admin"])),
):
    return templates.TemplateResponse(
        request=request,
        name="usuarios.html",
        context={"request": request},
    )
