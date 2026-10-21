"""Console / Markdown / JSON reporting."""
import json
from collections import Counter, defaultdict
from typing import Dict, List

from solution1.models import RawDocumentTree

from .engine import GATED_KINDS, Results, evaluate_gate
from .mutations import CLASS_INFO
from .scoring import BAD, Record, aggregate, by_magnitude, by_position

KIND_TITLES = {
    "detect": "Corruptions arithmetic MUST catch (|change| >= material delta)",
    "detect_small": "Same classes below the material delta (rounding zone, informational)",
    "gap": "Known gaps: arithmetic cannot see these, a second signal is needed (informational)",
    "no_degrade": "Fail-open tests: noise must not silently reduce what is checked",
    "flag": "Degenerate output: must never be vouched for",
    "invariant": "Equivalent re-spellings: verdict must not change",
}
OUTCOME_COLS = ["caught", "not_vouched", "weak", "silent", "surfaced", "silent_degraded",
                "benign", "ok", "false_alarm", "crash"]


def pct(x) -> str:
    return "-" if x is None else f"{100 * x:5.1f}%"


def _ncells(doc: RawDocumentTree) -> int:
    return sum(1 for r in doc.rows for s in r.values if s.strip())


def render_markdown(results: Results, corpus: Dict[str, RawDocumentTree]) -> str:
    agg = aggregate(results.records)
    problems = evaluate_gate(results)
    L: List[str] = []
    L.append(f"# Verification harness v2 - verifier `{results.verifier}`")
    L.append("")
    L.append(f"- mutations scored: **{len(results.records):,}** in {results.seconds:.1f}s "
             f"(stride {results.cfg.stride}, material delta {results.cfg.material_delta:g} reported units)")
    L.append(f"- gate: **{'PASS' if not problems else 'FAIL'}** ({len(problems)} violation(s))")
    L.append("")
    L.append("## Clean documents (must not be flagged)")
    L.append("| document | rows | non-empty cells | flagged | evaluated eqs | coverage |")
    L.append("|---|---:|---:|:---:|---:|---:|")
    for doc, v in results.clean.items():
        L.append(f"| {doc} | {len(corpus[doc].rows)} | {_ncells(corpus[doc])} | "
                 f"{'**FALSE ALARM**' if v.flagged else 'no'} | "
                 f"{'-' if v.evaluated_equations is None else v.evaluated_equations} | "
                 f"{pct(v.coverage)} |")
    if any(v.flagged for v in results.clean.values()):
        L.append("")
        L.append("_Documents flagged when clean are scored differentially: a mutation only counts as caught if the "
                 "verifier's findings change. Detection rates on those documents are therefore upper bounds "
                 "(any change to an already-failing document can alter the findings by accident)._")
    L.append("")
    L.append("## Must-catch corruptions, by document")
    L.append("| document | n | caught/not vouched | silent | rate |")
    L.append("|---|---:|---:|---:|---:|")
    for doc in results.clean:
        rs = [r for r in results.records if r.doc == doc and r.kind == "detect"]
        if rs:
            ok = sum(1 for r in rs if r.outcome in ("caught", "not_vouched"))
            L.append(f"| {doc} | {len(rs)} | {ok} | {sum(1 for r in rs if r.outcome in BAD['detect'])} "
                     f"| {pct(ok / len(rs))} |")
    for kind in ("detect", "no_degrade", "flag", "invariant", "gap", "detect_small"):
        rows = [(cls, a["by_kind"][kind]) for cls, a in agg.items() if kind in a["by_kind"]]
        if not rows:
            continue
        L.append("")
        gated = " **[gated]**" if kind in GATED_KINDS else ""
        L.append(f"## {KIND_TITLES[kind]}{gated}")
        cols = [c for c in OUTCOME_COLS if any(c in k["outcomes"] for _, k in rows)]
        L.append("| class | n | " + " | ".join(cols) + " | rate |")
        L.append("|---|---:|" + "---:|" * len(cols) + "---:|")
        for cls, k in sorted(rows, key=lambda t: t[0]):
            L.append(f"| `{cls}` | {k['n']} | " + " | ".join(str(k['outcomes'].get(c, 0)) for c in cols)
                     + f" | {pct(k['rate'])} |")
    pos, mag = by_position(results.records), by_magnitude(results.records)
    if pos:
        L.append("")
        L.append("## Single-digit misreads: detection by digit position and size")
        L.append("| position (from right) | n | detected |")
        L.append("|---|---:|---:|")
        for p in sorted(pos):
            n = sum(pos[p].values())
            det = pos[p]["caught"] + pos[p]["not_vouched"]
            L.append(f"| 10^{p}{'+' if p == 6 else ''} | {n} | {pct(det / n)} |")
        L.append("")
        L.append("| \\|change\\| (reported units) | n | detected |")
        L.append("|---|---:|---:|")
        for label in ("1-9", "10-99", "100-999", "1000+"):
            if label in mag:
                n = sum(mag[label].values())
                L.append(f"| {label} | {n} | {pct((mag[label]['caught'] + mag[label]['not_vouched']) / n)} |")
        realistic = [r for r in results.records if r.cls == "digit_sub" and r.meta.get("realistic")]
        if realistic:
            det = sum(1 for r in realistic if r.outcome in ("caught", "not_vouched"))
            L.append("")
            L.append(f"Visually-confusable digit pairs only (3/8, 1/7, 0/6, ...): "
                     f"{det}/{len(realistic)} detected ({pct(det / len(realistic))}).")
    if problems:
        L.append("")
        L.append("## Gate violations")
        L.extend(f"- {p}" for p in problems)
    L.append("")
    L.append("## Worst offenders (first examples per gated class)")
    examples: Dict[str, List[Record]] = defaultdict(list)
    for r in results.records:
        if r.kind in GATED_KINDS and r.outcome in BAD[r.kind] and len(examples[r.cls]) < 4:
            examples[r.cls].append(r)
    for cls in sorted(examples):
        L.append(f"- `{cls}` - {CLASS_INFO.get(cls, '')}")
        for r in examples[cls]:
            L.append(f"  - [{r.doc}] {r.outcome}: {r.desc}")
    return "\n".join(L) + "\n"


def render_json(results: Results) -> str:
    agg = aggregate(results.records)
    samples: Dict[str, List[Dict]] = defaultdict(list)
    for r in results.records:
        if r.outcome in ("silent", "silent_degraded", "weak", "false_alarm", "crash") and len(samples[r.cls]) < 25:
            samples[r.cls].append({"doc": r.doc, "outcome": r.outcome, "desc": r.desc})
    return json.dumps({
        "verifier": results.verifier,
        "mutations": len(results.records),
        "seconds": round(results.seconds, 1),
        "clean": {d: {"flagged": v.flagged, "evaluated_equations": v.evaluated_equations,
                      "coverage": v.coverage} for d, v in results.clean.items()},
        "classes": agg,
        "gate_violations": evaluate_gate(results),
        "bad_examples": samples,
    }, ensure_ascii=False, indent=2)
