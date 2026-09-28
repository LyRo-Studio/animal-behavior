"""Migration-safety test for 0017 (issue #172): until now the cutting-worker
deleted a succeeded job's source upload but left its paths set. The paths
now mean "still on disk" (`source_retained`), so the upgrade clears them on
every succeeded job and leaves every other job alone.

Runs against a throwaway database migrated only as far as 0016 first, same
convention as test_migration_consolidation_condition.py.
"""

from pathlib import Path

from sqlalchemy import create_engine, text

from alembic import command
from alembic.config import Config

_ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"

_STATUSES = ("queued", "running", "succeeded", "failed", "cancelled")


def _seed_one_job_per_status(scratch_url: str) -> None:
    engine = create_engine(scratch_url)
    try:
        with engine.begin() as conn:
            for status in _STATUSES:
                conn.execute(
                    text(
                        "INSERT INTO cutting_jobs "
                        "(test_id, status, reference_camera, phase_timestamps, "
                        "c1_source_path, c2_source_path) "
                        "VALUES ('T001', :status, 'C1', '{}', :c1, :c2)"
                    ),
                    {
                        "status": status,
                        "c1": f"/data/cutting-uploads/{status}-c1/blob",
                        "c2": f"/data/cutting-uploads/{status}-c2/blob",
                    },
                )
    finally:
        engine.dispose()


def _source_paths_by_status(scratch_url: str) -> dict[str, tuple[str | None, str | None]]:
    engine = create_engine(scratch_url)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT status, c1_source_path, c2_source_path FROM cutting_jobs")
            )
            return {row.status: (row.c1_source_path, row.c2_source_path) for row in rows}
    finally:
        engine.dispose()


def test_upgrade_clears_the_source_paths_of_succeeded_jobs_only(scratch_database):
    config = Config(str(_ALEMBIC_INI))
    command.upgrade(config, "0016")
    _seed_one_job_per_status(scratch_database)

    command.upgrade(config, "0017")

    paths = _source_paths_by_status(scratch_database)
    assert paths["succeeded"] == (None, None)
    for status in ("queued", "running", "failed", "cancelled"):
        assert paths[status] == (
            f"/data/cutting-uploads/{status}-c1/blob",
            f"/data/cutting-uploads/{status}-c2/blob",
        )


def test_downgrade_leaves_the_cleared_paths_cleared(scratch_database):
    """Irreversible: the deleted directories can't be pointed at again."""
    config = Config(str(_ALEMBIC_INI))
    command.upgrade(config, "0016")
    _seed_one_job_per_status(scratch_database)
    command.upgrade(config, "0017")

    command.downgrade(config, "0016")

    assert _source_paths_by_status(scratch_database)["succeeded"] == (None, None)
