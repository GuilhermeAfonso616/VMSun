from __future__ import annotations

import re
from urllib.parse import quote, urlsplit, urlunsplit


_PASSWORD_TOKEN_RE = re.compile(r"((?:password|passwd|pass|pwd)=)[^&/?#]+", re.IGNORECASE)


def mask_url_credentials(url: str | None) -> str | None:
    if not url:
        return url

    try:
        parts = urlsplit(url)
    except Exception:
        return url

    if not parts.scheme or not parts.netloc:
        return url

    netloc = parts.netloc
    if "@" in parts.netloc:
        host = parts.hostname or ""
        port = f":{parts.port}" if parts.port else ""
        username = parts.username or ""
        if username:
            masked_user = quote(username, safe="")
            auth = f"{masked_user}:***@" if parts.password is not None else f"{masked_user}@"
        else:
            auth = "***@"
        netloc = f"{auth}{host}{port}"

    path = _PASSWORD_TOKEN_RE.sub(r"\1***", parts.path)
    query = _PASSWORD_TOKEN_RE.sub(r"\1***", parts.query)
    return urlunsplit((parts.scheme, netloc, path, query, parts.fragment))


def sanitize_url_for_log(url: str | None) -> str | None:
    """Remove todo o userinfo antes de registrar uma URL em logs."""
    if not url:
        return url

    try:
        parts = urlsplit(url)
    except Exception:
        return url

    if not parts.scheme or not parts.netloc:
        return url

    host = parts.hostname or ""
    port = f":{parts.port}" if parts.port else ""
    path = _PASSWORD_TOKEN_RE.sub(r"\1***", parts.path)
    query = _PASSWORD_TOKEN_RE.sub(r"\1***", parts.query)
    return urlunsplit((parts.scheme, f"{host}{port}", path, query, parts.fragment))
