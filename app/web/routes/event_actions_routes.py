from __future__ import annotations

import os
import json
from pathlib import Path

import cv2
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, Response

from app.analytics_v2.revalidation.person_crop_revalidator import reset_person_crop_revalidator
from app.core.config import settings
from app.core.logging import get_logger
from app.db.base import SessionLocal
from app.db.models import Camera, Event, User
from app.services.event_snapshot_store import EventSnapshotStore, event_snapshot_store
from app.services.feedback_tuning_service import (
    generate_policy_suggestions,
)
from app.services.event_service import (
    EventServiceError,
    record_event_feedback,
)
from app.services.incident_service import (
    acknowledge_incident,
    add_incident_comment,
    assign_incident,
    close_incident,
    correlate_incident_events,
    create_manual_incident,
    reopen_incident,
    update_incident_details,
)
from app.services.onedrive_client import onedrive_client
from app.services.revalidator_policy_store import normalize_revalidator_mode, save_revalidator_policy
from app.web.infrastructure import require_web_auth


router = APIRouter()
logger = get_logger("app.web.event_actions")

_AUTHORIZED_ROLES = ["admin", "supervisor", "operator"]


def _redirect_back(request: Request, fallback: str) -> str:
    return request.headers.get("referer") or fallback


def _set_person_revalidator_mode(mode: str) -> str:
    selected = normalize_revalidator_mode(mode)
    save_revalidator_policy(selected)
    os.environ["PERSON_REVALIDATOR_MODE"] = selected
    settings.person_revalidator_mode = selected
    reset_person_crop_revalidator()
    return selected


def _parse_bbox_json(value: str | None) -> list[float] | None:
    if not value:
        return None
    try:
        data = json.loads(value)
        if isinstance(data, (list, tuple)) and len(data) == 4:
            return [float(item) for item in data]
    except Exception:
        return None
    return None


