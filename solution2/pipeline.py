"""
The solution2 pipeline:  render -> VLM (Stage 1) -> verify -> join split pages -> repair -> export with per-cell status.

    python -m solution2.pipeline statement.pdf [--pages 1 2] [--out solution2/output/<name>]
                                 [--model qwen3-vl:4b-instruct] [--dpi 200] [--no-repair] [--no-join]

solution1's pipeline (`python -m solution1.run_pipeline`) is untouched; this is a separate entry point that
reuses only solution1's Stage-1 prompt and data models. Per page it writes:

    page<N>_vlm_raw.json        what the VLM returned, byte for byte
    page<N>_stage1.json         the Stage-1 table the verifier saw (after repair, if any)
    page<N>.json / .xlsx / .md  the report: every cell with its status, repairs, findings, review windows, timing

Flow over the requested pages:
  1. every page is rendered, read by the VLM and verified on its own
  2. a page that does not verify alone is joined with an adjacent page of the same statement (same unit and period
     columns); if the pair verifies, both pages are `trusted_joined` (a statement that continues across a page break
     cannot balance on either page alone)
  3. only pages that are still untrusted go to the retry loop
Every stage is timed (render, VLM extract, verify, join, repair, export), per page, in the report and in summary.json.

Page statuses: trusted | trusted_joined | repaired | needs_review | no_table | extraction_failed.
Exit code 0 only if every page with a table is trusted, trusted_joined or repaired.
"""
import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from solution1.models import RawDocumentTree
from solution2.export import build_report, failure_report, write_excel, write_json, write_markdown
from solution2.extract import Extractor, ollama_extractor, render_page
from solution2.repair import PdfPage, RepairConfig, repair
from solution2.repair.reader import OllamaReader, Reader
from solution2.verify import DocumentVerdict, parse_stage1, verify_document
from solution2.verify.join import join_offset, join_tables, page_share, same_columns

ROOT = Path(__file__).resolve().parents[1]
OK_STATUSES = ("trusted", "trusted_joined", "repaired")


@dataclass
class PageOutcome:
    page: int
    status: str
    trusted: bool
    vlm_calls: int = 0
    files: Dict[str, str] = field(default_factory=dict)
    detail: str = ""
    timing: Dict[str, float] = field(default_factory=dict)
    joined_with: List[int] = field(default_factory=list)


@dataclass
class _Page:
    index: int
    n: int
    out_dir: Path
    source: Dict[str, Any]
    timing: Dict[str, float] = field(default_factory=dict)
    raw: Optional[RawDocumentTree] = None
    verdict: Optional[DocumentVerdict] = None
    failure: Optional[str] = None            # extraction_failed | no_table
    detail: str = ""
    joined_with: List[int] = field(default_factory=list)
    final: Optional[RawDocumentTree] = None
    attempts: List[Any] = field(default_factory=list)
    calls: int = 0
    status: str = ""
    files: Dict[str, str] = field(default_factory=dict)


def _timed(page: _Page, key: str, fn):
    t0 = time.perf_counter()
    try:
        return fn()
    finally:
        page.timing[key] = page.timing.get(key, 0.0) + (time.perf_counter() - t0)


def _try_join(pg: _Page, by_n: Dict[int, "_Page"], tree) -> bool:
    """Join `pg` with an adjacent page of the same statement; on success both pages take their share of the joint verdict."""
    for other_n in (pg.n - 1, pg.n + 1):
        other = by_n.get(other_n)
        if other is None or not same_columns(tree(pg), tree(other)):
            continue
        first, second = (pg, other) if pg.n < other.n else (other, pg)
        joined = _timed(pg, "join_s", lambda: verify_document(join_tables(tree(first), tree(second))))
        if not joined.trusted:
            continue
        shift = join_offset(tree(first))
        pg.verdict = page_share(joined, {r.row_index for r in tree(pg).rows}, shift if pg is second else 0)
        pg.joined_with.append(other.n)
        if other.status == "needs_review":                     # the neighbour was waiting for the same joint verdict
            other.verdict = page_share(joined, {r.row_index for r in tree(other).rows}, shift if other is second else 0)
            other.joined_with.append(pg.n)
            other.status = "trusted_joined"
        return True
    return False


