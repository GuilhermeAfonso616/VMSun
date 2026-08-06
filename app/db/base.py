import sqlite3

from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import NullPool

from app.core.config import settings

database_url = settings.database_url
is_sqlite = database_url.startswith("sqlite")

connect_args = {}
engine_kwargs = {"pool_pre_ping": True}
if is_sqlite:
    # SQLite continua útil para single-node, mas precisa de menos cache compartilhado
    # e de pragmas mais tolerantes para reduzir lock em cenário multi-processo.
    connect_args = {
        "check_same_thread": False,
        "timeout": 30.0,
    }
    engine_kwargs["poolclass"] = NullPool

engine = create_engine(database_url, connect_args=connect_args, **engine_kwargs)

if is_sqlite:
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, connection_record):  # noqa: ARG001
        if not isinstance(dbapi_connection, sqlite3.Connection):
            return

        try:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL;")
            cursor.execute("PRAGMA synchronous=NORMAL;")
            cursor.execute("PRAGMA busy_timeout=30000;")
            cursor.execute("PRAGMA foreign_keys=ON;")
            cursor.close()
        except Exception:
            pass

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()
