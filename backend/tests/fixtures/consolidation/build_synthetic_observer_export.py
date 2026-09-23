"""Build synthetic_observer_export.xlsx from a real Observer export's *shape*.

Copies only structure from the real export — the header rows of `Results`,
`Results (2)` and `Results (REL)`, the yellow out-of-sight header fills, and
`Results (REL)`'s row-2 formulas (cell references only) — and fills every
data cell with generated values for two fake tests (T901/T902, dogs
D901/D902) across all 15 phases. No measurement, test ID, dog ID or other
metadata value from the real export is copied.

The values are generated to satisfy consolidation/'s own validation: every
metric is numeric, counts are whole numbers, out-of-sight time and each
behaviour's duration fit inside the phase, and raw `Results` matches
`Results (2)` (with "-" for some zeros, as real Observer exports do).

Re-run from backend/ (only needed if the real export's column layout
changes):

    PYTHONPATH=.. .venv/bin/python \\
        tests/fixtures/consolidation/build_synthetic_observer_export.py \\
        /path/to/real_observer_export.xlsx
"""

import random
import sys
from pathlib import Path

from consolidation.observer_import import normaliseer_kop
from openpyxl import Workbook, load_workbook
from openpyxl.styles import PatternFill

OUTPUT = Path(__file__).with_name("synthetic_observer_export.xlsx")
TESTS = [("T901", "D901", "M"), ("T902", "D902", "V")]
PHASE_DURATION_S = 120.0
OOS_DURATION_S = 10.0
_YELLOW = PatternFill("solid", fgColor="FFFF00")


def _is_metric(header: str) -> bool:
    return header.startswith(("Total duration ", "Total number ")) or header == "Duration"


def _is_yellow(cell) -> bool:
    color = cell.fill.fgColor
    return color.type == "rgb" and color.rgb[-6:] == "FFFF00"


def build(real_export: Path) -> None:
    rng = random.Random(115)
    real = load_workbook(real_export)
    raw_headers = [c.value for c in real["Results"][1]]
    source_cells = list(real["Results (2)"][1])
    source_headers = [c.value for c in source_cells]
    rel_headers = [c.value for c in real["Results (REL)"][1]]
    rel_formulas = [
        c.value if isinstance(c.value, str) and c.value.startswith("=") else None
        for c in real["Results (REL)"][2]
    ]
    real.close()

    rows = []
    for test_id, dog_id, sex in TESTS:
        for phase in range(1, 16):
            values = {}
            for header in source_headers:
                if header == "Duration":
                    values[header] = PHASE_DURATION_S
                elif header.startswith("Total duration Out of sight"):
                    values[header] = OOS_DURATION_S
                elif header.startswith("Total duration "):
                    values[header] = rng.choice([0.0, 0.0, 0.0, 4.5, 12.25, 30.0])
                elif header.startswith("Total number "):
                    values[header] = rng.choice([0, 0, 1, 2, 3])
            values.update(
                {
                    "Observations": f"{test_id}_synthetic_F{phase}",
                    "Aanwezigheid FP in fase": "true" if phase <= 7 else "false",
                    "Dog ID": dog_id,
                    "Fase": phase if phase <= 7 else phase - 8,
                    "Geslacht hond": sex,
                    "Test ID": test_id,
                }
            )
            # Keyed by normalised header: Results (2) writes "stress0"/"self0"
            # where raw Results has "stress-"/"self-" (see normaliseer_kop).
            rows.append({normaliseer_kop(h): v for h, v in values.items()})

    workbook = Workbook()
    raw = workbook.active
    raw.title = "Results"
    raw.append(raw_headers)
    for values in rows:
        raw.append(
            [
                # Real exports write "-" for many zero measurements.
                "-"
                if _is_metric(str(h)) and values.get(normaliseer_kop(h)) == 0 and rng.random() < 0.5
                # Columns only in raw Results (e.g. Observer's own container
                # columns) are never read — any placeholder will do.
                else values.get(normaliseer_kop(h), 0 if _is_metric(str(h)) else "synthetic")
                for h in raw_headers
            ]
        )

    source = workbook.create_sheet("Results (2)")
    source.append(source_headers)
    for cell, real_cell in zip(source[1], source_cells, strict=True):
        if _is_yellow(real_cell):
            cell.fill = _YELLOW
    for values in rows:
        source.append([values[normaliseer_kop(h)] for h in source_headers])

    rel = workbook.create_sheet("Results (REL)")
    rel.append(rel_headers)
    rel.append(rel_formulas)

    workbook.save(OUTPUT)


if __name__ == "__main__":
    build(Path(sys.argv[1]))
    print(f"Wrote {OUTPUT}")
