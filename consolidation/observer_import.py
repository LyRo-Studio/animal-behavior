"""Import van de aangeleverde Observer-export; behoud iedere modifiercombinatie."""
from pathlib import Path
import hashlib
import math
import re
import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter
from consolidation.consolidatie import consolideer, SOURCE_NAME

# De grenzen staan als gedragsnamen in Results (2), niet als vaste kolomnummers.
# De vijf OOS-kolommen zijn geel gemarkeerd. Vocalisatie gebruikt de volledige
# faseduur, zoals ook de formules in Results (REL) expliciet aangeven.
GROUPS = [
    ("General posture/locomotion of the dog", "Lying down sternally head up",
     "Other general posture/locomotion of the dog", "Out of sight position / locomotion"),
    ("Attention of the dog", "Attention to familiar person", "Attention other",
     "Out of sight attention"),
    ("Vocalisation by the dog", "Panting", "No vocalisation", None),
    ("Exploratory and self-maintenance behaviours by the dog", "Pushing snout",
     "Other expl self-maint beh dog", "Out of sight expl self maint beh dog"),
    ("Stress-related behaviours by the dog", "Yawning", "Other stress-rel beh dog",
     "Out of sight stress-related"),
    ("Tail position", "Tail tucked", "Other tail", "Out of sight tail"),
]
TOLERANTIE_S = 0.001  # Observer exporteert seconden met een beperkte decimale precisie.
DELEN = {
    "1": {"naam": "Met eigenaar", "fases": tuple(range(1, 8)),
          "bestand": "consolidatie_deel1_met_eigenaar.xlsx", "tabblad": "met_eigenaar"},
    "2": {"naam": "Zonder eigenaar", "fases": tuple(range(9, 16)),
          "bestand": "consolidatie_deel2_zonder_eigenaar.xlsx", "tabblad": "zonder_eigenaar"},
    "1+2": {"naam": "Met en zonder eigenaar samen", "fases": tuple(range(1, 8)) + tuple(range(9, 16)),
            "bestand": "consolidatie_deel1_en_2_samen.xlsx", "tabblad": "samen"},
}


def normaliseer_kop(value):
    return str(value).replace("stress0", "stress-").replace("self0", "self-")


def fase_uit_observatie(value):
    match = re.fullmatch(r"(T\d+)_.+_F(\d+)", str(value))
    if not match:
        raise ValueError(f"Onherkenbare observatienaam: {value!r}")
    phase = int(match[2])
    if not 1 <= phase <= 15:
        raise ValueError(f"Ongeldige fase in observatienaam: {value}")
    return match[1], phase


def _getal(value, context):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < 0):
        raise ValueError(f"Ongeldige meetwaarde in {context}: {value!r}")
    return float(value)


