"""
Exporters for solution2: every cell is exported with its verification status and repair provenance.

(solution1's exporter writes the same numbers with no status and no trust flag; it is left untouched.)

Status vocabulary per cell: exact | rounding | unverified | failed | structure_ambiguous, plus `repaired`
(changed by the retry loop; the original reading is kept) and `blank` for empty cells.
A page that is not trusted is still exported, with the failing equations and the windows left for review.
"""
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from solution1.models import RawDocumentTree
from solution2.repair.loop import Attempt
from solution2.verify import DocumentVerdict
from solution2.verify.parsing import BLANK, parse_cell

TRUSTED_STATUSES = ("trusted", "trusted_joined", "repaired")


def diff_documents(original: RawDocumentTree, final: RawDocumentTree):
    """Cells the retry loop changed, rows it inserted, rows it removed."""
    before = {r.row_index: r for r in original.rows}
    changed: Dict[Tuple[int, int], str] = {}
    inserted = set()
    for r in final.rows:
        old = before.get(r.row_index)
        if old is None:
            inserted.add(r.row_index)
            continue
        for c in range(max(len(old.values), len(r.values))):
            a = old.values[c] if c < len(old.values) else ""
            b = r.values[c] if c < len(r.values) else ""
            if a != b:
                changed[(r.row_index, c)] = a
    kept = {r.row_index for r in final.rows}
    removed = [r.account_raw for r in original.rows if r.row_index not in kept]
    return changed, inserted, removed


def build_report(source: Dict[str, Any], original: RawDocumentTree, final: RawDocumentTree,
                 verdict: DocumentVerdict, status: str, attempts: Sequence[Attempt] = ()) -> Dict[str, Any]:
    changed, inserted, removed = diff_documents(original, final)
    ncols = max([len(final.metadata.periods)] + [len(r.values) for r in final.rows])
    mult = verdict.unit_multiplier
    rows = []
    for r in final.rows:
        cells = []
        for c in range(ncols):
            raw = r.values[c] if c < len(r.values) else ""
            cell = parse_cell(raw)
            if cell.kind == BLANK and (r.row_index, c) not in changed:
                continue
            cells.append({
                "col": c, "raw": raw, "value": cell.value,
                "value_jpy": cell.value * mult if cell.value is not None and mult else None,
                "status": verdict.cell_status.get((r.row_index, c), "blank" if cell.kind == BLANK else "unverified"),
                "repaired": (r.row_index in inserted) or (r.row_index, c) in changed,
                "original_raw": changed.get((r.row_index, c)),
            })
        rows.append({"row_index": r.row_index, "label": r.account_raw, "role": r.role, "indent": r.indent_level,
                     "inserted_by_repair": r.row_index in inserted, "cells": cells})
    label = {r.row_index: r.account_raw for r in final.rows}
    return {
        "source": source,
        "status": status,
        "trusted": status in TRUSTED_STATUSES,
        "coverage": verdict.coverage,
        "unit_raw": final.metadata.unit_raw,
        "unit_multiplier": mult,
        "title": final.metadata.title,
        "periods": [{"col": i, "label": p.raw_label, "raw_date": p.raw_date,
                     "date": verdict.period_dates[i] if i < len(verdict.period_dates) else None}
                    for i, p in enumerate(final.metadata.periods)],
        "rounding_convention": verdict.convention,
        "rows": rows,
        "equations": [{"kind": e.kind, "total": e.total[1], "components": [n for _, n in e.components],
                       "results": {str(c): {"diff": d, "status": s} for c, (d, s) in e.results.items()}}
                      for e in verdict.equations],
        "findings": [{"kind": f.kind, "severity": f.severity, "message": f.message} for f in verdict.findings],
        "repairs": [{"window": a.kind, "rows": a.rows, "outcome": a.outcome, "detail": a.detail,
                     "applied": a.applied, "proposed_not_applied": a.proposed if a.outcome != "accepted" else []}
                    for a in attempts],
        "removed_by_repair": removed,
        "needs_review": [{"kind": w.kind, "rows": [label.get(i, str(i)) for i in w.rows], "cols": w.cols}
                         for w in verdict.windows] if status not in TRUSTED_STATUSES else [],
    }


def failure_report(source: Dict[str, Any], status: str, detail: str) -> Dict[str, Any]:
    return {"source": source, "status": status, "trusted": False, "detail": detail, "rows": [], "findings": [],
            "repairs": [], "equations": [], "needs_review": []}


def write_json(report: Dict[str, Any], path) -> None:
    Path(path).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


# ----------------------------------------------------------------------------- Excel
_FILLS = {"failed": "F8CBAD", "structure_ambiguous": "FFE699", "unverified": "FFE699", "rounding": "E2EFDA"}


