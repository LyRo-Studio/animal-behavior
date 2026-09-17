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
    instead of ever writing any output at all — simulates a whole-batch
    failure such as the bundled model failing to load.
    """

    version: str = "1.0.0-fake"
    failing_video_stems: frozenset[str] = field(default_factory=frozenset)
    raises: Exception | None = None
    calls: list[list[Path]] = field(default_factory=list)

    def run_reporting(self, video_paths: list[Path], *, output_dir: Path) -> None:
        self.calls.append(list(video_paths))
        if self.raises is not None:
            raise self.raises

        succeeded_any = False
        for video_path in video_paths:
            stem = Path(video_path).stem
            # Mirrors dogtrace.reporting_v24.VideoReport.from_video, which
            # creates this directory up front for every video regardless of
            # whether it goes on to succeed — see orchestrator.py's
            # `_video_produced_output` docstring for why that matters.
            report_dir = output_dir / stem / "20260101_00h00"
            report_dir.mkdir(parents=True, exist_ok=True)
            if stem not in self.failing_video_stems:
                (report_dir / "track_report.xlsx").write_bytes(b"fake-track-report")
                succeeded_any = True

        if succeeded_any:
            (output_dir / "casiop_report.xlsx").write_bytes(b"fake-combined-report")
