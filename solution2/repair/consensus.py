"""
Majority vote over independent re-reads of one crop, and the patches that vote proposes.

Everything is anchored on the document's own rows. Each read is aligned to them by label; a cell gets a
consensus value only if at least `min_agree` reads give the same value (compared as parsed numbers, so
"4,553" and "4553" agree). Rows the reads agree on that the document lacks become insert proposals; rows
every read lacks become delete proposals.

Patches are restricted to the failing window: a cell may only change if the verifier did not vouch for it,
and rows may only be added/removed next to / inside the window. Nothing outside the window can be touched.
"""
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Optional, Sequence, Set, Tuple

from solution1.models import RawDocumentTree, RawRow
from solution2.verify.parsing import BAD, BLANK, clean_label, parse_cell

from .align import align_labels
from .reader import ReadRow


@dataclass
class Insert:
    after: Optional[int]           # row_index of the document row above (None: top of the crop)
    label: str
    values: List[str]
    votes: int


@dataclass
class Consensus:
    n_reads: int
    cells: Dict[int, List[Optional[str]]]     # row_index -> per-column consensus string (None: no majority)
    seen: Dict[int, int]                      # row_index -> reads that contained the row
    inserts: List[Insert]
    absent: List[int]                         # row_indexes that no read contains

    @property
    def unaligned_ratio(self) -> float:
        return sum(1 for n in self.seen.values() if n == 0) / len(self.seen) if self.seen else 1.0


@dataclass(frozen=True)
class Patch:
    kind: str                      # cell | insert | delete
    row_index: int                 # target row (cell/delete) or the row above (insert; -1 = top of the crop)
    col: int = -1
    old: str = ""
    new: str = ""
    label: str = ""
    values: Tuple[str, ...] = ()
    next_row: Optional[int] = None  # insert: row_index of the row that should follow
    votes: int = 0

    @property
    def group(self) -> str:
        return {"cell": f"row{self.row_index}", "insert": f"ins{self.row_index}:{self.label}",
                "delete": f"del{self.row_index}"}[self.kind]

    def describe(self, label_of: Dict[int, str]) -> str:
        if self.kind == "cell":
            return f"{label_of.get(self.row_index, self.row_index)!r} col {self.col}: {self.old!r} -> {self.new!r} ({self.votes} reads)"
        if self.kind == "insert":
            return f"insert row {self.label!r} {list(self.values)} ({self.votes} reads)"
        return f"delete row {label_of.get(self.row_index, self.row_index)!r}"


def _key(raw: str):
    c = parse_cell(raw)
    return (c.kind, c.value)


def _majority(raws: Sequence[str], min_agree: int) -> Optional[str]:
    votes = Counter(_key(r) for r in raws if parse_cell(r).kind != BAD)
    if not votes:
        return None
    key, n = votes.most_common(1)[0]
    if n < min_agree or list(votes.values()).count(n) > 1:        # no clear majority
        return None
    return next(r for r in raws if parse_cell(r).kind != BAD and _key(r) == key)


def build_consensus(doc_rows: Sequence[RawRow], reads: Sequence[List[ReadRow]], ncols: int,
                    min_agree: Optional[int] = None) -> Consensus:
    n = len(reads)
    min_agree = min_agree or n // 2 + 1
    doc_labels = [clean_label(r.account_raw)[0] for r in doc_rows]
    obs: List[List[Optional[ReadRow]]] = [[None] * n for _ in doc_rows]
    inserts_raw: Dict[Tuple[Optional[int], str], List[List[str]]] = {}
    for k, read in enumerate(reads):
        mapping = align_labels(doc_labels, [clean_label(x.label)[0] for x in read])
        reverse = {j: i for i, j in mapping.items()}
        for i, j in mapping.items():
            obs[i][k] = read[j]
        above: Optional[int] = None
        for j, x in enumerate(read):
            if j in reverse:
                above = doc_rows[reverse[j]].row_index
            elif clean_label(x.label)[0] and any(parse_cell(v).kind in ("number", "nil") for v in x.values):
                inserts_raw.setdefault((above, clean_label(x.label)[0]), []).append(x.values + [x.label])
    cells: Dict[int, List[Optional[str]]] = {}
    seen: Dict[int, int] = {}
    for i, row in enumerate(doc_rows):
        present = [o for o in obs[i] if o is not None]
        seen[row.row_index] = len(present)
        cells[row.row_index] = [
            _majority([o.values[c] if c < len(o.values) else "" for o in present], min_agree)
            for c in range(ncols)]
    inserts = []
    for (above, label), obs_rows in inserts_raw.items():
        if len(obs_rows) < min_agree:
            continue
        values = [_majority([v[c] for v in obs_rows if c < len(v) - 1], min_agree) for c in range(ncols)]
        if any(v is None for v in values):
            continue
        inserts.append(Insert(above, obs_rows[0][-1], values, len(obs_rows)))
    absent = [r.row_index for i, r in enumerate(doc_rows) if seen[r.row_index] == 0] if n >= min_agree else []
    return Consensus(n, cells, seen, inserts, absent)


