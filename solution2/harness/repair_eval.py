"""
Score the retry loop on real VLM output against ground truth.

    python -m solution2.harness.repair_eval --reader text            # exact-digit reader: tests the loop itself
    python -m solution2.harness.repair_eval --reader ollama --model qwen3-vl:4b-instruct

Every output the verifier does not trust is sent through `repair`, then the result is compared with the gold
document. Outcomes:

    fixed          final document trusted and every cell correct
    fixed_label    final document trusted, every NUMBER correct, but a label is off (a known verifier gap:
                   arithmetic cannot see labels; reported separately so it is not hidden)
    SILENT_WRONG   final document trusted but a number is wrong      <- must be 0
    improved       still not trusted, but fewer cells are wrong
    unchanged      sent back for review with the document untouched (too broken / no region / no consensus)
    worse          still not trusted and MORE cells are wrong        <- must be 0
"""
import argparse
import sys
import time
from pathlib import Path
from difflib import SequenceMatcher
from typing import Dict, List

from solution2.repair import PdfPage, RepairConfig, TextLayerReader, repair
from solution2.repair.reader import OllamaReader
from solution2.verify import parse_stage1, verify_document
from solution2.verify.parsing import parse_cell

from .collect_real import FIXTURES, PAGES
from .corpus import ROOT, load_corpus
from .realcheck import wrong_cells

EXTRA = [  # outputs from the earlier hand-run benchmark: the missed 4,553 and the fabricated 44,556
    ("benchmark_missed_4553", ROOT / "benchmark_results/qwen3-vl_4b-instruct/pass1_raw_output.json", "baseline_p1"),
    ("benchmark_fabricated_44556", ROOT / "benchmark_results/qwen3-vl_4b-instruct/pass2_rerun_clean_feedback.json", "baseline_p1"),
]


def numbers_match(out, gold) -> bool:
    """Label-blind check: do the output's numeric rows equal the gold's, in order, over the page's span?"""
    def numeric_rows(doc):
        return [tuple((c.kind, c.value) for c in map(parse_cell, r.values))
                for r in doc.rows if any(parse_cell(v).kind in ("number", "nil") for v in r.values)]
    a, g = numeric_rows(out), numeric_rows(gold)
    blocks = [b for b in SequenceMatcher(None, g, a, autojunk=False).get_matching_blocks() if b.size]
    if not blocks or sum(b.size for b in blocks) != len(a):
        return False
    lo, hi = min(b.a for b in blocks), max(b.a + b.size for b in blocks)
    return hi - lo == len(a)


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--reader", choices=["text", "ollama"], default="text")
    ap.add_argument("--model", default="qwen3-vl:4b-instruct")
    ap.add_argument("--only", default="", help="substring filter on the fixture name")
    ap.add_argument("--strict", action="store_true", help="exit 1 on SILENT_WRONG or worse")
    args = ap.parse_args(argv)

    corpus = load_corpus()
    jobs = [(p.stem, p.read_text(encoding="utf-8"), p.name.split("__")[0].split("@")[0]) for p in sorted(Path(FIXTURES).glob("*.json"))]
    jobs += [(n, p.read_text(encoding="utf-8"), page) for n, p, page in EXTRA if p.exists()]
    tally: Dict[str, int] = {}
    print(f"{'output':<54}{'wrong':>6}   {'status':<13}{'wrong':>6} {'calls':>6} {'secs':>6}   outcome")
    for name, text, page_name in jobs:
        if args.only not in name:
            continue
        try:
            raw, _ = parse_stage1(text)
        except Exception:
            continue
        gold = corpus[page_name.split("_")[0]]
        before, _ = wrong_cells(raw, gold)
        if verify_document(raw).trusted:
            continue
        pdf, index, _ = PAGES[page_name]
        page = PdfPage(pdf, index)
        reader = TextLayerReader(page) if args.reader == "text" else OllamaReader(args.model)
        t0 = time.time()
        res = repair(raw, page, reader, RepairConfig())
        after, _ = wrong_cells(res.raw, gold)
        if res.verdict.trusted and not after:
            outcome = "fixed"
        elif res.verdict.trusted and numbers_match(res.raw, gold):
            outcome = "fixed_label"
        elif res.verdict.trusted:
            outcome = "SILENT_WRONG"
        elif len(after) > len(before):
            outcome = "worse"
        elif len(after) < len(before):
            outcome = "improved"
        else:
            outcome = "unchanged"
        tally[outcome] = tally.get(outcome, 0) + 1
        print(f"{name:<54}{len(before):>6}   {res.status:<13}{len(after):>6} {res.vlm_calls:>6} {time.time() - t0:>6.0f}   {outcome}")
        for a in res.attempts:
            tag = f"      [{a.kind}] {a.outcome}" + (f": {a.detail}" if a.detail and a.outcome != "accepted" else "")
            print(tag)
            for x in a.applied:
                print(f"         applied {x}")
    print()
    print("  ".join(f"{k}={v}" for k, v in sorted(tally.items())) or "nothing to repair")
    return 1 if args.strict and (tally.get("SILENT_WRONG") or tally.get("worse")) else 0


if __name__ == "__main__":
    sys.exit(main())
