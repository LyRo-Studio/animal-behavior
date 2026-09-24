"""Test doubles for cutting-worker/tests. Named `doubles.py`, not
`fakes.py`, to avoid colliding with backend/tests/fakes.py — both
`cutting-worker/` and `../backend` sit on `sys.path` (see
cutting-worker/pyproject.toml's `pythonpath`), and neither `tests` directory
is a regular package, so same-named modules in both would otherwise shadow
each other unpredictably. Mirrors worker/tests/doubles.py's own reasoning.
"""

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, time
from io import BytesIO
from pathlib import Path

from app.services.cutting_jobs import SourceVideoUpload, create_cutting_job
from app.services.media_prober import ProbedMediaInfo
from app.services.s3_client import S3ObjectInfo, S3ObjectNotFoundError
from openpyxl import Workbook

from cutting_worker.video_cutter import PHASE_ORDER

_HEADERS = ["Test ID", "Dog ID", "C1/C2", *PHASE_ORDER]
# Every object reports the same last_modified; nothing here reads it.
_LAST_MODIFIED = datetime(2026, 1, 1, tzinfo=UTC)


@dataclass
class FakeS3Client:
    """Minimal in-memory double covering only what orchestrator.py actually
    calls (`upload_file`) plus `head_object`/`list_objects_info` (needed by
    `create_cutting_job` itself, for its source-collision and ticket #96
    existing-Cuts checks) — see
    app/services/s3_client.py's S3Client Protocol. Mirrors
    worker/tests/doubles.py's own minimal FakeS3Client.
    """

    objects: dict[str, bytes] = field(default_factory=dict)

    def head_object(self, key: str):
        if key not in self.objects:
            raise S3ObjectNotFoundError(key)
        raise AssertionError(f"unexpected collision: {key!r} already exists in FakeS3Client")

    def list_objects_info(
        self, prefix: str = "", *, recursive: bool = True
    ) -> Iterator[S3ObjectInfo]:
        # Same filtering as backend/tests/fakes.py's FakeS3Client (folder
        # markers skipped), so `has_cuts` behaves identically in both suites.
        for key, data in self.objects.items():
            if key.endswith("/") or not key.startswith(prefix):
                continue
            if not recursive and "/" in key[len(prefix) :]:
                continue
            yield S3ObjectInfo(key=key, size=len(data), last_modified=_LAST_MODIFIED)

    def upload_file(self, local_path: Path, key: str) -> None:
        self.objects[key] = Path(local_path).read_bytes()


@dataclass
class FakeMediaProber:
    """Stands in for FfprobeMediaProber — `create_cutting_job` (ticket #94)
    probes every upload before a job is ever created; every upload built by
    this module's `build_cutting_job` is fake bytes, never a real video, so
    this always reports a plausible, fixed result rather than shelling out
    to real ffprobe."""

    def probe(self, path: Path) -> ProbedMediaInfo:
        return ProbedMediaInfo(duration_seconds=600.0, width=1920, height=1080, codec="h264")