def propose_patches(doc_rows: Sequence[RawRow], consensus: Consensus, failing_cells: Set[Tuple[int, int]],
                    window_rows: Set[int]) -> List[Patch]:
    """Patches the consensus supports, limited to the failing window."""
    patches: List[Patch] = []
    order = [r.row_index for r in doc_rows]
    for row in doc_rows:
        cons = consensus.cells.get(row.row_index, [])
        for c, new in enumerate(cons):
            if new is None or (row.row_index, c) not in failing_cells:
                continue
            old = row.values[c] if c < len(row.values) else ""
            if _key(new) != _key(old):
                patches.append(Patch("cell", row.row_index, c, old, new, votes=consensus.n_reads))
    for ins in consensus.inserts:
        pos = order.index(ins.after) if ins.after in order else -1
        nxt = order[pos + 1] if pos + 1 < len(order) else None
        if (ins.after in window_rows) or (nxt in window_rows):
            patches.append(Patch("insert", ins.after if ins.after is not None else -1, label=ins.label,
                                 values=tuple(ins.values), next_row=nxt, votes=ins.votes))
    by_index = {r.row_index: r for r in doc_rows}
    for idx in consensus.absent:
        row = by_index[idx]
        plain_item = (row.role in ("item", "contra_item", "netted_item") and not clean_label(row.account_raw)[0].endswith("計")
                      and any(parse_cell(v).kind in ("number", "nil") for v in row.values))
        if idx in window_rows and plain_item:                      # structure rows are never deleted
            patches.append(Patch("delete", idx, votes=consensus.n_reads))
    return patches


def apply_patches(raw: RawDocumentTree, patches: Sequence[Patch]) -> RawDocumentTree:
    rows = list(raw.rows)
    next_index = max((r.row_index for r in rows), default=0) + 1
    for p in patches:
        position = {r.row_index: i for i, r in enumerate(rows)}
        if p.kind in ("cell", "delete") and p.row_index not in position:
            continue                                   # an earlier patch already removed the target
        if p.kind == "cell":
            i = position[p.row_index]
            values = list(rows[i].values) + [""] * max(0, p.col + 1 - len(rows[i].values))
            values[p.col] = p.new
            rows[i] = rows[i].model_copy(update={"values": values})
        elif p.kind == "delete":
            rows.pop(position[p.row_index])
        else:
            at = position[p.row_index] + 1 if p.row_index in position else position.get(p.next_row, 0)
            ref = rows[min(max(at - 1, 0), len(rows) - 1)] if rows else None
            rows.insert(at, RawRow(row_index=next_index, indent_level=ref.indent_level if ref else 0,
                                   account_raw=p.label, role="subtotal" if p.label.endswith("計") else "item",
                                   values=list(p.values)))
            next_index += 1
    # a heading that received numbers from the reads is a line item the VLM had turned into a heading
    for i, r in enumerate(rows):
        if r.row_index in {p.row_index for p in patches if p.kind == "cell"} and                 r.role in ("section_header", "group_header", "category_header") and any(v.strip() for v in r.values):
            rows[i] = r.model_copy(update={"role": "item"})
    # a line item whose cells were all blanked by the reads is a heading, not an empty item: re-tag it so
    # the verifier opens a section there (the row's own patch descriptions already log the blanking)
    touched = {p.row_index for p in patches if p.kind == "cell"}
    for i, r in enumerate(rows):
        if (r.row_index in touched and r.role in ("item", "contra_item", "netted_item", "subtotal", "grand_total")
                and not any(v.strip() for v in r.values)):
            rows[i] = r.model_copy(update={"role": "category_header"})
    return raw.model_copy(update={"rows": rows})
