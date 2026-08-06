"""Casos de uso de autenticacao e administracao de usuarios."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.core.security import hash_password, verify_password
from app.core.config import settings
from app.db.models import InstallationState, User
from app.services.audit_service import log_audit
from app.services.session_service import delete_user_sessions, revoke_user_sessions, trim_user_sessions


VALID_USER_ROLES = frozenset({"admin", "supervisor", "operator", "viewer", "dev"})
COMMON_PASSWORDS = frozenset({"admin", "admin123", "password", "senha123", "dev123", "123456789012"})
_UNSET = object()


@dataclass(frozen=True)
class UserServiceError(Exception):
    status_code: int
    detail: str

    def __str__(self) -> str:
        return self.detail


def validate_password(password: str, *, username: str | None = None) -> None:
    minimum = max(8, int(settings.password_min_length))
    if len(password) < minimum:
        raise UserServiceError(400, f"A senha deve ter pelo menos {minimum} caracteres.")
    normalized = password.casefold()
    if normalized in COMMON_PASSWORDS or (username and normalized == username.casefold()):
        raise UserServiceError(400, "Escolha uma senha que nao seja comum nem igual ao usuario.")
    if not any(char.islower() for char in password):
        raise UserServiceError(400, "A senha deve conter uma letra minuscula.")
    if not any(char.isupper() for char in password):
        raise UserServiceError(400, "A senha deve conter uma letra maiuscula.")
    if not any(char.isdigit() for char in password):
        raise UserServiceError(400, "A senha deve conter um numero.")


def _actor_can_bypass_password_policy(actor: User | None) -> bool:
    return bool(actor and actor.role == "dev")


def _validate_session_limit(value: int | None) -> int | None:
    if value is None:
        return None
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise UserServiceError(400, "Limite de sessoes invalido.") from exc
    if normalized < 1 or normalized > 10:
        raise UserServiceError(400, "O limite de sessoes deve ficar entre 1 e 10.")
    return normalized


def installation_setup_required(db: Session) -> bool:
    state = db.get(InstallationState, 1)
    if state is not None:
        return not bool(state.setup_completed)
    return db.query(User).count() == 0


def create_initial_admin(
    db: Session,
    *,
    username: str,
    password: str,
    name: str | None,
    ip_address: str | None = None,
) -> User:
    if not installation_setup_required(db) or db.query(User).count() != 0:
        raise UserServiceError(409, "A configuracao inicial desta instalacao ja foi concluida.")
    username = username.strip()
    if len(username) < 3:
        raise UserServiceError(400, "O usuario deve ter pelo menos 3 caracteres.")
    validate_password(password, username=username)
    user = User(
        username=username,
        password_hash=hash_password(password),
        name=(name or "").strip() or None,
        role="admin",
        is_active=True,
        must_change_password=False,
    )
    state = db.get(InstallationState, 1) or InstallationState(id=1)
    state.setup_completed = True
    state.setup_completed_at = datetime.now(timezone.utc)
    db.add_all([user, state])
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise UserServiceError(409, "A configuracao inicial desta instalacao ja foi concluida.") from exc
    db.refresh(user)
    log_audit(db, "installation_setup", user, "Administrador inicial criado", ip_address=ip_address)
    return user


def authenticate_user(
    db: Session,
    *,
    username: str,
    password: str,
    ip_address: str | None = None,
) -> User:
    user = db.query(User).filter(User.username == username).first()
    if not user:
        log_audit(
            db,
            "login_failed",
            username=username,
            details="Usuario nao existe",
            ip_address=ip_address,
        )
        raise UserServiceError(400, "Usuario ou senha incorretos.")

    if not user.is_active:
        log_audit(db, "login_failed", user=user, details="Usuario inativo", ip_address=ip_address)
        raise UserServiceError(400, "Usuario inativo.")

    now = datetime.now(timezone.utc)
    lockout_now = now
    if user.lockout_until and user.lockout_until.tzinfo is None:
        lockout_now = now.replace(tzinfo=None)
    if user.lockout_until and lockout_now < user.lockout_until:
        remaining_seconds = int((user.lockout_until - lockout_now).total_seconds())
        remaining_minutes = max(1, remaining_seconds // 60)
        log_audit(
            db,
            "login_failed_lockout",
            user=user,
            details=f"Tentativa durante bloqueio. Falta {remaining_minutes} min",
            ip_address=ip_address,
        )
        raise UserServiceError(
            403,
            f"Conta bloqueada por excesso de tentativas. Tente novamente em {remaining_minutes} minuto(s).",
        )

    if not verify_password(password, user.password_hash):
        user.login_attempts += 1
        details = f"Senha incorreta (tentativa {user.login_attempts}/5)"
        if user.login_attempts >= 5:
            user.lockout_until = now + timedelta(minutes=15)
            details = "Conta bloqueada por 15 minutos (5 tentativas incorretas)"
            log_audit(db, "account_locked", user=user, details=details, ip_address=ip_address)
        else:
            log_audit(db, "login_failed", user=user, details=details, ip_address=ip_address)
        db.commit()
        raise UserServiceError(400, "Usuario ou senha incorretos.")

    user.login_attempts = 0
    user.lockout_until = None
    db.commit()
    log_audit(
        db,
        "login_success",
        user=user,
        details="Login realizado com sucesso",
        ip_address=ip_address,
    )
    return user


def record_user_logout(
    db: Session,
    *,
    user: User,
    ip_address: str | None = None,
) -> None:
    """Registra o encerramento explícito de uma sessão web."""

    log_audit(
        db,
        "logout",
        user,
        "Logout realizado com sucesso",
        ip_address=ip_address,
    )


def create_user_account(
    db: Session,
    *,
    username: str,
    password: str,
    name: str | None,
    role: str,
    is_active: bool,
    actor: User,
    max_active_sessions: int | None = None,
    ip_address: str | None = None,
) -> User:
    if db.query(User).filter(User.username == username).first():
        raise UserServiceError(400, "Este nome de usuario ja esta em uso.")
    if role not in VALID_USER_ROLES:
        raise UserServiceError(400, "Papel de acesso invalido.")
    if not _actor_can_bypass_password_policy(actor):
        validate_password(password, username=username)

    user = User(
        username=username,
        password_hash=hash_password(password),
        name=name,
        role=role,
        is_active=is_active,
        max_active_sessions=_validate_session_limit(max_active_sessions),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    log_audit(
        db,
        "user_create",
        actor,
        f"Criou usuario: {user.username} ({user.role})",
        ip_address=ip_address,
    )
    return user


def update_user_account(
    db: Session,
    *,
    user_id: int,
    actor: User,
    password: str | None = None,
    name: str | None = None,
    role: str | None = None,
    is_active: bool | None = None,
    max_active_sessions: int | None | object = _UNSET,
    ip_address: str | None = None,
) -> User:
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise UserServiceError(404, "Usuario nao encontrado.")
    if role is not None and role not in VALID_USER_ROLES:
        raise UserServiceError(400, "Papel de acesso invalido.")

    changes: list[str] = []
    if password is not None:
        if not _actor_can_bypass_password_policy(actor):
            validate_password(password, username=user.username)
        user.password_hash = hash_password(password)
        user.must_change_password = False
        revoke_user_sessions(db, user.id)
        changes.append("senha alterada")
    if name is not None:
        user.name = name
        changes.append(f"nome={name}")
    if role is not None:
        if user.role == "admin" and user.is_active and role != "admin":
            other_admins = db.query(User).filter(
                User.role == "admin", User.is_active.is_(True), User.id != user.id
            ).count()
            if other_admins == 0:
                raise UserServiceError(400, "A instalacao deve manter ao menos um administrador ativo.")
        user.role = role
        changes.append(f"role={role}")
    if is_active is not None:
        if user_id == actor.id and not is_active:
            raise UserServiceError(400, "Voce nao pode desativar seu proprio usuario.")
        if user.role == "admin" and user.is_active and not is_active:
            other_admins = db.query(User).filter(
                User.role == "admin", User.is_active.is_(True), User.id != user.id
            ).count()
            if other_admins == 0:
                raise UserServiceError(400, "A instalacao deve manter ao menos um administrador ativo.")
        user.is_active = is_active
        if not is_active:
            revoke_user_sessions(db, user.id)
        changes.append(f"ativo={is_active}")
    if max_active_sessions is not _UNSET:
        normalized_limit = _validate_session_limit(max_active_sessions)
        user.max_active_sessions = normalized_limit
        trimmed_sessions = trim_user_sessions(db, user.id, normalized_limit)
        changes.append(
            f"limite_sessoes={normalized_limit or 'ilimitado'}"
            f"; sessoes_encerradas={trimmed_sessions}"
        )

    db.commit()
    db.refresh(user)
    log_audit(
        db,
        "user_update",
        actor,
        f"Alterou usuario {user.username}: {', '.join(changes)}",
        ip_address=ip_address,
    )
    return user


def delete_user_account(
    db: Session,
    *,
    user_id: int,
    actor: User,
    ip_address: str | None = None,
) -> None:
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise UserServiceError(404, "Usuario nao encontrado.")
    if user_id == actor.id:
        raise UserServiceError(400, "Voce nao pode excluir seu proprio usuario.")
    if user.role == "admin" and user.is_active:
        other_admins = db.query(User).filter(
            User.role == "admin", User.is_active.is_(True), User.id != user.id
        ).count()
        if other_admins == 0:
            raise UserServiceError(400, "A instalacao deve manter ao menos um administrador ativo.")

    username = user.username
    delete_user_sessions(db, user.id)
    db.delete(user)
    db.commit()
    log_audit(db, "user_delete", actor, f"Excluiu usuario: {username}", ip_address=ip_address)


def unlock_user_account(
    db: Session,
    *,
    user_id: int,
    actor: User,
    ip_address: str | None = None,
) -> User:
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise UserServiceError(404, "Usuario nao encontrado.")

    user.login_attempts = 0
    user.lockout_until = None
    db.commit()
    log_audit(
        db,
        "user_unlock",
        actor,
        f"Desbloqueou a conta do usuario: {user.username}",
        ip_address=ip_address,
    )
    return user


def update_own_profile(
    db: Session,
    *,
    actor: User,
    name: str | None = None,
    current_password: str | None = None,
    new_password: str | None = None,
    ip_address: str | None = None,
) -> User:
    if actor.id == 0:
        raise UserServiceError(400, "Nao e permitido alterar o perfil do usuario anonimo/bypass.")

    user = db.query(User).filter(User.id == actor.id).first()
    if not user:
        raise UserServiceError(404, "Usuario nao encontrado.")

    changes: list[str] = []
    if name is not None:
        user.name = name.strip()
        changes.append("nome atualizado")
    if new_password:
        if not current_password:
            raise UserServiceError(400, "Para alterar a senha, voce deve informar a senha atual.")
        if not verify_password(current_password, user.password_hash):
            raise UserServiceError(400, "Senha atual incorreta.")
        validate_password(new_password, username=user.username)
        if verify_password(new_password, user.password_hash):
            raise UserServiceError(400, "A nova senha deve ser diferente da senha atual.")
        user.password_hash = hash_password(new_password)
        user.must_change_password = False
        revoke_user_sessions(db, user.id)
        changes.append("senha alterada")

    if not changes:
        return user

    db.commit()
    db.refresh(user)
    log_audit(
        db,
        "profile_update",
        user,
        f"Atualizou seu proprio perfil: {', '.join(changes)}",
        ip_address=ip_address,
    )
    return user
