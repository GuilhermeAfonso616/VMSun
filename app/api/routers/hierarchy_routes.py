"""Rotas REST da Hierarquia do VMSun (Clientes, Locais, NVRs e Grupos)."""

from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.dependencies import get_db
from app.services.hierarchy_service import hierarchy_service

router = APIRouter(prefix="/hierarchy", tags=["hierarchy"])


class CustomerCreate(BaseModel):
    name: str
    code: Optional[str] = None
    description: Optional[str] = None


class SiteCreate(BaseModel):
    name: str
    customer_id: Optional[int] = None
    code: Optional[str] = None
    address: Optional[str] = None


class NvrCreate(BaseModel):
    name: str
    host: str
    username: str
    password: str
    site_id: Optional[int] = None
    vendor: str = "generic"
    port: int = 80
    total_channels: int = 16


class GroupCreate(BaseModel):
    name: str
    description: Optional[str] = None
    camera_ids: Optional[List[int]] = None


@router.get("/tree")
def get_hierarchy_tree(db: Session = Depends(get_db)):
    """Retorna a árvore hierárquica completa (Clientes -> Locais -> NVRs -> Canais)."""
    return hierarchy_service.get_full_tree(db)


@router.post("/customers")
def create_customer(payload: CustomerCreate, db: Session = Depends(get_db)):
    customer = hierarchy_service.create_customer(
        db, name=payload.name, code=payload.code, description=payload.description
    )
    return {"ok": True, "customer": {"id": customer.id, "name": customer.name, "code": customer.code}}


@router.post("/sites")
def create_site(payload: SiteCreate, db: Session = Depends(get_db)):
    site = hierarchy_service.create_site(
        db,
        name=payload.name,
        customer_id=payload.customer_id,
        code=payload.code,
        address=payload.address,
    )
    return {"ok": True, "site": {"id": site.id, "name": site.name, "customer_id": site.customer_id}}


@router.post("/nvrs")
def create_nvr(payload: NvrCreate, db: Session = Depends(get_db)):
    nvr = hierarchy_service.create_nvr(
        db,
        name=payload.name,
        host=payload.host,
        username=payload.username,
        password=payload.password,
        site_id=payload.site_id,
        vendor=payload.vendor,
        port=payload.port,
        total_channels=payload.total_channels,
    )
    return {"ok": True, "nvr": {"id": nvr.id, "name": nvr.name, "host": nvr.host, "site_id": nvr.site_id}}


@router.post("/groups")
def create_group(payload: GroupCreate, db: Session = Depends(get_db)):
    group = hierarchy_service.create_group(
        db,
        name=payload.name,
        description=payload.description,
        camera_ids=payload.camera_ids,
    )
    return {"ok": True, "group": {"id": group.id, "name": group.name, "camera_count": len(group.cameras)}}