def lees_observer(path, deel="1"):
    """Lees deel 1, deel 2 of 1+2 uit Results (2), met controle tegen Results.

    '-' wordt alleen als nul aanvaard wanneer de bewerkte kopie expliciet nul
    bevat. Blanke meetcellen zijn fouten. Fase wordt uit Observations gehaald:
    de bronkolom Fase nummert deel 2 opnieuw van 1 tot 7.
    """
    deel = str(deel)
    if deel not in DELEN:
        raise ValueError("Deel moet 1, 2 of 1+2 zijn.")
    gekozen_fases = DELEN[deel]["fases"]
    wb = load_workbook(path, data_only=False, keep_links=False)
    try:
        source = wb["Results (2)"]
        raw = wb["Results"]
        rel = wb["Results (REL)"]
        headers = {normaliseer_kop(c.value): c.column for c in source[1]}
        raw_headers = {normaliseer_kop(c.value): c.column for c in raw[1]}
        if len(headers) != source.max_column or len(raw_headers) != raw.max_column:
            raise ValueError("Dubbele kolomkoppen in de Observer-export.")
        raw_rows = {raw.cell(r, raw_headers["Observations"]).value: r
                    for r in range(2, raw.max_row + 1)}
        if len(raw_rows) != raw.max_row - 1:
            raise ValueError("Dubbele observaties in ruwe Results.")
        mapping, excluded, notes = [], [], []
        suffix_group = {}
        oos_columns = {}
        for group, first, last, oos in GROUPS:
            start = headers[f"Total duration {first} <No Modifier>"]
            end = headers[f"Total duration {last} <No Modifier>"]
            for c in range(start, end + 1):
                h = normaliseer_kop(source.cell(1, c).value)
                if not h.startswith("Total duration "):
                    raise ValueError("Onverwachte kolom binnen een gedragsgroep.")
                suffix_group[h.removeprefix("Total duration ")] = group
            oc = headers[f"Total duration {oos} <No Modifier>"] if oos else None
            oos_columns[group] = oc
            if oc:
                color = source.cell(1, oc).fill.fgColor
                if color.type != "rgb" or color.rgb[-6:] != "FFFF00":
                    raise ValueError(f"OOS-kolom is niet geel gemarkeerd: {oos}")
        base_names = sorted({s.removesuffix(" <No Modifier>") for s in suffix_group
                             if s.endswith(" <No Modifier>")}, key=len, reverse=True)
        for header, c in headers.items():
            if not header.startswith(("Total duration ", "Total number ")):
                continue
            kind = "duur" if header.startswith("Total duration ") else "aantal"
            suffix = header.removeprefix("Total duration ").removeprefix("Total number ")
            if suffix not in suffix_group:
                if suffix.startswith("Out of sight "):
                    reason = "Correctiekolom; alleen de duur wordt als noemer gebruikt"
                elif suffix.startswith("First contact with TP "):
                    reason = "Afzonderlijke first-contactmaat: volgens ethogram uitsluitend F1 met eigenaar"
                else:
                    protocol_start = headers["Total duration Standing bij TP <No Modifier>"]
                    protocol_count = headers["Total number Standing bij TP <No Modifier>"]
                    if not ((kind == "duur" and protocol_start <= c < headers[
                            "Total number Lying down sternally head up <No Modifier>"])
                            or (kind == "aantal" and c >= protocol_count)):
                        raise ValueError(f"Niet ingedeelde gedragskolom: {header}")
                    reason = "Protocolgedrag van testpersoon/vertrouwde persoon"
                excluded.append({"bronkolom": source.cell(1, c).column_letter,
                                 "bronkop": header, "reden": reason})
                continue
            base = next((b for b in base_names if suffix.startswith(b + " ")), None)
            if base is None:
                raise ValueError(f"Gedragsnaam niet te bepalen: {header}")
            group = suffix_group[suffix]
            oc = oos_columns[group]
            formula = rel.cell(2, c).value
            expected = source.cell(1, oc).column_letter if oc else "geen"
            refs = re.findall(r"\$([A-Z]+)2", str(formula))
            found = sorted(set(refs) - {"G"})
            if found != ([] if oc is None else [expected]):
                notes.append({"type": "bestaande_formule", "test_id": "", "fase": "",
                              "variabele": header,
                              "melding": f"Results (REL) verwijst naar {found}; gebruikt: {expected}."})
            mapping.append({"gedrag": header, "basisgedrag": base,
                            "modifier": suffix[len(base) + 1:], "groep": group,
                            "meettype": kind, "bronkolom": source.cell(1, c).column_letter,
                            "kolomnummer": c, "oos_kolom": expected})
        definitions = pd.DataFrame(mapping)
        phases, behaviors, invisibility, selection = [], [], [], []
        zero_count = 0
        for row in range(2, source.max_row + 1):
            observation = source.cell(row, headers["Observations"]).value
            label_test, phase = fase_uit_observatie(observation)
            test = source.cell(row, headers["Test ID"]).value
            owner_flag = str(source.cell(row, headers["Aanwezigheid FP in fase"]).value).lower()
            if owner_flag not in {"true", "false"} or (owner_flag == "true") != (phase <= 7):
                raise ValueError(f"Fase en aanwezigheid eigenaar/FP komen niet overeen: {observation}")
            raw_row = raw_rows[observation]
            if (raw.cell(raw_row, raw_headers["Test ID"]).value != test
                    or raw.cell(raw_row, raw_headers["Dog ID"]).value != source.cell(row, headers["Dog ID"]).value):
                raise ValueError(f"Test/Dog-ID verschillen tussen de twee brontabbladen: {observation}")
            if test != label_test:
                notes.append({"type": "observatienaam", "test_id": test, "fase": phase,
                              "variabele": observation,
                              "melding": "Typfout in observatienaam; Test ID en Dog ID uit beide brontabbladen komen overeen en worden gebruikt."})
            selection.append({"observatie": observation, "test_id": test,
                              "dog_id": source.cell(row, headers["Dog ID"]).value,
                              "fase": phase, "fase_bronkolom": source.cell(row, headers["Fase"]).value,
                              "fase_in_deel": phase if phase <= 7 else phase - 8,
                              "deel": 1 if phase <= 7 else 2, "met_eigenaar": owner_flag == "true",
                              "opgenomen": phase in gekozen_fases, "bronrij": row})
            if phase not in gekozen_fases:
                continue
            # Controleer alle statistieken, ook uitgesloten protocolkolommen.
            for header, c in headers.items():
                if not header.startswith(("Total duration ", "Total number ")) and header != "Duration":
                    continue
                actual = _getal(source.cell(row, c).value, f"Results (2)!{source.cell(row, c).coordinate}")
                original = raw.cell(raw_row, raw_headers[header]).value
                if original == "-" and actual == 0:
                    zero_count += 1
                elif not isinstance(original, (int, float)) or original != actual:
                    raise ValueError(f"Ruwe data wijkt af van kopie: {observation}, {header}")
            duration = _getal(source.cell(row, headers["Duration"]).value, observation)
            phases.append({"test_id": test, "fase": phase, "duur_s": duration})
            for group, oc in oos_columns.items():
                invisibility.append({"test_id": test, "fase": phase, "groep": group,
                                     "duur_s": float(source.cell(row, oc).value) if oc else 0.0})
            for m in mapping:
                value = _getal(source.cell(row, m["kolomnummer"]).value, observation)
                behaviors.append({"test_id": test, "fase": phase, "gedrag": m["gedrag"],
                                  "duur_s": value if m["meettype"] == "duur" else float("nan"),
                                  "aantal": value if m["meettype"] == "aantal" else float("nan")})
        selected = pd.DataFrame(selection)
        if selected.duplicated(["test_id", "fase"]).any():
            raise ValueError("Dubbele echte test/fase-combinatie.")
        dogs = selected[["test_id", "dog_id"]].drop_duplicates()
        if dogs.test_id.duplicated().any() or dogs.dog_id.isna().any():
            raise ValueError("Test heeft ontbrekend of tegenstrijdig Dog ID.")
        notes.append({"type": "broncontrole", "test_id": "", "fase": "", "variabele": "",
                      "melding": f"Alle gekozen waarden gelijk aan ruwe Results; {zero_count} '-' expliciet als 0 bevestigd door Results (2)."})
        return {"deel": deel, "invoer": {"fases": pd.DataFrame(phases),
                           "gedrag_groepen": definitions[["gedrag", "groep", "meettype"]],
                           "gedragingen": pd.DataFrame(behaviors),
                           "out_of_sight": pd.DataFrame(invisibility)},
                "definities": definitions, "honden": dogs, "selectie": selected,
                "uitgesloten": pd.DataFrame(excluded), "meldingen": pd.DataFrame(notes)}
    finally:
        wb.close()


