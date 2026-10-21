"""
Statements that continue across a page break.

A balance sheet whose assets end on one page and whose 固定資産合計 sits on the next cannot balance on either page alone.
Joined, it verifies like any other table. These helpers join two consecutive pages' tables and give each page its share
of the joint verdict, so a page-by-page pipeline can keep per-page reports.
"""
import dataclasses
from typing import Set

from solution1.models import RawDocumentTree

from .reducer import VOUCHED, DocumentVerdict


def same_columns(a: RawDocumentTree, b: RawDocumentTree) -> bool:
    """Same unit and same period columns: the precondition for two pages being parts of one statement."""
    return (a.metadata.unit_raw == b.metadata.unit_raw
            and [p.raw_date for p in a.metadata.periods] == [p.raw_date for p in b.metadata.periods])


def join_offset(first: RawDocumentTree) -> int:
    return max((r.row_index for r in first.rows), default=0)


def join_tables(first: RawDocumentTree, second: RawDocumentTree) -> RawDocumentTree:
    """Two consecutive pages as one table; the second page's row indexes are shifted by `join_offset(first)`."""
    offset = join_offset(first)
    rows = list(first.rows) + [r.model_copy(update={"row_index": r.row_index + offset}) for r in second.rows]
    return first.model_copy(update={"rows": rows})


def page_share(verdict: DocumentVerdict, original_indexes: Set[int], shift: int = 0) -> DocumentVerdict:
    """The part of a joint verdict that belongs to one page (`shift` is the offset its rows were given when joined)."""
    status = {(row - shift, col): s for (row, col), s in verdict.cell_status.items() if (row - shift) in original_indexes}
    coverage = (sum(1 for s in status.values() if s in VOUCHED) / len(status)) if status else None
    return dataclasses.replace(verdict, cell_status=status, coverage=coverage)
