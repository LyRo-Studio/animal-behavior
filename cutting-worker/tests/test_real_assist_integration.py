"""Opt-in real-assist/real-ffmpeg integration test for the cutting-worker
(ticket #95, part of issue #93's Feature C). Exercises `RealVideoCutter` —
the actual `assist` package (pinned at `master`,
github.com/vives-devbit/assist) plus real ffmpeg — against a real sample
source video pair, never a fake. Everything else in this directory
deliberately stays on `FakeVideoCutter`/`FakeS3Client` (issue #93's testing
strategy for this seam).

Opt-in only, via the `real_assist` marker — cutting-worker/pyproject.toml's
default `addopts` excludes it, so a plain `pytest` run and CI (no `assist`,
no ffmpeg installed there — see cutting-worker/Dockerfile's "no FastAPI/test
deps in the production image" boundary, mirrored for `assist` itself) never
touch either. Run for real, from inside a shell in a container built from
cutting-worker/Dockerfile (which does have both):

    pip install --no-cache-dir -r cutting-worker/requirements-dev.txt
    python -m pytest -m real_assist cutting-worker/tests/test_real_assist_integration.py

Needs two env vars naming a real local C1/C2 source video pair on disk
(never bundled into the repo — real recordings, not synthetic fixtures):
`REAL_ASSIST_C1_SOURCE_PATH` / `REAL_ASSIST_C2_SOURCE_PATH`. Skips (rather
than fails) whenever a prerequisite this test itself checks is missing —
`assist` not importable, ffmpeg not on PATH, or the env vars unset/pointing
at a nonexistent file — same "clear skip reason, not a crash" pattern as
worker/tests/test_real_gpu_inference.py's `real_gpu` marker.
"""

import os
import shutil
from pathlib import Path

import pytest

from cutting_worker.video_cutter import RealVideoCutter, _import_assist

pytestmark = pytest.mark.real_assist


def _require_real_assist_and_ffmpeg() -> tuple[Path, Path]:
    try:
        # `_import_assist()`, not a bare `import assist` — this test needs
        # the same `sys.argv`-clearing/`SystemExit`-swallowing workaround
        # `RealVideoCutter.cut()` itself uses (see video_cutter.py's own
        # docstring): a plain `import assist` here would run assist's Click
        # CLI against pytest's own argv and crash with `SystemExit` instead
        # of cleanly skipping or running the test (caught in review).
        _import_assist()
    except ImportError:
        pytest.skip("assist not installed — not running inside the cutting-worker image.")
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not on PATH.")

    c1 = os.environ.get("REAL_ASSIST_C1_SOURCE_PATH")
    c2 = os.environ.get("REAL_ASSIST_C2_SOURCE_PATH")
    if not c1 or not c2:
        pytest.skip(
            "REAL_ASSIST_C1_SOURCE_PATH/REAL_ASSIST_C2_SOURCE_PATH not set — "
            "point them at a real local C1/C2 source video pair to run this test."
        )
    c1_path, c2_path = Path(c1), Path(c2)
    if not c1_path.is_file() or not c2_path.is_file():
        pytest.skip("REAL_ASSIST_C1_SOURCE_PATH/REAL_ASSIST_C2_SOURCE_PATH don't point at files.")
    return c1_path, c2_path


def test_real_video_cutter_slices_a_real_source_pair_with_ffmpeg(tmp_path: Path):
    c1_path, c2_path = _require_real_assist_and_ffmpeg()
    output_dir = tmp_path / "output"

    RealVideoCutter().cut(
        test_id="T001",
        reference_camera="C1",
        phase_timestamps={"ME_F1": 0, "ME_F2": 30},
        source_paths={"C1": c1_path, "C2": c2_path},
        output_dir=output_dir,
    )

    assert (output_dir / "T001_C1_ME_F1.mp4").is_file()
    assert (output_dir / "T001_C2_ME_F1.mp4").is_file()
