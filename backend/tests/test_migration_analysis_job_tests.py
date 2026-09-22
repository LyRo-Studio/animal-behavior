"""Migration-safety test for 0008 (ticket #89 / issue #88's Feature B
foundation), matching this repo's convention (ticket #60's
test_db_session_isolation.py, ticket #72's
test_migration_remove_application_auth.py, ticket #82's
test_migration_audit_log.py).

Runs against a throwaway database migrated only as far as 0007 first — the
shared test schema (already at head, see conftest.py) can't be used to
observe `analysis_jobs.test_id` being backfilled into `analysis_job_tests`
and dropped.
"""

from pathlib import Path

from sqlalchemy import create_engine, inspect, text

from alembic import command
from alembic.config import Config

_ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"


def _seed_jobs(scratch_url: str) -> dict[str, int]:
    """Two single-Test analysis_jobs rows, the only shape that existed
    before this migration — the shape the migration has to backfill into
    `analysis_job_tests` without losing anything."""
    engine = create_engine(scratch_url)
    try:
        with engine.begin() as conn:
            ids = {}
            for test_id in ("T001", "T002"):
                ids[test_id] = conn.execute(
                    text(
                        "INSERT INTO analysis_jobs (test_id, requested_by_identity) "
                        "VALUES (:t, 'jan.peeters@vives.be') RETURNING id"
                    ),
                    {"t": test_id},
                ).scalar_one()
            return ids
    finally:
        engine.dispose()


def test_upgrade_backfills_analysis_job_tests_from_the_old_test_id_column(scratch_database):
    config = Config(str(_ALEMBIC_INI))
    command.upgrade(config, "0007")
    ids = _seed_jobs(scratch_database)

    command.upgrade(config, "0008")

    engine = create_engine(scratch_database)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT analysis_id, test_id FROM analysis_job_tests ORDER BY analysis_id")
            ).all()
    finally:
        engine.dispose()
    assert [tuple(row) for row in rows] == [
        (ids["T001"], "T001"),
        (ids["T002"], "T002"),
    ]


def test_upgrade_drops_the_old_test_id_column_and_index(scratch_database):
    config = Config(str(_ALEMBIC_INI))
    command.upgrade(config, "0007")
    _seed_jobs(scratch_database)

    command.upgrade(config, "0008")

    engine = create_engine(scratch_database)
    try:
        inspector = inspect(engine)
        job_columns = {column["name"] for column in inspector.get_columns("analysis_jobs")}
        test_columns = {column["name"] for column in inspector.get_columns("analysis_job_tests")}
        index_names = {index["name"] for index in inspector.get_indexes("analysis_job_tests")}
    finally:
        engine.dispose()

    assert "test_id" not in job_columns
    assert {"id", "analysis_id", "test_id"} <= test_columns
    assert "ix_analysis_job_tests_analysis_id" in index_names
    assert "ix_analysis_job_tests_test_id" in index_names


def test_downgrade_restores_test_id_from_the_first_associated_test(scratch_database):
    config = Config(str(_ALEMBIC_INI))
    command.upgrade(config, "0007")
    ids = _seed_jobs(scratch_database)
    command.upgrade(config, "0008")

    command.downgrade(config, "0007")

    engine = create_engine(scratch_database)
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT id, test_id FROM analysis_jobs ORDER BY id")).all()
            inspector = inspect(engine)
            tables = set(inspector.get_table_names())
    finally:
        engine.dispose()

    assert [tuple(row) for row in rows] == [
        (ids["T001"], "T001"),
        (ids["T002"], "T002"),
    ]
    assert "analysis_job_tests" not in tables
