"""Composicao da API HTTP do VMSun."""

from fastapi import APIRouter

from app.api.routers.audit_routes import router as audit_router
from app.api.routers.auth_user_routes import router as auth_user_router
from app.api.routers.backup_routes import router as backup_router
from app.api.routers.camera_configuration_routes import router as camera_configuration_router
from app.api.routers.camera_routes import router as camera_router
from app.api.routers.camera_runtime_routes import router as camera_runtime_router
from app.api.routers.notification_routes import router as notification_router
from app.api.routers.nvr_routes import router as nvr_router
from app.api.routers.operator_routes import router as operator_router
from app.api.routers.system_routes import router as system_router
from app.api.routers.view_routes import router as view_router
from app.core.logging import get_logger

logger = get_logger("app.api")
router = APIRouter(prefix="/api")

for domain_router in (
    auth_user_router,
    audit_router,
    camera_router,
    camera_configuration_router,
    camera_runtime_router,
    nvr_router,
    notification_router,
    view_router,
    backup_router,
    system_router,
    operator_router,
):
    router.include_router(domain_router)
