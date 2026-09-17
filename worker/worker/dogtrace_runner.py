"""The sole seam into DogTrace (ticket #47, "DogTrace integration boundary"
in issue #44) — `orchestrator.py` never imports `dogtrace` itself, so its
tests can inject a fake implementation of `DogTraceRunner` (see
`worker/tests/fakes.py`) instead of requiring the real, GPU-dependent
`dogtrace` package.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

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
