"""Migration-safety test for 0009 (ticket #94, part of issue #93's Feature
C), matching this repo's convention (ticket #82's
test_migration_audit_log.py, ticket #89's test_migration_analysis_job_tests.py).

Runs against a throwaway database migrated only as far as 0008 first — the
shared test schema (already at head, see conftest.py) can't be used to
observe the tables/enums being created for the first time.
"""

from pathlib import Path

from sqlalchemy import create_engine, inspect

from alembic import command
from alembic.config import Config

_ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"


def test_upgrade_creates_cutting_jobs_and_cutting_job_outputs_tables(scratch_database):
    config = Config(str(_ALEMBIC_INI))
    command.upgrade(config, "0008")

    command.upgrade(config, "0009")

    engine = create_engine(scratch_database)
    try:
        inspector = inspect(engine)
        job_columns = {column["name"]: column for column in inspector.get_columns("cutting_jobs")}
        output_columns = {
            column["name"]: column for column in inspector.get_columns("cutting_job_outputs")
        }
        output_uniques = {
            tuple(uc["column_names"])
            for uc in inspector.get_unique_constraints("cutting_job_outputs")
        }
    finally:
        engine.dispose()

    assert set(job_columns) == {
        "id",
        "test_id",
        "requested_by_identity",
        "status",
        "reference_camera",
        "phase_timestamps",
        "c1_source_path",
        "c2_source_path",
        "created_at",
        "started_at",
        "finished_at",
    }
    assert job_columns["test_id"]["nullable"] is False
    assert job_columns["requested_by_identity"]["nullable"] is True
    assert job_columns["status"]["nullable"] is False
    assert job_columns["reference_camera"]["nullable"] is False
    assert job_columns["phase_timestamps"]["nullable"] is False
    assert job_columns["c1_source_path"]["nullable"] is True
    assert job_columns["c2_source_path"]["nullable"] is True

    assert set(output_columns) == {
        "id",
        "cutting_job_id",
        "camera",
        "condition",
        "phase",
        "status",
        "failure_reason",
    }
    assert output_columns["cutting_job_id"]["nullable"] is False
    assert output_columns["status"]["nullable"] is False
    assert output_columns["failure_reason"]["nullable"] is True
    assert ("cutting_job_id", "camera", "condition", "phase") in output_uniques


def test_downgrade_drops_cutting_job_tables_and_enums(scratch_database):
    config = Config(str(_ALEMBIC_INI))
    command.upgrade(config, "0009")

    command.downgrade(config, "0008")

    engine = create_engine(scratch_database)
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
    finally:
        engine.dispose()

    assert "cutting_jobs" not in tables
    assert "cutting_job_outputs" not in tables


def test_cutting_job_status_and_output_status_enum_values(scratch_database):
    """The two new native Postgres enums carry exactly the values the ORM
    model (app/models/cutting_job.py) expects."""
    config = Config(str(_ALEMBIC_INI))
    command.upgrade(config, "0009")

    engine = create_engine(scratch_database)
    try:
        with engine.connect() as conn:
            job_status_values = {
                row[0]
                for row in conn.exec_driver_sql(
                    "SELECT unnest(enum_range(NULL::cutting_job_status))"
                )
            }
            output_status_values = {
                row[0]
                for row in conn.exec_driver_sql(
                    "SELECT unnest(enum_range(NULL::cutting_job_output_status))"
                )
            }
    finally:
        engine.dispose()

    assert job_status_values == {"queued", "running", "succeeded", "failed", "cancelled"}
    assert output_status_values == {"pending", "succeeded", "failed"}
