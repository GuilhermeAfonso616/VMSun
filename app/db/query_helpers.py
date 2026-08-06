"""Query helpers para trabalhar com modelos do VMSun de forma coerente e segura."""

from sqlalchemy.orm import Session
from app.db.models import Camera


def get_active_cameras_query(db: Session):
    """Retorna query builder para câmeras não deletadas (soft delete)."""
    return db.query(Camera).filter(Camera.is_deleted == False)  # noqa: E712


def get_active_cameras(db: Session):
    """Retorna todas as câmeras ativas (não soft-deleted)."""
    return get_active_cameras_query(db).all()


def get_active_camera_by_id(db: Session, camera_id: int):
    """Retorna uma câmera ativa por ID, ou None se não existe ou está deletada."""
    return get_active_cameras_query(db).filter(Camera.id == camera_id).first()


def get_active_camera_map(db: Session):
    """Retorna um dicionário {camera_id: camera} com câmeras ativas."""
    return {camera.id: camera for camera in get_active_cameras(db)}


def get_events_for_camera(db: Session, camera_id: int, limit: int = 50):
    return []


def get_feedback_for_event(db: Session, event_id: int):
    return None


def get_tuning_suggestions_for_camera(db: Session, camera_id: int, status: str = "pending"):
    return []


def get_config_history_for_camera(db: Session, camera_id: int, limit: int = 100):
    return []
