"""Test doubles for worker/tests. Named `doubles.py`, not `fakes.py`, to
avoid colliding with backend/tests/fakes.py — both `worker/` and
`../backend` sit on `sys.path` (see worker/pyproject.toml's `pythonpath`),
and neither `tests` directory is a regular package (no `__init__.py`,
matching backend/tests' own convention), so same-named modules in both
would otherwise shadow each other unpredictably.
"""

from dataclasses import dataclass, field
from pathlib import Path

from app.services.s3_client import S3ObjectNotFoundError

from worker.dogtrace_runner import ProgressCallback


@dataclass
class FakeS3Client:
    """Minimal in-memory double covering only what orchestrator.py actually
    calls (`download_file`, `upload_file`) — see
    app/services/s3_client.py's S3Client Protocol. Not a full duplicate of
    backend/tests/fakes.py's fuller FakeS3Client, which covers the
    browsing/streaming methods this worker never uses.
    """

    objects: dict[str, bytes] = field(default_factory=dict)

    def download_file(self, key: str, local_path: Path) -> Path:
        if key not in self.objects:
            raise S3ObjectNotFoundError(key)
        local_path = Path(local_path)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(self.objects[key])
        return local_path

    def upload_file(self, local_path: Path, key: str) -> None:
        self.objects[key] = Path(local_path).read_bytes()


@dataclass
class FakeDogTraceRunner:
    """Stands in for the real, GPU-dependent DogTraceRunner in tests — see
    worker/dogtrace_runner.py's DogTraceRunner Protocol.

    `failing_video_stems` names videos (by filename stem) that should NOT
    get a `track_report.xlsx` written for them, simulating that video's
    pipeline failing while the rest of the batch still succeeds (mirrors
    dogtrace's own per-video fault isolation). `raises`, if set, is raised
    after `raises_after_videos` videos have already been fully progressed
    (started + a terminal event) — `raises_after_videos=0` (the default)
    means before any video is attempted at all, simulating a whole-batch
    failure such as the bundled model failing to load; a higher value
    simulates DogTrace crashing partway through an otherwise-successful
    batch (e.g. a transient CUDA error), leaving the remaining videos
    without ever calling `progress` for them.
    """

    version: str = "1.0.0-fake"
    failing_video_stems: frozenset[str] = field(default_factory=frozenset)
    raises: Exception | None = None
    raises_after_videos: int = 0
    calls: list[list[Path]] = field(default_factory=list)

    def run_reporting(
        self,
        video_paths: list[Path],
        *,
        output_dir: Path,
        progress: ProgressCallback | None = None,
    ) -> None:
        self.calls.append(list(video_paths))
        if self.raises is not None and self.raises_after_videos == 0:
            raise self.raises

        succeeded_any = False
        for index, video_path in enumerate(video_paths):
            if self.raises is not None and index == self.raises_after_videos:
                raise self.raises

            if progress is not None:
                progress(video_path, "started")

            stem = Path(video_path).stem
            # Mirrors dogtrace.reporting_v24.VideoReport.from_video, which
            # creates this directory up front for every video regardless of
            # whether it goes on to succeed — its mere existence can't be
            # used to infer success (that's exactly why ticket #48 moved to
            # a progress callback instead of globbing for it).
            report_dir = output_dir / stem / "20260101_00h00"
            report_dir.mkdir(parents=True, exist_ok=True)
            if stem not in self.failing_video_stems:
                (report_dir / "track_report.xlsx").write_bytes(b"fake-track-report")
                succeeded_any = True
                if progress is not None:
                    progress(video_path, "succeeded")
            elif progress is not None:
                progress(video_path, "failed")

        if succeeded_any:
            (output_dir / "casiop_report.xlsx").write_bytes(b"fake-combined-report")