def process_pages(pdf_path, page_indexes: Sequence[int], out_dir: Path, extractor: Extractor,
                  reader_factory: Callable[[PdfPage], Reader], dpi: int = 200, do_repair: bool = True,
                  config: Optional[RepairConfig] = None, join: bool = True) -> List[PageOutcome]:
    out_dir.mkdir(parents=True, exist_ok=True)
    pages = [_Page(i, i + 1, out_dir, {"pdf": str(pdf_path), "page": i + 1, "dpi": dpi}) for i in page_indexes]

    # ---- 1. read and verify every page on its own
    for pg in pages:
        image = _timed(pg, "render_s", lambda: render_page(pdf_path, pg.index, out_dir / "images", dpi))
        text = _timed(pg, "extract_s", lambda: extractor(str(image)))
        if text is None:
            pg.failure, pg.detail = "extraction_failed", "the VLM returned nothing"
            continue
        (out_dir / f"page{pg.n}_vlm_raw.json").write_text(text, encoding="utf-8")
        pg.files["vlm_raw"] = str(out_dir / f"page{pg.n}_vlm_raw.json")
        try:
            pg.raw, notes = parse_stage1(text)
        except Exception as exc:
            pg.failure, pg.detail = "extraction_failed", f"not valid Stage-1 JSON: {str(exc)[:160]}"
            continue
        pg.source["loader_notes"] = notes
        pg.verdict = _timed(pg, "verify_s", lambda: verify_document(pg.raw))
        if any(f.kind == "no_numeric_cells" for f in pg.verdict.errors):
            pg.failure, pg.detail = "no_table", "the page contains no numbers"

    # ---- 2. a statement that continues across a page break verifies only when the pages are joined
    by_n = {pg.n: pg for pg in pages if pg.raw is not None and pg.failure is None}
    if join:
        for pg in sorted(by_n.values(), key=lambda p: p.n):
            if not pg.verdict.trusted:
                _try_join(pg, by_n, lambda p: p.raw)

    # ---- 3. repair what is still untrusted
    for pg in by_n.values():
        pg.final = pg.raw
        if pg.joined_with:
            pg.status = "trusted_joined"
        elif pg.verdict.trusted:
            pg.status = "trusted"
        elif do_repair:
            page = PdfPage(pdf_path, pg.index)
            result = _timed(pg, "repair_s", lambda: repair(pg.raw, page, reader_factory(page), config))
            pg.final, pg.verdict, pg.attempts, pg.calls = result.raw, result.verdict, result.attempts, result.vlm_calls
            pg.status = {"already_trusted": "trusted", "repaired": "repaired", "needs_review": "needs_review"}[result.status]
        else:
            pg.status = "needs_review"

    # ---- 3b. a half that needed repair may verify jointly once its (repaired) neighbour is fixed
    if join:
        for pg in sorted(by_n.values(), key=lambda p: p.n):
            if pg.status == "needs_review":
                if _try_join(pg, by_n, lambda p: p.final):
                    pg.status = "trusted_joined"

    # ---- 4. export
    outcomes = []
    for pg in pages:
        outcomes.append(_export(pg))
    return outcomes


def _export(pg: _Page) -> PageOutcome:
    t0 = time.perf_counter()
    status = pg.failure or pg.status
    if pg.failure:
        report = failure_report(pg.source, pg.failure, pg.detail)
    else:
        (pg.out_dir / f"page{pg.n}_stage1.json").write_text(pg.final.model_dump_json(indent=2), encoding="utf-8")
        pg.files["stage1"] = str(pg.out_dir / f"page{pg.n}_stage1.json")
        if pg.joined_with:
            pg.source["verified_jointly_with_pages"] = pg.joined_with
        report = build_report(pg.source, pg.raw, pg.final, pg.verdict, pg.status, pg.attempts)
    pg.timing["export_s"] = time.perf_counter() - t0          # (the report files below are the export; time them too)
    report["timing"] = {k: round(v, 3) for k, v in pg.timing.items()}
    for ext, writer in (("json", write_json), ("xlsx", write_excel), ("md", write_markdown)):
        if report["rows"] or ext == "json":
            path = pg.out_dir / f"page{pg.n}.{ext}"
            t1 = time.perf_counter()
            writer(report, path)
            pg.timing["export_s"] += time.perf_counter() - t1
            pg.files[ext] = str(path)
    pg.timing["total_s"] = sum(v for k, v in pg.timing.items() if k != "total_s")
    report["timing"] = {k: round(v, 3) for k, v in pg.timing.items()}
    write_json(report, pg.out_dir / f"page{pg.n}.json")                       # final copy with the complete timing
    return PageOutcome(pg.n, status, report["trusted"], pg.calls, pg.files, pg.detail, dict(pg.timing), pg.joined_with)


