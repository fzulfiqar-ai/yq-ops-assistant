"""Compare a built workbook with a reference workbook, part by part (the golden test of /yq-month).

Read straight from the two files' XML (no spreadsheet program, no third-party reader):

  * per sheet, every cell that holds a value or a formula: the formula text exactly, the kind of value
    (text, number, logical, error) and the value itself (numbers to 1e-9, relative to their size);
  * the sheet names and their order: the reference's sheets must appear in the built file in the same
    order, and the sheets the built file adds (the month-end pack's Stock, Movements, Reconciliation, KPI
    and KPI log, between Daily and Data) are named; each sheet's print area, autofilter and frozen pane;
  * per compared sheet, the counts of its rules and pictures: cell-value rules, data bars, colour scales,
    sparklines and charts, as the XML carries them (an added sheet's own rules never meet the reference's).

The sheets compared are the ones named, else the reference's sheets but Notes (the build's own settings and
controls, which carry the day it was prepared). Cells can be set aside by reference (the commentary, which
the approval step writes) and the report says how many were. Nothing is changed in either file.
"""
from __future__ import annotations

import math
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

M = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
R_ID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
X14 = "{http://schemas.microsoft.com/office/spreadsheetml/2009/9/main}"
TOLERANCE = 1e-9
SKIPPED = ("Notes",)                   # left out of a comparison unless it is named
COUNTS = ("cell_rules", "data_bars", "color_scales", "sparklines", "charts")


@dataclass
class SheetPart:
    name: str
    cells: dict = field(default_factory=dict)       # ref -> (kind, formula or None, value)
    autofilter: str | None = None
    pane: dict | None = None
    cell_rules: int = 0
    data_bars: int = 0
    color_scales: int = 0
    sparklines: int = 0
    charts: int = 0


@dataclass
class BookPart:
    sheets: list[SheetPart]
    print_areas: dict                                # sheet -> text
    names: dict                                      # defined name -> text (workbook level)

    def sheet(self, name: str) -> SheetPart | None:
        return next((s for s in self.sheets if s.name == name), None)


def _num(text: str):
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def read_book(path) -> BookPart:
    z = zipfile.ZipFile(Path(path))
    names = z.namelist()
    strings: list[str] = []
    if "xl/sharedStrings.xml" in names:
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall(M + "si"):
            strings.append("".join(t.text or "" for t in si.iter(M + "t")))
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = {r.get("Id"): r.get("Target") for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))}
    order = [(s.get("name"), "xl/" + rels[s.get(R_ID)].lstrip("/").removeprefix("xl/"))
             for s in wb.find(M + "sheets")]
    print_areas, defined = {}, {}
    dn = wb.find(M + "definedNames")
    for d in (dn if dn is not None else []):
        name, text = d.get("name"), d.text or ""
        if name == "_xlnm.Print_Area":
            print_areas[order[int(d.get("localSheetId"))][0]] = text
        elif d.get("localSheetId") is None:
            defined[name] = text
    sheets: list[SheetPart] = []
    for sname, part in order:
        root = ET.fromstring(z.read(part))
        sp = SheetPart(sname)
        for row in root.find(M + "sheetData"):
            for c in row:
                f = c.find(M + "f")
                v = c.find(M + "v")
                inline = c.find(M + "is")
                t = c.get("t", "n")
                if f is None and v is None and inline is None:
                    continue
                if inline is not None:
                    kind, value = "text", "".join(x.text or "" for x in inline.iter(M + "t"))
                elif t == "s":
                    kind, value = "text", strings[int(v.text)]
                elif t == "str":
                    kind, value = "text", (v.text or "") if v is not None else ""
                elif t == "b":
                    kind, value = "bool", (v.text or "") in ("1", "true")
                elif t == "e":
                    kind, value = "error", (v.text or "") if v is not None else ""
                else:
                    raw = v.text if v is not None else None
                    if raw in (None, ""):
                        kind, value = ("text", "") if f is not None else ("blank", None)
                    else:
                        kind, value = "number", _num(raw)
                sp.cells[c.get("r")] = (kind, f.text if f is not None else None, value)
        af = root.find(M + "autoFilter")
        sp.autofilter = af.get("ref") if af is not None else None
        sv = root.find(M + "sheetViews")
        pane = sv.find(f"{M}sheetView/{M}pane") if sv is not None else None
        sp.pane = dict(pane.attrib) if pane is not None else None
        for cf in root.iter(M + "cfRule"):
            kind = cf.get("type")
            if kind == "cellIs":
                sp.cell_rules += 1
            elif kind == "dataBar":
                sp.data_bars += 1
            elif kind == "colorScale":
                sp.color_scales += 1
        sp.sparklines = sum(1 for _ in root.iter(X14 + "sparkline"))
        sheets.append(sp)
    # charts: each sheet's drawing, counted through its relationships
    for sname, part in order:
        rel = part.replace("worksheets/", "worksheets/_rels/") + ".rels"
        if rel not in names:
            continue
        for r in ET.fromstring(z.read(rel)):
            if r.get("Type", "").endswith("/drawing"):
                target = "xl/drawings/" + r.get("Target").rsplit("/", 1)[-1]
                drel = target.replace("drawings/", "drawings/_rels/") + ".rels"
                n = 0
                if drel in names:
                    n = sum(1 for x in ET.fromstring(z.read(drel)) if x.get("Type", "").endswith("/chart"))
                sp = next(s for s in sheets if s.name == sname)
                sp.charts += n
    return BookPart(sheets, print_areas, defined)


