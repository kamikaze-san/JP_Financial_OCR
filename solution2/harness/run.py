"""
CLI:  python -m solution2.harness.run --verifier v1
      python -m solution2.harness.run --verifier mypkg.verify:ReducerVerifier --gate
      python -m solution2.harness.run --selftest
"""
import argparse
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from .adapters import make_verifier
from .corpus import load_corpus
from .engine import evaluate_gate, run_harness
from .mutations import MutationConfig, OPERATORS
from .report import render_json, render_markdown
from .selftest import run_selftest

REPORT_DIR = Path(__file__).resolve().parent / "reports"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="v2 deterministic-verification test harness")
    ap.add_argument("--verifier", default="v1", help="v1 | oracle | always_pass | always_flag | pkg.mod:Class")
    ap.add_argument("--docs", help="comma-separated subset of corpus documents")
    ap.add_argument("--classes", help=f"comma-separated subset of: {', '.join(c for c, _ in OPERATORS)}")
    ap.add_argument("--extra", nargs="*", default=[], help="extra gold RawDocumentTree JSON files")
    ap.add_argument("--stride", type=int, default=1, help="keep every n-th mutation per class (faster)")
    ap.add_argument("--material-delta", type=float, default=100.0,
                    help="|change| in reported units above which arithmetic must catch an error")
    ap.add_argument("--out", default=str(REPORT_DIR), help="report directory ('' to skip writing)")
    ap.add_argument("--gate", action="store_true", help="exit 1 on any gated violation")
    ap.add_argument("--selftest", action="store_true", help="validate the harness itself and exit")
    args = ap.parse_args(argv)

    corpus = load_corpus(args.extra)

    if args.selftest:
        failures = run_selftest(corpus, stride=max(args.stride, 3))
        print("SELFTEST", "FAILED" if failures else "PASSED")
        for f in failures:
            print(" -", f)
        return 1 if failures else 0

    verifier = make_verifier(args.verifier, corpus)
    cfg = MutationConfig(material_delta=args.material_delta, stride=args.stride)
    print(f"verifier={getattr(verifier, 'name', args.verifier)}  documents={list(corpus)}")
    results = run_harness(verifier, corpus, cfg,
                          classes=args.classes.split(",") if args.classes else None,
                          docs=args.docs.split(",") if args.docs else None,
                          progress=print)
    md = render_markdown(results, corpus)
    print()
    print(md)
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", results.verifier)
        (out / f"{slug}.md").write_text(md, encoding="utf-8")
        (out / f"{slug}.json").write_text(render_json(results), encoding="utf-8")
        print(f"reports written to {out}\\{slug}.md / .json")
    return 1 if args.gate and evaluate_gate(results) else 0


if __name__ == "__main__":
    sys.exit(main())