@dataclass
class FakeVideoCutter:
    """Stands in for the real, `assist`-backed RealVideoCutter in tests —
    see cutting_worker/video_cutter.py's VideoCutter Protocol.

    `missing_outputs` names (camera, "ME_F1"-style phase key) pairs that
    should NOT get a Cut file written for them, simulating that phase
    failing to slice while the rest of the job still succeeds. `raises`, if
    set, is raised before anything is written, simulating a whole-job
    failure (e.g. audio cross-correlation failing outright).

    Each file is written one at a time and reported through `on_output`
    (ticket #98), unless `report_outputs` is False (a cutter that writes but
    never reports). `after_each_output`, if set, is called with each file's
    path right after it's reported — a test's window into the job mid-run.
    """

    missing_outputs: frozenset[tuple[str, str]] = field(default_factory=frozenset)
    raises: Exception | None = None
    report_outputs: bool = True
    after_each_output: Callable[[Path], None] | None = None
    calls: list[dict] = field(default_factory=list)

    def cut(
        self,
        *,
        test_id: str,
        reference_camera: str,
        phase_timestamps: dict[str, int],
        source_paths: dict[str, Path],
        output_dir: Path,
        on_output: Callable[[Path], None],
    ) -> None:
        self.calls.append(
            {
                "test_id": test_id,
                "reference_camera": reference_camera,
                "phase_timestamps": dict(phase_timestamps),
                "source_paths": dict(source_paths),
            }
        )
        if self.raises is not None:
            raise self.raises

        output_dir.mkdir(parents=True, exist_ok=True)
        for camera in source_paths:
            for index in range(len(PHASE_ORDER) - 1):
                phase_name = PHASE_ORDER[index]
                next_phase_name = PHASE_ORDER[index + 1]
                if phase_name not in phase_timestamps or next_phase_name not in phase_timestamps:
                    continue
                if (camera, phase_name) in self.missing_outputs:
                    continue
                output_file = output_dir / f"{test_id}_{camera}_{phase_name}.mp4"
                output_file.write_bytes(b"fake-cut-bytes")
                if self.report_outputs:
                    on_output(output_file)
                if self.after_each_output is not None:
                    self.after_each_output(output_file)


def _workbook_bytes(test_id: str, *, reference_camera: str, phases: list[str]) -> bytes:
    """A workbook with one row for `test_id`, every header in `phases` given
    a distinct, valid (non-skipped) elapsed time, everything else blank
    (skipped) — mirrors backend/tests/test_cutting_jobs.py's own
    `_workbook_bytes` helper (duplicated, not imported: backend/tests isn't
    a package and isn't on this project's pythonpath either)."""
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(_HEADERS)
    row = [test_id, "Rex", reference_camera]
    for index, header in enumerate(PHASE_ORDER):
        row.append(time(index + 1, 0, 0) if header in phases else None)
    sheet.append(row)
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _source_video(uploads_root: Path, *, camera: str, test_id: str) -> SourceVideoUpload:
    """A fake uploaded source video, laid out the same way
    app/services/cutting_uploads.py's real chunked-upload intake would leave
    one on disk: its own directory, holding just the video bytes (`blob`
    isn't needed as a filename here — `create_cutting_job` only cares about
    `SourceVideoUpload.local_path`/`.filename`, not the directory shape
    around it)."""
    upload_dir = uploads_root / f"{camera}-{test_id}"
    upload_dir.mkdir(parents=True, exist_ok=True)
    path = upload_dir / "blob"
    path.write_bytes(b"not a real video, just needs to exist")
    return SourceVideoUpload(camera=camera, local_path=path, filename=f"{test_id}_{camera}_.mp4")


def build_cutting_job(
    db_session,
    *,
    test_id: str = "T001",
    reference_camera: str = "C1",
    cameras: tuple[str, ...] = ("C1",),
    phases: list[str] | None = None,
    uploads_root: Path,
):
    """Create a real, `queued` CuttingJob (via `create_cutting_job`) for
    orchestrator tests to claim and process — the fixed setup every test in
    this directory needs (a valid Excel row, `cameras` worth of fake
    uploads, a FakeS3Client/FakeMediaProber that always accept them), so
    each test only states what's different about it.
    """
    if phases is None:
        phases = PHASE_ORDER
    excel_bytes = _workbook_bytes(test_id, reference_camera=reference_camera, phases=phases)
    uploads = [_source_video(uploads_root, camera=camera, test_id=test_id) for camera in cameras]
    return create_cutting_job(
        db_session,
        requested_by_identity="jan.peeters@vives.be",
        test_id=test_id,
        excel_bytes=excel_bytes,
        uploads=uploads,
        s3=FakeS3Client(),
        media_prober=FakeMediaProber(),
    )
