"""The sole seam into `assist` (ticket #95, part of issue #93's Feature C)
— `orchestrator.py` never imports `assist` itself, so its tests can inject
`FakeVideoCutter` (see `cutting-worker/tests/doubles.py`) instead of
requiring the real package and real ffmpeg/audio files. Mirrors
`worker/dogtrace_runner.py`'s Protocol-plus-real-plus-fake shape.

`assist` (pinned at `master`, `github.com/vives-devbit/assist` — CONTEXT.md's
Feature C "`assist` reuse" decision) exposes `Slicer` (ffmpeg slicing),
`LagCorrelation` (audio cross-correlation), `FrameGrabber` (first-frame
timestamp probing), and `Camera`/`Phase`/`OutputFilename`. Its own
`PhaseSlicer`/`Test`/`read_excel` orchestration layer is deliberately not
reused (it loops over every Test in a whole directory/workbook per run,
duplicating validation this app already does its own way) — the per-phase
slicing loop below is this module's own code, built directly against this
app's `phase_timestamps: dict[str, int]` shape (keyed "ME_F1".."ZE_F8",
matching `assist.phase.Phase.name` exactly) rather than `assist`'s own
positional 16-element list.
"""

import logging
import sys
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)

# Phase 1..16 in assist.phase.Phase's own ordinal order (ME_F1..ME_F8,
# ZE_F1..ZE_F8) — a phase's Cut spans [PHASE_ORDER[i]'s timestamp,
# PHASE_ORDER[i+1]'s timestamp), so only the first 15 entries ever become a
# produced output; the 16th (ZE_F8) only ever serves as ZE_F7's end boundary
# (CONTEXT.md's "ZE_F8 cannot be produced" decision). Duplicated here rather
# than imported from `assist.phase.Phase` so this constant — used by
# `FakeVideoCutter` too (cutting-worker/tests/doubles.py) — never needs the
# real `assist` package installed.
PHASE_ORDER = [f"{condition}_F{n}" for condition in ("ME", "ZE") for n in range(1, 9)]


class VideoCutter(Protocol):
    def cut(
        self,
        *,
        test_id: str,
        reference_camera: str,
        phase_timestamps: dict[str, int],
        source_paths: dict[str, Path],
        output_dir: Path,
    ) -> None:
        """Slice every producible phase (see `PHASE_ORDER` above) for every
        camera in `source_paths` ("C1"/"C2" -> its local source video) into
        `output_dir`, named `f"{test_id}_{camera}_{phase}.mp4"`
        (`assist.output_filename.OutputFilename`'s own convention) — e.g.
        `T001_C1_ME_F1.mp4`. A phase missing either boundary timestamp (its
        own start, or the next phase's start to bound its end) is silently
        skipped, never written — `orchestrator.py` infers per-CuttingJobOutput
        success/failure from which expected files actually appear here, the
        same "no progress callback, watch the output directory" approach
        issue #93 documents for ticket #98's live progress.

        Raises only for a failure that makes the *whole* job unrunnable
        (e.g. two cameras given but their audio can't be cross-correlated at
        all) — an individual phase failing to slice is caught and skipped
        internally, not raised, so one bad phase never costs every other
        phase in the same job (deliberately not mirroring `assist`'s own
        `PhaseSlicer`, which has no such isolation and aborts the whole run
        on any single `Slicer.slice` failure).
        """
        ...


def _import_assist() -> None:
    """Import the `assist` package exactly once, working around a bug in
    its own `assist/__init__.py`: unconditionally invoking `assist`'s Click
    CLI at import time (`from .assist import assist; assist()`), wrapped in
    a bare `except Exception` that still lets Click's own `SystemExit`
    through. Importing *any* `assist.*` submodule first runs that
    `__init__.py` — against this process's real argv, that would either
    print an unrelated Click usage error or start looking for `./input`/
    `./output` directories that don't exist here.

    Clears argv and swallows the resulting SystemExit for this one, first
    import — Python caches `assist` in `sys.modules` afterward, so every
    later `import assist.<submodule>` in this same process is a cache hit
    that re-triggers none of this. Found by reading `assist`'s source
    directly (github.com/vives-devbit/assist@master) while building this
    module; not something CONTEXT.md's Feature C design pass could have
    caught without it.
    """
    if "assist" in sys.modules:
        return
    original_argv = sys.argv
    sys.argv = ["assist"]
    try:
        import assist  # noqa: F401
    except SystemExit:
        pass
    finally:
        sys.argv = original_argv


class RealVideoCutter:
    """The real VideoCutter, backed by the `assist` package installed in
    the cutting-worker image (see cutting-worker/Dockerfile). Imports
    `assist` lazily, inside `cut()` rather than at module level, so this
    module — and therefore `orchestrator.py` and its tests — stays
    importable without `assist` (and its ffmpeg/scipy/numpy/matplotlib
    dependencies) installed at all, same pattern as
    `worker/dogtrace_runner.py`'s `RealDogTraceRunner`.
    """

    def cut(
        self,
        *,
        test_id: str,
        reference_camera: str,
        phase_timestamps: dict[str, int],
        source_paths: dict[str, Path],
        output_dir: Path,
    ) -> None:
        _import_assist()
        from assist.framegrabber import FrameGrabber
        from assist.lag_correlation import LagCorrelation
        from assist.output_filename import OutputFilename
        from assist.phase import Phase
        from assist.slicer import Slicer

        output_dir.mkdir(parents=True, exist_ok=True)
        # `Slicer`'s constructor args are never read by `.slice()` itself
        # (each call takes an absolute input/output file path) — `output_dir`
        # for both is a harmless placeholder, not load-bearing.
        slicer = Slicer(str(output_dir), str(output_dir))

        other_camera = next((camera for camera in source_paths if camera != reference_camera), None)

        # Only the non-reference camera needs a timing correction — the
        # reference camera's own Cuts are sliced directly against its own
        # Excel timestamps (delta_time=0), mirroring assist.phase_slicer
        # .PhaseSlicer.slice_video_files's own `Camera(test.camera).name !=
        # camera['camera']` branch exactly.
        delta_time_by_camera: dict[str, float] = {reference_camera: 0.0}
        if other_camera is not None:
            reference_file = str(source_paths[reference_camera])
            other_file = str(source_paths[other_camera])
            desync_delay = LagCorrelation().correlate(reference_file, other_file)
            other_start_time = FrameGrabber().get_start_time(other_file)
            delta_time_by_camera[other_camera] = desync_delay + other_start_time

        for camera, local_path in source_paths.items():
            delta_time = delta_time_by_camera.get(camera, 0.0)
            for index in range(len(PHASE_ORDER) - 1):
                phase_name = PHASE_ORDER[index]
                start = phase_timestamps.get(phase_name)
                end = phase_timestamps.get(PHASE_ORDER[index + 1])
                # A phase this Test never ran (start missing), or with no
                # timestamp to bound its own end, produces nothing —
                # orchestrator.py's post-hoc directory scan then marks the
                # matching CuttingJobOutput failed. Also guards against a
                # non-monotonic pair (end <= start), which assist's own
                # `Slicer.slice` would otherwise hit an `assert` on.
                if start is None or end is None or end <= start:
                    continue

                output_file = output_dir / OutputFilename.get(test_id, camera, Phase[phase_name])
                try:
                    slicer.slice(
                        str(local_path),
                        start + delta_time,
                        end + delta_time,
                        str(output_file),
                    )
                except Exception:
                    logger.exception(
                        "test_id=%s camera=%s phase=%s failed to slice",
                        test_id,
                        camera,
                        phase_name,
                    )
