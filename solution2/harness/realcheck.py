"""
Score verifiers against ground truth on REAL VLM output (fixtures from `collect_real`).

    python -m solution2.harness.realcheck [--verifiers v1 v2] [--strict]

For every fixture the output is compared with the gold document (rows aligned by label sequence),
then each verifier's verdict is classified:

    true_trust     output correct, verifier vouches
    true_flag      output wrong, verifier flags
    SILENT_ERROR   a NUMBER is wrong and the verifier vouches   <- the failure that matters
    label_gap      every number right, a label is off, verifier vouches (known gap: arithmetic cannot see labels)
    false_alarm    output correct, verifier flags
    unparseable    the VLM did not return valid Stage-1 JSON (a pipeline failure, not a verdict)
"""
import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Tuple

from solution1.models import RawDocumentTree

from .adapters import make_verifier
from .collect_real import FIXTURES
from .corpus import load_corpus
from solution2.verify.parsing import clean_label, parse_cell


def wrong_cells(out: RawDocumentTree, gold: RawDocumentTree) -> Tuple[List[str], int]:
    """Differences between a page's output and the gold document (which may span several pages).
    Rows are paired by label sequence, fuzzily, so a typo in a label is not counted as wrong numbers."""
    from solution2.repair.align import align_labels
    g_labels = [clean_label(r.account_raw)[0] for r in gold.rows]
    o_labels = [clean_label(r.account_raw)[0] for r in out.rows]
    pairs = {j: i for i, j in align_labels(o_labels, g_labels).items()}        # gold row -> output row
    problems: List[str] = []
    if not pairs:
        return ["no rows could be paired with the gold document"], len(out.rows)
    lo, hi = min(pairs), max(pairs)
    paired_out = set(pairs.values())
    for gi, oi in pairs.items():
        g, o = gold.rows[gi], out.rows[oi]
        for c in range(max(len(g.values), len(o.values))):
            gv = parse_cell(g.values[c]) if c < len(g.values) else None
            ov = parse_cell(o.values[c]) if c < len(o.values) else None
            if (gv and gv.kind, gv and gv.value) != (ov and ov.kind, ov and ov.value) and                     not ({gv and gv.kind, ov and ov.kind} <= {"blank", None}):
                problems.append(f"{o.account_raw} col {c}: {o.values[c] if c < len(o.values) else None!r} "
                                f"!= {g.values[c] if c < len(g.values) else None!r}")
    for gi in range(lo, hi + 1):                    # gold rows inside the page's span that the output lacks
        if gi not in pairs and any(parse_cell(v).kind == "number" for v in gold.rows[gi].values):
            problems.append(f"missing row {gold.rows[gi].account_raw!r}")
    for oi, r in enumerate(out.rows):               # rows the output invented / garbled
        if oi not in paired_out and any(parse_cell(v).kind in ("number", "bad") for v in r.values):
            problems.append(f"unexpected row {r.account_raw!r} {r.values}")
    return problems, len(out.rows)


def numbers_match(out: RawDocumentTree, gold: RawDocumentTree) -> bool:
    """Label-blind check: do the output's numeric rows equal the gold's, in order, over the page's span?"""
    from difflib import SequenceMatcher

    def numeric_rows(doc):
        return [tuple((c.kind, c.value) for c in map(parse_cell, r.values))
                for r in doc.rows if any(parse_cell(v).kind in ("number", "nil") for v in r.values)]
    a, g = numeric_rows(out), numeric_rows(gold)
    blocks = [b for b in SequenceMatcher(None, g, a, autojunk=False).get_matching_blocks() if b.size]
    if not blocks or sum(b.size for b in blocks) != len(a):
        return False
    return max(b.a + b.size for b in blocks) - min(b.a for b in blocks) == len(a)


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    from solution2.verify.loader import parse_stage1

    ap = argparse.ArgumentParser()
    ap.add_argument("--verifiers", nargs="+", default=["v1", "v2"])
    ap.add_argument("--fixtures", default=str(FIXTURES))
    ap.add_argument("--strict", action="store_true", help="exit 1 if any verifier has a SILENT_ERROR")
    args = ap.parse_args(argv)

    corpus = load_corpus()
    verifiers = {v: make_verifier(v, corpus) for v in args.verifiers}
    totals: Dict[str, Counter] = defaultdict(Counter)
    print(f"{'fixture':<52}{'rows':>5} {'wrong':>6}   " + "   ".join(f"{v:<13}" for v in verifiers))
    for path in sorted(Path(args.fixtures).glob("*.json")):
        gold = corpus[path.name.split("_")[0]]
        text = path.read_text(encoding="utf-8")
        try:
            raw, _ = parse_stage1(text)
        except Exception as exc:
            print(f"{path.stem:<52}  unparseable ({str(exc)[:40]})")
            for v in verifiers:
                totals[v]["unparseable"] += 1
            continue
        problems, n = wrong_cells(raw, gold)
        cells = []
        for name, verifier in verifiers.items():
            flagged = verifier.verify(raw).flagged
            kind = {(False, False): "true_trust", (True, True): "true_flag",
                    (True, False): "SILENT_ERROR", (False, True): "false_alarm"}[(bool(problems), flagged)]
            if kind == "SILENT_ERROR" and numbers_match(raw, gold):
                kind = "label_gap"          # trusted, every number right, a label is off: arithmetic cannot see labels
            totals[name][kind] += 1
            cells.append(f"{kind:<13}")
        print(f"{path.stem:<52}{n:>5} {len(problems):>6}   " + "   ".join(cells))
        for p in problems[:3]:
            print(f"      - {p}")
    print()
    for name, c in totals.items():
        print(f"{name}: " + ", ".join(f"{k}={c[k]}" for k in
                                       ("true_trust", "true_flag", "SILENT_ERROR", "label_gap", "false_alarm", "unparseable")))
    return 1 if args.strict and any(c["SILENT_ERROR"] for c in totals.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
