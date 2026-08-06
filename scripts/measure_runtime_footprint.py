#!/usr/bin/env python3
"""Mede o footprint real do runtime e valida os clips de evento.

Roda DENTRO do container de runtime (tem acesso a /proc, ao banco e, com a
compose .gpu, ao nvidia-smi):

    docker compose -f docker-compose.yml -f docker-compose.gpu.yml \
        exec analitico-runtime python scripts/measure_runtime_footprint.py

Mostra:
  1) RAM (RSS) por processo Python do runtime (1 processo por camera no modelo atual).
  2) VRAM por PID via nvidia-smi (se disponivel no container; senao rode no host).
  3) Validacao de clips: amostra eventos recentes e confere se o arquivo de clip
     existe e nao esta vazio (apos a mudanca do ring para JPEG).

Tudo somente leitura. Nao altera nada.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path


def _read_rss_kb(pid: int) -> int | None:
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1])  # em kB
    except Exception:
        return None
    return None


def _cmdline(pid: int) -> str:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\x00", b" ").decode(errors="replace")
        return raw.strip()
    except Exception:
        return ""


def measure_processes() -> list[tuple[int, int, str]]:
    rows: list[tuple[int, int, str]] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        cmd = _cmdline(pid)
        if "python" not in cmd.lower():
            continue
        rss = _read_rss_kb(pid)
        if rss is None:
            continue
        rows.append((pid, rss, cmd))
    return sorted(rows, key=lambda item: item[1], reverse=True)


def vram_by_pid() -> dict[int, int]:
    result: dict[int, int] = {}
    if shutil.which("nvidia-smi") is None:
        return result
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-compute-apps=pid,used_memory", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
        ).stdout
    except Exception:
        return result
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 2 and parts[0].isdigit():
            try:
                result[int(parts[0])] = int(float(parts[1]))  # MiB
            except ValueError:
                continue
    return result


def validate_clips(db_path: str, sample: int = 20) -> None:
    if not os.path.exists(db_path):
        print(f"  [clips] banco nao encontrado em {db_path}")
        return
    con = sqlite3.connect(db_path)
    rows = list(con.execute(
        "SELECT id, camera_id, clip_path, created_at FROM events "
        "WHERE clip_path IS NOT NULL AND clip_path != '' "
        "ORDER BY id DESC LIMIT ?",
        (sample,),
    ))
    if not rows:
        print("  [clips] nenhum evento recente com clip_path.")
        return
    ok = missing = empty = 0
    for _id, _cam, clip_path, _created in rows:
        p = clip_path if os.path.isabs(clip_path) else os.path.join("/data", clip_path.lstrip("/"))
        if not os.path.exists(p):
            # tenta variações comuns (diretorio do clip)
            if os.path.isdir(p) and any(Path(p).iterdir()):
                ok += 1
                continue
            missing += 1
        elif os.path.getsize(p) == 0:
            empty += 1
        else:
            ok += 1
    print(f"  [clips] amostra={len(rows)} | ok={ok} | faltando={missing} | vazios={empty}")
    if rows:
        print(f"  [clips] exemplo: evento #{rows[0][0]} -> {rows[0][2]}")


def main() -> int:
    procs = measure_processes()
    vram = vram_by_pid()

    print("=== Processos Python do runtime (RSS) ===")
    total_rss_mb = 0.0
    worker_like = 0
    for pid, rss_kb, cmd in procs:
        rss_mb = rss_kb / 1024.0
        total_rss_mb += rss_mb
        vmib = vram.get(pid)
        tag = ""
        low = cmd.lower()
        if "spawn" in low or "worker" in low or "main.py" in low:
            worker_like += 1
            tag = "  <- worker?"
        print(f"  pid={pid:>7} rss={rss_mb:8.1f} MB  vram={(str(vmib)+' MiB') if vmib is not None else '-':>10}  {cmd[:70]}{tag}")

    print()
    print("=== Resumo ===")
    print(f"  processos python: {len(procs)} (parecem worker: {worker_like})")
    print(f"  RSS total python: {total_rss_mb/1024.0:.2f} GB")
    if vram:
        total_vram = sum(vram.values())
        print(f"  VRAM total (compute-apps): {total_vram} MiB ({total_vram/1024.0:.2f} GiB) em {len(vram)} PIDs")
    else:
        print("  VRAM: nvidia-smi indisponivel no container. Rode no HOST:")
        print("        nvidia-smi --query-compute-apps=pid,used_memory --format=csv")

    print()
    print("=== Validacao de clips (pos-JPEG) ===")
    db_path = os.environ.get("ANALYTICS_DB_PATH", "/data/analytics.db")
    validate_clips(db_path)

    print()
    print("Dica: rode tambem no HOST, em paralelo, para visao por container:")
    print("  docker stats --no-stream")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
