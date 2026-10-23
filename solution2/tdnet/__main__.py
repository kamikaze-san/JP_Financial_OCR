"""
No API key needed.

    python -m solution2.tdnet plan --from 2026-09-21 --to 2026-10-20 --n 40 --seed 7 --min-ifrs 8     # LIST ONLY
    python -m solution2.tdnet download --plan solution2/data/tdnet/plan.json --approve <token shown by plan>
    python -m solution2.tdnet prepare            # find balance-sheet pages, build text-layer silver gold; gold vs ambiguous
    python -m solution2.tdnet eval --split dev|test [--include-split] [--include-ambiguous] [--model qwen3-vl:4b-instruct]
    python -m solution2.tdnet snapshot           # copy filings + gold into the tracked solution2/data_sample/tdnet

TDnet's public viewer only keeps about the last 31 days, so `plan` warns when the range reaches further back.
"""
import argparse
import datetime
import json
import sys
from pathlib import Path

from .client import TdnetClient, TdnetError

DATA = Path(__file__).resolve().parents[1] / "data" / "tdnet"
WINDOW_DAYS = 31


def _client(args) -> TdnetClient:
    return TdnetClient(min_interval=args.interval)


def cmd_plan(args) -> int:
    from .plan import USED_CODES, build_plan, business_days, format_plan
    today = datetime.date.today()
    start = datetime.date.fromisoformat(args.from_) if args.from_ else today - datetime.timedelta(days=WINDOW_DAYS - 1)
    end = datetime.date.fromisoformat(args.to) if args.to else today - datetime.timedelta(days=1)
    if (today - start).days > WINDOW_DAYS:
        print(f"warning: {start} is more than {WINDOW_DAYS} days ago; the public viewer probably no longer lists it", file=sys.stderr)
    days = business_days(start, end)
    plan = build_plan(_client(args), days, args.n, args.seed, args.min_ifrs, args.test_fraction, args.allow_unknown,
                      tuple(set(USED_CODES) | set(args.exclude_code)), progress=print)
    if not plan["entries"]:
        print(f"no eligible 決算短信 found between {days[0]} and {days[-1]} (the viewer keeps about {WINDOW_DAYS} days); no plan written",
              file=sys.stderr)
        return 1
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    print()
    print(format_plan(plan))
    print(f"\nplan written to {out}. NOTHING HAS BEEN DOWNLOADED. To fetch exactly this list:\n"
          f"  python -m solution2.tdnet download --plan {out} --approve {plan['approval_token']}")
    return 0


def cmd_download(args) -> int:
    from .download import download_plan
    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    result = download_plan(_client(args), plan, DATA / "raw", args.approve, args.max_mb * 1024 * 1024, progress=print)
    print(f"downloaded {result['downloaded']}, failed {len(result['failed'])}")
    return 1 if result["failed"] else 0


def cmd_prepare(args) -> int:
    from .prepare import prepare_all, sample_docs
    doc_ids = None
    if args.sample or args.docs:
        doc_ids = args.docs or sample_docs(DATA / "raw", args.sample, args.split, args.seed, args.ifrs)
        print(f"preparing {len(doc_ids)} of the downloaded filings only: {doc_ids}")
    res = prepare_all(DATA / "raw", DATA / "prepared", doc_ids)
    shown = res["new"] if doc_ids is not None else res["manifest"]
    gold = [m for m in shown if m["status"] == "gold"]
    split = [m for m in shown if m["status"] == "gold_split"]
    amb = [m for m in shown if m["status"] == "ambiguous"]
    print(f"\n{len(gold)} gold pages, {len(split)} split-statement pages (gold when joined with a neighbour), "
          f"{len(amb)} ambiguous pages, from {len({m['docID'] for m in shown})} filings; "
          f"{len(res['skipped'])} filings had pages that could not be read; {len(res['no_balance_sheet'])} had no balance sheet found")
    if amb:
        print("\nAMBIGUOUS (silver built, verifier flags it: a verifier false alarm OR a text-layer error; read these):")
        for m in amb:
            print(f"  {m['id']:<26} {m['filerName'][:18]:<18} {m['standard']:<7} coverage "
                  f"{(m['v2_coverage'] or 0):.0%}  {m['v2_errors']}")
    for doc, reasons in list(res["skipped"].items())[:12]:
        print(f"  skipped {doc}: {reasons}")
    return 0


def cmd_snapshot(args) -> int:
    from .snapshot import snapshot
    res = snapshot(Path(args.src), Path(args.dst))
    print(f"copied {res['pdfs']} PDFs and {res['pages']} prepared pages to {args.dst}")
    return 0


