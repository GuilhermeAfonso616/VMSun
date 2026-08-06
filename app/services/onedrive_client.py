from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
from typing import Any

import requests

from app.core.config import settings


GRAPH_ROOT = "https://graph.microsoft.com/v1.0"


class OneDriveClient:
    def __init__(self):
        self.token_file = Path(settings.onedrive_token_file)

    @property
    def upload_toggle_file(self) -> Path:
        return self.token_file.parent / "onedrive_upload_enabled.json"

    def _load_archive_enabled_override(self) -> bool | None:
        if not self.upload_toggle_file.exists():
            return None
        try:
            payload = json.loads(self.upload_toggle_file.read_text(encoding="utf-8"))
            if isinstance(payload, dict) and isinstance(payload.get("enabled"), bool):
                return bool(payload["enabled"])
        except Exception:
            return None
        return None

    def archive_enabled(self) -> bool:
        override = self._load_archive_enabled_override()
        if override is not None:
            return override
        return bool(settings.onedrive_clip_archive_enabled)

    def set_archive_enabled(self, enabled: bool) -> dict[str, Any]:
        value = bool(enabled)
        settings.onedrive_clip_archive_enabled = value
        self.upload_toggle_file.parent.mkdir(parents=True, exist_ok=True)
        self.upload_toggle_file.write_text(
            json.dumps(
                {"enabled": value, "updated_at": datetime.now(timezone.utc).isoformat()},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return self.status(refresh_if_needed=value)

    def enabled(self) -> bool:
        return bool(self.archive_enabled() and settings.onedrive_client_id)

    def status(self, *, refresh_if_needed: bool = False) -> dict[str, Any]:
        token_exists = self.token_file.exists()
        payload: dict[str, Any] = {}
        token_error: str | None = None
        refresh_error: str | None = None
        if token_exists:
            try:
                payload = self._load_token_payload()
                if refresh_if_needed:
                    try:
                        self._access_token()
                        payload = self._load_token_payload()
                    except Exception as exc:
                        refresh_error = str(exc)
            except Exception as exc:
                token_error = str(exc)

        expires_at = self._expires_at(payload) if payload else None
        has_refresh_token = bool(payload.get("refresh_token"))
        return {
            "enabled": self.enabled(),
            "archive_enabled": self.archive_enabled(),
            "client_id_configured": bool(settings.onedrive_client_id),
            "token_exists": token_exists,
            "token_path": str(self.token_file),
            "token_error": token_error,
            "has_access_token": bool(payload.get("access_token")),
            "has_refresh_token": has_refresh_token,
            "refresh_enabled": bool(settings.onedrive_client_id and has_refresh_token),
            "refresh_error": refresh_error,
            "expires_at": expires_at.isoformat() if expires_at else None,
        }

    def save_token_text(self, token_text: str) -> dict[str, Any]:
        raw = str(token_text or "").strip()
        if not raw:
            raise ValueError("onedrive_token_empty")

        if raw.lower().startswith("bearer "):
            raw = raw[7:].strip()

        if raw.startswith("{"):
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("onedrive_token_invalid")
        else:
            payload = {"refresh_token": raw}

        if not payload.get("access_token") and not payload.get("refresh_token"):
            raise ValueError("onedrive_token_missing_access_or_refresh")

        self._save_token_payload(payload)
        return self.status()

    def _load_token_payload(self) -> dict[str, Any]:
        if not self.token_file.exists():
            raise RuntimeError("onedrive_token_missing")
        payload = json.loads(self.token_file.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise RuntimeError("onedrive_token_invalid")
        return payload

    def _save_token_payload(self, payload: dict[str, Any]) -> None:
        self.token_file.parent.mkdir(parents=True, exist_ok=True)
        self.token_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _expires_at(payload: dict[str, Any]) -> datetime | None:
        value = payload.get("expires_at")
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value))
        except Exception:
            return None

    def _refresh_token(self, payload: dict[str, Any]) -> dict[str, Any]:
        refresh_token = payload.get("refresh_token")
        if not refresh_token:
            raise RuntimeError("onedrive_refresh_token_missing")
        response = requests.post(
            f"https://login.microsoftonline.com/{settings.onedrive_tenant}/oauth2/v2.0/token",
            data={
                "client_id": settings.onedrive_client_id,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "scope": "offline_access Files.ReadWrite.AppFolder",
            },
            timeout=float(settings.onedrive_upload_timeout_seconds),
        )
        response.raise_for_status()
        refreshed = response.json()
        expires_in = int(refreshed.get("expires_in") or 3600)
        refreshed["expires_at"] = (datetime.now(timezone.utc) + timedelta(seconds=max(60, expires_in - 60))).isoformat()
        if not refreshed.get("refresh_token"):
            refreshed["refresh_token"] = refresh_token
        self._save_token_payload(refreshed)
        return refreshed

    def _access_token(self) -> str:
        payload = self._load_token_payload()
        expires_at = self._expires_at(payload)
        if not payload.get("access_token") or expires_at is None or expires_at <= datetime.now(timezone.utc):
            payload = self._refresh_token(payload)
        return str(payload["access_token"])

    def _request_with_access_token(self, method: str, url: str, **kwargs) -> requests.Response:
        headers = dict(kwargs.pop("headers", {}) or {})
        headers["Authorization"] = f"Bearer {self._access_token()}"
        response = requests.request(method, url, headers=headers, **kwargs)
        if response.status_code != 401:
            return response

        payload = self._load_token_payload()
        if not payload.get("refresh_token"):
            return response

        refreshed = self._refresh_token(payload)
        retry_headers = dict(headers)
        retry_headers["Authorization"] = f"Bearer {refreshed['access_token']}"
        return requests.request(method, url, headers=retry_headers, **kwargs)

    def _audit_filename(self, *, event_id: int, suffix: str, extension: str) -> str:
        prefix = str(settings.onedrive_audit_prefix or "audit_pending").strip("_") or "audit_pending"
        clean_suffix = str(suffix or "artifact").strip("_") or "artifact"
        clean_extension = str(extension or "bin").lstrip(".")
        return f"{prefix}_event_{event_id}_{clean_suffix}.{clean_extension}"

    def _upload_bytes(self, *, filename: str, content: bytes, content_type: str) -> dict[str, Any]:
        if not self.enabled():
            raise RuntimeError("onedrive_disabled")
        response = self._request_with_access_token(
            "PUT",
            f"{GRAPH_ROOT}/me/drive/special/approot:/{filename}:/content",
            headers={"Content-Type": content_type},
            data=content,
            timeout=float(settings.onedrive_upload_timeout_seconds),
        )
        response.raise_for_status()
        payload = response.json()
        return {
            "item_id": payload.get("id"),
            "web_url": payload.get("webUrl"),
            "name": payload.get("name"),
        }

    def upload_audit_clip(self, *, event_id: int, clip_file: Path) -> dict[str, Any]:
        if not clip_file.exists():
            raise FileNotFoundError(str(clip_file))
        filename = self._audit_filename(event_id=event_id, suffix="clip", extension="mp4")
        return self._upload_bytes(
            filename=filename,
            content=clip_file.read_bytes(),
            content_type="video/mp4",
        )

    def upload_audit_snapshot(self, *, event_id: int, snapshot_file: Path) -> dict[str, Any]:
        if not snapshot_file.exists():
            raise FileNotFoundError(str(snapshot_file))
        extension = snapshot_file.suffix.lstrip(".") or "jpg"
        content_type = "image/png" if extension.lower() == "png" else "image/jpeg"
        filename = self._audit_filename(event_id=event_id, suffix="snapshot", extension=extension)
        return self._upload_bytes(
            filename=filename,
            content=snapshot_file.read_bytes(),
            content_type=content_type,
        )

    def upload_audit_event(self, *, event_id: int, event_payload: dict[str, Any]) -> dict[str, Any]:
        filename = self._audit_filename(event_id=event_id, suffix="event", extension="json")
        content = json.dumps(event_payload, ensure_ascii=False, indent=2, default=str).encode("utf-8")
        return self._upload_bytes(
            filename=filename,
            content=content,
            content_type="application/json; charset=utf-8",
        )

    def upload_operator_performance_log(self, *, filename: str, payload: dict[str, Any]) -> dict[str, Any]:
        content = json.dumps(payload, ensure_ascii=False, indent=2, default=str).encode("utf-8")
        return self._upload_bytes(
            filename=filename,
            content=content,
            content_type="application/json; charset=utf-8",
        )

    def item_download_url(self, item_id: str) -> str:
        if not self.enabled() or not item_id:
            raise RuntimeError("onedrive_disabled")
        response = self._request_with_access_token(
            "GET",
            f"{GRAPH_ROOT}/me/drive/items/{item_id}",
            params={"select": "id,name,@microsoft.graph.downloadUrl"},
            timeout=float(settings.onedrive_upload_timeout_seconds),
        )
        response.raise_for_status()
        payload = response.json()
        download_url = str(payload.get("@microsoft.graph.downloadUrl") or "").strip()
        if not download_url:
            raise RuntimeError("onedrive_download_url_missing")
        return download_url

    def delete_item(self, item_id: str) -> bool:
        if not self.enabled() or not item_id:
            return False
        response = self._request_with_access_token(
            "DELETE",
            f"{GRAPH_ROOT}/me/drive/items/{item_id}",
            timeout=float(settings.onedrive_upload_timeout_seconds),
        )
        if response.status_code == 404:
            return True
        response.raise_for_status()
        return response.status_code == 204


onedrive_client = OneDriveClient()
