"""Rotas exclusivas do usuário DEV para Matriz de Permissões e Simulação de Visão."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from app.db.models import User
from app.services import role_permissions_service as perm_service
from app.web.infrastructure import require_web_auth, templates

router = APIRouter(tags=["dev_permissions"])

DEV_ROLES = ["dev"]


@router.get("/dev/permissions", response_class=HTMLResponse)
def dev_permissions_page(
    request: Request,
    current_user: User = Depends(require_web_auth(DEV_ROLES)),
):
    matrix = perm_service.load_role_permissions_matrix()

    # Preparar estrutura formatada para o template
    nav_items = []
    for item in perm_service.DEFAULT_NAV_ITEMS:
        key = item["key"]
        allowed = matrix.get("nav", {}).get(key, item["default_roles"])
        nav_items.append({
            "key": key,
            "label": item["label"],
            "roles": {r: (r in allowed) for r in perm_service.ROLES},
        })

    action_items = []
    for item in perm_service.DEFAULT_ACTION_ITEMS:
        key = item["key"]
        allowed = matrix.get("actions", {}).get(key, item["default_roles"])
        action_items.append({
            "key": key,
            "label": item["label"],
            "roles": {r: (r in allowed) for r in perm_service.ROLES},
        })

    preview_role = request.cookies.get("dev_preview_role", "")

    return templates.TemplateResponse(
        request=request,
        name="dev_permissions.html",
        context={
            "request": request,
            "current_user": current_user,
            "nav_items": nav_items,
            "action_items": action_items,
            "roles": perm_service.ROLES,
            "role_labels": perm_service.ROLE_LABELS,
            "preview_role": preview_role,
            "title": "Matriz de Permissões (DEV) | SunOrus",
        },
    )



@router.post("/dev/permissions")
async def save_dev_permissions(
    request: Request,
    current_user: User = Depends(require_web_auth(DEV_ROLES)),
):
    form_data = await request.form()

    new_nav: dict[str, list[str]] = {}
    for item in perm_service.DEFAULT_NAV_ITEMS:
        key = item["key"]
        new_nav[key] = []
        for role in perm_service.ROLES:
            form_key = f"nav_{key}_{role}"
            if form_key in form_data:
                new_nav[key].append(role)

    new_actions: dict[str, list[str]] = {}
    for item in perm_service.DEFAULT_ACTION_ITEMS:
        key = item["key"]
        new_actions[key] = []
        for role in perm_service.ROLES:
            form_key = f"action_{key}_{role}"
            if form_key in form_data:
                new_actions[key].append(role)

    perm_service.save_role_permissions_matrix({
        "nav": new_nav,
        "actions": new_actions,
    })

    return RedirectResponse(url="/dev/permissions?saved=1", status_code=303)


@router.post("/dev/preview-role")
async def toggle_dev_role_preview(
    request: Request,
    response: Response,
    target_role: str = Form(""),
    current_user: User = Depends(require_web_auth(DEV_ROLES)),
):
    valid_target = target_role.strip().lower()
    if valid_target not in perm_service.ROLES and valid_target != "":
        raise HTTPException(status_code=400, detail="Perfil de simulação inválido.")

    redirect_url = request.headers.get("referer") or "/dev/permissions"
    res = RedirectResponse(url=redirect_url, status_code=303)

    if valid_target and valid_target != "dev":
        res.set_cookie("dev_preview_role", valid_target, max_age=86400, httponly=True)
    else:
        res.delete_cookie("dev_preview_role")

    return res
