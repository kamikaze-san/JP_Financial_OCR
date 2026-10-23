"""
Score the PDF text-layer witness (witness.py) on the VLM answers saved by earlier runs; no model is called.

For every saved first-read answer, each row that carries numbers is classified against the gold page:
    correct        the row (label and numbers) is in the gold
    label_typo     the numbers are in the gold under a label that is at least 70% similar (a misread or normalised character)
    misattributed  the numbers are in the gold but under an unrelated label (a value that slid onto another row)
    number_wrong   the numbers are not in the gold (a wrong, invented or truncated value)
and the witness's verdict on that row (agree / label_variant / conflict / unknown) is tallied against it.

CAUTION about what this measures: for the TDnet pages the gold is itself built from the PDF text layer (silver gold), so the comparison tests
the witness's matching of VLM rows to text lines, not the text layer's own correctness. The independent evidence is `independence()`:
the text layer of the hand-checked benchmark documents agrees with their hand-built gold. Producer diversity and scans are not covered.

Splits: A = the five filings the witness was developed on, B = the other five dev filings (held out), T = the sealed test filings (scored once),
C = five more dev filings (one with a PDF producer not seen before), read after the witness was frozen.
    python -m solution2.witness_eval --splits A
"""
import argparse
import json
import random
import sys
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from solution1.models import RawDocumentTree  # noqa: E402
from solution2.formats import numeric_rows  # noqa: E402
from solution2.repair.page import PdfPage  # noqa: E402
from solution2.verify import parse_stage1  # noqa: E402
from solution2.verify.parsing import NIL, NUMBER, clean_label, parse_cell  # noqa: E402
from solution2.witness import check_page, row_numbers  # noqa: E402

T = ROOT / "solution2" / "data" / "tdnet"
RUNS = T / "eval_runs"
A_DOCS = {"140120261006546514", "140120261007547344", "140120261014550192", "140120261014550624", "140120261015551188"}
C_DOCS = {"140120261015551203", "140120260926540626", "140120261013549194", "140120260928540923", "140120261002544993"}   # a fifth dev sample, read after the witness was frozen
D_DOCS = {"140120261009548315", "140120261014550131", "140120261014550891", "140120261015551119"}   # the last four dev filings, read after the witness was frozen
E_DOCS = {"140120261013549760", "140120261007547203", "140120261015551650", "140120261013550007", "140120261009548383", "140120260929542125", "140120260926540603", "140120260930543256", "140120261013549514", "140120261006546736"}   # ten filings downloaded after the witness was frozen
B_DOCS = {"140120260820523764", "140120260925540214", "140120261008548224", "140120261014550474", "140120261014550808"}

# (name, {split: run folder under eval_runs}); one folder per model/format answer set, first reads only
SOURCES = {
    "4B json": {"A": "dev", "B": "dev_b2_qwen4b_rejoin", "T": "test_sealed_4b_json"},
    "2B json": {"A": "dev_qwen2b", "B": "dev_b2_qwen2b"},
    "4B html": {"A": "dev_fmt_html_qwen4b_A", "B": "dev_fmt_html_qwen4b_B"},
    "2B html": {"A": "dev_fmt_html_qwen2b_A", "B": "dev_fmt_html_qwen2b_B"},
    "4B markdown": {"A": "dev_fmt_markdown_qwen4b_A_replay", "B": "dev_fmt_markdown_qwen4b_B_replay"},
    "2B markdown": {"A": "dev_fmt_markdown_qwen2b_A_replay", "B": "dev_fmt_markdown_qwen2b_B_replay", "T": "test_sealed_2b_markdown", "C": "dev_c_2b_markdown", "D": "dev_d_2b_markdown", "E": "test_e_2b_markdown"},
    "8B markdown": {"A": "dev_fmt_markdown_qwen8b_A_replay", "B": "dev_fmt_markdown_qwen8b_B_replay"},
    "4B bands": {"A": "dev_fmt_bands_qwen4b_A_final", "B": "dev_fmt_bands_qwen4b_B_final", "T": "test_sealed_4b_bands", "C": "dev_c_4b_bands", "D": "dev_d_4b_bands", "E": "test_e_4b_bands"},
    "2B bands": {"A": "dev_fmt_bands_qwen2b_A", "B": "dev_fmt_bands_qwen2b_B"},
    "4B bands+md": {"A": "dev_fmt_bandsmd_qwen4b_A", "B": "dev_fmt_bandsmd_qwen4b_B"},
    "2B bands+md": {"A": "dev_fmt_bandsmd_qwen2b_A", "B": "dev_fmt_bandsmd_qwen2b_B"},
}