def process_page(pdf_path, page_index: int, out_dir: Path, extractor: Extractor,
                 reader_factory: Callable[[PdfPage], Reader], dpi: int = 200, do_repair: bool = True,
                 config: Optional[RepairConfig] = None) -> PageOutcome:
    """One page on its own (no neighbour to join with)."""
    return process_pages(pdf_path, [page_index], out_dir, extractor, reader_factory, dpi, do_repair, config, join=False)[0]


def run_pipeline(pdf_path, out_dir=None, pages: Optional[Sequence[int]] = None, extractor: Optional[Extractor] = None,
                 reader_factory: Optional[Callable[[PdfPage], Reader]] = None, model: str = "qwen3-vl:4b-instruct",
                 dpi: int = 200, do_repair: bool = True, config: Optional[RepairConfig] = None,
                 join: bool = True) -> List[PageOutcome]:
    """`pages` are 1-based; default is every page of the PDF."""
    import fitz
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF file not found: {pdf_path}")
    out_dir = Path(out_dir) if out_dir else ROOT / "solution2" / "output" / pdf_path.stem
    extractor = extractor or ollama_extractor(model)
    reader_factory = reader_factory or (lambda page: OllamaReader(model))
    with fitz.open(pdf_path) as doc:
        total = len(doc)
    chosen = [p - 1 for p in pages] if pages else list(range(total))
    outcomes = process_pages(pdf_path, chosen, out_dir, extractor, reader_factory, dpi, do_repair, config, join)
    (out_dir / "summary.json").write_text(json.dumps([o.__dict__ for o in outcomes], ensure_ascii=False, indent=2),
                                          encoding="utf-8")
    return outcomes


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="PDF -> VLM -> verify -> join -> repair -> per-cell-status export")
    ap.add_argument("pdf")
    ap.add_argument("--pages", type=int, nargs="*", help="1-based page numbers (default: all)")
    ap.add_argument("--out", help="output directory (default: solution2/output/<pdf name>)")
    ap.add_argument("--model", default="qwen3-vl:4b-instruct")
    ap.add_argument("--dpi", type=int, default=200)
    ap.add_argument("--no-repair", action="store_true", help="verify only; do not run the retry loop")
    ap.add_argument("--no-join", action="store_true", help="verify every page on its own (no joining of split statements)")
    args = ap.parse_args(argv)

    print("=" * 72)
    print("  solution2 pipeline: render -> VLM -> verify -> join -> repair -> export")
    print("=" * 72)
    outcomes = run_pipeline(args.pdf, args.out, args.pages, model=args.model, dpi=args.dpi,
                            do_repair=not args.no_repair, join=not args.no_join)
    for o in outcomes:
        t = o.timing
        print(f"page {o.page}: {o.status.upper():<16} VLM repair calls: {o.vlm_calls}"
              + (f"   joined with page {o.joined_with}" if o.joined_with else "")
              + (f"   {o.detail}" if o.detail else ""))
        print(f"    time: total {t.get('total_s', 0):.1f}s = render {t.get('render_s', 0):.1f} + VLM extract "
              f"{t.get('extract_s', 0):.1f} + verify {t.get('verify_s', 0) * 1000:.0f}ms + join {t.get('join_s', 0) * 1000:.0f}ms"
              f" + repair {t.get('repair_s', 0):.1f} + export {t.get('export_s', 0):.1f}")
        for kind in ("xlsx", "md", "json"):
            if kind in o.files:
                print(f"    {kind:<5} {o.files[kind]}")
    failed = [o for o in outcomes if o.status not in OK_STATUSES + ("no_table",)]
    print("-" * 72)
    print("ALL PAGES TRUSTED" if not failed else f"{len(failed)} page(s) need review or re-extraction")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
