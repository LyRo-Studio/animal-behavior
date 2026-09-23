# Consolidation fixtures

`invoer_observer.xlsx` and `invoer_duurtijden.xlsx` are **blank input templates**
for `consolidation/consolidatie.py`'s generic `lees_invoer`/`consolideer` path
(sheets: `fases`, `gedrag_groepen`, `gedragingen`, `out_of_sight`), not filled-in
sample data. Their own `LEESMIJ` sheet says so explicitly: "INVOERSJABLOON -
ontbrekende duurtijden, geen meetresultaten" (input template — missing
durations, no measurement results). Every `duur_s` cell in `fases` is blank,
which fails `consolidatie.py`'s own validation (`ValueError` on
`duur_s moet ingevulde, eindige, niet-negatieve seconden bevatten`) — verified
by running the pipeline against them.

They're useful for asserting sheet/column *structure* and for exercising
validation-error paths, but they cannot exercise a successful `consolideer()`
run. Neither file has the shape `observer_import.py`'s `lees_observer` expects
either (`Results (2)` / `Results` / `Results (REL)` sheets).

A real, successfully-processing Observer export was used to verify the
`lees_observer` → `bereken_observer` → `schrijf_resultaat` pipeline during
ticket #114's review, but it is real research data and was deliberately not
committed here. Ticket #115's adapter-seam test needs its own synthetic or
anonymized fixture with that sheet shape to cover the success path.