def manifest() -> List[dict]:
    return json.loads((T / "prepared" / "manifest.json").read_text(encoding="utf-8"))


def split_of(entry: dict) -> str:
    if entry["docID"] in C_DOCS:
        return "C"
    if entry["docID"] in D_DOCS:
        return "D"
    if entry["docID"] in E_DOCS:
        return "E"
    return "T" if entry["split"] == "test" else ("A" if entry["docID"] in A_DOCS else "B")


def _key(label: str, values) -> Tuple[str, Tuple]:
    return clean_label(label)[0].replace("　", "").replace(" ", ""), row_numbers(values)


def truth_classes(raw: RawDocumentTree, gold: RawDocumentTree) -> Dict[int, str]:
    """row_index -> correct | label_typo | misattributed | number_wrong, for the rows of `raw` that carry numbers."""
    left = Counter(numeric_rows(gold))
    cls: Dict[int, str] = {}
    numeric = [r for r in raw.rows if row_numbers(r.values)]
    for r in numeric:
        k = _key(r.account_raw, r.values)
        if left[k] > 0:
            left[k] -= 1
            cls[r.row_index] = "correct"
    # a row the answer repeats more often than the gold has it: every copy is "duplicate" (which copy the witness flags is arbitrary)
    answer_counts = Counter(_key(r.account_raw, r.values) for r in numeric)
    gold_counts = Counter(numeric_rows(gold))
    for r in numeric:
        k = _key(r.account_raw, r.values)
        if answer_counts[k] > gold_counts.get(k, 0) > 0:
            cls[r.row_index] = "duplicate"
    by_values: Dict[Tuple, List[str]] = defaultdict(list)
    for (lab, vals), n in left.items():
        by_values[vals].extend([lab] * n)
    for r in numeric:
        if r.row_index in cls:
            continue
        lab, vals = _key(r.account_raw, r.values)
        cands = by_values.get(vals) or []
        if not cands:
            cls[r.row_index] = "number_wrong"
            continue
        best = max(cands, key=lambda c: SequenceMatcher(None, c, lab).ratio())
        cands.remove(best)
        cls[r.row_index] = "label_typo" if SequenceMatcher(None, best, lab).ratio() >= 0.7 else "misattributed"
    return cls


def load_raw(folder: str, entry: dict) -> Optional[RawDocumentTree]:
    n = entry["page_index"] + 1
    found = list((RUNS / folder).glob(f"{entry['docID']}*/page{n}_vlm_raw.json"))
    if not found:
        return None
    try:
        return parse_stage1(found[0].read_text(encoding="utf-8"))[0]
    except Exception:
        return None


def verdict_name(c) -> str:
    return c.verdict if c.verdict != "conflict" else f"conflict:{c.kind}"


