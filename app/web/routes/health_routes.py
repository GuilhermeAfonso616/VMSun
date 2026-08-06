from __future__ import annotations

import os

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.core.timezone import now_brazil_naive
from app.db.base import SessionLocal
from app.db.models import Camera
from app.services.camera_operational_state import build_operational_snapshot
from app.services.camera_registry import registry
from app.services.frame_store import frame_store
from app.services.metrics_store import metrics_store
from app.services.operational_diagnostics_service import build_ai_operational_diagnostics
from app.services.runtime_client import RuntimeClientError, get_runtime_health_snapshot, remote_runtime_enabled, runtime_get


router = APIRouter()


@router.get("/health/live")
def health_live():
    return JSONResponse(
        {
            "status": "ok",
            "pid": os.getpid(),
            "timestamp": now_brazil_naive().isoformat(),
        }
    )


@router.get("/health/ready")
def health_ready():
    db = SessionLocal()
    try:
        db.query(Camera.id).limit(1).all()
        if remote_runtime_enabled():
            runtime_get("/internal/health/ready")
        else:
            registry.list_workers()
        metrics_store.get_metrics(0)
        frame_store.get_raw_frame_metadata(0)
        frame_store.get_processed_frame_metadata(0)
        return JSONResponse(
            {
                "status": "ok",
                "db": "ok",
                "registry": "remote" if remote_runtime_enabled() else "ok",
                "stores": "ok",
                "timestamp": now_brazil_naive().isoformat(),
            }
        )
    except RuntimeClientError as exc:
        return JSONResponse(
            {
                "status": "error",
                "detail": f"runtime indisponivel: {exc}",
                "timestamp": now_brazil_naive().isoformat(),
            },
            status_code=503,
        )
    except Exception as exc:
        return JSONResponse(
            {
                "status": "error",
                "detail": str(exc),
                "timestamp": now_brazil_naive().isoformat(),
            },
            status_code=503,
        )
    finally:
        db.close()


@router.get("/health/cameras")
def health_cameras():
    db = SessionLocal()
    try:
        health_snapshot = get_runtime_health_snapshot()
        if remote_runtime_enabled():
            status_code = 503 if health_snapshot.get("status") == "error" else 200
            return JSONResponse(health_snapshot, status_code=status_code)
        return JSONResponse(
            {
                **health_snapshot,
                "operational": build_operational_snapshot(db),
            }
        )
    finally:
        db.close()


@router.get("/health/operational")
def health_operational():
    db = SessionLocal()
    try:
        health_snapshot = get_runtime_health_snapshot()
        payload = {
            "status": "ok" if health_snapshot.get("status") != "error" else "degraded",
            "timestamp": now_brazil_naive().isoformat(),
            "runtime": health_snapshot,
            "ai_diagnostics": build_ai_operational_diagnostics(db),
        }
        if not remote_runtime_enabled():
            payload["operational"] = build_operational_snapshot(db)
        return JSONResponse(payload, status_code=503 if payload["status"] == "degraded" else 200)
    finally:
        db.close()
