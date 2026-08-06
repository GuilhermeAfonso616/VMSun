from __future__ import annotations

import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

from app.core.config import settings


def _resolve(path_like: Any) -> Path:
    """Resolve um caminho de setting: absolutos ficam como estao; relativos sao
    ancorados em app_base_dir (o CWD do container e /app)."""
    p = Path(str(path_like))
    return p if p.is_absolute() else (Path(settings.app_base_dir) / p)


def _db_paths() -> list[Path]:
    url = str(getattr(settings, "database_url", "") or "")
    prefix = "sqlite:///"
    if url.startswith(prefix):
        db_path = Path(url[len(prefix):])
        return [
            db_path,
            Path(f"{db_path}-wal"),
            Path(f"{db_path}-shm"),
            Path(f"{db_path}-journal"),
        ]
    return []


def _models_dir() -> Path:
    # Deriva a raiz dos modelos subindo pelo caminho do detector ate a pasta
    # "models" (funciona tanto no dev quanto no container, onde o volume remapeia).
    try:
        p = _resolve(settings.detector_model_path)
        for parent in [p] + list(p.parents):
            if parent.name == "models":
                return parent
    except Exception:
        pass
    return _resolve("models")


def _disk_usage_path() -> Path:
    configured = str(getattr(settings, "storage_monitor_disk_path", "") or "").strip()
    if configured:
        path = _resolve(configured)
        if path.exists():
            return path

    candidates: list[Path] = []
    db_paths = _db_paths()
    if db_paths:
        candidates.append(db_paths[0].parent)
    candidates.extend(
        [
            _resolve(settings.runtime_state_dir),
            _resolve(settings.event_snapshots_dir),
            _resolve(settings.logs_dir),
            Path("/data"),
            Path(settings.app_base_dir),
        ]
    )
    for path in candidates:
        if path.exists():
            return path
    return Path(settings.app_base_dir)


# Categorias monitoradas. `growing=True` = pasta que cresce com a operacao (entra
# na projecao de enchimento); estaticas como modelos ficam de fora da projecao.
# Caminhos vem dos settings (sobrescritos por env no Docker), nao de BASE_DIR.
def _categories() -> list[dict[str, Any]]:
    cats = [
        {"key": "event_media", "label": "Snapshots e clips de eventos", "path": _resolve(settings.event_snapshots_dir), "growing": True},
        {"key": "runtime_state", "label": "Estado de runtime (historicos)", "path": _resolve(settings.runtime_state_dir), "growing": True},
        {"key": "logs", "label": "Logs", "path": _resolve(settings.logs_dir), "growing": True},
        {"key": "debug_frames", "label": "Frames de debug", "path": _resolve(settings.debug_frames_dir), "growing": True},
        {"key": "datasets", "label": "Datasets de revalidacao", "path": _resolve(getattr(settings, "revalidator_feedback_dataset_dir", "datasets")).parent, "growing": True},
        {"key": "models", "label": "Modelos de IA", "path": _models_dir(), "growing": False},
    ]
    db_paths = _db_paths()
    if db_paths:
        cats.insert(1, {"key": "database", "label": "Banco de dados", "paths": db_paths, "growing": True})
    return cats


_CACHE_LOCK = Lock()
_CACHE: dict[str, Any] | None = None
_CACHE_AT = 0.0
_CACHE_TTL = 30.0


def _path_stats(path: Path) -> dict[str, Any]:
    """Soma tamanho e conta arquivos de uma pasta ou arquivo, guardando o
    intervalo de datas de modificacao para estimar a taxa de crescimento."""
    total = 0
    files = 0
    oldest: float | None = None
    newest: float | None = None
    exists = path.exists()
    if exists and path.is_file():
        try:
            st = path.stat()
            total = st.st_size
            files = 1
            oldest = st.st_mtime
            newest = st.st_mtime
        except OSError:
            pass
    elif exists:
        stack = [str(path)]
        while stack:
            current = stack.pop()
            try:
                with os.scandir(current) as it:
                    for entry in it:
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                stack.append(entry.path)
                            elif entry.is_file(follow_symlinks=False):
                                st = entry.stat(follow_symlinks=False)
                                total += st.st_size
                                files += 1
                                mtime = st.st_mtime
                                oldest = mtime if oldest is None else min(oldest, mtime)
                                newest = mtime if newest is None else max(newest, mtime)
                        except OSError:
                            continue
            except OSError:
                continue
    return {
        "size_bytes": total,
        "files": files,
        "oldest_at": _iso(oldest),
        "newest_at": _iso(newest),
        "_oldest": oldest,
        "_newest": newest,
        "exists": exists,
    }


