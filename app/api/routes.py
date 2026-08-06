"""Composicao da API HTTP para integracoes externas e automacoes.

Os endpoints e casos de uso vivem em modulos por dominio. Este arquivo mantem
apenas a composicao dos routers e reexports temporarios para compatibilidade.
"""

from fastapi import APIRouter

from app.api.dependencies import get_current_user, get_db, require_role
from app.api.schemas.camera_schemas import (
    CameraAnalyticsConfigUpdate,
    CameraAnalyticsProfileUpdate,
    CameraCreate,
    CameraLearningPolicyUpdate,
    CameraOpsConfigUpdate,
    NuisanceProfileUpdate,
    NvrChannelSelection,
    NvrCreateChannelsRequest,
    NvrDiscoverRequest,
    ThresholdProfileUpdate,
)
from app.api.schemas.event_schemas import EventFeedbackCreate, EventUpdate, SuggestionCreateFromEvent
from app.api.schemas.operator_schemas import OneDriveTokenUpdate, OneDriveUploadToggle
from app.api.routers.auth_user_routes import (
    ProfileUpdate,
    UserCreate,
    UserLogin,
    UserOut,
    UserUpdate,
    VALID_USER_ROLES,
    create_user,
    delete_user,
    get_me,
    list_users,
    login,
    router as auth_user_router,
    unlock_user,
    update_my_profile,
    update_user,
)
from app.api.routers.audit_routes import (
    AuditLogOut,
    ClientAuditLog,
    create_client_audit_log,
    list_audit_logs,
    router as audit_router,
)
from app.api.routers.backup_routes import BackupRequest, export_backup, import_backup, router as backup_router
from app.api.routers.camera_configuration_routes import (
    get_camera_analytics_profile,
    router as camera_configuration_router,
    update_analytics_config,
    update_camera_analytics_profile,
    update_ops_config,
)
from app.api.routers.camera_routes import create_camera, list_cameras, router as camera_router
from app.api.routers.camera_runtime_routes import router as camera_runtime_router, start_camera, stop_camera
from app.api.routers.config_history_routes import config_history, config_rollback, router as config_history_router
from app.api.routers.drive_routes import (
    operator_drive_reviewed_events_upload,
    operator_drive_token,
    operator_drive_token_status,
    operator_drive_upload_toggle,
    router as drive_router,
)
from app.api.routers.event_routes import (
    list_events,
    review_events,
    router as event_router,
    submit_event_feedback,
    update_event,
)
from app.api.routers.feedback_routes import (
    feedback_active_learning,
    feedback_apply_suggestion,
    feedback_drift,
    feedback_metrics,
    feedback_reject_suggestion,
    feedback_suggestions,
    router as feedback_router,
    update_camera_learning_policy,
)
from app.api.routers.nvr_routes import (
    create_nvr_channels,
    discover_nvr,
    nvr_channel_health,
    router as nvr_router,
)
from app.api.routers.notification_routes import router as notification_router
from app.api.routers.operator_routes import operator_bootstrap, operator_performance_log, router as operator_router
from app.api.routers.system_routes import api_version, health, router as system_router
from app.api.routers.view_routes import (
    TemporalSequenceIn,
    TemporalSequenceOut,
    TemporalSequenceStep,
    ViewPresetIn,
    ViewPresetOut,
    create_or_update_temporal_sequence,
    create_or_update_view_preset,
    delete_temporal_sequence,
    delete_view_preset,
    get_temporal_sequences,
    get_view_presets,
    router as view_router,
)
from app.core.logging import get_logger
from app.services.operator_service import (
    camera_operator_status as _camera_operator_status,
    operator_performance_filename as _operator_performance_filename,
    operator_registration as _operator_registration,
)


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
    event_router,
    feedback_router,
    config_history_router,
    view_router,
    backup_router,
    system_router,
    drive_router,
    operator_router,
):
    router.include_router(domain_router)
