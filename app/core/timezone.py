from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def _resolve_brazil_tz():
    try:
        return ZoneInfo("America/Sao_Paulo")
    except ZoneInfoNotFoundError:
        # Keep app startup resilient even if system tz database is missing.
        return timezone(timedelta(hours=-3), name="BRT")


BRAZIL_TZ = _resolve_brazil_tz()


def _coerce_datetime(value) -> datetime | None:
    if value is None:
        return None

    if isinstance(value, datetime):
        return value

    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None

        normalized = text.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(normalized)
        except Exception:
            return None

    return None


def as_brazil_datetime(value) -> datetime | None:
    dt = _coerce_datetime(value)
    if dt is None:
        return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(BRAZIL_TZ)


def now_brazil() -> datetime:
    return datetime.now(BRAZIL_TZ)


def now_brazil_naive() -> datetime:
    return now_brazil().replace(tzinfo=None)


def utc_now_naive() -> datetime:
    """Equivalente nao depreciado de `datetime.utcnow()` (mesmo valor naive)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def as_brazil_naive(value) -> datetime | None:
    dt = as_brazil_datetime(value)
    if dt is None:
        return None
    return dt.replace(tzinfo=None)


def brazil_date_bounds_as_utc_naive(value: str | date) -> tuple[datetime, datetime]:
    if isinstance(value, date) and not isinstance(value, datetime):
        target_date = value
    else:
        target_date = datetime.fromisoformat(str(value)).date()

    start_local = datetime.combine(target_date, time.min, tzinfo=BRAZIL_TZ)
    end_local = start_local + timedelta(days=1)
    return (
        start_local.astimezone(timezone.utc).replace(tzinfo=None),
        end_local.astimezone(timezone.utc).replace(tzinfo=None),
    )


def format_brazil_datetime(value, fmt: str = "%d/%m/%Y %H:%M:%S") -> str:
    dt = as_brazil_datetime(value)
    if dt is None:
        return "-"
    return dt.strftime(fmt)
