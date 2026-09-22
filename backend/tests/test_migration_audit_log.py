"""Migration-safety test for 0007 (ticket #82, issue #79), matching this
repo's convention (ticket #60's test_db_session_isolation.py, ticket #72's
test_migration_remove_application_auth.py).

Runs against a throwaway database migrated only as far as 0006 first — the
shared test schema (already at head, see conftest.py) can't be used to
observe the table/enum being created for the first time.
"""

from pathlib import Path

from sqlalchemy import create_engine, inspect

from alembic import command
from alembic.config import Config

_ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"


def test_upgrade_creates_audit_log_table_and_enum(scratch_database):
    config = Config(str(_ALEMBIC_INI))
    command.upgrade(config, "0006")

    command.upgrade(config, "0007")

    engine = create_engine(scratch_database)
    try:
        inspector = inspect(engine)
        columns = {column["name"]: column for column in inspector.get_columns("audit_log")}
    finally:
        engine.dispose()

    assert set(columns) == {
        "id",
        "occurred_at",
        "identity",
        "identity_verified",
        "action",
        "target_type",
        "target",
        "failure_reason",
    }
    assert columns["identity"]["nullable"] is True
    assert columns["identity_verified"]["nullable"] is False
    assert columns["target_type"]["nullable"] is False
    assert columns["target"]["nullable"] is False
    assert columns["failure_reason"]["nullable"] is True


def test_downgrade_drops_audit_log_table_and_enum(scratch_database):
    config = Config(str(_ALEMBIC_INI))
    command.upgrade(config, "0007")

    command.downgrade(config, "0006")

    engine = create_engine(scratch_database)
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
    finally:
        engine.dispose()

    assert "audit_log" not in tables