def evaluate(splits: List[str], verbose: bool = False) -> dict:
    entries = [e for e in manifest() if split_of(e) in splits]
    pages: Dict[Tuple[str, int], PdfPage] = {}
    tally = defaultdict(Counter)                    # (split, source) -> Counter[(truth, verdict)]
    page_stats = defaultdict(Counter)
    examples = defaultdict(list)
    for name, folders in SOURCES.items():
        for e in entries:
            sp = split_of(e)
            if sp not in folders:
                continue
            raw = load_raw(folders[sp], e)
            if raw is None:
                continue
            gold = RawDocumentTree.model_validate_json((T / "prepared" / Path(e["silver"]).name).read_text(encoding="utf-8"))
            pk = (e["pdf"], e["page_index"])
            page = pages.setdefault(pk, PdfPage(e["pdf"], e["page_index"]))
            chk = check_page(e["pdf"], e["page_index"], raw, page)
            cls = truth_classes(raw, gold)
            rowc = {c.row_index: c for c in chk.rows}
            wrong_page = flagged_page = False
            for r in raw.rows:
                if r.row_index not in cls:
                    continue
                c = rowc[r.row_index]
                flagged = c.verdict in ("conflict", "label_variant")
                wrong_page |= cls[r.row_index] != "correct"
                flagged_page |= flagged
                if cls[r.row_index] == "duplicate":
                    page_stats[(sp, name)]["dup_rows"] += 1
                    page_stats[(sp, name)]["dup_rows_flagged"] += flagged
                    continue
                tally[(sp, name)][(cls[r.row_index], verdict_name(c))] += 1
                if verbose and ((cls[r.row_index] == "correct") == flagged or (cls[r.row_index] != "correct" and c.verdict == "agree")):
                    examples[(sp, name)].append((e["id"][-12:], cls[r.row_index], verdict_name(c), r.account_raw, c.pdf_label, c.kind))
            gold_missing = sum((Counter(v for _, v in numeric_rows(gold)) - Counter(row_numbers(r.values) for r in raw.rows if row_numbers(r.values))).values())   # a gold row whose numbers appear nowhere in the answer
            page_stats[(sp, name)]["pages"] += 1
            page_stats[(sp, name)]["unusable"] += 0 if chk.usable else 1
            page_stats[(sp, name)]["wrong_pages"] += wrong_page
            page_stats[(sp, name)]["wrong_pages_flagged"] += wrong_page and flagged_page
            page_stats[(sp, name)]["right_pages"] += not wrong_page
            page_stats[(sp, name)]["right_pages_flagged"] += (not wrong_page) and flagged_page
            page_stats[(sp, name)]["gold_missing_pages"] += gold_missing > 0
            page_stats[(sp, name)]["gold_missing_flagged"] += gold_missing > 0 and bool(chk.missing)
            page_stats[(sp, name)]["no_gold_missing"] += gold_missing == 0
            page_stats[(sp, name)]["no_gold_missing_but_listed"] += gold_missing == 0 and bool(chk.missing)
    return {"tally": tally, "pages": page_stats, "examples": examples}


def summarise(result: dict, splits: List[str]) -> str:
    out = []
    for sp in splits:
        total = Counter()
        pg = Counter()
        for (s, name), c in result["tally"].items():
            if s == sp:
                total.update(c)
        for (s, name), c in result["pages"].items():
            if s == sp:
                pg.update(c)
        if not total:
            continue
        wrong = {k: sum(v for (t, vd), v in total.items() if t == k) for k in ("label_typo", "misattributed", "number_wrong")}
        right = sum(v for (t, vd), v in total.items() if t == "correct")
        flag = lambda vd: vd.startswith("conflict") or vd == "label_variant"
        out.append(f"=== split {sp}: {sum(wrong.values())} wrong rows and {right} correct rows (all answer sets pooled)")
        out.append(f"  {'truth':<14}{'rows':>6}{'detected':>10}{'agree(missed)':>15}{'unknown':>9}   detection rate (of decided rows)")
        for k, n in wrong.items():
            det = sum(v for (t, vd), v in total.items() if t == k and flag(vd))
            agree = sum(v for (t, vd), v in total.items() if t == k and vd == "agree")
            unk = sum(v for (t, vd), v in total.items() if t == k and vd == "unknown")
            out.append(f"  {k:<14}{n:>6}{det:>10}{agree:>15}{unk:>9}   {det / (det + agree):.1%}" if det + agree else f"  {k:<14}{n:>6}{det:>10}{agree:>15}{unk:>9}   n/a")
        fa = sum(v for (t, vd), v in total.items() if t == "correct" and flag(vd))
        unk = sum(v for (t, vd), v in total.items() if t == "correct" and vd == "unknown")
        out.append(f"  correct rows: {right}; flagged (false alarm) {fa} ({fa / right:.2%}); unknown {unk} ({unk / right:.2%})")
        out.append(f"  pages: {pg['pages']} answers, unusable text layer {pg['unusable']}; wrong pages flagged {pg['wrong_pages_flagged']}/{pg['wrong_pages']}; "
                   f"correct pages flagged {pg['right_pages_flagged']}/{pg['right_pages']}; pages with a gold row missing from the answer: witness lists a missing line on "
                   f"{pg['gold_missing_flagged']}/{pg['gold_missing_pages']}; on pages where nothing is missing it lists a line anyway on "
                   f"{pg['no_gold_missing_but_listed']}/{pg['no_gold_missing']}")
    return "\n".join(out)