def _stats_for_paths(paths: list[Path]) -> dict[str, Any]:
    total = 0
    files = 0
    oldest: float | None = None
    newest: float | None = None
    exists = False
    for path in paths:
        stats = _path_stats(path)
        if not stats["exists"]:
            continue
        exists = True
        total += int(stats["size_bytes"] or 0)
        files += int(stats["files"] or 0)
        item_oldest = stats.get("_oldest")
        item_newest = stats.get("_newest")
        if item_oldest is not None:
            oldest = item_oldest if oldest is None else min(oldest, item_oldest)
        if item_newest is not None:
            newest = item_newest if newest is None else max(newest, item_newest)
    return {
        "size_bytes": total,
        "files": files,
        "oldest_at": _iso(oldest),
        "newest_at": _iso(newest),
        "_oldest": oldest,
        "_newest": newest,
        "exists": exists,
    }


def _iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(ts, timezone.utc).replace(tzinfo=None, microsecond=0).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _growth_bytes_per_day(cat: dict[str, Any], stats: dict[str, Any]) -> float | None:
    """Estima a taxa de crescimento pela razao tamanho/idade dos arquivos.
    Retorna None quando o intervalo observado e curto demais para confiar."""
    oldest = stats.get("_oldest")
    newest = stats.get("_newest")
    if oldest is None or newest is None:
        return None
    span_days = (newest - oldest) / 86400.0
    if span_days < 0.5:
        return None
    return stats["size_bytes"] / span_days


def compute_storage_report(force: bool = False) -> dict[str, Any]:
    global _CACHE, _CACHE_AT
    now = time.monotonic()
    with _CACHE_LOCK:
        if not force and _CACHE is not None and (now - _CACHE_AT) < _CACHE_TTL:
            return _CACHE

    base = _disk_usage_path()
    try:
        usage = shutil.disk_usage(str(base))
        disk = {
            "path": str(base),
            "total_bytes": int(usage.total),
            "used_bytes": int(usage.used),
            "free_bytes": int(usage.free),
            "used_percent": round(usage.used / usage.total * 100, 1) if usage.total else None,
        }
    except OSError:
        disk = {"path": str(base), "total_bytes": None, "used_bytes": None, "free_bytes": None, "used_percent": None}

    categories: list[dict[str, Any]] = []
    tracked_bytes = 0
    growth_per_day = 0.0
    growth_known = False
    for cat in _categories():
        paths = cat.get("paths")
        if paths:
            stats = _stats_for_paths(list(paths))
            display_path = ", ".join(str(path) for path in paths)
        else:
            stats = _path_stats(cat["path"])
            display_path = str(cat["path"])
        if not stats["exists"]:
            # Caminho inexistente (ex.: remapeado por volume no Docker): omite.
            continue
        tracked_bytes += stats["size_bytes"]
        entry = {
            "key": cat["key"],
            "label": cat["label"],
            "path": display_path,
            "size_bytes": stats["size_bytes"],
            "files": stats["files"],
            "oldest_at": stats["oldest_at"],
            "newest_at": stats["newest_at"],
            "exists": stats["exists"],
            "growing": bool(cat["growing"]),
        }
        if cat["growing"]:
            rate = _growth_bytes_per_day(cat, stats)
            entry["growth_bytes_per_day"] = round(rate, 2) if rate is not None else None
            if rate is not None and rate > 0:
                growth_per_day += rate
                growth_known = True
        categories.append(entry)

    categories.sort(key=lambda item: item["size_bytes"], reverse=True)

    free_bytes = disk.get("free_bytes")
    days_until_full = None
    if growth_known and growth_per_day > 0 and free_bytes:
        days_until_full = round(free_bytes / growth_per_day, 1)

    report = {
        "generated_at": datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0).isoformat(),
        "disk": disk,
        "categories": categories,
        "totals": {"tracked_bytes": tracked_bytes},
        "growth": {
            "bytes_per_day": round(growth_per_day, 2) if growth_known else None,
            "days_until_full": days_until_full,
            "based_on": "mtime dos arquivos das pastas que crescem",
        },
        "retention": {
            "event_retention_days": int(getattr(settings, "event_retention_days", 0) or 0),
            "operational_history_retention_days": int(getattr(settings, "operational_history_retention_days", 0) or 0),
            "resource_history_retention_days": int(getattr(settings, "resource_history_retention_days", 0) or 0),
        },
    }

    with _CACHE_LOCK:
        _CACHE = report
        _CACHE_AT = now
    return report
