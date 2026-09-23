"""The vendored `assist` snapshot (ticket #112, see cutting-worker/assist/
VENDORED.md). Only the pure-Python primitives are exercised here — the
ffmpeg/scipy-backed `Slicer`/`LagCorrelation`/`FrameGrabber` stay behind the
opt-in `real_assist` marker (test_real_assist_integration.py), since CI never
installs requirements-assist.txt.
"""

import subprocess
import sys
from pathlib import Path

from assist.output_filename import OutputFilename
from assist.phase import Phase

CUTTING_WORKER_ROOT = Path(__file__).resolve().parent.parent


def test_importing_assist_runs_no_cli():
    """Upstream's `assist/__init__.py` ran its Click CLI at import time, so an
    import under a foreign argv printed a usage error or exited. The vendored
    `__init__.py` drops that — a fresh interpreter (the module cache would
    hide it otherwise) imports it silently, whatever argv says."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.argv = ['cutting_worker', '--bogus']; "
            "import assist, assist.phase, assist.camera, assist.output_filename",
        ],
        cwd=CUTTING_WORKER_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert result.stderr == ""


def test_output_filename_matches_the_cut_naming_convention():
    """The `<Test>_<Camera>_<Phase>.mp4` names Cuts are stored under in
    `cuts/<Test>/` (CONTEXT.md's Language section) — `FakeVideoCutter` writes
    the same names, so the orchestrator tests rely on this too."""
    assert OutputFilename.get("T001", "C2", Phase.ZE_F7) == "T001_C2_ZE_F7.mp4"