def _snapshot_display_response(snapshot_file: Path, bbox_json: str | None, *, clean: bool = False):
    if clean:
        return FileResponse(snapshot_file)

    bbox = _parse_bbox_json(bbox_json)
    if not bbox:
        return FileResponse(snapshot_file)

    frame = cv2.imread(str(snapshot_file))
    if frame is None:
        return FileResponse(snapshot_file)

    output = EventSnapshotStore.draw_bbox(frame, bbox, color=(0, 165, 255), thickness=2)
    ok, encoded = cv2.imencode(".jpg", output, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
    if not ok:
        return FileResponse(snapshot_file)

    return Response(
        content=encoded.tobytes(),
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )


def _browser_compatible_clip_file(clip_dir: Path, clip_file: Path, metadata_path: Path) -> Path:
    metadata = {}
    if metadata_path.exists():
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except Exception:
            metadata = {}

    if str(metadata.get("video_codec") or "").lower() == "h264":
        return clip_file

    browser_name = str(metadata.get("browser_video_file") or "clip_browser.mp4")
    browser_file = clip_dir / browser_name
    if browser_file.exists() and browser_file.stat().st_size > 0:
        return browser_file

    if event_snapshot_store._transcode_browser_mp4(clip_file, browser_file):
        metadata["browser_video_file"] = browser_file.name
        metadata["browser_video_codec"] = "h264"
        try:
            metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.warning(
                "Falha ao atualizar metadata do clipe browser",
                extra={"action": "event_clip_metadata_update", "status": "error", "reason": exc.__class__.__name__},
            )
        return browser_file

    return clip_file


def _remote_clip_redirect(event: Event):
    if str(getattr(event, "clip_remote_status", "") or "").lower() != "uploaded":
        raise HTTPException(status_code=404, detail="Arquivo de clipe nao encontrado")

    item_id = str(getattr(event, "clip_remote_item_id", "") or "").strip()
    if not item_id:
        raise HTTPException(status_code=404, detail="Clipe remoto sem item_id")

    try:
        download_url = onedrive_client.item_download_url(item_id)
    except Exception as exc:
        logger.exception(
            "Failed to resolve OneDrive clip download URL event_id=%s",
            getattr(event, "id", "-"),
            extra={
                "event_id": getattr(event, "id", "-"),
                "action": "event_clip_onedrive_download_url_failed",
                "status": "error",
                "reason": exc.__class__.__name__,
            },
        )
        raise HTTPException(status_code=502, detail="Clipe remoto indisponivel") from exc

    return RedirectResponse(
        url=download_url,
        status_code=307,
        headers={"Cache-Control": "private, max-age=300"},
    )


def _valid_local_file(path: Path | None) -> bool:
    if path is None or not path.exists() or not path.is_file():
        return False
    try:
        return path.stat().st_size > 0
    except OSError:
        return False


@router.post("/events/incidents/manual")
def create_manual_incident_web(
    request: Request,
    camera_id: int = Form(...),
    title: str = Form(...),
    description: str = Form(""),
    priority: str = Form("medium"),
    team: str = Form(""),
    assignee_user_id: str = Form(""),
    current_user: User = Depends(require_web_auth(_AUTHORIZED_ROLES)),
):
    db = SessionLocal()
    try:
        try:
            target_id = int(assignee_user_id) if assignee_user_id.strip() else None
            create_manual_incident(
                db,
                current_user,
                camera_id=camera_id,
                title=title,
                description=description,
                priority=priority,
                team=team,
                assignee_user_id=target_id,
            )
        except (ValueError, EventServiceError) as exc:
            if isinstance(exc, EventServiceError):
                raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
            raise HTTPException(status_code=400, detail="Responsavel invalido.") from exc
        return RedirectResponse(url="/events?only_open=1", status_code=303)
    finally:
        db.close()


@router.post("/events/{event_id}/ack")
def acknowledge_event(
    request: Request,
    event_id: int,
    current_user: User = Depends(require_web_auth(_AUTHORIZED_ROLES)),
):
    db = SessionLocal()
    try:
        try:
            acknowledge_incident(db, event_id, current_user)
        except EventServiceError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        return RedirectResponse(url=_redirect_back(request, "/events"), status_code=303)
    finally:
        db.close()


@router.post("/events/{event_id}/close")
def close_event(
    request: Request,
    event_id: int,
    resolution_code: str | None = Form(None),
    resolution_comment: str = Form(""),
    current_user: User = Depends(require_web_auth(_AUTHORIZED_ROLES)),
):
    db = SessionLocal()
    try:
        try:
            close_incident(
                db,
                event_id,
                current_user,
                resolution_code=resolution_code or "other",
                comment=resolution_comment,
            )
        except EventServiceError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        return RedirectResponse(url=_redirect_back(request, "/events"), status_code=303)
    finally:
        db.close()


@router.post("/events/{event_id}/reopen")
def reopen_event(
    request: Request,
    event_id: int,
    comment: str = Form("Reaberto pela operacao"),
    current_user: User = Depends(require_web_auth(_AUTHORIZED_ROLES)),
):
    db = SessionLocal()
    try:
        try:
            reopen_incident(db, event_id, current_user, comment=comment)
        except EventServiceError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        return RedirectResponse(url=_redirect_back(request, "/events"), status_code=303)
    finally:
        db.close()


@router.post("/events/{event_id}/note")
def update_event_note(
    request: Request,
    event_id: int,
    operator_note: str = Form(""),
    current_user: User = Depends(require_web_auth(_AUTHORIZED_ROLES)),
):
    db = SessionLocal()
    try:
        try:
            add_incident_comment(db, event_id, current_user, operator_note)
        except EventServiceError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        return RedirectResponse(url=_redirect_back(request, "/events"), status_code=303)
    finally:
        db.close()


@router.post("/events/{event_id}/assign")
def assign_event(
    request: Request,
    event_id: int,
    assignee_user_id: str = Form(""),
    current_user: User = Depends(require_web_auth(_AUTHORIZED_ROLES)),
):
    db = SessionLocal()
    try:
        try:
            target_id = int(assignee_user_id) if str(assignee_user_id).strip() else None
            assign_incident(db, event_id, target_id, current_user)
        except (ValueError, EventServiceError) as exc:
            if isinstance(exc, EventServiceError):
                raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
            raise HTTPException(status_code=400, detail="Responsavel invalido.") from exc
        return RedirectResponse(url=_redirect_back(request, "/events"), status_code=303)
    finally:
        db.close()


@router.post("/events/{event_id}/details")
def update_incident_details_web(
    request: Request,
    event_id: int,
    priority: str = Form("medium"),
    team: str = Form(""),
    current_user: User = Depends(require_web_auth(_AUTHORIZED_ROLES)),
):
    db = SessionLocal()
    try:
        try:
            update_incident_details(db, event_id, current_user, priority=priority, team=team)
        except EventServiceError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        return RedirectResponse(url=_redirect_back(request, "/events"), status_code=303)
    finally:
        db.close()


@router.post("/events/{event_id}/correlate")
def correlate_incident_web(
    request: Request,
    event_id: int,
    related_event_ids: str = Form(""),
    current_user: User = Depends(require_web_auth(_AUTHORIZED_ROLES)),
):
    db = SessionLocal()
    try:
        try:
            event_ids = [int(value.strip()) for value in related_event_ids.split(",") if value.strip()]
            if not event_ids:
                raise EventServiceError(400, "Informe ao menos um ID de evento.")
            correlate_incident_events(db, event_id, event_ids, current_user)
        except (ValueError, EventServiceError) as exc:
            if isinstance(exc, EventServiceError):
                raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
            raise HTTPException(status_code=400, detail="Lista de eventos invalida.") from exc
        return RedirectResponse(url=_redirect_back(request, "/events"), status_code=303)
    finally:
        db.close()


@router.post("/events/revalidator-mode")
def update_events_revalidator_mode(
    request: Request,
    mode: str = Form(...),
    current_user: User = Depends(require_web_auth(_AUTHORIZED_ROLES)),
):
    selected = _set_person_revalidator_mode(mode)
    get_logger("app.web.revalidator").info(
        "Person revalidator mode updated mode=%s",
        selected,
        extra={
            "action": "person_revalidator_mode_update",
            "status": "running",
            "reason": "operator_toggle",
        },
    )
    return RedirectResponse(url=_redirect_back(request, "/events"), status_code=303)


@router.post("/events/{event_id}/feedback")
def submit_event_feedback_web(
    request: Request,
    event_id: int,
    label: str = Form(...),
    probable_cause: str | None = Form(None),
    operator_note: str = Form(""),
    reviewed_by: str = Form("operador"),
    auto_suggest: bool = Form(True),
    current_user: User = Depends(require_web_auth(_AUTHORIZED_ROLES)),
):
    db = SessionLocal()
    try:
        try:
            record_event_feedback(
                db,
                event_id,
                label=label,
                probable_cause=probable_cause,
                operator_note=operator_note,
                reviewed_by=reviewed_by,
                auto_suggest=auto_suggest,
                commit_feedback_first=False,
            )
        except EventServiceError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        return RedirectResponse(url=_redirect_back(request, "/events/review"), status_code=303)
    finally:
        db.close()


@router.post("/events/{event_id}/suggestion")
def create_event_suggestion_web(
    request: Request,
    event_id: int,
    current_user: User = Depends(require_web_auth(_AUTHORIZED_ROLES)),
):
    db = SessionLocal()
    try:
        event = db.query(Event).filter(Event.id == event_id).first()
        if not event:
            raise HTTPException(status_code=404, detail="Evento não encontrado")
        camera = db.query(Camera).filter(Camera.id == event.camera_id).first()
        if camera:
            generate_policy_suggestions(db, camera)
            db.commit()
        return RedirectResponse(url=_redirect_back(request, "/events/review"), status_code=303)
    finally:
        db.close()


@router.get("/events/{event_id}/snapshot")
def event_snapshot(
    event_id: int,
    clean: bool = False,
    current_user: User = Depends(require_web_auth(_AUTHORIZED_ROLES)),
):
    db = SessionLocal()
    try:
        event = db.query(Event).filter(Event.id == event_id).first()
        if not event or not event.snapshot_path:
            raise HTTPException(status_code=404, detail="Snapshot não encontrado")

        snapshot_file = Path(event.snapshot_path)
        if not snapshot_file.exists():
            raise HTTPException(status_code=404, detail="Arquivo de snapshot não encontrado")

        return _snapshot_display_response(snapshot_file, event.bbox_json, clean=clean)
    finally:
        db.close()


@router.get("/events/{event_id}/clip/{part}")
def event_clip(
    event_id: int,
    part: str,
    current_user: User = Depends(require_web_auth(_AUTHORIZED_ROLES)),
):
    db = SessionLocal()
    try:
        event = db.query(Event).filter(Event.id == event_id).first()
        if not event:
            raise HTTPException(status_code=404, detail="Clipe não encontrado")

        clip_dir = Path(event.clip_path) if event.clip_path else None

        normalized_part = str(part or "").strip().lower()
        if normalized_part not in {"before", "after", "video"}:
            raise HTTPException(status_code=404, detail="Parte do clipe inválida")

        if normalized_part == "video":
            metadata_path = clip_dir / "metadata.json" if clip_dir is not None else Path()
            video_name = "clip.mp4"
            if clip_dir is not None and metadata_path.exists():
                try:
                    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                    video_name = str(metadata.get("video_file") or video_name)
                except Exception:
                    video_name = "clip.mp4"
            clip_file = clip_dir / video_name if clip_dir is not None else None
        else:
            if clip_dir is None or not clip_dir.exists() or not clip_dir.is_dir():
                raise HTTPException(status_code=404, detail="Clipe não encontrado")
            clip_file = clip_dir / f"{normalized_part}.jpg"
        if normalized_part == "video" and not _valid_local_file(clip_file):
            return _remote_clip_redirect(event)

        if clip_file is None or not clip_file.exists():
            if normalized_part == "video":
                return _remote_clip_redirect(event)
            raise HTTPException(status_code=404, detail="Arquivo de clipe não encontrado")

        if normalized_part == "video":
            clip_file = _browser_compatible_clip_file(clip_dir, clip_file, metadata_path)
            if not _valid_local_file(clip_file):
                return _remote_clip_redirect(event)
            return FileResponse(
                clip_file,
                media_type="video/mp4",
                filename=clip_file.name,
                content_disposition_type="inline",
                headers={
                    "Accept-Ranges": "bytes",
                    "Cache-Control": "private, max-age=3600",
                },
            )
        return FileResponse(clip_file)
    finally:
        db.close()
