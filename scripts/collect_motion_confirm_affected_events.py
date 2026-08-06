"""Collect events instrumented by the motion confirmation policy."""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import settings
from app.db.models import Camera, Event


MOTION_CONFIRM_RE = re.compile(
    r"motion_confirm_passed\s*=\s*(?P<passed>true|false)"
    r"(?:\s*\|\s*|\s+)"
    r"motion_blobs_median\s*=\s*(?P<blobs>\d+(?:\.\d+)?)"
    r"(?:\s*\|\s*|\s+)"
    r"motion_area_pct_median\s*=\s*(?P<area>\d+(?:\.\d+)?)"
    r"(?:\s*\|\s*|\s+)"
    r"motion_confirm_mode\s*=\s*(?P<mode>\w+)",
    re.IGNORECASE,
)


def _optional_value(details: str, key: str, default: str = "") -> str:
    match = re.search(rf"{re.escape(key)}\s*=\s*([^\s|]+)", details, re.IGNORECASE)
    return match.group(1) if match else default


def _score(details: str, key: str) -> float:
    value = _optional_value(details, key, "0")
    try:
        return float(value)
    except ValueError:
        return 0.0


def main() -> int:
    database_url = settings.database_url
    print(f"Connecting to database: {database_url}")

    engine_kwargs = {}
    connect_args = {}
    if database_url.startswith("sqlite"):
        connect_args = {"check_same_thread": False, "timeout": 30.0}
        engine_kwargs["poolclass"] = NullPool
    engine = create_engine(database_url, connect_args=connect_args, **engine_kwargs)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    output_dir = PROJECT_ROOT / "reports"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_csv = output_dir / "motion_confirm_affected_events.csv"
    rows: list[dict[str, object]] = []
    total_instrumented = 0
    total_legacy_veto_candidates = 0

    with Session() as session:
        stmt = (
            select(Event, Camera)
            .join(Camera, Event.camera_id == Camera.id)
            .where(Event.details.like("%motion_confirm_passed=%"))
        )
        for event, camera in session.execute(stmt).all():
            total_instrumented += 1
            details = event.details or ""
            match = MOTION_CONFIRM_RE.search(details)
            if not match:
                continue

            groups = match.groupdict()
            passed = groups["passed"].lower() == "true"
            ia2_person_score = _score(details, "revalidator_person")
            ia3_person_score = _score(details, "far_revalidator_person")
            strong_person = ia2_person_score >= 0.50 or ia3_person_score >= 0.10
            legacy_veto_candidate = not passed and not strong_person
            total_legacy_veto_candidates += int(legacy_veto_candidate)

            rows.append(
                {
                    "event_id": event.id,
                    "camera_id": camera.id,
                    "camera_name": camera.name,
                    "event_type": event.event_type,
                    "created_at": event.created_at.isoformat() if event.created_at else "",
                    "status": event.status,
                    "is_alarm_active_now": event.is_alarm_active,
                    "motion_passed": passed,
                    "motion_blobs_median": float(groups["blobs"]),
                    "motion_area_pct_median": float(groups["area"]),
                    "motion_displacement_norm": _optional_value(
                        details, "motion_confirm_displacement_norm", ""
                    ),
                    "motion_has_mask": _optional_value(details, "motion_confirm_has_mask", "unknown"),
                    "motion_signal": _optional_value(details, "motion_confirm_signal", "legacy"),
                    "motion_boost": _optional_value(details, "motion_confirm_boost", "false"),
                    "ia2_person_score": ia2_person_score,
                    "ia3_person_score": ia3_person_score,
                    "strong_person_evidence": strong_person,
                    "legacy_motion_veto_candidate": legacy_veto_candidate,
                }
            )

    rows.sort(key=lambda row: (not bool(row["legacy_motion_veto_candidate"]), str(row["created_at"])))
    fields = [
        "event_id",
        "camera_id",
        "camera_name",
        "event_type",
        "created_at",
        "status",
        "is_alarm_active_now",
        "motion_passed",
        "motion_blobs_median",
        "motion_area_pct_median",
        "motion_displacement_norm",
        "motion_has_mask",
        "motion_signal",
        "motion_boost",
        "ia2_person_score",
        "ia3_person_score",
        "strong_person_evidence",
        "legacy_motion_veto_candidate",
    ]
    with output_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Found {total_instrumented} instrumented events.")
    print(
        f"{total_legacy_veto_candidates} are legacy motion-veto candidates. "
        "The current policy never blocks an event solely for lack of motion."
    )
    print(f"Detailed report saved to: {output_csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
