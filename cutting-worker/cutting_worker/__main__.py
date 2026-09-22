"""Entrypoint for the cutting-worker (ticket #95, part of issue #93's
Feature C): polls Postgres for queued CuttingJobs and runs each one through
`assist`. Run as `python -m cutting_worker` (see
cutting-worker/Dockerfile's CMD). Mirrors `worker/worker/__main__.py`.
"""

import logging
import shutil
import time
from pathlib import Path

from app.core.config import settings
from app.db.session import SessionLocal
from app.services.cutting_jobs import requeue_stuck_running_cutting_jobs
from app.services.s3_client import get_s3_client
from sqlalchemy.orm import Session

from cutting_worker.orchestrator import process_next_job
from cutting_worker.video_cutter import RealVideoCutter

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)


def requeue_orphaned_jobs(db: Session, *, work_root: Path) -> None:
    """Recover any CuttingJob left `running` by a previous cutting-worker
    process that crashed or was restarted mid-job, mirroring
    `worker/worker/__main__.py`'s own `requeue_orphaned_jobs` — see
    `app.services.cutting_jobs.requeue_stuck_running_cutting_jobs`. Run once,
    before polling starts, so a job stuck `running` from before this process
    existed is never left to sit there forever, and its now-orphaned
    output-scratch directory (nothing can still be reading/writing it — the
    process that owned it is gone) never lingers on disk either. The job's
    local *source* upload is untouched here — it isn't under `work_root` at
    all (see `orchestrator.py`), and staying in place is exactly what makes
    requeuing it safe.
    """
    requeued = requeue_stuck_running_cutting_jobs(db)

    for job in requeued:
        shutil.rmtree(work_root / str(job.id), ignore_errors=True)
        logger.warning("cutting_job_id=%s requeued after an orphaned running state", job.id)


def main() -> None:
    work_root = Path(settings.cutting_worker_work_dir)
    work_root.mkdir(parents=True, exist_ok=True)

    db = SessionLocal()
    try:
        requeue_orphaned_jobs(db, work_root=work_root)
    finally:
        db.close()

    s3_client = get_s3_client()
    video_cutter = RealVideoCutter()
    poll_interval = settings.cutting_worker_poll_interval_seconds

    logger.info("Cutting worker started; polling every %.1fs", poll_interval)

    while True:
        db = SessionLocal()
        try:
            processed = process_next_job(
                db, s3_client=s3_client, video_cutter=video_cutter, work_root=work_root
            )
        finally:
            db.close()

        if not processed:
            time.sleep(poll_interval)


if __name__ == "__main__":
    main()
