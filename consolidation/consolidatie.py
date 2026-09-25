"""Gedragsduur en aantallen consolideren met een noemer per gedragsgroep."""
import math
import pandas as pd

SCHEMAS = {
    "fases": ["test_id", "fase", "duur_s"],
    "gedrag_groepen": ["gedrag", "groep"],
    "gedragingen": ["test_id", "fase", "gedrag", "duur_s"],
    "out_of_sight": ["test_id", "fase", "groep", "duur_s"],
}


def _controleer(frame, name, keys, numeric=True):
    missing = set(SCHEMAS[name]) - set(frame.columns)
    if missing:
        raise ValueError(f"{name}: missing columns {sorted(missing)}.")
    optional = {"gedrag_groepen": ["meettype"], "gedragingen": ["aantal"]}
    columns = SCHEMAS[name] + [c for c in optional.get(name, []) if c in frame]
    frame = frame[columns].copy()
    if frame.empty:
        raise ValueError(f"{name}: no input.")
    for column in keys:
        if frame[column].isna().any():
            raise ValueError(f"{name}: missing value in {column}.")
        if column != "fase":
            if not frame[column].map(lambda x: isinstance(x, str) and bool(x.strip())).all():
                raise ValueError(f"{name}: invalid text in {column}.")
            frame[column] = frame[column].str.strip()
    if "fase" in frame:
        valid = frame.fase.map(lambda x: not isinstance(x, bool)
                               and isinstance(x, (int, float))
                               and math.isfinite(x) and x == int(x) and 1 <= x <= 15)
        if not valid.all():
            raise ValueError(f"{name}: invalid phase (expected a whole number 1-15).")
        frame["fase"] = frame.fase.astype(int)
    if frame.duplicated(keys).any():
        raise ValueError(f"{name}: duplicate key {keys}.")
    if numeric:
        valid = frame.duur_s.map(lambda x: not isinstance(x, bool)
                                and isinstance(x, (int, float))
                                and math.isfinite(x) and x >= 0)
        if not valid.all():
            raise ValueError(f"{name}: duur_s must hold finite, non-negative seconds.")
        frame["duur_s"] = frame.duur_s.astype(float)
    return frame


def fase_in_conditie(fase):
    """Observer-fase 12 -> ('ZE', 'F4'). Fase 8 (vertrek eigenaar) telt nooit mee."""
    return ("ME", f"F{fase}") if fase <= 7 else ("ZE", f"F{fase - 8}")


def fase_label(fase):
    """Observer-fase 12 -> 'ZE F4'."""
    return " ".join(fase_in_conditie(fase))


def fase_labels(fases):
    """{1, 2, 9} -> 'ME F1, ME F2, ZE F1'."""
    return ", ".join(fase_label(x) for x in sorted(set(fases)))


def _eerste(frame):
    """De eerste rij van `frame`, of None: voor een foutmelding die een geval noemt."""
    return next(frame.itertuples(), None)


