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

from worker.dogtrace_runner import RealDogTraceRunner
from worker.orchestrator import process_next_job

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)


def _requeue_orphaned_jobs(work_root: Path) -> None:
    """Recover any AnalysisJob left `running` by a previous worker process
    that crashed or was restarted mid-job — see
    `app.services.analyses.requeue_stuck_running_jobs` and CONTEXT.md's
    "Analysis worker" decision. Run once, before polling starts.
    """
    db = SessionLocal()
    try:
        requeued = requeue_stuck_running_jobs(db)
    finally:
        db.close()

    for job in requeued:
        shutil.rmtree(work_root / str(job.id), ignore_errors=True)
        logger.warning("analysis_id=%s requeued after an orphaned running state", job.id)


def main() -> None:
    work_root = Path(settings.analysis_worker_work_dir)
    work_root.mkdir(parents=True, exist_ok=True)

    _requeue_orphaned_jobs(work_root)

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