def bereken_observer(bron):
    deel = bron.get("deel", "1")
    resultaat, detail = consolideer(**bron["invoer"], tolerantie_s=TOLERANTIE_S,
                                   geselecteerde_fases=DELEN[deel]["fases"])
    labels = bron["definities"][["gedrag", "basisgedrag", "modifier", "bronkolom", "oos_kolom"]]
    resultaat = resultaat.merge(labels, on="gedrag", validate="many_to_one").merge(
        bron["honden"], on="test_id", validate="many_to_one")
    resultaat["deel"] = int(deel) if deel in {"1", "2"} else deel
    resultaat["met_eigenaar"] = (deel == "1") if deel != "1+2" else None
    resultaat["selectie"] = DELEN[deel]["naam"]
    detail = detail.merge(labels, on="gedrag", validate="many_to_one")
    detail = detail.merge(bron["selectie"].query("opgenomen")[[
        "test_id", "fase", "observatie", "fase_in_deel", "deel", "met_eigenaar", "bronrij"]],
        on=["test_id", "fase"], validate="many_to_one")
    notes = bron["meldingen"].to_dict("records")
    for row in detail[detail.gedrag_s > detail.zichtbaar_s + 1e-9].itertuples():
        notes.append({"type": "afronding", "test_id": row.test_id, "fase": row.fase,
                      "variabele": row.gedrag,
                      "melding": f"Gedragsduur {row.gedrag_s} s; zichtbare tijd {row.zichtbaar_s} s. Binnen {TOLERANTIE_S} s exporttolerantie; waarden niet aangepast."})
    noemers = detail[["test_id", "fase", "groep", "fase_duur_s", "out_of_sight_s", "zichtbaar_s"]].drop_duplicates()
    groep_totalen = noemers.groupby(["test_id", "groep"], as_index=False).agg(
        totale_faseduur_s=("fase_duur_s", "sum"), out_of_sight_s=("out_of_sight_s", "sum"),
        zichtbaar_s=("zichtbaar_s", "sum"), aantal_fases=("fase", "nunique"))
    for row in groep_totalen[groep_totalen.zichtbaar_s == 0].itertuples():
        notes.append({"type": "geen_zichtbare_tijd", "test_id": row.test_id, "fase": str(DELEN[deel]["fases"]),
                      "variabele": row.groep, "melding": "Volledig out of sight: fracties/frequenties blijven leeg."})
    # Brede tabel: een rij per test; duurkolommen bevatten fracties en
    # aantal-kolommen frequenties per seconde, overeenkomstig de opdracht.
    resultaat["geconsolideerde_waarde"] = resultaat.fractie.where(
        resultaat.meettype == "duur", resultaat.frequentie_per_s)
    if bron.get("indeling") == "ethogram":
        extra = bron["definities"][["gedrag", "ethogram_code", "state_event", "ethogram_rij", "groep_export"]]
        resultaat = resultaat.merge(extra, on="gedrag", validate="many_to_one")
        detail = detail.merge(extra, on="gedrag", validate="many_to_one")
        order = {name: i for i, name in enumerate(bron["definities"].gedrag)}
        resultaat = resultaat.assign(_volgorde=resultaat.gedrag.map(order)).sort_values(
            ["test_id", "_volgorde"]).drop(columns="_volgorde").reset_index(drop=True)
        detail = detail.assign(_volgorde=detail.gedrag.map(order)).sort_values(
            ["test_id", "fase", "_volgorde"]).drop(columns="_volgorde").reset_index(drop=True)
    wide = resultaat.pivot(index=["test_id", "dog_id"], columns="gedrag", values="geconsolideerde_waarde")
    wide = wide.reindex(columns=bron["definities"].gedrag).reset_index()
    wide.columns.name = None
    return {"per_test": wide, "consolidatie": resultaat, "controle_per_fase": detail,
            "noemers_per_groep": groep_totalen, "meldingen": pd.DataFrame(notes)}


