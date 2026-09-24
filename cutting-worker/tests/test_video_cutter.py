"""`RealVideoCutter.cut`'s own per-phase loop (ticket #98's `on_output_written`),
with `assist`'s ffmpeg/scipy-backed modules stubbed in `sys.modules` — CI
never installs requirements-assist.txt, and the real thing is covered by the
opt-in test_real_assist_integration.py instead.
"""

import sys
import types
from pathlib import Path

import pytest

from cutting_worker.video_cutter import RealVideoCutter


class _FakeSlicer:
    """Writes the requested output file, except for any whose name contains
    one of `failing` — those raise, like a real ffmpeg failure would."""

    failing: tuple[str, ...] = ()

    def __init__(self, input_dir: str, output_dir: str) -> None:
        pass

    def slice(self, input_file: str, start: float, end: float, output_file: str) -> None:
        if any(name in output_file for name in self.failing):
            raise RuntimeError("simulated ffmpeg failure")
        Path(output_file).write_bytes(b"cut")


@pytest.fixture(autouse=True)
def _stub_heavy_assist_modules(monkeypatch):
    _FakeSlicer.failing = ()
    for name, attributes in (
        ("assist.slicer", {"Slicer": _FakeSlicer}),
        ("assist.lag_correlation", {"LagCorrelation": object}),
        ("assist.framegrabber", {"FrameGrabber": object}),
    ):
        module = types.ModuleType(name)
        module.__dict__.update(attributes)
        monkeypatch.setitem(sys.modules, name, module)


def _cut(tmp_path: Path, on_output_written) -> Path:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    output_dir = tmp_path / "output"
    RealVideoCutter().cut(
        test_id="T001",
        reference_camera="C1",
        phase_timestamps={"ME_F1": 0, "ME_F2": 30, "ME_F3": 60, "ME_F4": 90},
        source_paths={"C1": source},
        output_dir=output_dir,
        on_output_written=on_output_written,
    )
    return output_dir


def test_each_phase_is_reported_once_its_file_is_written(tmp_path):
    reported: list[Path] = []

    def on_output_written(path: Path) -> None:
        # Already complete on disk when reported, never ahead of it.
        assert path.read_bytes() == b"cut"
        reported.append(path)

    _cut(tmp_path, on_output_written)

    # ME_F4 has no next timestamp to bound its end, so it's never produced.
    assert [path.name for path in reported] == [
        "T001_C1_ME_F1.mp4",
        "T001_C1_ME_F2.mp4",
        "T001_C1_ME_F3.mp4",
    ]


def test_a_phase_that_fails_to_slice_is_not_reported_and_the_rest_carry_on(tmp_path):
    _FakeSlicer.failing = ("ME_F2",)
    reported: list[Path] = []

    _cut(tmp_path, reported.append)

    assert [path.name for path in reported] == ["T001_C1_ME_F1.mp4", "T001_C1_ME_F3.mp4"]


def test_an_error_raised_by_on_output_propagates_instead_of_being_taken_as_a_slice_failure(
    tmp_path,
):
    def on_output_written(path: Path) -> None:
        raise ConnectionError("simulated upload failure")

    with pytest.raises(ConnectionError):
        _cut(tmp_path, on_output_written)