def per_source(result: dict, sp: str) -> str:
    lines = [f"  per answer set, split {sp}: wrong rows detected / decided, correct rows falsely flagged / correct rows"]
    for (s, name), c in sorted(result["tally"].items()):
        if s != sp:
            continue
        flag = lambda vd: vd.startswith("conflict") or vd == "label_variant"
        wrong_det = sum(v for (t, vd), v in c.items() if t != "correct" and flag(vd))
        wrong_dec = sum(v for (t, vd), v in c.items() if t != "correct" and vd != "unknown")
        wrong_all = sum(v for (t, vd), v in c.items() if t != "correct")
        fa = sum(v for (t, vd), v in c.items() if t == "correct" and flag(vd))
        right = sum(v for (t, vd), v in c.items() if t == "correct")
        lines.append(f"    {name:<13} wrong rows {wrong_all:>4}: detected {wrong_det:>4}/{wrong_dec:<4} | false alarms {fa:>3}/{right}")
    return "\n".join(lines)


# ----------------------------------------------------------------------------- injected errors on correct answers
CONFUSABLE = {"繰": "練", "賞": "貸", "売": "買", "受": "払", "債": "責", "権": "務", "資": "貸", "固": "個", "有": "冇", "未": "末"}


def inject(raw: RawDocumentTree, gold: RawDocumentTree, rng: random.Random) -> Dict[str, Optional[RawDocumentTree]]:
    """Copies of a (fully correct) answer with one error of each kind: a block of values shifted up one row, a label character swapped,
    a digit changed, a heading given a value."""
    out: Dict[str, Optional[RawDocumentTree]] = {}
    rows = list(raw.rows)
    num = [i for i, r in enumerate(rows) if row_numbers(r.values) and any(parse_cell(v).kind == NUMBER for v in r.values)]
    # shifted block: 5 consecutive numeric rows
    blocks = [num[i:i + 5] for i in range(len(num) - 4) if num[i + 4] - num[i] == 4]
    if blocks:
        b = rng.choice(blocks)
        vals = [rows[i].values for i in b]
        new = list(rows)
        for k, i in enumerate(b[:-1]):
            new[i] = rows[i].model_copy(update={"values": vals[k + 1]})
        new[b[-1]] = rows[b[-1]].model_copy(update={"values": [""] * len(vals[-1])})
        out["shifted block"] = raw.model_copy(update={"rows": new})
    else:
        out["shifted block"] = None
    # label character swapped
    cands = [(i, ch) for i in num for ch in rows[i].account_raw if ch in CONFUSABLE]
    if cands:
        i, ch = rng.choice(cands)
        lab = rows[i].account_raw.replace(ch, CONFUSABLE[ch], 1)
        new = list(rows)
        new[i] = rows[i].model_copy(update={"account_raw": lab})
        out["label char swapped"] = raw.model_copy(update={"rows": new})
    else:
        out["label char swapped"] = None
    # one digit of one value changed
    i = rng.choice(num)
    r = rows[i]
    vi = next(k for k, v in enumerate(r.values) if parse_cell(v).kind == NUMBER)
    v = r.values[vi]
    pos = rng.choice([k for k, ch in enumerate(v) if ch.isdigit()])
    nv = v[:pos] + str((int(v[pos]) + rng.randint(1, 8)) % 10) + v[pos + 1:]
    vals = list(r.values)
    vals[vi] = nv
    new = list(rows)
    new[i] = r.model_copy(update={"values": vals})
    out["digit changed"] = raw.model_copy(update={"rows": new})
    # a heading row given a value
    heads = [i for i, r in enumerate(rows) if not row_numbers(r.values) and r.account_raw.strip()]
    if heads:
        i = rng.choice(heads)
        new = list(rows)
        new[i] = rows[i].model_copy(update={"values": ["－"] * max(1, len(raw.metadata.periods))})
        out["value on a heading"] = raw.model_copy(update={"rows": new})
    else:
        out["value on a heading"] = None
    return out


