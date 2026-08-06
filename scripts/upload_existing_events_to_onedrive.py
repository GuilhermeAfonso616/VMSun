from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import settings, sqlite_url_for  # noqa: E402
from app.core.timezone import utc_now_naive  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sobe eventos existentes, snapshots e clips locais para a pasta do app no OneDrive."
    )
    parser.add_argument("--database", help="Caminho do analytics.db. Padrao: data/analytics.db.")
    parser.add_argument("--client-id", default=settings.onedrive_client_id, help="Application/client ID do Entra.")
    parser.add_argument("--tenant", default=settings.onedrive_tenant, help="Tenant ID/dominio do Entra.")
    parser.add_argument("--token-file", default=settings.onedrive_token_file, help="Arquivo onedrive_token.json.")
    parser.add_argument("--prefix", default=settings.onedrive_audit_prefix, help="Prefixo dos arquivos no OneDrive.")
    parser.add_argument("--limit", type=int, help="Limita a quantidade de eventos processados.")
    parser.add_argument("--since", help="Processa eventos criados a partir desta data ISO, ex: 2026-06-01.")
    parser.add_argument("--force", action="store_true", help="Reenvia artefatos mesmo quando o campo remoto ja esta uploaded.")
    parser.add_argument("--dry-run", action="store_true", help="Mostra o que seria enviado, sem fazer upload nem atualizar banco.")
    return parser.parse_args()


def configure_runtime(args: argparse.Namespace) -> None:
    if args.database:
        settings.database_url = sqlite_url_for(Path(args.database))
    settings.onedrive_clip_archive_enabled = True
    settings.onedrive_client_id = str(args.client_id or "").strip()
    settings.onedrive_tenant = str(args.tenant or "").strip()
    settings.onedrive_token_file = str(args.token_file or "").strip()
    settings.onedrive_audit_prefix = str(args.prefix or "audit_pending").strip() or "audit_pending"


