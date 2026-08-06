"""Modelos SQLAlchemy do VMSun.

Customer: Empresa / Cliente final
Site: Instalação física
Nvr: Gravador de Vídeo de Rede (Intelbras, Dahua, Hikvision, Genérico)
Camera: Câmera individual ou Canal do NVR
CameraGroup: Grupos operacionais (Perímetro, Acessos, etc.)
User: Usuário e permissões
AuditLog & ViewPreset: Logs e layouts operacionais
"""

from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Text, Boolean, UniqueConstraint, Table, text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.core.credential_crypto import EncryptedString
from app.db.base import Base


class Customer(Base):
    """Cliente / Empresa atendida."""

    __tablename__ = "customers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, index=True)
    code = Column(String(50), nullable=True, unique=True)
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True, server_default=text("true"))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    sites = relationship("Site", back_populates="customer", cascade="all, delete-orphan")


class Site(Base):
    """Local / Instalação física do cliente (ex: Matriz, Filial, Depósito)."""

    __tablename__ = "sites"

    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id", ondelete="CASCADE"), nullable=True, index=True)
    name = Column(String, nullable=False, index=True)
    code = Column(String(50), nullable=True)
    address = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True, server_default=text("true"))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    customer = relationship("Customer", back_populates="sites")
    nvrs = relationship("Nvr", back_populates="site", cascade="all, delete-orphan")
    cameras = relationship("Camera", back_populates="site")


class Nvr(Base):
    """Gravador de Vídeo em Rede (NVR / DVR)."""

    __tablename__ = "nvrs"

    id = Column(Integer, primary_key=True, index=True)
    site_id = Column(Integer, ForeignKey("sites.id", ondelete="CASCADE"), nullable=True, index=True)
    name = Column(String, nullable=False, index=True)
    host = Column(String, nullable=False)  # IP ou Hostname
    port = Column(Integer, nullable=False, default=80)
    rtsp_port = Column(Integer, nullable=False, default=554)
    sdk_port = Column(Integer, nullable=True)
    vendor = Column(String(50), nullable=False, default="generic")  # hikvision, dahua, intelbras, generic
    model = Column(String(100), nullable=True)
    username = Column(String, nullable=False)
    password = Column(EncryptedString, nullable=False)
    total_channels = Column(Integer, nullable=False, default=16)
    status = Column(String(32), nullable=False, default="offline")
    last_seen_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    site = relationship("Site", back_populates="nvrs")
    channels = relationship("Camera", back_populates="nvr")


# Tabela de associação n:n entre Câmeras e Grupos Operacionais
camera_group_members = Table(
    "camera_group_members",
    Base.metadata,
    Column("group_id", Integer, ForeignKey("camera_groups.id", ondelete="CASCADE"), primary_key=True),
    Column("camera_id", Integer, ForeignKey("cameras.id", ondelete="CASCADE"), primary_key=True),
)


class CameraGroup(Base):
    """Grupo lógico de câmeras (ex: Perímetro, Acessos, Câmeras Críticas)."""

    __tablename__ = "camera_groups"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, index=True)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    cameras = relationship("Camera", secondary=camera_group_members, back_populates="groups")


class Camera(Base):
    """Câmera individual ou Canal lógico do NVR."""

    __tablename__ = "cameras"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    ip = Column(String, nullable=False)
    onvif_port = Column(Integer, default=80)
    username = Column(String, nullable=False)
    password = Column(EncryptedString, nullable=False)
    manufacturer = Column(String(120), nullable=False, default="Nao informada")
    model = Column(String(120), nullable=True)

    # Hierarquia física do VMSun
    customer_id = Column(Integer, ForeignKey("customers.id", ondelete="SET NULL"), nullable=True, index=True)
    site_id = Column(Integer, ForeignKey("sites.id", ondelete="SET NULL"), nullable=True, index=True)
    nvr_id = Column(Integer, ForeignKey("nvrs.id", ondelete="SET NULL"), nullable=True, index=True)
    channel_number = Column(Integer, nullable=True)

    # Perfis de Stream (Main Stream vs Substream)
    rtsp_url = Column(EncryptedString, nullable=True)       # Main Stream URL
    sub_rtsp_url = Column(EncryptedString, nullable=True)   # Substream URL

    status = Column(String, default="idle")
    source_type = Column(String, nullable=True, default="camera")  # "camera" ou "nvr_channel"
    source_parent_id = Column(Integer, nullable=True)
    source_channel = Column(Integer, nullable=True)
    source_stream_kind = Column(String, nullable=True)
    source_brand = Column(String, nullable=True)
    source_provider = Column(String, nullable=True)

    site_name = Column(String, nullable=True)
    group_name = Column(String, nullable=True)
    camera_priority = Column(String, nullable=True, default="medium")
    auto_start_enabled = Column(Boolean, nullable=True, default=False)

    # Soft delete
    is_deleted = Column(Boolean, nullable=False, default=False, index=True)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    site = relationship("Site", back_populates="cameras")
    nvr = relationship("Nvr", back_populates="channels")
    groups = relationship("CameraGroup", secondary=camera_group_members, back_populates="cameras")


