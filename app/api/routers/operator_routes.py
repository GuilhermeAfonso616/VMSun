"""Endpoints de bootstrap e telemetria do cliente operador."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.api.dependencies import get_db
from app.services.operator_service import build_operator_bootstrap, store_operator_performance


router = APIRouter(prefix="/operator", tags=["operator"])


@router.get("/bootstrap")
def operator_bootstrap(
    request: Request,
    register_paths: bool = Query(True),
    db: Session = Depends(get_db),
):
    """Entrega caminhos de midia sem expor credenciais ou URLs RTSP de origem."""
    return build_operator_bootstrap(
        db,
        request_hostname=request.url.hostname,
        register_paths=register_paths,
    )


@router.post("/performance-log")
async def operator_performance_log(request: Request):
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="JSON de performance invalido") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Payload de performance deve ser um objeto JSON")

    return store_operator_performance(
        payload,
        client_host=request.client.host if request.client is not None else None,
    )
