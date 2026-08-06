import os
import time
import json
import hmac
import hashlib
import base64
from typing import Any, Dict, Optional
from app.core.config import settings

def hash_password(password: str) -> str:
    """Gera o hash PBKDF2-SHA256 da senha usando um salt aleatório."""
    salt = os.urandom(16).hex()
    pwd_bytes = password.encode("utf-8")
    salt_bytes = salt.encode("utf-8")
    pbkdf = hashlib.pbkdf2_hmac("sha256", pwd_bytes, salt_bytes, 100000)
    return f"{salt}${pbkdf.hex()}"

def verify_password(password: str, hashed: str) -> bool:
    """Verifica a senha contra o hash armazenado de forma segura contra timing attacks."""
    try:
        salt, key = hashed.split("$", 1)
        pwd_bytes = password.encode("utf-8")
        salt_bytes = salt.encode("utf-8")
        pbkdf = hashlib.pbkdf2_hmac("sha256", pwd_bytes, salt_bytes, 100000)
        return hmac.compare_digest(pbkdf.hex(), key)
    except Exception:
        return False

def _base64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("utf-8")

def _base64url_decode(data: str) -> bytes:
    padding = "=" * (4 - (len(data) % 4))
    return base64.urlsafe_b64decode((data + padding).encode("utf-8"))

def create_access_token(data: Dict[str, Any], expires_delta: int = 86400) -> str:
    """Gera um token assinado por HMAC-SHA256 estruturado como um JWT padrão."""
    header = {"alg": "HS256", "typ": "JWT"}
    payload = data.copy()
    payload["exp"] = int(time.time()) + expires_delta

    header_b64 = _base64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    payload_b64 = _base64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    
    signing_input = f"{header_b64}.{payload_b64}".encode("utf-8")
    secret = settings.auth_secret_key.encode("utf-8")
    
    signature = hmac.new(secret, signing_input, hashlib.sha256).digest()
    signature_b64 = _base64url_encode(signature)
    
    return f"{header_b64}.{payload_b64}.{signature_b64}"

def decode_access_token(token: str) -> Optional[Dict[str, Any]]:
    """Decodifica e valida o token de acesso HMAC. Retorna o payload se válido e não expirado."""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        
        header_b64, payload_b64, signature_b64 = parts
        signing_input = f"{header_b64}.{payload_b64}".encode("utf-8")
        secret = settings.auth_secret_key.encode("utf-8")
        
        expected_signature = hmac.new(secret, signing_input, hashlib.sha256).digest()
        expected_signature_b64 = _base64url_encode(expected_signature)
        
        if not hmac.compare_digest(signature_b64, expected_signature_b64):
            return None
        
        payload_data = json.loads(_base64url_decode(payload_b64).decode("utf-8"))
        if int(payload_data.get("exp", 0)) < int(time.time()):
            return None
        
        return payload_data
    except Exception:
        return None