def schrijf_resultaat(path, bron, berekend, source_path):
    path = Path(path)
    path.parent.mkdir(exist_ok=True)
    deel = bron.get("deel", "1")
    uitleg = [
        f"ECHTE RESULTATEN - {DELEN[deel]['naam']}; deel {deel}.",
        f"Echte fases uit Observations: {DELEN[deel]['fases']}. Aanwezigheid FP in fase is onafhankelijk gecontroleerd.",
        "per_test: een rij per hond/test; alle oorspronkelijke gedrags- en modifierkolommen afzonderlijk.",
        "Total duration-kolommen: som gedragsduur / (som faseduur - som groeps-OOS-duur). Dit zijn fracties, geen percentages.",
        "Total number-kolommen: som aantallen / dezelfde noemer. Eenheid: voorkomens per zichtbare seconde.",
        "consolidatie: alle tellers/noemers, percentages, frequenties per seconde/minuut en status.",
        "Modifiers worden niet opgeteld tot basisgedrag; combinaties blijven behouden en worden alleen over fases gesommeerd.",
        "Vocalisatie heeft geen OOS-kolom: volledige faseduur, zoals de bronformules aangeven.",
        "OOS-aantallen zijn geen tijd: alleen OOS-duurtijden worden afgetrokken.",
        "Deel 1 gebruikt echte fases 1-7; deel 2 echte fases 9-15 (hernummerd 1-7 in de bron). Samen gebruikt alle 14 fases. Fase 8 telt nooit mee.",
        "Samen wordt berekend uit de opgetelde tellers en noemers, niet als gemiddelde van beide delen.",
        ("Groepsindeling en kolomvolgorde volgen het ethogram; Stiffening up gebruikt exploratie-OOS (IW)."
         if bron.get("indeling") == "ethogram" else "Groepsindeling volgt de bronexport; zie controle_ethogram.xlsx voor verschillen."),
        "Protocolgedrag valt buiten de hondgedragsgroepen. First contact with TP is volgens ethogram een aparte F1-maat: zie uitgesloten.",
        "Nul zichtbare tijd: lege fractie/frequentie met status geen_zichtbare_tijd; nooit vervangen door 0%.",
        "Ruwe Results en Results (2) zijn per gekozen meetcel vergeleken. Bronbestanden zijn niet gewijzigd.",
        "Labels met stress0/self0 zijn hersteld naar stress-/self-, overeenkomstig ruwe Results.",
        f"Maximale tolerantie op individuele tijden: {TOLERANTIE_S} s wegens exportafronding; afwijkingen staan in meldingen.",
        "Bestaande fasepercentages uit Results (REL) zijn niet opgeteld of gemiddeld. Foutieve groepsverwijzingen staan in meldingen.",
        f"Bron: {Path(source_path).name}",
        f"SHA256 bron: {hashlib.sha256(Path(source_path).read_bytes()).hexdigest()}",
    ]
    tables = {"per_test": berekend["per_test"], "LEESMIJ": pd.DataFrame({"uitleg": uitleg}),
              **{k: v for k, v in berekend.items() if k != "per_test"},
              "variabelen": bron["definities"].drop(columns="kolomnummer"),
              "fase_selectie": bron["selectie"], "uitgesloten": bron["uitgesloten"]}
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for name, table in tables.items():
            table.to_excel(writer, sheet_name=name, index=False)
            ws = writer.book[name]
            ws.freeze_panes = "C2" if name == "per_test" else "A2"
            ws.auto_filter.ref = ws.dimensions
            for cell in ws[1]:
                cell.font = Font(bold=True, color="FFFFFF")
                cell.fill = PatternFill("solid", fgColor="245447")
                ws.column_dimensions[cell.column_letter].width = 24
            if name == "LEESMIJ":
                ws.column_dimensions["A"].width = 140
            if name == "per_test" and bron.get("indeling") == "ethogram":
                groepeer_kolommen(ws, bron["definities"])
    return path


