"""
Turn downloaded 決算短信 PDFs into evaluation pages with text-layer silver gold as ground truth.

For each balance-sheet page (a total such as 資産合計 / 負債合計 / 資本合計 on a page that, or whose predecessor, is titled 貸借対照表 / 財政状態計算書):
  silver     the table read straight from the PDF text layer (no VLM); a page that cannot be read cleanly is skipped, with the reason
  gold       silver AND the verifier trusts it: two independent paths (text-layer digits, accounting identities) agree
  gold_split silver exists, the verifier does not trust the page alone, but DOES trust it joined with the adjacent page: the
             statement continues across the page break (so a page-by-page verifier cannot balance it)
  ambiguous  silver exists but the verifier flags it: either a verifier weakness or a text-layer error. These are LISTED for
             inspection and are not used as ground truth by default (selection bias: a verifier that decides which pages count
             as clean can hide its own false alarms, so the ambiguous list must be read, not ignored)
"""
import json
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Tuple

from solution1.models import RawDocumentTree

from solution2.harness.silver import silver_gold
from solution2.harness.unseen_scan import BS_MARKERS
from solution2.verify import verify_document
from solution2.verify.join import join_tables, same_columns
from solution2.verify.parsing import parse_unit

TITLES = ("貸借対照表", "財政状態計算書")
MAX_PAGES_PER_FILING = 4


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text.replace("\n", "").replace(" ", "").replace("　", ""))


def bs_candidate_pages(pdf) -> List[int]:
    import fitz
    with fitz.open(pdf) as doc:
        texts = [_norm(p.get_text()) for p in doc]
    pages = []
    for i, t in enumerate(texts):
        titled = any(w in t for w in TITLES) or (i > 0 and any(w in texts[i - 1] for w in TITLES))
        if any(m in t for m in BS_MARKERS) and titled:
            pages.append(i)
    return pages[:MAX_PAGES_PER_FILING]


def split_partners(docs: Dict[int, RawDocumentTree], trusted: Dict[int, bool]) -> Dict[int, List[int]]:
    """For every page the verifier does not trust alone: the adjacent page(s) it IS trusted with when the two are
    joined. A statement that continues on the next page (assets on one page, 固定資産合計 on the next) cannot balance on
    either page alone; that is a limit of verifying page by page, not an error in the page."""
    partners: Dict[int, List[int]] = {}
    for page in sorted(docs):
        if trusted[page]:
            continue
        for other in (page - 1, page + 1):
            if other in docs and same_columns(docs[page], docs[other]):
                lo, hi = sorted((page, other))
                if verify_document(join_tables(docs[lo], docs[hi])).trusted:
                    partners.setdefault(page, []).append(other)
    return partners


def prepare_pdf(pdf: Path, meta: Dict[str, Any], out_dir: Path) -> Tuple[List[Dict[str, Any]], List[Tuple[int, str]]]:
    """-> (manifest rows for gold + ambiguous pages, [(page, reason silver could not be built)])"""
    out_dir.mkdir(parents=True, exist_ok=True)
    rows, skipped = [], []
    docs: Dict[int, RawDocumentTree] = {}
    trusted: Dict[int, bool] = {}
    for page in bs_candidate_pages(pdf):
        try:
            silver = silver_gold(pdf, page)
        except Exception as exc:
            skipped.append((page + 1, str(exc)[:90]))
            continue
        n_cells = sum(1 for r in silver.rows for v in r.values if v.strip())
        if n_cells < 10:
            skipped.append((page + 1, f"only {n_cells} numeric cells"))
            continue
        verdict = verify_document(silver)
        docs[page], trusted[page] = silver, verdict.trusted
        ident = f"{meta['docID']}_p{page + 1}"
        path = out_dir / f"{ident}.json"
        path.write_text(silver.model_dump_json(indent=1), encoding="utf-8")
        rows.append({
            "id": ident, "docID": meta["docID"], "filerName": meta.get("filerName", ""), "secCode": meta.get("secCode", ""),
            "title": meta.get("title", ""), "split": meta.get("split", "dev"), "standard": meta.get("standard", "unknown"),
            "pdf": str(pdf), "page_index": page, "unit_multiplier": parse_unit(silver.metadata.unit_raw),
            "n_cells": n_cells, "columns": len(silver.metadata.periods), "silver": str(path),
            "status": "gold" if verdict.trusted else "ambiguous", "v2_coverage": verdict.coverage,
            "v2_errors": sorted({f.kind for f in verdict.errors}), "joined_with": []})
    partners_of = split_partners(docs, trusted)
    for row in rows:                                   # a statement that continues on the next page is not ambiguous
        if row["status"] == "ambiguous" and partners_of.get(row["page_index"]):
            row["status"] = "gold_split"
            row["joined_with"] = [f"{meta['docID']}_p{q + 1}" for q in partners_of[row["page_index"]]]
    return rows, skipped


def prepare_all(raw_dir: Path, out_dir: Path, doc_ids=None, progress=print) -> Dict[str, Any]:
    """Prepare the downloaded filings (all, or only `doc_ids`). Incremental: the manifest keeps pages of filings
    prepared earlier, and only the filings processed now are replaced."""
    ledger = json.loads((raw_dir / "downloads.json").read_text(encoding="utf-8"))
    manifest_path = out_dir / "manifest.json"
    previous = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else []
    wanted = set(doc_ids) if doc_ids is not None else set(ledger)
    manifest: List[Dict[str, Any]] = [m for m in previous if m["docID"] not in wanted]
    new_rows: List[Dict[str, Any]] = []
    skipped: Dict[str, List[Tuple[int, str]]] = {}
    no_bs: List[str] = []
    for doc_id, rec in sorted(ledger.items()):
        if doc_id not in wanted:
            continue
        pdf = raw_dir / doc_id / rec["file"]
        if not pdf.exists():
            continue
        meta = {"docID": doc_id, **{k: rec.get(k, "") for k in ("filerName", "secCode", "title", "split", "standard")}}
        rows, skip = prepare_pdf(pdf, meta, out_dir)
        new_rows += rows
        if skip:
            skipped[doc_id] = skip
        if not rows and not skip:
            no_bs.append(doc_id)
        progress(f"{doc_id} {rec.get('filerName', '')[:20]:<20} {rec.get('standard', ''):<7} "
                 f"gold {sum(r['status'] == 'gold' for r in rows)}  split {sum(r['status'] == 'gold_split' for r in rows)}  ambiguous {sum(r['status'] == 'ambiguous' for r in rows)}  "
                 f"skipped {len(skip)}")
    manifest += new_rows
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"manifest": manifest, "new": new_rows, "skipped": skipped, "no_balance_sheet": no_bs}


def sample_docs(raw_dir: Path, n: int, split: str = "dev", seed: int = 0, ifrs: int = 0) -> List[str]:
    """A seeded sample of downloaded filings from one split, with at least `ifrs` IFRS ones if available."""
    import random
    ledger = json.loads((raw_dir / "downloads.json").read_text(encoding="utf-8"))
    pool = sorted(d for d, r in ledger.items() if (split == "all" or r.get("split") == split) and (raw_dir / d / r["file"]).exists())
    random.Random(seed).shuffle(pool)
    chosen = [d for d in pool if ledger[d].get("standard") == "IFRS"][:ifrs]
    chosen += [d for d in pool if d not in chosen][: max(0, n - len(chosen))]
    return sorted(chosen[:n])
