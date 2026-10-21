"""
    python -m solution2.verify path/to/stage1_output.json [--min-coverage 0.95] [-v]

Verifies a saved Stage-1 VLM output and prints what is trusted, what failed and what is unverified.
"""
import argparse
import sys
from collections import Counter

from .loader import parse_stage1
from .reducer import VerifierConfig, verify_document


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Deterministic verification of a Stage-1 table")
    ap.add_argument("path")
    ap.add_argument("--min-coverage", type=float, default=0.95)
    ap.add_argument("-v", "--verbose", action="store_true", help="also list every equation")
    args = ap.parse_args(argv)

    with open(args.path, encoding="utf-8") as f:
        raw, notes = parse_stage1(f.read())
    v = verify_document(raw, VerifierConfig(min_coverage=args.min_coverage))
    for n in notes:
        print(f"  [loader] {n}")

    print(f"{'TRUSTED' if v.trusted else 'NOT TRUSTED'}   coverage {v.coverage:.0%}" if v.coverage is not None
          else "NOT TRUSTED   no numeric cells")
    print(f"unit x{v.unit_multiplier}   periods {v.period_dates}   rounding convention: {v.convention}   "
          f"equations evaluated: {v.equations_evaluated}")
    print("cells: " + ", ".join(f"{k}={n}" for k, n in sorted(Counter(v.cell_status.values()).items())))
    for f in v.findings:
        print(f"  [{f.severity}] {f.kind}: {f.message}")
    if args.verbose:
        for e in v.equations:
            res = "  ".join(f"col{c}: {d if d is not None else '?'} ({s})" for c, (d, s) in e.results.items())
            print(f"  {e.kind:<4} {e.total[1]!r} <- {len(e.components)} rows   {res}")
    return 0 if v.trusted else 1


if __name__ == "__main__":
    sys.exit(main())
