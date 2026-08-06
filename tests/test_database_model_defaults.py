from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from app.db.models import LockdownDelivery, User


def _postgres_create_sql(model) -> str:
    return str(CreateTable(model.__table__).compile(dialect=postgresql.dialect()))


def test_integer_counters_use_numeric_defaults_in_postgres_ddl():
    user_sql = _postgres_create_sql(User)
    delivery_sql = _postgres_create_sql(LockdownDelivery)

    assert "login_attempts INTEGER DEFAULT 0 NOT NULL" in user_sql
    assert "attempt_count INTEGER DEFAULT 0 NOT NULL" in delivery_sql
    assert "login_attempts INTEGER DEFAULT false" not in user_sql
    assert "attempt_count INTEGER DEFAULT false" not in delivery_sql
