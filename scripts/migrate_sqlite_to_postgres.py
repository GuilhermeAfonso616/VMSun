"""Migra os dados do SQLite local para o PostgreSQL configurado em DATABASE_URL.

Uso comum:
    python scripts/migrate_sqlite_to_postgres.py --truncate
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import MetaData, Table, create_engine, inspect, select, text


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_SQLITE_URL = f"sqlite:///{(ROOT_DIR / 'data' / 'analytics.db').as_posix()}"
CHUNK_SIZE = 500

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sqlite-url",
        default=DEFAULT_SQLITE_URL,
        help=f"Origem SQLite. Default: {DEFAULT_SQLITE_URL}",
    )
    parser.add_argument(
        "--postgres-url",
        default=os.environ.get("DATABASE_URL", ""),
        help="Destino PostgreSQL. Default: valor de DATABASE_URL.",
    )
    parser.add_argument(
        "--truncate",
        action="store_true",
        help="Limpa as tabelas do PostgreSQL antes de copiar os dados.",
    )
    return parser.parse_args()


def _copy_table(source_engine: Any, target_conn: Any, target_table: Table) -> int:
    source_metadata = MetaData()
    source_table = Table(target_table.name, source_metadata, autoload_with=source_engine)

    target_column_names = {column.name for column in target_table.columns}
    common_column_names = [
        column.name for column in source_table.columns if column.name in target_column_names
    ]
    if not common_column_names:
        return 0

    selected_columns = [source_table.c[name] for name in common_column_names]
    copied = 0
    batch: list[dict[str, Any]] = []

    with source_engine.connect() as source_conn:
        for row in source_conn.execute(select(*selected_columns)).mappings():
            batch.append(dict(row))
            if len(batch) >= CHUNK_SIZE:
                target_conn.execute(target_table.insert(), batch)
                copied += len(batch)
                batch.clear()

    if batch:
        target_conn.execute(target_table.insert(), batch)
        copied += len(batch)

    return copied


def _reset_identity(target_conn: Any, table_name: str) -> None:
    dialect = target_conn.engine.dialect.name
    if dialect != "postgresql":
        return

    quoted = target_conn.engine.dialect.identifier_preparer.quote(table_name)
    target_conn.execute(
        text(
            f"""
            SELECT setval(
                pg_get_serial_sequence('{table_name}', 'id'),
                COALESCE(MAX(id), 1),
                MAX(id) IS NOT NULL
            )
            FROM {quoted}
            """
        )
    )


def main() -> int:
    args = _parse_args()
    if not args.postgres_url:
        raise SystemExit("Informe --postgres-url ou configure DATABASE_URL.")
    if args.postgres_url.startswith("sqlite"):
        raise SystemExit("O destino precisa ser PostgreSQL, nao SQLite.")

    os.environ["DATABASE_URL"] = args.postgres_url

    from app.db.base import Base, engine as target_engine
    import app.db.models  # noqa: F401  # registra os modelos no metadata

    source_engine = create_engine(args.sqlite_url)
    source_inspector = inspect(source_engine)

    Base.metadata.create_all(bind=target_engine)
    copied_by_table: dict[str, int] = {}

    with target_engine.begin() as target_conn:
        if args.truncate:
            for table in reversed(Base.metadata.sorted_tables):
                quoted = target_engine.dialect.identifier_preparer.quote(table.name)
                target_conn.execute(text(f"TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE"))

        for table in Base.metadata.sorted_tables:
            if not source_inspector.has_table(table.name):
                continue
            copied_by_table[table.name] = _copy_table(source_engine, target_conn, table)

        for table in Base.metadata.sorted_tables:
            if "id" in table.columns:
                _reset_identity(target_conn, table.name)

    for table_name, count in copied_by_table.items():
        print(f"{table_name}: {count} linhas copiadas")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
