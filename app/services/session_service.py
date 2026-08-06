"""Ciclo de vida de sessoes revogaveis para API, web e cliente desktop."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import create_access_token
from app.db.models import User, UserSession


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _comparable(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class SessionLoginContext:
    other_active_sessions: int
    revoked_sessions: int


def get_active_user_sessions(db: Session, user_id: int) -> list[UserSession]:
    now = utc_now()
    return (
        db.query(UserSession)
        .filter(
            UserSession.user_id == user_id,
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > now,
        )
        .order_by(UserSession.last_seen_at.desc(), UserSession.created_at.desc())
        .all()
    )


def _normalized_session_limit(user: User) -> int | None:
    value = getattr(user, "max_active_sessions", None)
    if value is None:
        return None
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return None


def trim_user_sessions(db: Session, user_id: int, limit: int | None) -> int:
    if limit is None:
        return 0
    sessions = get_active_user_sessions(db, user_id)
    sessions_to_revoke = sessions[max(1, int(limit)):]
    now = utc_now()
    for session in sessions_to_revoke:
        session.revoked_at = now
    return len(sessions_to_revoke)


def create_login_session(
    db: Session,
    user: User,
    *,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> tuple[UserSession, str, SessionLoginContext]:
    existing = get_active_user_sessions(db, user.id)
    limit = _normalized_session_limit(user)
    revoked_count = 0
    if limit is not None:
        keep_existing = max(0, limit - 1)
        sessions_to_revoke = existing[keep_existing:]
        now = utc_now()
        for previous in sessions_to_revoke:
            previous.revoked_at = now
        revoked_count = len(sessions_to_revoke)

    now = utc_now()
    expires_at = now + timedelta(seconds=max(60, settings.session_ttl_seconds))
    session = UserSession(
        id=uuid4().hex,
        user_id=user.id,
        created_at=now,
        expires_at=expires_at,
        last_seen_at=now,
        ip_address=ip_address,
        user_agent=(user_agent or "")[:500] or None,
    )
    db.add(session)
    db.commit()
    token = create_access_token(
        {
            "user_id": user.id,
            "username": user.username,
            "role": user.role,
            "sid": session.id,
        },
        expires_delta=max(60, settings.session_ttl_seconds),
    )
    return session, token, SessionLoginContext(
        other_active_sessions=len(existing),
        revoked_sessions=revoked_count,
    )


def create_user_session(
    db: Session,
    user: User,
    *,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> tuple[UserSession, str]:
    session, token, _context = create_login_session(
        db,
        user,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return session, token


def active_session(db: Session, session_id: str, user_id: int) -> UserSession | None:
    session = db.query(UserSession).filter(
        UserSession.id == session_id,
        UserSession.user_id == user_id,
    ).first()
    if not session or session.revoked_at is not None:
        return None
    if _comparable(session.expires_at) <= utc_now():
        return None
    last_seen = _comparable(session.last_seen_at or session.created_at)
    now = utc_now()
    if last_seen <= now - timedelta(seconds=60):
        session.last_seen_at = now
        db.commit()
    return session


def revoke_session(db: Session, session_id: str | None) -> bool:
    if not session_id:
        return False
    session = db.query(UserSession).filter(UserSession.id == session_id).first()
    if not session or session.revoked_at is not None:
        return False
    session.revoked_at = utc_now()
    db.commit()
    return True


def revoke_user_sessions(db: Session, user_id: int, *, except_session_id: str | None = None) -> int:
    query = db.query(UserSession).filter(
        UserSession.user_id == user_id,
        UserSession.revoked_at.is_(None),
    )
    if except_session_id:
        query = query.filter(UserSession.id != except_session_id)
    count = query.update({UserSession.revoked_at: utc_now()}, synchronize_session=False)
    return int(count)


def delete_user_sessions(db: Session, user_id: int) -> None:
    db.query(UserSession).filter(UserSession.user_id == user_id).delete(synchronize_session=False)