def parse_since(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise SystemExit(f"Data invalida em --since: {value}") from exc


def resolve_local_path(value: str | None) -> Path | None:
    if not value:
        return None
    raw = str(value).strip().replace("\\", "/")
    if not raw:
        return None
    if raw.startswith("/data/"):
        candidate = PROJECT_ROOT / "data" / raw.removeprefix("/data/")
    else:
        path = Path(value)
        candidate = path if path.is_absolute() else PROJECT_ROOT / path
    try:
        return candidate.resolve()
    except Exception:
        return candidate


def parse_json(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except Exception:
        return value


def event_json_payload(event: Any) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "event": {
            "id": event.id,
            "camera_id": event.camera_id,
            "event_type": event.event_type,
            "rule_id": event.rule_id,
            "track_id": event.track_id,
            "severity": event.severity,
            "status": event.status,
            "lifecycle_action": event.lifecycle_action,
            "alarm_category": event.alarm_category,
            "alarm_eligible": event.alarm_eligible,
            "is_alarm_active": event.is_alarm_active,
            "started_at": event.started_at,
            "ended_at": event.ended_at,
            "created_at": event.created_at,
            "confidence": event.confidence,
            "event_score": event.event_score,
            "detector_score": event.detector_score,
            "details": event.details,
            "correlation_key": event.correlation_key,
        },
        "evidence": {
            "bbox": parse_json(event.bbox_json),
            "zone_id": event.zone_id,
            "roi_id": event.roi_id,
            "scene_profile": event.scene_profile,
            "camera_family": event.camera_family,
        },
        "artifacts": {
            "snapshot_path": event.snapshot_path,
            "clip_path": event.clip_path,
            "clip_remote_web_url": getattr(event, "clip_remote_web_url", None),
            "snapshot_remote_web_url": getattr(event, "snapshot_remote_web_url", None),
        },
        "profile_snapshot": parse_json(event.active_profile_snapshot),
        "threshold_snapshot": parse_json(event.threshold_snapshot),
        "nuisance_profile_snapshot": parse_json(event.nuisance_profile_snapshot),
    }


def should_upload(status: str | None, *, force: bool) -> bool:
    return force or str(status or "").lower() != "uploaded"


def main() -> int:
    args = parse_args()
    configure_runtime(args)

    from app.db.models import Event  # noqa: WPS433
    from app.db.base import SessionLocal  # noqa: WPS433
    from app.services.db_migrations import ensure_runtime_schema  # noqa: WPS433
    from app.services.onedrive_client import OneDriveClient  # noqa: WPS433

    ensure_runtime_schema()
    client = OneDriveClient()
    if not args.dry_run and not client.enabled():
        raise SystemExit("OneDrive desativado: informe --client-id e --token-file validos.")

    since = parse_since(args.since)
    db = SessionLocal()
    counters = {
        "events_seen": 0,
        "event_json_uploaded": 0,
        "snapshot_uploaded": 0,
        "clip_uploaded": 0,
        "missing_snapshot": 0,
        "missing_clip": 0,
        "failed": 0,
        "skipped_uploaded": 0,
    }
    try:
        query = db.query(Event).order_by(Event.id.asc())
        if since is not None:
            query = query.filter(Event.created_at >= since)
        if args.limit:
            query = query.limit(max(1, int(args.limit)))

        for event in query.all():
            counters["events_seen"] += 1
            event_id = int(event.id)
            changed = False

            if should_upload(getattr(event, "snapshot_remote_status", None), force=args.force):
                snapshot_file = resolve_local_path(event.snapshot_path)
                if snapshot_file and snapshot_file.exists():
                    if not args.dry_run:
                        try:
                            remote = client.upload_audit_snapshot(event_id=event_id, snapshot_file=snapshot_file)
                            event.snapshot_remote_item_id = remote.get("item_id")
                            event.snapshot_remote_web_url = remote.get("web_url")
                            event.snapshot_remote_status = "uploaded"
                            event.snapshot_remote_uploaded_at = utc_now_naive()
                            changed = True
                        except Exception as exc:
                            event.snapshot_remote_status = "failed"
                            changed = True
                            counters["failed"] += 1
                            print(f"[erro] evento {event_id}: snapshot: {exc}")
                    counters["snapshot_uploaded"] += 1
                elif event.snapshot_path:
                    counters["missing_snapshot"] += 1
            else:
                counters["skipped_uploaded"] += 1

            if should_upload(getattr(event, "clip_remote_status", None), force=args.force):
                clip_dir = resolve_local_path(event.clip_path)
                clip_file = clip_dir / "clip.mp4" if clip_dir else None
                if clip_file and clip_file.exists():
                    if not args.dry_run:
                        try:
                            remote = client.upload_audit_clip(event_id=event_id, clip_file=clip_file)
                            event.clip_remote_item_id = remote.get("item_id")
                            event.clip_remote_web_url = remote.get("web_url")
                            event.clip_remote_status = "uploaded"
                            event.clip_remote_uploaded_at = utc_now_naive()
                            changed = True
                        except Exception as exc:
                            event.clip_remote_status = "failed"
                            changed = True
                            counters["failed"] += 1
                            print(f"[erro] evento {event_id}: clip: {exc}")
                    counters["clip_uploaded"] += 1
                elif event.clip_path:
                    counters["missing_clip"] += 1
            else:
                counters["skipped_uploaded"] += 1

            if should_upload(getattr(event, "event_remote_status", None), force=args.force):
                if not args.dry_run:
                    try:
                        remote = client.upload_audit_event(event_id=event_id, event_payload=event_json_payload(event))
                        event.event_remote_item_id = remote.get("item_id")
                        event.event_remote_web_url = remote.get("web_url")
                        event.event_remote_status = "uploaded"
                        event.event_remote_uploaded_at = utc_now_naive()
                        changed = True
                    except Exception as exc:
                        event.event_remote_status = "failed"
                        changed = True
                        counters["failed"] += 1
                        print(f"[erro] evento {event_id}: json: {exc}")
                counters["event_json_uploaded"] += 1
            else:
                counters["skipped_uploaded"] += 1

            if changed:
                db.commit()

        if args.dry_run:
            db.rollback()
    finally:
        db.close()

    mode = "DRY-RUN" if args.dry_run else "UPLOAD"
    print(f"[{mode}] resumo:")
    for key, value in counters.items():
        print(f"  {key}: {value}")
    return 0 if counters["failed"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
