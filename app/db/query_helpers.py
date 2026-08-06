"""Query helpers para trabalhar com modelos de forma coerente e segura.

Define padrões reutilizáveis para queries de câmeras, eventos e dados relacionados,
com suporte a soft delete e filtros comuns.
"""

from sqlalchemy.orm import Session
from app.db.models import Camera, Event, EventFeedback, TuningSuggestion, ConfigVersionHistory


def get_active_cameras_query(db: Session):
    """Retorna query builder para câmeras não deletadas (soft delete).
    
    Uso: db.query(Camera).filter_(*get_active_cameras_filter())
    Ou: cameras = get_active_cameras_query(db).all()
    """
    return db.query(Camera).filter(Camera.is_deleted == False)  # noqa: E712


def get_active_cameras(db: Session):
    """Retorna todas as câmeras ativas (não soft-deleted)."""
    return get_active_cameras_query(db).all()


def get_active_camera_by_id(db: Session, camera_id: int):
    """Retorna uma câmera ativa por ID, ou None se não existe ou está deletada."""
    return get_active_cameras_query(db).filter(Camera.id == camera_id).first()


def get_active_camera_map(db: Session):
    """Retorna um dicionário {camera_id: camera} com câmeras ativas.
    
    Útil para enriquecer objetos de eventos com dados de câmera.
    """
    return {camera.id: camera for camera in get_active_cameras(db)}


def get_events_for_camera(db: Session, camera_id: int, limit: int = 50):
    """Retorna eventos recentes de uma câmera, mesmo que a câmera esteja deletada.
    
    Preserva o histórico para câmeras deletadas (soft delete).
    """
    return (
        db.query(Event)
        .filter(Event.camera_id == camera_id)
        .order_by(Event.id.desc())
        .limit(limit)
        .all()
    )


def get_feedback_for_event(db: Session, event_id: int):
    """Retorna feedback associado a um evento."""
    return (
        db.query(EventFeedback)
        .filter(EventFeedback.event_id == event_id)
        .order_by(EventFeedback.reviewed_at.desc())
        .first()
    )


def get_tuning_suggestions_for_camera(db: Session, camera_id: int, status: str = "pending"):
    """Retorna sugestões de tuning para uma câmera (mesmo se deletada)."""
    query = db.query(TuningSuggestion).filter(TuningSuggestion.camera_id == camera_id)
    if status:
        query = query.filter(TuningSuggestion.status == status)
    return query.all()


def get_config_history_for_camera(db: Session, camera_id: int, limit: int = 100):
    """Retorna histórico de configuração para uma câmera (mesmo se deletada)."""
    return (
        db.query(ConfigVersionHistory)
        .filter(ConfigVersionHistory.camera_id == camera_id)
        .order_by(ConfigVersionHistory.id.desc())
        .limit(limit)
        .all()
    )
