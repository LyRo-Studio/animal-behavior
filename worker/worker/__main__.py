"""Entrypoint for the analysis worker (ticket #47, part of #44): polls
Postgres for queued AnalysisJobs and runs each one through DogTrace. Run as
`python -m worker` (see worker/Dockerfile's CMD).
"""

import logging
import shutil
import time
from pathlib import Path

from app.core.config import settings
from app.db.session import SessionLocal
from app.services.analyses import requeue_stuck_running_jobs
from app.services.s3_client import get_s3_client
from sqlalchemy.orm import Session

from worker.dogtrace_runner import RealDogTraceRunner
from worker.orchestrator import process_next_job

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)


def requeue_orphaned_jobs(db: Session, *, work_root: Path) -> None:
    """Recover any AnalysisJob left `running` by a previous worker process
    that crashed or was restarted mid-job (ticket #50) — see
    `app.services.analyses.requeue_stuck_running_jobs` and CONTEXT.md's
    "Crash recovery: requeue, not fail" decision. Run once, before polling
    starts, so a job stuck `running` from before this process existed is
    never left to sit there forever, and its now-orphaned temp input
    directory (nothing can still be reading/writing it — the process that
    owned it is gone) never lingers on disk either.

    `db` injected, `work_root` keyword-only — same shape as
    `process_next_job` below. Not underscore-prefixed for the same reason
    that one isn't: like the poll loop's body, this is otherwise-untestable
    process-startup glue (real DB engine, real filesystem), so it's the
    deliberate seam `worker/tests/test_startup_recovery.py` exercises
    directly, not a private detail incidentally exposed to tests.

    This also fixes a latent bug in the previous shape of this function: it
    used to open and close its own short-lived session *before* reading
    `job.id` in the loop below. `SessionLocal` defaults to
    `expire_on_commit=True`, and `requeue_stuck_running_jobs` commits
    internally — so every attribute on `job` was already expired by the
    time the loop ran, and reading one off a session that was then also
    closed would raise `DetachedInstanceError`. In other words: the very
    first time a real worker crash ever left a job orphaned, recovery
    itself would have crashed on the next startup. Never triggered before
    ticket #50 added test coverage that actually calls this with a
    genuinely stuck job in play.
    """
    requeued = requeue_stuck_running_jobs(db)

    for job in requeued:
        shutil.rmtree(work_root / str(job.id), ignore_errors=True)
        logger.warning("analysis_id=%s requeued after an orphaned running state", job.id)


def main() -> None:
    work_root = Path(settings.analysis_worker_work_dir)
    work_root.mkdir(parents=True, exist_ok=True)

    db = SessionLocal()
    try:
        requeue_orphaned_jobs(db, work_root=work_root)
    finally:
        db.close()

    s3_client = get_s3_client()
    dogtrace_runner = RealDogTraceRunner()
    poll_interval = settings.analysis_worker_poll_interval_seconds

    logger.info(
        "Analysis worker started (dogtrace %s); polling every %.1fs",
        dogtrace_runner.version,
        poll_interval,
    )

    while True:
        db = SessionLocal()
        try:
            processed = process_next_job(
                db, s3_client=s3_client, dogtrace_runner=dogtrace_runner, work_root=work_root
            )
        finally:
            db.close()

        if not processed:
            time.sleep(poll_interval)


if __name__ == "__main__":
    main()
