"""Agregador das rotas web do sistema."""

from fastapi import APIRouter

from app.web.routes.account_routes import router as account_router
from app.web.routes.camera_configuration_routes import (
    router as camera_configuration_router,
)
from app.web.routes.camera_creation_routes import router as camera_creation_router
from app.web.routes.camera_detail_routes import router as camera_detail_router
from app.web.routes.camera_network_routes import router as camera_network_router
from app.web.routes.camera_operation_routes import router as camera_operation_router
from app.web.routes.camera_overview_routes import router as camera_overview_router
from app.web.routes.camera_source_routes import router as camera_source_router
from app.web.routes.camera_stream_routes import router as camera_stream_router
from app.web.routes.dashboard_routes import router as dashboard_router
from app.web.routes.diagnostics_routes import router as diagnostics_router
from app.web.routes.event_actions_routes import router as event_actions_router
from app.web.routes.event_listing_routes import router as event_listing_router
from app.web.routes.gateway_routes import router as gateway_router
from app.web.routes.health_routes import router as health_router
from app.web.routes.hik_source_routes import router as hik_source_router
from app.web.routes.lockdown_routes import router as lockdown_router
from app.web.routes.monitor_routes import router as monitor_router
from app.web.routes.nvr_routes import router as nvr_router
from app.web.routes.notification_routes import router as notification_router
from app.web.routes.revalidator_routes import router as revalidator_router
from app.web.routes.sdk_test_routes import router as sdk_test_router
from app.web.routes.video_helper_routes import router as video_helper_router


from app.web.routes.dev_permissions_routes import (
    router as dev_permissions_router,
)


router = APIRouter()
router.include_router(dev_permissions_router)
router.include_router(event_actions_router)
router.include_router(gateway_router)
router.include_router(health_router)
router.include_router(revalidator_router)
router.include_router(nvr_router)
router.include_router(notification_router)
router.include_router(camera_network_router)
router.include_router(camera_creation_router)
router.include_router(camera_source_router)
router.include_router(camera_configuration_router)
router.include_router(camera_operation_router)
router.include_router(camera_stream_router)
router.include_router(monitor_router)
router.include_router(video_helper_router)
router.include_router(sdk_test_router)
router.include_router(diagnostics_router)
router.include_router(hik_source_router)
router.include_router(dashboard_router)
router.include_router(camera_overview_router)
router.include_router(camera_detail_router)
router.include_router(event_listing_router)
router.include_router(lockdown_router)
router.include_router(account_router)
