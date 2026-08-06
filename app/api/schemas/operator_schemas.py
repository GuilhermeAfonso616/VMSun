"""Contratos HTTP das integracoes do cliente operador."""

from pydantic import BaseModel


class OneDriveTokenUpdate(BaseModel):
    token: str


class OneDriveUploadToggle(BaseModel):
    enabled: bool