def pas_ethogram_indeling_toe(bron, path="20241218_Printbaar ethogram.xlsx"):
    """Behoud metingen, orden op ethogram en koppel iedere groep aan haar OOS-duur.

    Currently broken: `ethogram_controle` and the reference file above are not
    in this repo, so this (and therefore verwerk_alle_delen) cannot run today.
    Confirmed during ticket #114's review; excluded from the web feature.
    """
    from ethogram_controle import lees_ethogram, ALIASES
    eth, _ = lees_ethogram(path)
    result = {k: v.copy() if isinstance(v, pd.DataFrame) else v for k, v in bron.items()}
    result["invoer"] = {k: v.copy() for k, v in bron["invoer"].items()}
    defs = bron["definities"].copy()
    defs["groep_export"] = defs.groep
    by_name = eth.set_index("ethogram_gedrag")
    oos_cols = defs.groupby("groep").oos_kolom.first().to_dict()
    modifier_order = {}
    for base, modifier in defs[["basisgedrag", "modifier"]].itertuples(index=False, name=None):
        modifier_order.setdefault((base, modifier), len(modifier_order))
    for i, row in defs.iterrows():
        name = ALIASES.get(row.basisgedrag, row.basisgedrag)
        record = by_name.loc[name]
        if isinstance(record, pd.DataFrame):
            raise ValueError(f"Dubbele gedragsnaam in ethogram: {name}")
        defs.loc[i, "groep"] = record.ethogram_groep
        defs.loc[i, "oos_kolom"] = oos_cols[record.ethogram_groep]
        defs.loc[i, "ethogram_code"] = record.code
        defs.loc[i, "state_event"] = record.state_event
        defs.loc[i, "ethogram_rij"] = int(record.ethogram_rij)
    defs["ethogram_rij"] = defs.ethogram_rij.astype(int)
    defs["_modifier"] = [modifier_order[(r.basisgedrag, r.modifier)] for r in defs.itertuples()]
    defs["_type"] = defs.meettype.map({"duur": 0, "aantal": 1})
    defs = defs.sort_values(["ethogram_rij", "_modifier", "_type"]).drop(columns=["_modifier", "_type"]).reset_index(drop=True)
    result["definities"] = defs
    result["invoer"]["gedrag_groepen"] = defs[["gedrag", "groep", "meettype"]].copy()
    result["indeling"] = "ethogram"
    notes = result["meldingen"].to_dict("records")
    for row in defs[defs.groep != defs.groep_export].itertuples():
        notes.append({"type": "ethogram_groepscorrectie", "test_id": "", "fase": "",
                      "variabele": row.gedrag,
                      "melding": f"Van {row.groep_export} naar {row.groep}, OOS {row.oos_kolom}; ethogram rij {row.ethogram_rij}."})
    result["meldingen"] = pd.DataFrame(notes)
    return result


