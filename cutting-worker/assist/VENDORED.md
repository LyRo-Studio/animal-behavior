# Vendored `assist`

A copy of part of [`vives-devbit/assist`](https://github.com/vives-devbit/assist)
(a private repository) taken so the cutting-worker Docker build doesn't need GitHub
credentials: ticket #112. `CONTEXT.md` ("`assist` vendored, ticket #112") records the
decision. This is a permanent fork: upstream changes are **not** picked up
automatically.

- **Source:** `master` at `55b0fb1ee6a035e18a0bbbf279fcdb4a520d78e0` (2026-03-03,
  "Merge pull request #1 from vives-devbit/copilot/update-usage-documentation").
- **Authors / maintainers (per upstream `setup.py`):** Sille Van Landschoot, Jonas Lannoo.
- **License:** upstream declares none. The maintainers agreed to this copy being
  published in this (public) repository (2026-09-23).

## Copied unmodified

Byte-identical to the source commit (same git blob SHA):

| File | Used for |
| --- | --- |
| `camera.py` | `Camera` |
| `framegrabber.py` | `FrameGrabber.get_start_time` |
| `lag_correlation.py` | `LagCorrelation` (audio cross-correlation) |
| `output_filename.py` | `OutputFilename` |
| `phase.py` | `Phase` |
| `slicer.py` | `Slicer` (ffmpeg slicing) |

These files are excluded from ruff (`cutting-worker/pyproject.toml`) so they stay
diffable against upstream. Check a file with
`git hash-object assist/<file>.py` against
`gh api repos/vives-devbit/assist/contents/assist/<file>.py?ref=<sha> --jq .sha`.

## Changed

- `__init__.py` has been replaced. Upstream's ran the Click CLI at import time
  (`from .assist import assist; assist()`). That made importing any submodule exit
  or print a usage error, which `cutting_worker/video_cutter.py` used to work
  around with `_import_assist()`.

## Left out

- The CLI and Excel batch layer (`assist.py`, `phase_slicer.py`, `test.py`,
  `slice.py`). This app has always done its own per-Test orchestration (CONTEXT.md,
  Feature C).
- The notebooks, the sample Excel file, `docs/`, and upstream packaging
  (`setup.py`, `pyproject.toml`, `requirements.txt`).
  `cutting-worker/requirements-assist.txt` lists the runtime dependencies of the
  copied modules instead, without `click`, `pandas` and `openpyxl`, which only the
  omitted layer used.

## Updating from upstream

Copy the changed file(s) over from a newer commit, then update the SHA above. Also
re-check `requirements-assist.txt` and the `real_assist` integration test
(`tests/test_real_assist_integration.py`).
