"""Criptografia transparente para segredos operacionais persistidos no banco."""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.types import String, TypeDecorator

from app.core.config import settings


PREFIX = "enc:v1:"


def _fernet() -> Fernet:
    digest = hashlib.sha256(settings.credential_encryption_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_credential(value: str | None) -> str | None:
    if value is None or value.startswith(PREFIX):
        return value
    return PREFIX + _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_credential(value: str | None) -> str | None:
    if value is None or not value.startswith(PREFIX):
        # Compatibilidade de leitura durante a migracao de bancos legados.
        return value
    try:
        return _fernet().decrypt(value[len(PREFIX):].encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("Nao foi possivel descriptografar a credencial da camera.") from exc


class EncryptedString(TypeDecorator):
    """String cifrada no banco e apresentada em texto claro apenas ao dominio."""

    impl = String
    cache_ok = True

    def process_bind_param(self, value, dialect):  # noqa: ANN001, ARG002
        return encrypt_credential(value)

    def process_result_value(self, value, dialect):  # noqa: ANN001, ARG002
        return decrypt_credential(value)
