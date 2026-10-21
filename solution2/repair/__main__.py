"""
    python -m solution2.repair stage1.json --pdf statement.pdf [--page 0] [--model qwen3-vl:4b-instruct]
                                           [--reader ollama|text] [--out repaired.json]

Verifies a Stage-1 output and, if it is not trusted, runs the retry loop against the source PDF page.
Exit code 0 only if the final document is trusted.
"""
import argparse
import sys

from solution2.verify import parse_stage1

from .loop import RepairConfig, repair
from .page import PdfPage
from .reader import OllamaReader, TextLayerReader


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Location-only retry loop for a Stage-1 table")
    ap.add_argument("stage1")
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--page", type=int, default=0, help="0-based page index")
    ap.add_argument("--reader", choices=["ollama", "text"], default="ollama")
    ap.add_argument("--model", default="qwen3-vl:4b-instruct")
    ap.add_argument("--out", help="write the repaired Stage-1 JSON here")
    args = ap.parse_args(argv)

    with open(args.stage1, encoding="utf-8") as f:
        raw, notes = parse_stage1(f.read())
    page = PdfPage(args.pdf, args.page)
    reader = OllamaReader(args.model) if args.reader == "ollama" else TextLayerReader(page)
    res = repair(raw, page, reader, RepairConfig())

    for n in notes:
        print(f"[loader] {n}")
    print(f"{res.status.upper()}   VLM calls: {res.vlm_calls}   "
          f"failed cells {sum(1 for s in res.initial.cell_status.values() if s == 'failed')} -> "
          f"{sum(1 for s in res.verdict.cell_status.values() if s == 'failed')}")
    for a in res.attempts:
        print(f"  [{a.kind}] {a.outcome}" + (f": {a.detail}" if a.detail else ""))
        for line in a.applied:
            print(f"      applied {line}")
        if a.outcome != "accepted":
            for line in a.proposed:
                print(f"      proposed (not applied) {line}")
    for f in res.verdict.errors:
        print(f"  still untrusted: {f.message}")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(res.raw.model_dump_json(indent=2))
    return 0 if res.verdict.trusted else 1


if __name__ == "__main__":
    sys.exit(main())
