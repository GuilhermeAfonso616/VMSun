"""Identificacao da versao do servidor e do contrato do cliente operador."""

from __future__ import annotations

import os


SERVER_VERSION = os.getenv("ANALITICO_SERVER_VERSION", "0.6.54").strip() or "0.6.54"
SERVER_RELEASE_TAG = os.getenv("ANALITICO_SERVER_RELEASE_TAG", "onedrive-clip-playback").strip() or "onedrive-clip-playback"
SERVER_COMMIT = os.getenv("ANALITICO_SERVER_COMMIT", "").strip()
OPERATOR_API_VERSION = 1
RECOMMENDED_OPERATOR_CLIENT_VERSION = os.getenv(
    "ANALITICO_OPERATOR_CLIENT_VERSION",
    "0.6.32",
).strip() or "0.6.32"


def build_info_payload() -> dict[str, object]:
    return {
        "server_version": SERVER_VERSION,
        "server_release_tag": SERVER_RELEASE_TAG,
        "server_commit": SERVER_COMMIT or None,
        "operator_api_version": OPERATOR_API_VERSION,
        "recommended_operator_client_version": RECOMMENDED_OPERATOR_CLIENT_VERSION,
    }


def web_version_text() -> str:
    suffix = f" | {SERVER_RELEASE_TAG}" if SERVER_RELEASE_TAG else ""
    return f"Web v{SERVER_VERSION}{suffix}"