def same_value(kind: str, a, b) -> bool:
    if kind == "number":
        if a is None or b is None:
            return a is b
        return math.isclose(a, b, rel_tol=TOLERANCE, abs_tol=TOLERANCE)
    return a == b


@dataclass
class SheetResult:
    name: str
    compared: int = 0
    set_aside: int = 0
    formula: list = field(default_factory=list)
    kind: list = field(default_factory=list)
    value: list = field(default_factory=list)
    missing: list = field(default_factory=list)       # in the reference only
    extra: list = field(default_factory=list)         # in the built file only
    parts: list = field(default_factory=list)         # print area / filter / pane differences
    counts: dict = field(default_factory=dict)        # what -> (built, reference): this sheet's own rules and pictures

    @property
    def ok(self) -> bool:
        return (not (self.formula or self.kind or self.value or self.missing or self.extra or self.parts)
                and all(a == b for a, b in self.counts.values()))


@dataclass
class Result:
    sheets: list[SheetResult]
    names_ok: bool
    names_note: str
    counts: dict                                      # what -> (built, reference), added up over the compared sheets
    added: list = field(default_factory=list)         # the built file's sheets the reference does not have

    @property
    def ok(self) -> bool:
        return self.names_ok and all(s.ok for s in self.sheets) and all(a == b for a, b in self.counts.values())


def sheet_order(built: list[str], reference: list[str]) -> tuple[bool, str, list[str]]:
    """(the reference's sheets appear in the built file in the same order, the words for it, the sheets added)."""
    added = [n for n in built if n not in reference]
    if built == reference:
        return True, "same sheets in the same order", added
    rest = iter(built)
    if all(name in rest for name in reference):           # an ordered subsequence: each found after the one before
        return True, (f"the reference's {len(reference)} sheets in the same order, and {len(added)} added: "
                      + ", ".join(added)), added
    return False, f"built {built} against {reference}", added


