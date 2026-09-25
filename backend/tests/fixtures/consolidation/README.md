# Consolidation fixtures

A real, successfully-processing Observer export was used to verify the
`lees_observer` → `bereken_observer` → `schrijf_resultaat` pipeline during
ticket #114's review, but it is real research data and was deliberately not
committed here.

`synthetic_observer_export.xlsx` (ticket #115) covers that success path
instead. It has the real export's structure: the `Results` / `Results (2)` /
`Results (REL)` header rows (behaviour names), the yellow out-of-sight
header fills, and `Results (REL)`'s row-2 cell-reference formulas. Every
data value is generated, for two fake tests (T901/T902, dogs D901/D902)
across all 15 phases; no measurement, ID or other value comes from the real
export. `build_synthetic_observer_export.py` regenerates it from a real
export (see its docstring). Only needed if Observer's column layout changes.