def groepeer_kolommen(ws, definitions):
    """Een doorlopende kop per groep, met de exacte variabelenamen daaronder."""
    ws.insert_rows(1)
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=2)
    ws.cell(1, 1, "Test / hond")
    colors = ["245447", "285F82", "70477F", "996329", "80464A", "3B6870"]
    group_order = definitions.groep.drop_duplicates().tolist()
    for index, group in enumerate(group_order):
        positions = [i + 3 for i, g in enumerate(definitions.groep) if g == group]
        if positions != list(range(min(positions), max(positions) + 1)):
            raise ValueError(f"Groepskolommen staan niet aaneengesloten: {group}")
        ws.merge_cells(start_row=1, start_column=min(positions), end_row=1, end_column=max(positions))
        ws.cell(1, min(positions), group)
        for c in positions:
            for r in [1, 2]:
                ws.cell(r, c).fill = PatternFill("solid", fgColor=colors[index % len(colors)])
                ws.cell(r, c).font = Font(bold=True, color="FFFFFF")
            ws.cell(2, c).alignment = Alignment(wrap_text=True, vertical="top")
    ws.row_dimensions[1].height = 26
    ws.row_dimensions[2].height = 95
    ws.freeze_panes = "C3"
    ws.auto_filter.ref = f"A2:{get_column_letter(ws.max_column)}{ws.max_row}"


