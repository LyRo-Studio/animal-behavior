"""The sole seam into DogTrace (ticket #47, "DogTrace integration boundary"
in issue #44) — `orchestrator.py` never imports `dogtrace` itself, so its
tests can inject a fake implementation of `DogTraceRunner` (see
`worker/tests/doubles.py`) instead of requiring the real, GPU-dependent
`dogtrace` package.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from app.services.media_browser import _TEST_ID_RE

# Called by `dogtrace.runner.run_reporting` (as of dogtrace 1.1.1, ticket
# #48) once before and once after each video: `progress(video_path,
# "started")`, then `progress(video_path, "succeeded")` or `progress(
# video_path, "failed")`.
ProgressCallback = Callable[[Path, str], None]


class DogTraceRunner(Protocol):
    @property
    def version(self) -> str:
        """`dogtrace.__version__` — the sole reproducibility field recorded
        per job (CONTEXT.md/issue #44's "Reproducibility" decision)."""
        ...

    def run_reporting(
        self,
        video_paths: list[Path],
        *,
        output_dir: Path,
        progress: ProgressCallback | None = None,
    ) -> None:
        """Run the CASOP pipeline on `video_paths`, writing every produced
        artifact under `output_dir`. Mirrors `dogtrace.runner.run_reporting`
        itself looping per video with its own per-video `try`/`except`
        (issue #44: "this is the existing, deliberate mechanism behind
        'continues on individual failure'") — one video's failure is never
        expected to raise out of this call, only a failure before any video
        could be attempted (e.g. the bundled model failing to load) is.

        `progress`, if given, is invoked per `ProgressCallback` above — see
        `orchestrator.py`'s use of it to update `analysis_job_videos.status`
        in near-real-time (ticket #48).
        """
        ...

    def split_report_by_test(self, video_paths: list[Path], *, output_dir: Path) -> None:
        """Split the combined `output_dir/casiop_report.xlsx` that
        `run_reporting` already wrote into one
        `output_dir/<test_id>/casiop_report.xlsx` per Test it contains
        (ticket #91, issue #88's Feature B). Only called for a job spanning
        more than one Test, and only after `run_reporting` returned. A
        no-op when there's no combined report (every video failed).

        `video_paths` is the same list `run_reporting` was given. The real
        implementation reads the Test ids from the report itself and
        ignores it; `FakeDogTraceRunner` derives them from it instead, so
        orchestration tests never need a real report file.
        """
        ...


class RealDogTraceRunner:
    """The real DogTraceRunner, backed by the `dogtrace` package bundled in
    the `lynndelaere/dogtrace:1.1.1` worker image (see worker/Dockerfile).

    Imports `dogtrace` lazily, inside these methods rather than at module
    level, so this module — and therefore `orchestrator.py` and its tests —
    stays importable outside that image, where `dogtrace`'s GPU/torch/
    ultralytics stack is never installed.
    """

    @property
    def version(self) -> str:
        import dogtrace

        return dogtrace.__version__

    def run_reporting(
        self,
        video_paths: list[Path],
        *,
        output_dir: Path,
        progress: ProgressCallback | None = None,
    ) -> None:
        import dogtrace.runner

        dogtrace.runner.run_reporting(video_paths, output_dir=output_dir, progress=progress)

    def split_report_by_test(self, video_paths: list[Path], *, output_dir: Path) -> None:
        # The one place (with dogtrace itself) that knows the combined
        # report's shape: a single sheet written by `DataFrame.to_excel(...,
        # index=False)`, one row per succeeded video, with `info_test_id`
        # parsed from each video's filename by dogtrace's own pipeline.
        # pandas/openpyxl come with dogtrace in the worker image, so they're
        # imported lazily for the same reason `dogtrace` is.
        import pandas as pd

        combined_report = output_dir / "casiop_report.xlsx"
        if not combined_report.exists():
            return

        rows = pd.read_excel(combined_report)
        for test_id, test_rows in rows.groupby("info_test_id", sort=True):
            # Only ever a real Test id becomes a directory name — the value
            # comes from a file, not from anything this worker validated.
            if not isinstance(test_id, str) or not _TEST_ID_RE.match(test_id):
                continue
            test_dir = output_dir / test_id
            test_dir.mkdir(exist_ok=True)
            test_rows.to_excel(test_dir / "casiop_report.xlsx", index=False)
