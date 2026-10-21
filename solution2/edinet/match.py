"""
Match PDF pages to XBRL gold and decide which pages can serve as ground truth.

For every page that contains 資産合計:
  1. build the text-layer silver table (harness/silver.py)
  2. check each of its numbers against the XBRL facts of the same period (value x unit within one unit of a fact)
A page whose numbers are >= 98% explained by XBRL is three-way agreed ground truth: text-layer digits, XBRL facts, and
(where the verifier trusts it) accounting identities. Pages that XBRL explains but the verifier flags are the most
valuable finds: verifier false alarms on clean, independently confirmed data.
"""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from solution1.models import RawDocumentTree
from solution2.harness.silver import silver_gold
from solution2.harness.unseen_scan import candidate_pages
from solution2.verify import verify_document
from solution2.verify.parsing import NUMBER, parse_cell, parse_date, parse_unit

from .xbrl_csv import BsGold

EXPLAINED_MIN = 0.98


@dataclass
class PageMatch:
    page: int                                    # 0-based
    scope: str = ""
    standard: str = ""
    unit_multiplier: Optional[int] = None
    n_cells: int = 0
    explained: float = 0.0
    unexplained: List[str] = field(default_factory=list)
    silver_error: str = ""
    v2_trusted: Optional[bool] = None
    v2_coverage: Optional[float] = None

    @property
    def usable(self) -> bool:
        return not self.silver_error and self.n_cells >= 10 and self.explained >= EXPLAINED_MIN


def explain(silver: RawDocumentTree, gold: BsGold) -> Tuple[int, int, List[str]]:
    """(cells checked, cells explained, up to 8 unexplained cells)."""
    mult = parse_unit(silver.metadata.unit_raw)
    if mult is None:
        return 0, 0, []
    dated = [(parse_date(p.raw_date), c) for c, p in enumerate(silver.metadata.periods) if parse_date(p.raw_date)]
    dated.sort(reverse=True)                                       # newest date first == most recent context first
    contexts = gold.contexts_by_recency()
    checked = explained = 0
    misses: List[str] = []
    for rank, (_, col) in enumerate(dated):
        if rank >= len(contexts):
            break
        facts = gold.values(contexts[rank])
        for row in silver.rows:
            cell = parse_cell(row.values[col]) if col < len(row.values) else None
            if cell is None or cell.kind != NUMBER or not cell.value:
                continue
            checked += 1
            target = cell.value * mult
            if any(abs(f - target) < mult for f in facts):
                explained += 1
            elif len(misses) < 8:
                misses.append(f"{row.account_raw} col {col}: {cell.raw}")
    return checked, explained, misses


def match_pdf(pdf: Path, golds: List[BsGold]) -> List[PageMatch]:
    results: List[PageMatch] = []
    for page in candidate_pages(pdf):
        pm = PageMatch(page)
        try:
            silver = silver_gold(pdf, page)
        except Exception as exc:
            pm.silver_error = str(exc)[:90]
            results.append(pm)
            continue
        best = None
        for g in golds:
            n, ok, misses = explain(silver, g)
            if n and (best is None or ok / n > best[0]):
                best = (ok / n, n, misses, g)
        if best:
            pm.explained, pm.n_cells, pm.unexplained = best[0], best[1], best[2]
            pm.scope, pm.standard = best[3].scope, best[3].standard
        pm.unit_multiplier = parse_unit(silver.metadata.unit_raw)
        v = verify_document(silver)
        pm.v2_trusted, pm.v2_coverage = v.trusted, v.coverage
        results.append(pm)
    return results


def prepare_entry(pdf: Path, golds: List[BsGold], out_dir: Path, meta: Dict[str, str]) -> List[Dict]:
    """Write silver JSON for every usable page; returns manifest rows."""
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = []
    for pm in match_pdf(pdf, golds):
        if not pm.usable:
            continue
        silver = silver_gold(pdf, pm.page)
        ident = f"{meta['docID']}_p{pm.page + 1}"
        (out_dir / f"{ident}.json").write_text(silver.model_dump_json(indent=1), encoding="utf-8")
        manifest.append({"id": ident, **meta, "pdf": str(pdf), "page_index": pm.page, "scope": pm.scope,
                         "standard": pm.standard, "unit_multiplier": pm.unit_multiplier,
                         "explained": round(pm.explained, 4), "n_cells": pm.n_cells,
                         "v2_trusted": pm.v2_trusted, "v2_coverage": pm.v2_coverage,
                         "silver": str(out_dir / f"{ident}.json")})
    return manifest
