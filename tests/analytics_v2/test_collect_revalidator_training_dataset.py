from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path

import cv2
import numpy as np

from scripts.collect_revalidator_training_dataset import (
    collect_one,
    ensure_package_dirs,
    query_events,
)


def _create_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE cameras (
                id INTEGER PRIMARY KEY,
                name TEXT,
                ip TEXT
            );
            CREATE TABLE events (
                id INTEGER PRIMARY KEY,
                camera_id INTEGER,
                event_type TEXT,
                track_id INTEGER,
                detector_score REAL,
                confidence REAL,
                event_score REAL,
                details TEXT,
                snapshot_path TEXT,
                clip_path TEXT,
                bbox_json TEXT,
                severity TEXT,
                status TEXT,
                alarm_eligible BOOLEAN,
                lifecycle_action TEXT,
                is_alarm_active BOOLEAN,
                rule_id TEXT,
                zone_id TEXT,
                roi_id TEXT,
                created_at TEXT,
                started_at TEXT,
                ended_at TEXT
            );
            CREATE TABLE event_feedback (
                id INTEGER PRIMARY KEY,
                event_id INTEGER,
                camera_id INTEGER,
                label TEXT,
                probable_cause TEXT,
                operator_note TEXT,
                reviewed_by TEXT,
                reviewed_at TEXT
            );
            """
        )
        conn.execute("INSERT INTO cameras (id, name, ip) VALUES (1, 'Cam Teste', '10.0.0.10')")
        conn.execute(
            """
            INSERT INTO events (
                id, camera_id, event_type, track_id, detector_score, confidence,
                event_score, details, snapshot_path, bbox_json, status, created_at, started_at
            )
            VALUES (100, 1, 'person_entered', 7, 0.8, 0.81, 0.82, 'revalidator_person=0.99',
                    ?, '[20, 30, 80, 150]', 'persisted', '2026-05-11 10:00:00', '2026-05-11 10:00:00')
            """,
            (str(path.parent / "snap.jpg"),),
        )
        conn.execute(
            """
            INSERT INTO event_feedback (id, event_id, camera_id, label, reviewed_by, reviewed_at)
            VALUES (1, 100, 1, 'false_positive', 'operador', '2026-05-11 11:00:00')
            """
        )
        conn.execute(
            """
            INSERT INTO event_feedback (id, event_id, camera_id, label, reviewed_by, reviewed_at)
            VALUES (2, 100, 1, 'true_positive', 'operador', '2026-05-11 12:00:00')
            """
        )
        conn.commit()
    finally:
        conn.close()


def test_collect_training_dataset_exports_latest_feedback_and_crop_variants(tmp_path):
    image = np.full((180, 120, 3), 120, dtype=np.uint8)
    cv2.rectangle(image, (20, 30), (80, 150), (255, 255, 255), -1)
    assert cv2.imwrite(str(tmp_path / "snap.jpg"), image)

    db_path = tmp_path / "analytics.db"
    _create_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = query_events(
            conn,
            since=None,
            until=None,
            camera_id=None,
            include_unreviewed=False,
            include_feedback_history=False,
            limit=None,
        )
    finally:
        conn.close()

    assert len(rows) == 1
    assert rows[0]["feedback_label"] == "true_positive"

    package_dir = tmp_path / "package"
    ensure_package_dirs(package_dir)
    metadata = collect_one(
        rows[0],
        package_dir=package_dir,
        base_dir=tmp_path,
        snapshot_roots=[],
        ia2_margin=0.20,
        ia3_margin=0.55,
        min_crop_size=16,
        include_snapshot_copy=True,
    )

    assert metadata["class_name"] == "person"
    assert metadata["crop_ia2_path"]
    assert metadata["crop_ia3_far_path"]
    assert metadata["context_path"]
    assert metadata["snapshot_copy_path"]
    assert (package_dir / metadata["crop_ia2_path"]).exists()
    assert (package_dir / metadata["crop_ia3_far_path"]).exists()
    metadata_path = package_dir / metadata["metadata_path"]
    saved = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert saved["event_id"] == 100
    assert saved["label"] == "true_positive"