def write_excel(report: Dict[str, Any], path) -> None:
    import openpyxl
    from openpyxl.comments import Comment
    from openpyxl.styles import Alignment, Font, PatternFill

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Balance Sheet"
    ok = report["trusted"]
    ws["A1"] = report.get("title") or "Financial statement"
    ws["A1"].font = Font(bold=True, size=14)
    ws["A2"] = (f"Status: {report['status'].upper()}" + ("" if ok else "  - NOT TRUSTED, see the Verification sheet")
                + f"   |   unit: {report.get('unit_raw')}   |   coverage: "
                + (f"{report['coverage']:.0%}" if report.get("coverage") is not None else "n/a"))
    ws["A2"].font = Font(bold=True, color="006100" if ok else "9C0006")
    periods = report.get("periods", [])
    headers = ["Account", "Role"]
    for p in periods:
        headers += [f"{p['label']} {p['date'] or p['raw_date']}", "JPY", "Status"]
    for i, h in enumerate(headers, start=1):
        c = ws.cell(row=4, column=i, value=h)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", start_color="1F4E79")
        c.alignment = Alignment(wrap_text=True, vertical="center")
    r_out = 5
    for row in report["rows"]:
        ws.cell(row=r_out, column=1, value="  " * row["indent"] + row["label"] + ("   [inserted by repair]" if row["inserted_by_repair"] else ""))
        ws.cell(row=r_out, column=2, value=row["role"])
        if row["role"] in ("subtotal", "grand_total"):
            for col in (1, 2):
                ws.cell(row=r_out, column=col).font = Font(bold=True)
        for cell in row["cells"]:
            base = 3 + 3 * cell["col"]
            v = ws.cell(row=r_out, column=base, value=cell["value"])
            v.number_format = '#,##0;[Red]-#,##0;"-"'
            j = ws.cell(row=r_out, column=base + 1, value=cell["value_jpy"])
            j.number_format = '#,##0;[Red]-#,##0;"-"'
            label = cell["status"] + (" (repaired)" if cell["repaired"] else "")
            s = ws.cell(row=r_out, column=base + 2, value=label)
            fill = _FILLS.get(cell["status"])
            for target in (v, j, s):
                if fill:
                    target.fill = PatternFill("solid", start_color=fill)
            if cell["repaired"]:
                v.font = Font(bold=True, color="1F4E79")
                v.comment = Comment(f"Changed by the retry loop. Original reading: {cell['original_raw']!r}", "solution2")
        r_out += 1
    ws.column_dimensions["A"].width = 44
    for i in range(2, len(headers) + 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = 16

    vs = wb.create_sheet("Verification")
    vs.append(["Status", report["status"]])
    vs.append(["Trusted", report["trusted"]])
    vs.append(["Rounding convention", report.get("rounding_convention")])
    vs.append([])
    vs.append(["Findings"])
    for f in report["findings"]:
        vs.append([f["severity"], f["kind"], f["message"]])
    vs.append([])
    vs.append(["Repairs by the retry loop"])
    for rep in report["repairs"]:
        vs.append([rep["window"], rep["outcome"], rep["detail"]])
        for a in rep["applied"]:
            vs.append(["", "applied", a])
        for a in rep["proposed_not_applied"]:
            vs.append(["", "proposed, not applied", a])
    if report["needs_review"]:
        vs.append([])
        vs.append(["Left for review"])
        for w in report["needs_review"]:
            vs.append([w["kind"], ", ".join(w["rows"]), f"columns {w['cols']}"])
    vs.append([])
    vs.append(["Equations checked"])
    for e in report["equations"]:
        vs.append([e["kind"], e["total"], json.dumps(e["results"], ensure_ascii=False)])
    vs.column_dimensions["A"].width = 24
    vs.column_dimensions["B"].width = 28
    vs.column_dimensions["C"].width = 90
    wb.save(path)


# ----------------------------------------------------------------------------- Markdown
_MARK = {"exact": "✓", "rounding": "≈", "failed": "✗", "structure_ambiguous": "?", "unverified": "?", "blank": ""}


def _md_cell(c) -> str:
    if c is None:
        return ""
    nil = c["value"] == 0 and not any(ch.isdigit() for ch in c["raw"])          # a printed dash stays a dash
    text = "" if c["value"] is None else (c["raw"] if nil else f"{c['value']:,}")
    return f"{text} {_MARK.get(c['status'], '')}{' ⟲' if c['repaired'] else ''}".strip()


def write_markdown(report: Dict[str, Any], path) -> None:
    L: List[str] = []
    ok = report["trusted"]
    L.append(f"# {report.get('title') or 'Financial statement'}")
    L.append(f"**Status: {report['status'].upper()}** {'(trusted)' if ok else '(NOT trusted - needs review)'} | "
             f"unit {report.get('unit_raw')} | coverage "
             + (f"{report['coverage']:.0%}" if report.get("coverage") is not None else "n/a"))
    L.append("")
    periods = report.get("periods", [])
    L.append("| Account | " + " | ".join(f"{p['label']} {p['date'] or ''}" for p in periods) + " |")
    L.append("|:---|" + "---:|" * len(periods))
    for row in report["rows"]:
        cells = {c["col"]: c for c in row["cells"]}
        parts = [_md_cell(cells.get(i)) for i in range(len(periods))]
        L.append(f"| {'&nbsp;&nbsp;' * row['indent']}{row['label']} | " + " | ".join(parts) + " |")
    L.append("")
    L.append("✓ exact  ≈ within rounding  ✗ failed  ? not verified  ⟲ changed by the retry loop")
    for title, items in (("Findings", [f"{f['severity']}: {f['message']}" for f in report["findings"]
                                       if f["severity"] == "error" or f["kind"] in ("heading_with_values",)]),
                         ("Repairs applied", [a for r in report["repairs"] for a in r["applied"]]),
                         ("Left for review", [f"{w['kind']}: {', '.join(w['rows'])}" for w in report["needs_review"]])):
        if items:
            L.append("")
            L.append(f"## {title}")
            L.extend(f"- {i}" for i in items)
    Path(path).write_text("\n".join(L) + "\n", encoding="utf-8")
