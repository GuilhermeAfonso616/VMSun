"""Stub de compatibilidade para event_listing_service no VMSun."""

def parse_optional_int_filter(value: str | None) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return int(value)
    except ValueError:
        return None

def parse_optional_str_filter(value: str | None) -> str | None:
    return str(value).strip() if value else None
