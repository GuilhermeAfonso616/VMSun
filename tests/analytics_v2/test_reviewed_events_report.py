from __future__ import annotations

import sqlite3
from datetime import datetime

from scripts.generate_reviewed_events_report import extract_ai_scores, infer_false_positive_reason, load_reviewed_events


def test_extract_ai_scores_from_details():
    row = {
        "detector_score": 0.7,
        "event_score": 0.8,
        "details": "Pessoa | revalidator_person=0.183 threshold=0.01 mode=block | far_revalidator_person=0.0006 threshold=0.005",
    }

    scores = extract_ai_scores(row)

    assert scores["ia1_detector_score"] == 0.7
    assert scores["ia2_person_score"] == 0.183
    assert scores["ia2_status"] == "negou_visual_mas_passou_politica_conservadora"
    assert scores["ia3_person_score"] == 0.0006
    assert scores["ia3_status"] == "negou_pessoa_forte"


def test_false_positive_reason_mentions_ia2_and_ia3():
    row = {
        "feedback_label": "false_positive",
        "probable_cause": "glass_reflection",
        "ia1_detector_score": 0.7,
        "ia2_person_score": 0.18,
        "ia3_person_score": 0.0006,
    }

    reason = infer_false_positive_reason(row)

    assert "IA2 nao confirmou pessoa" in reason
    assert "IA3 negou pessoa muito forte" in reason


def test_load_reviewed_events_uses_latest_feedback(tmp_path):
    db_path = tmp_path / "analytics.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE cameras (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE events (
            id INTEGER PRIMARY KEY,
            camera_id INTEGER,
            event_type TEXT,
            started_at TEXT,
            ended_at TEXT,
            created_at TEXT,
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
            alarm_eligible INTEGER,
            lifecycle_action TEXT,
            is_alarm_active INTEGER,
            rule_id TEXT,
            zone_id TEXT,
            roi_id TEXT
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
    conn.execute("INSERT INTO cameras VALUES (14, 'Teste14')")
    conn.execute(
        """
        INSERT INTO events VALUES (
            5274, 14, 'person_entered', '2026-05-10 09:10:58', NULL, '2026-05-10 09:10:58',
            1020, 0.7, 0.7, 0.7,
            'Pessoa | revalidator_person=0.183 threshold=0.01 | far_revalidator_person=0.0006 threshold=0.005',
            '/data/a.jpg', NULL, '[1,2,3,4]', 'high', 'persisted', 1, 'open', 1, 'intrusion_default', 'roi', 'roi'
        )
        """
    )
    conn.execute("INSERT INTO event_feedback VALUES (1, 5274, 14, 'true_positive', 'normal_human_flow', '', 'a', '2026-05-10 10:00:00')")
    conn.execute("INSERT INTO event_feedback VALUES (2, 5274, 14, 'false_positive', 'glass_reflection', 'vidro', 'a', '2026-05-10 11:00:00')")
    conn.commit()
    conn.close()

    rows = load_reviewed_events(
        f"sqlite:///{db_path.as_posix()}",
        datetime(2026, 5, 10, 0, 0, 0),
        datetime(2026, 5, 11, 0, 0, 0),
    )

    assert len(rows) == 1
    assert rows[0]["feedback_label_normalized"] == "false_positive"
    assert rows[0]["probable_cause"] == "glass_reflection"