def verwerk_alle_delen(source_path=SOURCE_NAME, output_dir="output"):
    output_dir = Path(output_dir)
    output_dir.mkdir(exist_ok=True)
    resultaten, bronnen = {}, {}
    for deel, config in DELEN.items():
        bronnen[deel] = pas_ethogram_indeling_toe(lees_observer(source_path, deel=deel))
        resultaten[deel] = bereken_observer(bronnen[deel])
        schrijf_resultaat(output_dir / config["bestand"], bronnen[deel], resultaten[deel], source_path)
    overzicht = output_dir / "consolidatie_alle_delen.xlsx"
    uitleg = [
        "DRIE AFZONDERLIJKE RESULTATEN: met_eigenaar, zonder_eigenaar en samen. Bron: Results (2).",
        "Elk resultaat heeft 10 tests en behoudt alle 538 oorspronkelijke gedrags-/modifierkolommen.",
        "met_eigenaar: deel 1, echte fases 1-7; zonder_eigenaar: deel 2, echte fases 9-15 (1-7 binnen deel 2).",
        "samen: alle 14 fases; som gedragswaarde / (som faseduur - som groeps-OOS-duur). Geen gemiddelde van de twee resultaten.",
        "Total duration: tijdsfractie. Total number: frequentie per zichtbare seconde. Zie de detailbestanden voor percentages en frequentie per minuut.",
        "Elke groep staat aaneengesloten onder een eigen gekleurde kop, in ethogramvolgorde. Binnen elk gedrag staan de modifiers en hun duurtijd/aantal naast elkaar.",
        "Individuele gedragingen zijn niet opgeteld tot een groepsscore. First contact blijft een afzonderlijke F1-maat.",
        "Stiffening up volgt nu het ethogram: exploratie/zelfverzorging, OOS IW. Het oude ethogramcontroleverslag beschrijft de eerdere bronindeling.",
        "Event-duurkolommen bevatten nul en zijn geen inhoudelijke maat; gebruik hun frequenties. Zie variabelen voor State/Event.",
        "Nul zichtbare tijd geeft een lege uitkomst. Fase 8 is uitgesloten.",
        f"Bron: {Path(source_path).name}; SHA256: {hashlib.sha256(Path(source_path).read_bytes()).hexdigest()}",
    ]
    with pd.ExcelWriter(overzicht, engine="openpyxl") as writer:
        for deel, config in DELEN.items():
            resultaten[deel]["per_test"].to_excel(writer, sheet_name=config["tabblad"], index=False)
        pd.DataFrame({"uitleg": uitleg}).to_excel(writer, sheet_name="LEESMIJ", index=False)
        pd.concat([r["noemers_per_groep"].assign(selectie=DELEN[d]["naam"]) for d, r in resultaten.items()],
                  ignore_index=True).to_excel(writer, sheet_name="noemers_per_groep", index=False)
        bronnen["1+2"]["definities"].drop(columns="kolomnummer").to_excel(writer, sheet_name="variabelen", index=False)
        bronnen["1+2"]["selectie"].to_excel(writer, sheet_name="fases", index=False)
        for ws in writer.book:
            ws.freeze_panes = "C2"
            ws.auto_filter.ref = ws.dimensions
            for cell in ws[1]:
                cell.font = Font(bold=True, color="FFFFFF")
                cell.fill = PatternFill("solid", fgColor="245447")
                ws.column_dimensions[cell.column_letter].width = 24
        writer.book["LEESMIJ"].column_dimensions["A"].width = 140
        for deel, config in DELEN.items():
            groepeer_kolommen(writer.book[config["tabblad"]], bronnen[deel]["definities"])
    return bronnen, resultaten, overzicht


if __name__ == "__main__":
    bronnen, resultaten, pad = verwerk_alle_delen()
    print(pad.resolve())
    for deel, result in resultaten.items():
        print(DELEN[deel]["naam"], len(result["per_test"]), "tests;",
              len(bronnen[deel]["invoer"]["fases"]), "fases")