class CameraPtzProfile(Base):
    """Perfil e capacidade PTZ da câmera."""

    __tablename__ = "camera_ptz_profiles"

    id = Column(Integer, primary_key=True, index=True)
    camera_id = Column(Integer, ForeignKey("cameras.id"), nullable=False, unique=True, index=True)
    status = Column(String(32), nullable=False, default="unknown", server_default="unknown")
    detected_brand = Column(String(32), nullable=True)
    selected_backend = Column(String(32), nullable=True)
    fallback_backend = Column(String(32), nullable=True)
    control_port = Column(Integer, nullable=True)
    channel = Column(Integer, nullable=True)
    profile_token = Column(String(255), nullable=True)
    supports_pan_tilt = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    supports_zoom = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    supports_presets = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    continuous_move = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    configuration_fingerprint = Column(String(64), nullable=True)
    failure_count = Column(Integer, nullable=False, default=0, server_default=text("0"))
    last_error_category = Column(String(64), nullable=True)
    last_error = Column(Text, nullable=True)
    diagnostics_json = Column(Text, nullable=True)
    last_verified_at = Column(DateTime(timezone=True), nullable=True)
    last_success_at = Column(DateTime(timezone=True), nullable=True)
    next_retry_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class User(Base):
    """Usuário do VMSun."""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    name = Column(String, nullable=True)
    role = Column(String, nullable=False, default="operator")  # "admin", "supervisor", "operator", "viewer"
    is_active = Column(Boolean, default=True)
    must_change_password = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    login_attempts = Column(Integer, nullable=False, default=0, server_default=text("0"))
    lockout_until = Column(DateTime(timezone=True), nullable=True)
    max_active_sessions = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class UserPermission(Base):
    """Permissão granular de usuário por escopo (Cliente, Local, NVR, Câmera ou Grupo)."""

    __tablename__ = "user_permissions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id", ondelete="CASCADE"), nullable=True, index=True)
    site_id = Column(Integer, ForeignKey("sites.id", ondelete="CASCADE"), nullable=True, index=True)
    nvr_id = Column(Integer, ForeignKey("nvrs.id", ondelete="CASCADE"), nullable=True, index=True)
    camera_id = Column(Integer, ForeignKey("cameras.id", ondelete="CASCADE"), nullable=True, index=True)
    group_id = Column(Integer, ForeignKey("camera_groups.id", ondelete="CASCADE"), nullable=True, index=True)
    can_view = Column(Boolean, nullable=False, default=True, server_default=text("true"))
    can_control_ptz = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    can_manage = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class InstallationState(Base):
    __tablename__ = "installation_state"

    id = Column(Integer, primary_key=True, default=1)
    setup_completed = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    setup_completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class UserSession(Base):
    __tablename__ = "user_sessions"

    id = Column(String, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True, index=True)
    last_seen_at = Column(DateTime(timezone=True), nullable=True)
    ip_address = Column(String, nullable=True)
    user_agent = Column(String, nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    user_id = Column(Integer, nullable=True, index=True)
    username = Column(String, nullable=True, index=True)
    action = Column(String, nullable=False, index=True)
    details = Column(Text, nullable=True)
    ip_address = Column(String, nullable=True)


class ViewPreset(Base):
    __tablename__ = "view_presets"

    id = Column(String, primary_key=True, index=True)
    owner_user_id = Column(Integer, nullable=False, index=True)
    name = Column(String, nullable=False)
    grid_size = Column(Integer, nullable=False, default=16)
    camera_ids = Column(String, nullable=False)  # JSON string: '[1, 2, null, 4]'
    hide_offline = Column(Boolean, default=False)
    view_config_json = Column(Text, nullable=True)
    is_shared = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class TemporalSequence(Base):
    __tablename__ = "temporal_sequences"

    id = Column(String, primary_key=True, index=True)
    owner_user_id = Column(Integer, nullable=False, index=True)
    name = Column(String, nullable=False)
    steps = Column(String, nullable=False)  # JSON string: '[{"viewId": "view_123", "duration": 8}]'
    is_shared = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    created_at = Column(DateTime(timezone=True), server_default=func.now())


# Stubs de compatibilidade para imports legado
class Event(Base):
    __tablename__ = "events_legacy_stub"
    id = Column(Integer, primary_key=True)

class EventFeedback(Base):
    __tablename__ = "event_feedbacks_legacy_stub"
    id = Column(Integer, primary_key=True)

class TuningSuggestion(Base):
    __tablename__ = "tuning_suggestions_legacy_stub"
    id = Column(Integer, primary_key=True)

class ConfigVersionHistory(Base):
    __tablename__ = "config_version_history_stub"
    id = Column(Integer, primary_key=True)

class NotificationChannel(Base):
    __tablename__ = "notification_channels_stub"
    id = Column(Integer, primary_key=True)

class NotificationDelivery(Base):
    __tablename__ = "notification_deliveries_stub"
    id = Column(Integer, primary_key=True)

class Incident(Base):
    __tablename__ = "incidents_stub"
    id = Column(Integer, primary_key=True)

class LockdownDelivery(Base):
    __tablename__ = "lockdown_deliveries_stub"
    id = Column(Integer, primary_key=True)
