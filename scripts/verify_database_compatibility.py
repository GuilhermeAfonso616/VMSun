"""Validate the runtime schema on a disposable SQLite or PostgreSQL database.

The application currently uses SQLAlchemy ``create_all`` followed by additive,
idempotent migrations. This command exercises the same sequence twice and
checks that every mapped table/column exists afterwards.

When no URL is supplied, a temporary SQLite database is used. An explicit
database must be empty; the command deliberately refuses to touch a database
that already contains tables.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


LEGACY_TABLES_SQL = (
    """
    CREATE TABLE cameras (
        id INTEGER PRIMARY KEY,
        name VARCHAR NOT NULL,
        ip VARCHAR NOT NULL,
        onvif_port INTEGER,
        username VARCHAR NOT NULL,
        password VARCHAR NOT NULL,
        rtsp_url VARCHAR,
        status VARCHAR,
        created_at DATETIME
    )
    """,
    """
    CREATE TABLE events (
        id INTEGER PRIMARY KEY,
        camera_id INTEGER NOT NULL REFERENCES cameras(id),
        event_type VARCHAR NOT NULL,
        track_id INTEGER,
        confidence FLOAT,
        details TEXT,
        created_at DATETIME
    )
    """,
    """
    CREATE TABLE users (
        id INTEGER PRIMARY KEY,
        username VARCHAR NOT NULL,
        password_hash VARCHAR NOT NULL,
        name VARCHAR,
        role VARCHAR NOT NULL,
        is_active BOOLEAN,
        created_at DATETIME,
        updated_at DATETIME
    )
    """,
    """
    CREATE TABLE view_presets (
        id VARCHAR PRIMARY KEY,
        name VARCHAR NOT NULL,
        grid_size INTEGER NOT NULL,
        camera_ids VARCHAR NOT NULL,
        hide_offline BOOLEAN,
        boxes_enabled BOOLEAN,
        created_at DATETIME
    )
    """,
    """
    CREATE TABLE temporal_sequences (
        id VARCHAR PRIMARY KEY,
        name VARCHAR NOT NULL,
        steps VARCHAR NOT NULL,
        created_at DATETIME
    )
    """,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate Analitico database compatibility.")
    parser.add_argument("--mode", choices=("fresh", "legacy"), default="fresh")
    parser.add_argument(
        "--database-url",
        default="",
        help="Empty disposable database URL. Omit to use a temporary SQLite database.",
    )
    return parser.parse_args()


def _configure_environment(database_url: str, runtime_dir: Path) -> None:
    os.environ["DATABASE_URL"] = database_url
    os.environ["RUNTIME_STATE_DIR"] = str(runtime_dir)
    os.environ["AUTH_SECRET_KEY_FILE"] = str(runtime_dir / "auth_secret_key")
    os.environ["AUTH_SECRET_KEY"] = "stage0-database-compatibility-check"


def _validate(database_url: str, mode: str, runtime_dir: Path) -> None:
    _configure_environment(database_url, runtime_dir)

    # Imports must happen after configuring DATABASE_URL because these modules
    # intentionally create their engine once per process.
    from sqlalchemy import inspect, text

    from app.db import models  # noqa: F401 - registers SQLAlchemy mappings
    from app.db.base import Base, engine
    from app.services.db_migrations import ensure_runtime_schema

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    if existing_tables:
        raise RuntimeError(
            "Compatibility checks require an empty database; found: "
            + ", ".join(sorted(existing_tables))
        )

    if mode == "legacy":
        if engine.dialect.name != "sqlite":
            raise RuntimeError("Legacy upgrade simulation is currently supported only on SQLite.")
        with engine.begin() as connection:
            for statement in LEGACY_TABLES_SQL:
                connection.execute(text(statement))

    Base.metadata.create_all(bind=engine)
    ensure_runtime_schema()
    ensure_runtime_schema()

    inspector = inspect(engine)
    actual_tables = set(inspector.get_table_names())
    expected_tables = set(Base.metadata.tables)
    missing_tables = expected_tables - actual_tables
    if missing_tables:
        raise RuntimeError("Missing mapped tables: " + ", ".join(sorted(missing_tables)))

    missing_columns: list[str] = []
    for table_name, table in Base.metadata.tables.items():
        actual_columns = {column["name"] for column in inspector.get_columns(table_name)}
        for column in table.columns:
            if column.name not in actual_columns:
                missing_columns.append(f"{table_name}.{column.name}")
    if missing_columns:
        raise RuntimeError("Missing mapped columns: " + ", ".join(sorted(missing_columns)))

    event_indexes = {index["name"] for index in inspector.get_indexes("events")}
    required_indexes = {
        "idx_events_camera_created",
        "idx_events_status_created",
        "idx_events_created",
    }
    missing_indexes = required_indexes - event_indexes
    if missing_indexes:
        raise RuntimeError("Missing runtime indexes: " + ", ".join(sorted(missing_indexes)))

    print(
        f"Database compatibility OK: dialect={engine.dialect.name} "
        f"mode={mode} tables={len(actual_tables)}"
    )
    engine.dispose()


def main() -> int:
    args = parse_args()
    if args.database_url:
        runtime_dir = PROJECT_ROOT / ".tmp_db_compat_runtime"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        _validate(args.database_url, args.mode, runtime_dir)
        return 0

    with tempfile.TemporaryDirectory(prefix="analitico-db-compat-") as temp_dir:
        root = Path(temp_dir)
        database_url = f"sqlite:///{(root / 'compatibility.db').as_posix()}"
        _validate(database_url, args.mode, root / "runtime")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Database compatibility failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