def consolideer(fases, gedrag_groepen, gedragingen, out_of_sight, niveaus, tolerantie_s=1e-9):
    """Som gedrag / (som fase - som groeps-OOS), voor ieder niveau apart.

    `niveaus` koppelt ieder niveau aan zijn fases (`fases`); een niveau met
    `per_fase` rekent iedere fase apart. Invoer bevat fasesommen, geen losse
    gebeurtenissen. Iedere combinatie van aanwezige fase en gedefinieerd
    gedrag/groep moet expliciet aanwezig zijn. Zichtbare tijd van hoogstens
    `tolerantie_s` is geen zichtbare tijd: lege waarde met status
    no_visible_time, nooit 0. Een niveau zonder aanwezige fases heeft geen rijen.

    Geeft de sommen (per niveau, test[, fase] en gedrag), de invoer per fase
    en de noemers (per niveau, test[, fase] en groep).
    """
    for naam, niveau in niveaus.items():
        gekozen = tuple(niveau["fases"])
        if (not gekozen or len(set(gekozen)) != len(gekozen)
                or any(type(x) is not int or not 1 <= x <= 15 for x in gekozen)):
            raise ValueError(f"Invalid phases for level {naam}: use unique whole phase numbers 1-15.")
    alle_fases = {x for niveau in niveaus.values() for x in niveau["fases"]}
    f = _controleer(fases, "fases", ["test_id", "fase"])
    m = _controleer(gedrag_groepen, "gedrag_groepen", ["gedrag"], numeric=False)
    if m.groep.isna().any() or not m.groep.map(
            lambda x: isinstance(x, str) and bool(x.strip())).all():
        raise ValueError("gedrag_groepen: missing or invalid group.")
    m["groep"] = m.groep.str.strip()
    # Oude invoer met alleen duur_s blijft bruikbaar. Een aanwezige maar lege
    # meettype-kolom wordt bewust niet automatisch als duur geinterpreteerd.
    if "meettype" not in m:
        m["meettype"] = "duur"
    if not m.meettype.isin(["duur", "aantal"]).all():
        raise ValueError("meettype must be duur or aantal.")
    b = _controleer(gedragingen, "gedragingen", ["test_id", "fase", "gedrag"], numeric=False)
    if not set(b.gedrag) <= set(m.gedrag):
        raise ValueError("Unknown behaviour: it has no group.")
    if "aantal" not in b:
        b["aantal"] = float("nan")
    b = b.merge(m, on="gedrag", validate="many_to_one")
    for column, kind in [("duur_s", "duur"), ("aantal", "aantal")]:
        selected = b.meettype == kind
        valid = b.loc[selected, column].map(
            lambda x: not isinstance(x, bool) and isinstance(x, (int, float))
            and math.isfinite(x) and x >= 0
            and (kind != "aantal" or x == int(x)))
        if not valid.all():
            raise ValueError(f"{column}: missing or invalid measurement; counts must be whole numbers.")
        if b.loc[~selected, column].notna().any():
            raise ValueError(f"{column}: must be empty for the other statistic.")
        b[column] = b[column].astype(float)
    o = _controleer(out_of_sight, "out_of_sight", ["test_id", "fase", "groep"])
    f = f[f.fase.isin(alle_fases)].copy()
    b = b[b.fase.isin(alle_fases)].copy()
    o = o[o.fase.isin(alle_fases)].copy()
    if f.empty:
        raise ValueError(f"The export has no Observations in the phases of any level ({fase_labels(alle_fases)}).")
    if not set(o.groep) <= set(m.groep):
        raise ValueError("Out of Sight for an unknown group.")
    phase_keys = ["test_id", "fase"]
    for frame, name in [(b, "gedragingen"), (o, "out_of_sight")]:
        joined = frame.merge(f[phase_keys], on=phase_keys, how="left", indicator=True)
        if (joined._merge != "both").any():
            raise ValueError(f"{name}: test/phase missing from fases.")
    expected_b = f[phase_keys].merge(m[["gedrag"]], how="cross")
    expected_o = f[phase_keys].merge(m[["groep"]].drop_duplicates(), how="cross")
    for expected, actual, keys, name in [
        (expected_b, b, phase_keys + ["gedrag"], "gedragingen"),
        (expected_o, o, phase_keys + ["groep"], "out_of_sight"),
    ]:
        check = expected.merge(actual[keys], on=keys, how="left", indicator=True)
        if (check._merge != "both").any():
            raise ValueError(f"{name}: missing measurements; a measured zero must be explicit.")
    visibility = expected_o.merge(
        f.rename(columns={"duur_s": "fase_duur_s"}), on=phase_keys,
        validate="many_to_one").merge(
        o.rename(columns={"duur_s": "out_of_sight_s"}),
        on=phase_keys + ["groep"], validate="one_to_one")
    visibility["zichtbaar_s"] = visibility.fase_duur_s - visibility.out_of_sight_s
    if (r := _eerste(visibility[visibility.zichtbaar_s < -tolerantie_s])) is not None:
        raise ValueError(
            f"Out of Sight exceeds Duration for test {r.test_id}, Observer phase {r.fase} "
            f"({fase_label(r.fase)}), {r.groep}: {r.out_of_sight_s} s against {r.fase_duur_s} s.")
    visibility["zichtbaar_s"] = visibility.zichtbaar_s.clip(lower=0)
    detail = b.rename(columns={"duur_s": "gedrag_s"}).merge(
        visibility, on=phase_keys + ["groep"], validate="many_to_one")
    if (r := _eerste(detail[detail.gedrag_s > detail.zichtbaar_s + tolerantie_s])) is not None:
        raise ValueError(
            f"{r.gedrag} lasts longer than its visible time for test {r.test_id}, Observer phase "
            f"{r.fase} ({fase_label(r.fase)}): {r.gedrag_s} s against {r.zichtbaar_s} s visible.")
    if (r := _eerste(detail[(detail.zichtbaar_s <= tolerantie_s) & (detail.aantal > 0)])) is not None:
        raise ValueError(
            f"{r.gedrag} is above 0 without visible time for test {r.test_id}, Observer phase "
            f"{r.fase} ({fase_label(r.fase)}): {r.groep} was out of sight the whole phase.")
    sommen, noemers = [], []
    for naam, niveau in niveaus.items():
        sleutel = phase_keys if niveau.get("per_fase") else ["test_id"]
        d = detail[detail.fase.isin(niveau["fases"])]
        v = visibility[visibility.fase.isin(niveau["fases"])]
        som = d.groupby(sleutel + ["groep", "gedrag", "meettype"], as_index=False).agg(
            gedrag_s=("gedrag_s", lambda x: x.sum(min_count=1)),
            aantal=("aantal", lambda x: x.sum(min_count=1)),
            totale_faseduur_s=("fase_duur_s", "sum"),
            out_of_sight_s=("out_of_sight_s", "sum"),
            aantal_fases=("fase", "nunique"),
            aanwezige_fases=("fase", fase_labels),
        )
        noemer = v.groupby(sleutel + ["groep"], as_index=False).agg(
            totale_faseduur_s=("fase_duur_s", "sum"),
            out_of_sight_s=("out_of_sight_s", "sum"),
            aanwezige_fases=("fase", fase_labels),
        )
        for frame, lijst in [(som, sommen), (noemer, noemers)]:
            frame.insert(0, "level", naam)
            lijst.append(frame)
    sums, noemers = pd.concat(sommen, ignore_index=True), pd.concat(noemers, ignore_index=True)
    for frame in (sums, noemers):
        if "fase" in frame:
            frame["fase"] = frame.fase.astype("Int64")
        # Eerst sommeren en dan delen, geen gemiddelde van fasepercentages.
        frame["zichtbaar_s"] = (frame.totale_faseduur_s - frame.out_of_sight_s).clip(lower=0)
        frame["status"] = frame.zichtbaar_s.map(
            lambda x: "ok" if x > tolerantie_s else "no_visible_time")
    zichtbaar = sums.zichtbaar_s.where(sums.status == "ok")
    sums["fractie"] = sums.gedrag_s / zichtbaar
    sums["percentage"] = 100 * sums.fractie
    sums["frequentie_per_s"] = sums.aantal / zichtbaar
    sums["frequentie_per_min"] = 60 * sums.frequentie_per_s
    return sums, detail.sort_values(phase_keys + ["groep", "gedrag"]).reset_index(drop=True), noemers
