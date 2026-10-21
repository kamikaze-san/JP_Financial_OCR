"""
    export EDINET_API_KEY=...                       (the key is never accepted on the command line)

    python -m solution2.edinet plan --dates 2025-06-24 2025-06-25 --n 40 --seed 7        # LIST ONLY: nothing is downloaded
    python -m solution2.edinet download --plan solution2/data/edinet/plan.json --approve <token shown by plan>
    python -m solution2.edinet prepare                                                  # parse CSV, find BS pages, build gold
    python -m solution2.edinet eval --split dev|test [--model qwen3-vl:4b-instruct]
    python -m solution2.edinet inspect path/to/file.csv                                 # what the CSV reader understood
"""
import argparse
import datetime
import json
import sys
from pathlib import Path

from .client import EdinetClient, EdinetError, load_key

DATA = Path(__file__).resolve().parents[1] / "data" / "edinet"


def _client(args) -> EdinetClient:
    return EdinetClient(load_key(args.key_file), min_interval=args.interval)


def cmd_plan(args) -> int:
    from .plan import build_plan, format_plan
    dates = [datetime.date.fromisoformat(d) for d in args.dates]
    plan = build_plan(_client(args), dates, args.n, args.seed, tuple(args.types), args.test_fraction, progress=print)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    print()
    print(format_plan(plan))
    print(f"\nplan written to {out}. NOTHING HAS BEEN DOWNLOADED. To fetch exactly this list:\n"
          f"  python -m solution2.edinet download --plan {out} --approve {plan['approval_token']}")
    return 0


def cmd_download(args) -> int:
    from .download import download_plan
    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    result = download_plan(_client(args), plan, DATA / "raw", args.approve, args.max_mb * 1024 * 1024, progress=print)
    print(f"downloaded {result['downloaded']}, failed {len(result['failed'])}")
    return 1 if result["failed"] else 0


def cmd_prepare(args) -> int:
    from .match import prepare_entry
    from .xbrl_csv import golds_from_dir
    ledger = json.loads((DATA / "raw" / "downloads.json").read_text(encoding="utf-8"))
    manifest, skipped = [], []
    for doc_id, rec in sorted(ledger.items()):
        folder = DATA / "raw" / doc_id
        golds = golds_from_dir(folder / "csv")
        if not golds:
            skipped.append((doc_id, "no balance-sheet facts in the CSV"))
            continue
        meta = {"docID": doc_id, "filerName": rec.get("filerName", ""), "secCode": rec.get("secCode", ""),
                "split": rec.get("split", "dev"), "periodEnd": rec.get("periodEnd", "")}
        rows = prepare_entry(folder / f"{doc_id}.pdf", golds, DATA / "prepared", meta)
        manifest += rows
        print(f"{doc_id} {meta['filerName'][:26]:<26} {golds[0].standard:<7} pages usable as gold: {len(rows)}")
        if not rows:
            skipped.append((doc_id, "no page whose numbers XBRL explains"))
    (DATA / "prepared").mkdir(parents=True, exist_ok=True)
    (DATA / "prepared" / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n{len(manifest)} gold pages from {len(ledger) - len(skipped)} filings; skipped: {skipped}")
    flagged = [m for m in manifest if not m["v2_trusted"]]
    print(f"verifier flags {len(flagged)} of these XBRL-confirmed clean pages (false alarms to investigate): "
          f"{[m['id'] for m in flagged]}")
    return 0


def cmd_eval(args) -> int:
    from .eval import format_report, run_eval
    manifest = json.loads((DATA / "prepared" / "manifest.json").read_text(encoding="utf-8"))
    rep = run_eval(manifest, args.split, args.model, DATA / "eval_runs" / args.split, args.only, not args.no_repair,
                   not args.no_join)
    text = format_report(rep)
    print("\n" + text)
    (DATA / f"eval_{args.split}.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    (DATA / f"eval_{args.split}.txt").write_text(text, encoding="utf-8")
    return 0


def cmd_inspect(args) -> int:
    from .xbrl_csv import describe
    print(describe(args.csv))
    return 0


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="EDINET downloader + XBRL-CSV gold for the solution2 evaluation")
    ap.add_argument("--key-file", help="file containing the API key (alternative to the EDINET_API_KEY variable)")
    ap.add_argument("--interval", type=float, default=2.5, help="seconds between API requests")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan", help="list filings and sizes; downloads nothing")
    p.add_argument("--dates", nargs="+", required=True, help="filing dates YYYY-MM-DD (one documents.json call each)")
    p.add_argument("--types", nargs="+", default=["120"], help="docTypeCode: 120 annual report, 160 semi-annual")
    p.add_argument("--n", type=int, default=40)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--test-fraction", type=float, default=0.5)
    p.add_argument("--out", default=str(DATA / "plan.json"))
    p.set_defaults(fn=cmd_plan)
    p = sub.add_parser("download", help="download exactly the plan that was approved")
    p.add_argument("--plan", required=True)
    p.add_argument("--approve", required=True, help="the approval token printed by `plan`")
    p.add_argument("--max-mb", type=int, default=500)
    p.set_defaults(fn=cmd_download)
    p = sub.add_parser("prepare", help="build gold pages from the downloaded filings")
    p.set_defaults(fn=cmd_prepare)
    p = sub.add_parser("eval", help="run VLM -> verify -> repair on the prepared pages")
    p.add_argument("--split", choices=["dev", "test", "all"], default="dev")
    p.add_argument("--model", default="qwen3-vl:4b-instruct")
    p.add_argument("--only", default="")
    p.add_argument("--no-repair", action="store_true")
    p.add_argument("--no-join", action="store_true", help="verify every page on its own (no joining of split statements)")
    p.set_defaults(fn=cmd_eval)
    p = sub.add_parser("inspect", help="show what the CSV reader understood from one file")
    p.add_argument("csv")
    p.set_defaults(fn=cmd_inspect)
    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except EdinetError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