def injected_test(splits: List[str], per_page: int = 3, seed: int = 0) -> str:
    rng = random.Random(seed)
    stats = defaultdict(Counter)
    for e in [e for e in manifest() if split_of(e) in splits]:
        sp = split_of(e)
        folder = SOURCES["4B bands"].get(sp)
        raw = load_raw(folder, e) if folder else None
        if raw is None:
            continue
        gold = RawDocumentTree.model_validate_json((T / "prepared" / Path(e["silver"]).name).read_text(encoding="utf-8"))
        cls = truth_classes(raw, gold)
        if any(v != "correct" for v in cls.values()):
            continue                                                  # only answers that are fully correct are corrupted
        page = PdfPage(e["pdf"], e["page_index"])
        base = check_page(e["pdf"], e["page_index"], raw, page)
        for _ in range(per_page):
            for kind, bad in inject(raw, gold, rng).items():
                if bad is None:
                    continue
                chk = check_page(e["pdf"], e["page_index"], bad, page)
                before = {c.row_index: c.verdict for c in base.rows}
                changed = [c for c in chk.rows if c.verdict in ("conflict", "label_variant") and before.get(c.row_index) != c.verdict]
                stats[(sp, kind)]["n"] += 1
                stats[(sp, kind)]["detected"] += bool(changed) or (kind == "shifted block" and bool(chk.missing and not base.missing))
                stats[(sp, kind)]["unusable"] += not chk.usable
    lines = ["  injected errors on fully correct 4B-bands answers (one error per copy): detected / injected"]
    for (sp, kind), c in sorted(stats.items()):
        lines.append(f"    split {sp}  {kind:<20} {c['detected']:>4}/{c['n']:<4} ({c['detected'] / c['n']:.0%})" + (f"  unusable {c['unusable']}" if c["unusable"] else ""))
    return "\n".join(lines)


def independence() -> str:
    """The text layer of the hand-checked benchmark documents vs their hand-built gold (independent of the witness)."""
    from solution2.benchmark_docs import documents
    from solution2.harness.silver import silver_gold
    lines = ["  independence check: text layer vs the hand-built gold of the benchmark documents (rows with numbers)"]
    for name, (pdf, pages, gold) in documents().items():
        if name == "yamato":
            lines.append("    yamato: two-sided page, not comparable by the text-layer reader (declined)")
            continue
        got = Counter()
        try:
            for p in pages:
                got.update(numeric_rows(silver_gold(pdf, p)))
        except Exception as exc:
            lines.append(f"    {name}: text-layer gold could not be built ({exc})")
            continue
        g = Counter(numeric_rows(gold))
        lines.append(f"    {name}: {sum((g & got).values())}/{sum(g.values())} gold rows found in the text layer, {sum((got - g).values())} text-layer rows not in the hand gold")
    return "\n".join(lines)


def abstention_demo() -> str:
    """Where the witness declines, on PDFs that are not ordinary single-table statements (no model, no gold)."""
    from solution2.witness import text_layer_status
    lines = ["  abstention on other PDFs (text layer status / two-sided layout):"]
    for label, path, pages in (("Yamato annual report, T-account pages", "dataset/raw_pdfs/yamato_fs66.pdf", (0, 10)),
                               ("web page printed to PDF (Bandai Namco)", "dataset/raw_pdfs/bandainamco_2024.pdf", (0, 1, 2)),
                               ("web page printed to PDF (Suzuki)", "dataset/raw_pdfs/suzuki_2023.pdf", (0, 1, 2))):
        for pg in pages:
            ok, why = text_layer_status(ROOT / path, pg)
            two = PdfPage(ROOT / path, pg).is_two_column() if ok else None
            verdict = "usable" if ok and not two else ("abstains: two-sided layout" if ok else f"abstains: {why}")
            lines.append(f"    {label} p{pg + 1}: {verdict}")
    return "\n".join(lines)


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--splits", nargs="+", default=["A"], choices=["A", "B", "T", "C", "D", "E"])
    ap.add_argument("--verbose", action="store_true", help="list false alarms and misses")
    ap.add_argument("--no-inject", action="store_true")
    args = ap.parse_args(argv)
    res = evaluate(args.splits, args.verbose)
    print(summarise(res, args.splits))
    for sp in args.splits:
        print(per_source(res, sp))
        if args.verbose:
            for (s, name), ex in sorted(res["examples"].items()):
                if s == sp:
                    for x in ex[:25]:
                        print("    ", name, x)
    if not args.no_inject:
        print(injected_test(args.splits))
    print(independence())
    print(abstention_demo())
    return 0


if __name__ == "__main__":
    sys.exit(main())
