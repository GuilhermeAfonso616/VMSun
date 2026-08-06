#!/usr/bin/env python3
"""Gera SLO operacional usando apenas os JSONL persistidos pelo runtime."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


HEALTHY_STATES = {"ia", "online"}
EXCLUDED_STATES = {"stopped", "unknown"}


def parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def iter_jsonl(paths: Iterable[Path]) -> Iterable[dict[str, Any]]:
    for path in paths:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                payload = json.loads(line)
            except (TypeError, ValueError):
                continue
            if isinstance(payload, dict):
                yield payload


def summarize_window(
    samples: list[dict[str, Any]],
    events: list[dict[str, Any]],
    *,
    now: datetime,
    hours: int,
) -> dict[str, Any]:
    since = now - timedelta(hours=hours)
    camera_states: dict[int, Counter[str]] = defaultdict(Counter)
    camera_names: dict[int, str] = {}
    sample_count = 0
    for sample in samples:
        ts = parse_datetime(sample.get("ts"))
        if ts is None or ts < since or ts > now:
            continue
        sample_count += 1
        for camera in sample.get("cameras") or []:
            if not isinstance(camera, dict):
                continue
            try:
                camera_id = int(camera.get("id") or camera.get("camera_id") or 0)
            except (TypeError, ValueError):
                continue
            if not camera_id:
                continue
            state = str(camera.get("state") or "unknown").strip().lower()
            camera_states[camera_id][state] += 1
            camera_names[camera_id] = str(
                camera.get("name") or camera.get("camera_name") or f"Camera {camera_id}"
            )

    cameras: list[dict[str, Any]] = []
    for camera_id, states in camera_states.items():
        observed = sum(states.values())
        eligible = sum(count for state, count in states.items() if state not in EXCLUDED_STATES)
        healthy = sum(states[state] for state in HEALTHY_STATES)
        ia = states["ia"]
        cameras.append({
            "camera_id": camera_id,
            "camera_name": camera_names.get(camera_id, f"Camera {camera_id}"),
            "observed_samples": observed,
            "eligible_samples": eligible,
            "healthy_samples": healthy,
            "ia_samples": ia,
            "availability_percent": round(healthy / eligible * 100.0, 2) if eligible else None,
            "ia_availability_percent": round(ia / eligible * 100.0, 2) if eligible else None,
            "states": dict(sorted(states.items())),
        })
    cameras.sort(
        key=lambda item: (
            101.0 if item["availability_percent"] is None else item["availability_percent"],
            item["camera_name"].lower(),
        )
    )

    event_counts: Counter[str] = Counter()
    for event in events:
        ts = parse_datetime(event.get("ts"))
        if ts is not None and since <= ts <= now:
            event_counts[str(event.get("event") or "unknown")] += 1

    eligible_total = sum(item["eligible_samples"] for item in cameras)
    healthy_total = sum(item["healthy_samples"] for item in cameras)
    ia_total = sum(item["ia_samples"] for item in cameras)
    return {
        "hours": hours,
        "start": since.isoformat(),
        "end": now.isoformat(),
        "history_samples": sample_count,
        "camera_count": len(cameras),
        "availability_percent": round(healthy_total / eligible_total * 100.0, 2) if eligible_total else None,
        "ia_availability_percent": round(ia_total / eligible_total * 100.0, 2) if eligible_total else None,
        "supervisor_events": dict(sorted(event_counts.items())),
        "cameras": cameras,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# SLO operacional do Analitico",
        "",
        f"Gerado em `{report['generated_at']}`.",
        "",
        "Paradas `stopped` e amostras `unknown` ficam fora do denominador para nao punir paradas manuais.",
    ]
    for window in report["windows"]:
        lines.extend([
            "",
            f"## Ultimas {window['hours']} horas",
            "",
            f"- Disponibilidade operacional: `{window['availability_percent']}`%",
            f"- Disponibilidade com IA confirmada: `{window['ia_availability_percent']}`%",
            f"- Cameras observadas: `{window['camera_count']}`",
            f"- Amostras: `{window['history_samples']}`",
            "",
            "| Camera | Operacional | IA | Amostras elegiveis | Estados |",
            "|---|---:|---:|---:|---|",
        ])
        for camera in window["cameras"]:
            states = ", ".join(f"{key}={value}" for key, value in camera["states"].items())
            lines.append(
                f"| {camera['camera_id']} - {camera['camera_name']} "
                f"| {camera['availability_percent']}% | {camera['ia_availability_percent']}% "
                f"| {camera['eligible_samples']} | {states} |"
            )
        if window["supervisor_events"]:
            events = ", ".join(
                f"{key}={value}" for key, value in window["supervisor_events"].items()
            )
            lines.extend(["", f"Eventos do supervisor: `{events}`"])
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Gera relatorio SLO 24h/7d/30d")
    parser.add_argument(
        "--history-dir",
        default="/mnt/analitico_ssd/Analitico_Go_V4/data/runtime_state/operational_history",
    )
    parser.add_argument(
        "--events-file",
        default="/mnt/analitico_ssd/supervisor/events.jsonl",
    )
    parser.add_argument(
        "--output-dir",
        default="/mnt/analitico_ssd/supervisor/slo",
    )
    parser.add_argument("--windows", default="24,168,720")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    history_dir = Path(args.history_dir)
    events_path = Path(args.events_file)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    samples = list(iter_jsonl(sorted(history_dir.glob("camera_status_*.jsonl"))))
    events = list(iter_jsonl([events_path]))
    now = datetime.now(timezone.utc)
    windows = [max(1, int(value.strip())) for value in args.windows.split(",") if value.strip()]
    report = {
        "generated_at": now.isoformat(),
        "windows": [
            summarize_window(samples, events, now=now, hours=hours)
            for hours in windows
        ],
    }
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    json_path = output_dir / f"slo_{stamp}.json"
    md_path = output_dir / f"slo_{stamp}.md"
    json_text = json.dumps(report, ensure_ascii=False, indent=2)
    md_text = render_markdown(report)
    json_path.write_text(json_text, encoding="utf-8")
    md_path.write_text(md_text, encoding="utf-8")
    (output_dir / "latest.json").write_text(json_text, encoding="utf-8")
    (output_dir / "latest.md").write_text(md_text, encoding="utf-8")
    print(md_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
