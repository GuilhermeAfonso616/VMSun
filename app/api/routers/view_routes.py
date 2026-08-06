"""Mosaicos salvos e sequencias temporais da API."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.api.dependencies import get_db, require_role
from app.db.models import TemporalSequence, User, ViewPreset


router = APIRouter(tags=["views"])


class ViewPresetIn(BaseModel):
    id: str
    name: str
    grid_size: int
    camera_ids: list[int | None]
    hide_offline: bool = False
    boxes_enabled: bool = True
    view_config: dict[str, Any] = Field(default_factory=dict)
    is_shared: bool = False


class ViewPresetOut(BaseModel):
    id: str
    name: str
    grid_size: int
    camera_ids: list[int | None]
    hide_offline: bool
    boxes_enabled: bool
    view_config: dict[str, Any] = Field(default_factory=dict)
    is_shared: bool
    owner_username: str | None = None
    can_manage: bool


class TemporalSequenceStep(BaseModel):
    viewId: str
    duration: int


class TemporalSequenceIn(BaseModel):
    id: str
    name: str
    steps: list[TemporalSequenceStep]
    is_shared: bool = False


class TemporalSequenceOut(BaseModel):
    id: str
    name: str
    steps: list[TemporalSequenceStep]
    is_shared: bool
    owner_username: str | None = None
    can_manage: bool


def _view_owner_names(db: Session, items: list) -> dict[int, str]:
    owner_ids = {int(item.owner_user_id) for item in items if getattr(item, "owner_user_id", None) is not None}
    if not owner_ids:
        return {}
    return {
        int(user.id): str(user.username)
        for user in db.query(User).filter(User.id.in_(owner_ids)).all()
    }


def _can_share_views(current_user: User) -> bool:
    return str(current_user.role or "").lower() == "admin"


def _require_view_owner(item, current_user: User) -> None:
    if item.owner_user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Este mosaico pertence a outro usuário.")


@router.get("/view-presets", response_model=list[ViewPresetOut])
def get_view_presets(
    current_user: User = Depends(require_role(["admin", "supervisor", "operator", "viewer"])),
    db: Session = Depends(get_db),
):
    items = (
        db.query(ViewPreset)
        .filter(or_(ViewPreset.owner_user_id == current_user.id, ViewPreset.is_shared.is_(True)))
        .order_by(ViewPreset.created_at.desc())
        .all()
    )
    owner_names = _view_owner_names(db, items)
    out = []
    for item in items:
        try:
            camera_ids = json.loads(item.camera_ids)
        except (TypeError, ValueError, json.JSONDecodeError):
            camera_ids = []
        try:
            view_config = json.loads(item.view_config_json or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            view_config = {}
        out.append(
            ViewPresetOut(
                id=item.id,
                name=item.name,
                grid_size=item.grid_size,
                camera_ids=camera_ids,
                hide_offline=item.hide_offline,
                boxes_enabled=item.boxes_enabled,
                view_config=view_config if isinstance(view_config, dict) else {},
                is_shared=bool(item.is_shared),
                owner_username=owner_names.get(item.owner_user_id),
                can_manage=item.owner_user_id == current_user.id,
            )
        )
    return out


@router.post("/view-presets")
def create_or_update_view_preset(
    payload: ViewPresetIn,
    current_user: User = Depends(require_role(["admin", "supervisor", "operator"])),
    db: Session = Depends(get_db),
):
    existing = db.query(ViewPreset).filter(ViewPreset.id == payload.id).first()
    if payload.is_shared and not _can_share_views(current_user):
        raise HTTPException(status_code=403, detail="Somente administradores podem compartilhar mosaicos.")

    camera_ids_json = json.dumps(payload.camera_ids)
    view_config_json = json.dumps(payload.view_config)
    if existing:
        _require_view_owner(existing, current_user)
        existing.name = payload.name
        existing.grid_size = payload.grid_size
        existing.camera_ids = camera_ids_json
        existing.hide_offline = payload.hide_offline
        existing.boxes_enabled = payload.boxes_enabled
        existing.view_config_json = view_config_json
        existing.is_shared = payload.is_shared
    else:
        db.add(
            ViewPreset(
                id=payload.id,
                owner_user_id=current_user.id,
                name=payload.name,
                grid_size=payload.grid_size,
                camera_ids=camera_ids_json,
                hide_offline=payload.hide_offline,
                boxes_enabled=payload.boxes_enabled,
                view_config_json=view_config_json,
                is_shared=payload.is_shared,
            )
        )

    db.commit()
    return {"status": "success"}


@router.delete("/view-presets/{preset_id}")
def delete_view_preset(
    preset_id: str,
    current_user: User = Depends(require_role(["admin", "supervisor", "operator"])),
    db: Session = Depends(get_db),
):
    existing = db.query(ViewPreset).filter(ViewPreset.id == preset_id).first()
    if not existing:
        raise HTTPException(status_code=404, detail="Mosaico não encontrado")
    _require_view_owner(existing, current_user)
    db.delete(existing)
    db.commit()
    return {"status": "success"}


@router.get("/temporal-sequences", response_model=list[TemporalSequenceOut])
def get_temporal_sequences(
    current_user: User = Depends(require_role(["admin", "supervisor", "operator", "viewer"])),
    db: Session = Depends(get_db),
):
    items = (
        db.query(TemporalSequence)
        .filter(or_(TemporalSequence.owner_user_id == current_user.id, TemporalSequence.is_shared.is_(True)))
        .order_by(TemporalSequence.created_at.desc())
        .all()
    )
    owner_names = _view_owner_names(db, items)
    out = []
    for item in items:
        try:
            steps_data = json.loads(item.steps)
            steps = [TemporalSequenceStep(**step) for step in steps_data]
        except (TypeError, ValueError, json.JSONDecodeError):
            steps = []
        out.append(
            TemporalSequenceOut(
                id=item.id,
                name=item.name,
                steps=steps,
                is_shared=bool(item.is_shared),
                owner_username=owner_names.get(item.owner_user_id),
                can_manage=item.owner_user_id == current_user.id,
            )
        )
    return out


@router.post("/temporal-sequences")
def create_or_update_temporal_sequence(
    payload: TemporalSequenceIn,
    current_user: User = Depends(require_role(["admin", "supervisor", "operator"])),
    db: Session = Depends(get_db),
):
    existing = db.query(TemporalSequence).filter(TemporalSequence.id == payload.id).first()
    if payload.is_shared and not _can_share_views(current_user):
        raise HTTPException(status_code=403, detail="Somente administradores podem compartilhar sequências.")

    steps_json = json.dumps([step.model_dump() for step in payload.steps])
    if existing:
        _require_view_owner(existing, current_user)
        existing.name = payload.name
        existing.steps = steps_json
        existing.is_shared = payload.is_shared
    else:
        db.add(
            TemporalSequence(
                id=payload.id,
                owner_user_id=current_user.id,
                name=payload.name,
                steps=steps_json,
                is_shared=payload.is_shared,
            )
        )

    db.commit()
    return {"status": "success"}


@router.delete("/temporal-sequences/{seq_id}")
def delete_temporal_sequence(
    seq_id: str,
    current_user: User = Depends(require_role(["admin", "supervisor", "operator"])),
    db: Session = Depends(get_db),
):
    existing = db.query(TemporalSequence).filter(TemporalSequence.id == seq_id).first()
    if not existing:
        raise HTTPException(status_code=404, detail="Sequência temporal não encontrada")
    _require_view_owner(existing, current_user)
    db.delete(existing)
    db.commit()
    return {"status": "success"}