def cmd_eval(args) -> int:
    from solution2.edinet.eval import format_report, run_eval
    from .snapshot import resolve_manifest
    data = Path(args.data_dir)
    manifest = resolve_manifest(json.loads((data / "prepared" / "manifest.json").read_text(encoding="utf-8")), data)
    keep = {"gold"} | ({"gold_split"} if args.include_split else set()) | ({"gold_split", "ambiguous"} if args.include_ambiguous else set())
    manifest = [m for m in manifest if m["status"] in keep and (not args.docs or m["docID"] in args.docs)]
    tag = f"{args.split}_{args.run_name}" if args.run_name else args.split
    rep = run_eval(manifest, args.split, args.model, data / "eval_runs" / tag, args.only, not args.no_repair,
                   not args.no_join, args.dpi, args.format,
                   Path(args.replay_from) if args.replay_from else None, args.num_ctx or None)
    text = format_report(rep)
    print("\n" + text)
    (data / f"eval_{tag}.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    (data / f"eval_{tag}.txt").write_text(text, encoding="utf-8")
    return 0


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Keyless TDnet 決算短信 source for the solution2 evaluation")
    ap.add_argument("--interval", type=float, default=1.5, help="seconds between requests")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan", help="list filings, PDF URLs and sizes; downloads nothing")
    p.add_argument("--from", dest="from_", help="first date (default: 30 days ago)")
    p.add_argument("--to", help="last date (default: yesterday)")
    p.add_argument("--n", type=int, default=40)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--min-ifrs", type=int, default=0, help="include at least this many IFRS filings if available")
    p.add_argument("--test-fraction", type=float, default=0.5)
    p.add_argument("--allow-unknown", action="store_true", help="also keep titles that name no accounting standard (ETFs)")
    p.add_argument("--exclude-code", nargs="*", default=[], help="extra 4-character securities codes to leave out")
    p.add_argument("--out", default=str(DATA / "plan.json"))
    p.set_defaults(fn=cmd_plan)
    p = sub.add_parser("download", help="download exactly the plan that was approved")
    p.add_argument("--plan", required=True)
    p.add_argument("--approve", required=True, help="the approval token printed by `plan`")
    p.add_argument("--max-mb", type=int, default=300)
    p.set_defaults(fn=cmd_download)
    p = sub.add_parser("prepare", help="find balance-sheet pages and build text-layer silver gold (no network)")
    p.add_argument("--sample", type=int, default=0, help="prepare only a seeded sample of this many downloaded filings")
    p.add_argument("--split", choices=["dev", "test", "all"], default="dev",
                   help="split to sample from (default dev: keeps test unseen)")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--ifrs", type=int, default=0, help="include at least this many IFRS filings in the sample")
    p.add_argument("--docs", nargs="*", help="prepare exactly these docIDs")
    p.set_defaults(fn=cmd_prepare)
    p = sub.add_parser("eval", help="run VLM -> verify -> repair on the prepared pages")
    p.add_argument("--split", choices=["dev", "test", "all"], default="dev")
    p.add_argument("--model", default="qwen3-vl:4b-instruct")
    p.add_argument("--only", default="")
    p.add_argument("--include-split", action="store_true", help="also score pages of statements that continue across pages")
    p.add_argument("--include-ambiguous", action="store_true")
    p.add_argument("--no-repair", action="store_true")
    p.add_argument("--no-join", action="store_true", help="verify every page on its own (no joining of split statements)")
    p.add_argument("--format", choices=["json", "html", "markdown", "bands", "bands_md"], default="json",
                   help="what the first read is: solution1 Stage-1 JSON, Qwen HTML / Markdown tables, or bands (the page cut into bands of text lines)")
    p.add_argument("--replay-from", help="re-parse the saved HTML/Markdown answers under this folder instead of calling the model")
    p.add_argument("--num-ctx", type=int, default=0, help="context window for the model calls (0 = each extractor's default); a smaller one keeps an 8B model mostly on the GPU")
    p.add_argument("--dpi", type=int, default=200, help="render DPI for the first full-page read (repair crops use their own)")
    p.add_argument("--docs", nargs="*", help="score only these docIDs")
    p.add_argument("--run-name", default="", help="suffix for the output folder / report names (e.g. the model), so runs do not overwrite each other")
    p.add_argument("--data-dir", default=str(DATA), help="folder with prepared/manifest.json (default: solution2/data/tdnet; "
                   "the tracked sample is solution2/data_sample/tdnet)")
    p.set_defaults(fn=cmd_eval)
    p = sub.add_parser("snapshot", help="copy filings, plan and gold pages to a tracked folder (relative paths)")
    p.add_argument("--src", default=str(DATA))
    p.add_argument("--dst", default=str(DATA.parents[1] / "data_sample" / "tdnet"))
    p.set_defaults(fn=cmd_snapshot)
    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except TdnetError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