def compare(built, reference, sheets: list[str] | None = None, set_aside: dict | None = None) -> Result:
    a, b = read_book(built), read_book(reference)
    set_aside = {k: set(v) for k, v in (set_aside or {}).items()}
    names_a, names_b = [s.name for s in a.sheets], [s.name for s in b.sheets]
    names_ok, note, added = sheet_order(names_a, names_b)
    wanted = list(sheets) if sheets is not None else [n for n in names_b if n not in SKIPPED]
    results: list[SheetResult] = []
    for name in wanted:
        sa, sb = a.sheet(name), b.sheet(name)
        res = SheetResult(name)
        if sa is None or sb is None:
            res.parts.append("sheet missing in the " + ("built file" if sa is None else "reference"))
            results.append(res)
            continue
        res.counts = {what: (getattr(sa, what), getattr(sb, what)) for what in COUNTS}
        aside = set_aside.get(name, set())
        for ref in sorted(set(sa.cells) | set(sb.cells), key=_ref_key):
            if ref in aside:
                res.set_aside += 1
                continue
            ca, cb = sa.cells.get(ref), sb.cells.get(ref)
            if cb is None:
                if ca[0] != "blank":
                    res.extra.append(ref)
                continue
            if ca is None:
                if cb[0] != "blank":
                    res.missing.append(ref)
                continue
            res.compared += 1
            if (ca[1] or None) != (cb[1] or None):
                res.formula.append((ref, ca[1], cb[1]))
            if ca[0] != cb[0]:
                res.kind.append((ref, ca[0], cb[0], ca[2], cb[2]))
            elif not same_value(ca[0], ca[2], cb[2]):
                res.value.append((ref, ca[2], cb[2]))
        if a.print_areas.get(name) != b.print_areas.get(name):
            res.parts.append(f"print area {a.print_areas.get(name)} against {b.print_areas.get(name)}")
        if sa.autofilter != sb.autofilter:
            res.parts.append(f"autofilter {sa.autofilter} against {sb.autofilter}")
        pa = {k: v for k, v in (sa.pane or {}).items() if k in ("xSplit", "ySplit", "topLeftCell", "state")}
        pb = {k: v for k, v in (sb.pane or {}).items() if k in ("xSplit", "ySplit", "topLeftCell", "state")}
        if pa != pb:
            res.parts.append(f"pane {pa or 'none'} against {pb or 'none'}")
        results.append(res)
    counts = {what: (sum(s.counts[what][0] for s in results if s.counts),
                     sum(s.counts[what][1] for s in results if s.counts)) for what in COUNTS}
    return Result(results, names_ok, note, counts, added)


def _ref_key(ref: str):
    m = re.match(r"([A-Z]+)(\d+)", ref)
    col = 0
    for ch in m.group(1):
        col = col * 26 + ord(ch) - 64
    return int(m.group(2)), col


COUNT_WORDS = {"cell_rules": "cell rules", "data_bars": "data bars", "color_scales": "colour scales",
               "sparklines": "sparklines", "charts": "charts"}


def summary(res: Result, examples: int = 3) -> list[str]:
    """The comparison in plain lines (values shown only as far as needed to find the cell)."""
    out = [("Sheets: " + res.names_note)]
    for s in res.sheets:
        head = (f"{s.name}: {s.compared:,} cells compared" + (f", {s.set_aside} set aside" if s.set_aside else ""))
        if s.ok:
            out.append(head + ": identical")
            continue
        bits = []
        for label, items in (("formula", s.formula), ("kind of value", s.kind), ("value", s.value),
                             ("only in the reference", s.missing), ("only in the built file", s.extra)):
            if items:
                refs = ", ".join((x[0] if isinstance(x, tuple) else x) for x in items[:examples])
                bits.append(f"{len(items)} {label} ({refs}{', ...' if len(items) > examples else ''})")
        bits += [f"{COUNT_WORDS[k]} {a} (reference {b})" for k, (a, b) in s.counts.items() if a != b]
        out.append(head + ": " + "; ".join(bits + s.parts))
    out.append("Counts, sheet by sheet on the compared sheets: "
               + "; ".join(f"{COUNT_WORDS[k]} {a} (reference {b})" for k, (a, b) in res.counts.items()))
    return out
