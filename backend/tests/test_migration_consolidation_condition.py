"""Migration-safety test for 0016 (ticket #149): one upload now produces
every Consolidation level, so the Condition goes. Consolidations made under
the old per-Condition format are removed; the audit log is untouched.

Runs against a throwaway database migrated only as far as 0015 first, same
convention as test_migration_audit_log.py.
"""

from pathlib import Path

from sqlalchemy import create_engine, inspect, text

from alembic import command
from alembic.config import Config

_ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"


def _seed_old_consolidations(scratch_url: str) -> None:
    """Two old-format consolidations, each with its audit rows."""
    engine = create_engine(scratch_url)
    try:
        with engine.begin() as conn:
            for condition, status in [("ME", "completed"), ("ZE", "failed")]:
                consolidation_id = conn.execute(
                    text(
                        "INSERT INTO consolidations "
                        "(original_filename, condition, status, input_size_bytes) "
                        "VALUES ('export.xlsx', :condition, :status, 1) RETURNING id"
                    ),
                    {"condition": condition, "status": status},
                ).scalar_one()
                for action in ("consolidation_started", f"consolidation_{status}"):
                    conn.execute(
                        text(
                            "INSERT INTO audit_log "
                            "(identity_verified, action, target_type, target) "
                            "VALUES (false, :action, 'consolidation', :target)"
                        ),
                        {"action": action, "target": str(consolidation_id)},
                    )
    finally:
        engine.dispose()


def _audit_rows(conn) -> list[tuple]:
    return [
        tuple(row)
        for row in conn.execute(
            text("SELECT id, occurred_at, action, target FROM audit_log ORDER BY id")
        )
    ]


def _enum_types(conn) -> set[str]:
    return set(conn.execute(text("SELECT typname FROM pg_type WHERE typtype = 'e'")).scalars())


def test_upgrade_removes_every_consolidation_and_the_condition(scratch_database):
    config = Config(str(_ALEMBIC_INI))
    command.upgrade(config, "0015")
    _seed_old_consolidations(scratch_database)
    engine = create_engine(scratch_database)
    try:
        with engine.connect() as conn:
            audit_before = _audit_rows(conn)

        command.upgrade(config, "0016")

        columns = {column["name"] for column in inspect(engine).get_columns("consolidations")}
        with engine.connect() as conn:
            remaining = conn.execute(text("SELECT count(*) FROM consolidations")).scalar_one()
            audit_after = _audit_rows(conn)
            enum_types = _enum_types(conn)
    finally:
        engine.dispose()

    assert remaining == 0
    assert "condition" not in columns
    assert "consolidation_condition" not in enum_types
    assert "consolidation_status" in enum_types
    assert len(audit_before) == 4
    assert audit_after == audit_before


def test_downgrade_restores_only_the_condition_column(scratch_database):
    config = Config(str(_ALEMBIC_INI))
    command.upgrade(config, "0016")
    engine = create_engine(scratch_database)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO consolidations (original_filename, status, input_size_bytes) "
                    "VALUES ('export.xlsx', 'completed', 1)"
                )
            )

        command.downgrade(config, "0015")

        columns = {
            column["name"]: column for column in inspect(engine).get_columns("consolidations")
        }
        with engine.connect() as conn:
            conditions = conn.execute(text("SELECT condition FROM consolidations")).scalars().all()
            enum_types = _enum_types(conn)
    finally:
        engine.dispose()

    assert columns["condition"]["nullable"] is False
    assert columns["condition"]["default"] is None
    assert "consolidation_condition" in enum_types
    # A row made after the upgrade holds every level; ME_ZE was the old
    # Condition covering both.
    assert conditions == ["ME_ZE"]
