"""Serviço de gerenciamento de permissões e visibilidade de recursos por perfil (DEV Only)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.core.config import settings

PERMISSIONS_FILE_PATH = Path("data/role_permissions.json")

DEFAULT_NAV_ITEMS = [
    {"key": "dashboard", "label": "Dashboard", "default_roles": ["admin", "supervisor", "dev"]},
    {"key": "health", "label": "Saúde do Sistema", "default_roles": ["admin", "supervisor", "dev"]},
    {"key": "alerts", "label": "Alertas Operacionais", "default_roles": ["admin", "supervisor", "operator", "dev"]},
    {"key": "notifications", "label": "Notificações / Webhooks", "default_roles": ["admin", "supervisor", "dev"]},
    {"key": "history", "label": "Histórico de Recursos", "default_roles": ["admin", "supervisor", "dev"]},
    {"key": "audit", "label": "Auditoria de Ações", "default_roles": ["admin", "supervisor", "dev"]},
    {"key": "availability", "label": "Relatórios de Disponibilidade", "default_roles": ["admin", "supervisor", "dev"]},
    {"key": "cameras", "label": "Gestão de Câmeras", "default_roles": ["admin", "supervisor", "dev"]},
    {"key": "nvr", "label": "Gestão de NVRs / Fontes", "default_roles": ["admin", "supervisor", "dev"]},
    {"key": "monitor", "label": "Monitoramento VMS", "default_roles": ["admin", "supervisor", "operator", "dev"]},
    {"key": "mosaics", "label": "Mosaicos VMS", "default_roles": ["admin", "supervisor", "operator", "dev"]},
    {"key": "new_camera", "label": "Nova Câmera", "default_roles": ["admin", "supervisor", "dev"]},
    {"key": "events", "label": "Central de Eventos", "default_roles": ["admin", "supervisor", "operator", "dev"]},
    {"key": "event_review", "label": "Validação de Eventos", "default_roles": ["admin", "supervisor", "operator", "dev"]},
    {"key": "audit_queue", "label": "Fila Silenciosa de Auditoria", "default_roles": ["admin", "supervisor", "dev"]},
    {"key": "lockdown", "label": "Envios Lockdown", "default_roles": ["admin", "supervisor", "dev"]},
    {"key": "storage", "label": "Armazenamento & Disco", "default_roles": ["admin", "supervisor", "dev"]},
    {"key": "diagnostics", "label": "Diagnóstico & Redes", "default_roles": ["admin", "supervisor", "dev"]},
    {"key": "sdk_test", "label": "Laboratório / Teste SDK", "default_roles": ["admin", "dev"]},
    {"key": "users", "label": "Gestão de Usuários", "default_roles": ["admin", "dev"]},
]

DEFAULT_ACTION_ITEMS = [
    {"key": "control_ptz", "label": "Controle PTZ no Monitor", "default_roles": ["admin", "supervisor", "operator", "dev"]},
    {"key": "create_manual_incident", "label": "Criar Incidente Manual", "default_roles": ["admin", "supervisor", "operator", "dev"]},
    {"key": "close_incident", "label": "Fechar / Encerrar Incidente", "default_roles": ["admin", "supervisor", "operator", "dev"]},
    {"key": "reopen_incident", "label": "Reabrir Incidente Fechado", "default_roles": ["admin", "supervisor", "dev"]},
    {"key": "approve_tuning", "label": "Aprovar Sugestões de Auto-Tuning", "default_roles": ["admin", "supervisor", "dev"]},
    {"key": "export_dossier", "label": "Exportar Dossiê de Evidências em PDF", "default_roles": ["admin", "supervisor", "operator", "dev"]},
    {"key": "manage_users", "label": "Criar/Editar Usuários", "default_roles": ["admin", "dev"]},
    {"key": "manage_notifications", "label": "Configurar Canais de Notificação", "default_roles": ["admin", "supervisor", "dev"]},
]

ROLES = ["operator", "supervisor", "admin", "dev"]
ROLE_LABELS = {
    "operator": "Operador",
    "supervisor": "Supervisor",
    "admin": "Administrador",
    "dev": "Desenvolvedor (DEV)",
}


def _build_default_matrix() -> dict[str, Any]:
    nav_matrix: dict[str, list[str]] = {}
    for item in DEFAULT_NAV_ITEMS:
        nav_matrix[item["key"]] = list(item["default_roles"])

    action_matrix: dict[str, list[str]] = {}
    for item in DEFAULT_ACTION_ITEMS:
        action_matrix[item["key"]] = list(item["default_roles"])

    return {
        "nav": nav_matrix,
        "actions": action_matrix,
    }


def load_role_permissions_matrix() -> dict[str, Any]:
    """Carrega a matriz de permissões atualizada do arquivo JSON ou constrói o padrão."""
    if not PERMISSIONS_FILE_PATH.exists():
        matrix = _build_default_matrix()
        save_role_permissions_matrix(matrix)
        return matrix

    try:
        data = json.loads(PERMISSIONS_FILE_PATH.read_text(encoding="utf-8"))
        default = _build_default_matrix()

        # Garante integridade de chaves novas
        nav_matrix = data.get("nav", {})
        for item in DEFAULT_NAV_ITEMS:
            if item["key"] not in nav_matrix:
                nav_matrix[item["key"]] = list(item["default_roles"])

        action_matrix = data.get("actions", {})
        for item in DEFAULT_ACTION_ITEMS:
            if item["key"] not in action_matrix:
                action_matrix[item["key"]] = list(item["default_roles"])

        # O perfil DEV sempre mantém todas as permissões para evitar lock-out
        for k in nav_matrix:
            if "dev" not in nav_matrix[k]:
                nav_matrix[k].append("dev")
        for k in action_matrix:
            if "dev" not in action_matrix[k]:
                action_matrix[k].append("dev")

        return {"nav": nav_matrix, "actions": action_matrix}
    except Exception:
        return _build_default_matrix()


def save_role_permissions_matrix(matrix: dict[str, Any]) -> None:
    """Salva a matriz de permissões editada pelo usuário DEV."""
    PERMISSIONS_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Garantir que o perfil 'dev' nunca perca acesso aos módulos críticos
    nav_matrix = matrix.get("nav", {})
    action_matrix = matrix.get("actions", {})

    for k in nav_matrix:
        if isinstance(nav_matrix[k], list) and "dev" not in nav_matrix[k]:
            nav_matrix[k].append("dev")

    for k in action_matrix:
        if isinstance(action_matrix[k], list) and "dev" not in action_matrix[k]:
            action_matrix[k].append("dev")

    clean_matrix = {
        "nav": nav_matrix,
        "actions": action_matrix,
    }
    PERMISSIONS_FILE_PATH.write_text(json.dumps(clean_matrix, indent=2, ensure_ascii=False), encoding="utf-8")


def is_nav_item_visible(role: str, nav_key: str) -> bool:
    """Verifica se um item da navegação deve ser exibido para a função/perfil especificada."""
    if role == "dev":
        return True
    matrix = load_role_permissions_matrix()
    allowed_roles = matrix.get("nav", {}).get(nav_key, [])
    return role in allowed_roles


def is_action_allowed(role: str, action_key: str) -> bool:
    """Verifica se uma ação operacional é permitida para a função/perfil especificada."""
    if role == "dev":
        return True
    matrix = load_role_permissions_matrix()
    allowed_roles = matrix.get("actions", {}).get(action_key, [])
    return role in allowed_roles
