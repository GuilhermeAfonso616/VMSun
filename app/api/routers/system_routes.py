"""Endpoints basicos de identidade e disponibilidade da API."""

from fastapi import APIRouter

from app.core.build_info import build_info_payload


router = APIRouter(tags=["system"])


@router.get("/version")
def api_version():
    return build_info_payload()


@router.get("/health")
def health():
    return {"status": "ok"}
