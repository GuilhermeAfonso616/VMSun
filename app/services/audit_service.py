"""Persistencia compartilhada do log de auditoria.

Interfaces HTTP e web dependem deste servico, evitando que uma camada de
apresentacao importe funcoes internas da outra.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models import AuditLog, User


logger = get_logger("app.audit")


def log_audit(
    db: Session,
    action: str,
    user: User | None = None,
    details: str | None = None,
    ip_address: str | None = None,
    username: str | None = None,
) -> AuditLog | None:
    """Registra uma acao e preserva a operacao principal se a auditoria falhar."""
    try:
        entry = AuditLog(
            user_id=user.id if user else None,
            username=username or (user.username if user else None),
            action=action,
            details=details,
            ip_address=ip_address,
        )
        db.add(entry)
        db.commit()
        return entry
    except Exception as exc:
        db.rollback()
        logger.warning("Failed to write audit log: %s", exc)
        return None
