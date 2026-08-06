"""Pagina administrativa da central de notificacoes."""

from fastapi import APIRouter, Depends, Request

from app.db.models import User
from app.web.infrastructure import require_web_auth, templates


router = APIRouter()


@router.get("/notificacoes")
@router.get("/notifications")
def notifications_page(
    request: Request,
    current_user: User = Depends(require_web_auth(["admin", "supervisor"])),
):
    return templates.TemplateResponse(
        request=request,
        name="notifications.html",
        context={"request": request, "can_manage": current_user.role in {"admin", "dev"}},
    )
