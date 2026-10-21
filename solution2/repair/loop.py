"""
The automated retry loop: find what the verifier does not trust, re-read just that region, and apply only
what independent reads agree on AND the verifier confirms.

    verify -> failing windows -> crop the page around each window -> N fresh independent reads
           -> majority vote -> patches limited to the window -> keep a patch only if the verifier is
              strictly happier and nothing it vouched for is lost -> verify again

Guarantees (each is enforced in code and covered by a test):
  * leak-free: the reader's prompt depends only on the number of columns (no labels, numbers, deficits)
  * fresh: every read is a single new chat turn on one image; no earlier answer is in context
  * scoped: cells change only if the verifier did not vouch for them and they lie in the failing window;
    rows are inserted/deleted only inside or next to the window
  * confirmed: a patch needs a majority of reads AND a strict improvement from the verifier
  * fail-closed: anything unresolved ends as `needs_review`; the loop never forces a pass
"""
from dataclasses import dataclass, field
from itertools import combinations
from typing import Dict, List, Optional, Sequence, Set, Tuple

from solution1.models import RawDocumentTree
from solution2.verify import DocumentVerdict, VerifierConfig, Window, verify_document
from solution2.verify.reducer import FAILED, STRUCTURE_AMBIGUOUS, UNVERIFIED, VOUCHED

from .consensus import Patch, apply_patches, build_consensus, propose_patches
from .page import PdfPage
from .reader import Crop, Reader


@dataclass
class RepairConfig:
    reads: Tuple[Tuple[int, float], ...] = ((300, 4.0), (400, 10.0), (250, 16.0))   # (dpi, padding pt) per read
    min_valid_reads: int = 2
    max_rounds: int = 2
    max_windows: int = 6
    max_broken_fraction: float = 0.9     # more failed cells than this: re-extract the page, do not spend reads on it
    max_subset_groups: int = 6
    verifier: Optional[VerifierConfig] = None


@dataclass
class Attempt:
    kind: str
    rows: List[str]
    cols: List[int]
    outcome: str       # accepted | rejected | no_region | reads_failed | no_consensus | no_patch | too_broken | error
    detail: str = ""
    region: Optional[Tuple[float, float, float, float]] = None
    reads_ok: int = 0
    reads_total: int = 0
    proposed: List[str] = field(default_factory=list)
    applied: List[str] = field(default_factory=list)


@dataclass
class RepairResult:
    status: str                      # already_trusted | repaired | needs_review
    raw: RawDocumentTree
    verdict: DocumentVerdict
    initial: DocumentVerdict
    attempts: List[Attempt]
    vlm_calls: int

    @property
    def applied(self) -> List[str]:
        return [p for a in self.attempts for p in a.applied]


# ----------------------------------------------------------------------------- acceptance
def _failed(v: DocumentVerdict) -> int:
    return sum(1 for s in v.cell_status.values() if s == FAILED)


def _failing_totals(v: DocumentVerdict) -> Set[int]:
    return {f.key[1] for f in v.errors if f.kind == "equation_failed"}


def _vouched(v: DocumentVerdict) -> Set[Tuple[int, int]]:
    return {k for k, s in v.cell_status.items() if s in VOUCHED}


def improves(old: DocumentVerdict, new: DocumentVerdict, removed_rows: Set[int] = frozenset()) -> bool:
    """The verifier must be strictly happier, with nothing it vouched for lost and no new failing equation."""
    if any(k not in _vouched(new) for k in _vouched(old) if k[0] not in removed_rows):
        return False
    if new.trusted and not old.trusted:
        return True
    if (new.coverage or 0.0) < (old.coverage or 0.0) - 1e-9:
        return False
    if not _failing_totals(new) <= _failing_totals(old):
        return False
    new_kinds = {f.kind for f in new.errors} - {"low_coverage"}
    return _failed(new) < _failed(old) and new_kinds <= {f.kind for f in old.errors}


def _best_subset(raw: RawDocumentTree, verdict: DocumentVerdict, patches: Sequence[Patch],
                 cfg: RepairConfig) -> Optional[Tuple[RawDocumentTree, DocumentVerdict, List[Patch]]]:
    groups: Dict[str, List[Patch]] = {}
    for p in patches:
        groups.setdefault(p.group, []).append(p)
    names = list(groups)
    if len(names) <= cfg.max_subset_groups:
        subsets = [c for n in range(len(names), 0, -1) for c in combinations(names, n)]
    else:
        subsets = [tuple(names)] + [(n,) for n in names]
    best = None
    for subset in subsets:
        chosen = [p for n in subset for p in groups[n]]
        candidate = apply_patches(raw, chosen)
        v = verify_document(candidate, cfg.verifier)
        removed = {p.row_index for p in chosen if p.kind == "delete"}
        if not improves(verdict, v, removed):
            continue
        key = (not v.trusted, _failed(v), len(subset))
        if best is None or key < best[0]:
            best = (key, candidate, v, chosen)
    return best[1:] if best else None


# ----------------------------------------------------------------------------- windows
def _merge_windows(raw: RawDocumentTree, windows: Sequence[Window]) -> List[Window]:
    pos = {r.row_index: i for i, r in enumerate(raw.rows)}
    spans = sorted(((min(pos[i] for i in w.rows), max(pos[i] for i in w.rows), w) for w in windows if w.rows),
                   key=lambda t: t[0])
    merged: List[List] = []
    for lo, hi, w in spans:
        if merged and lo <= merged[-1][1] + 2:
            m = merged[-1]
            m[1] = max(m[1], hi)
            m[2] = Window("failed" if "failed" in (m[2].kind, w.kind) else m[2].kind,
                          sorted(set(m[2].rows) | set(w.rows), key=pos.get), sorted(set(m[2].cols) | set(w.cols)))
        else:
            merged.append([lo, hi, Window(w.kind, sorted(set(w.rows), key=pos.get), sorted(w.cols))])
    return [m[2] for m in merged]


def _repair_window(raw, verdict, window, page: PdfPage, reader: Reader, cfg: RepairConfig, ncols: int):
    """Returns (raw, verdict, attempt, vlm_calls)."""
    label = {r.row_index: r.account_raw for r in raw.rows}
    pos = {r.row_index: i for i, r in enumerate(raw.rows)}
    att = Attempt(window.kind, [label[i] for i in window.rows], window.cols, "no_region")
    window_rows = set(window.rows)
    failing_cells = {(i, c) for i in window.rows for c in window.cols
                     if verdict.cell_status.get((i, c)) in (FAILED, UNVERIFIED, STRUCTURE_AMBIGUOUS)}
    # a value-less row (a heading) right beside the window may really be a line item the VLM turned into a
    # heading; its blank cells are not vouched for either, so the reads may fill them (still majority + verifier)
    lo, hi = min(pos[i] for i in window.rows), max(pos[i] for i in window.rows)
    for q in range(max(0, lo - 1), hi + 1):
        r = raw.rows[q]
        if not any(v.strip() for v in r.values):
            failing_cells |= {(r.row_index, c) for c in window.cols}
    regions = page.locate(raw, window.rows)
    if not regions:
        att.detail = ("a two-column (T-account) page cannot be cropped by row" if page.is_two_column()
                      else "too few of the window's rows could be found on the page")
        return raw, verdict, att, 0

    calls, patches, seen_somewhere = 0, [], set()
    for region in regions:
        att.region = region.rect
        reads = []
        for dpi, pad in cfg.reads:
            x0, y0, x1, y1 = region.rect
            image = page.render(region.rect, dpi, pad, tag="read")
            calls += 1
            att.reads_total += 1
            result = reader.read(Crop(image, (x0 - pad, y0 - pad, x1 + pad, y1 + pad), dpi), ncols)
            if result is not None:
                reads.append(result)
        att.reads_ok += len(reads)
        if len(reads) < cfg.min_valid_reads:
            att.outcome, att.detail = "reads_failed", f"only {len(reads)} of {len(cfg.reads)} reads were usable"
            continue
        inside = sorted(region.row_indexes, key=pos.get)
        lo, hi = pos[inside[0]], pos[inside[-1]]
        rows_ext = raw.rows[max(0, lo - 2): hi + 3]          # anchors just outside the crop stop false inserts
        consensus = build_consensus(rows_ext, reads, ncols)
        if consensus.unaligned_ratio > 0.5:
            att.outcome, att.detail = "no_consensus", "the reads' labels do not line up with the document's rows"
            continue
        seen_somewhere |= {i for i, n in consensus.seen.items() if n}
        patches += propose_patches(rows_ext, consensus, failing_cells, window_rows)
    # overlapping crops can disagree about a row: a row any crop saw is never deleted
    patches = [p for p in patches if not (p.kind == "delete" and p.row_index in seen_somewhere)]

    att.proposed = [p.describe(label) for p in patches]
    if not patches:
        if att.outcome not in ("reads_failed", "no_consensus"):
            att.outcome, att.detail = "no_patch", "the reads agree with the document in every untrusted cell"
        return raw, verdict, att, calls
    found = _best_subset(raw, verdict, patches, cfg)
    if not found:
        att.outcome, att.detail = "rejected", "no combination of the proposed patches makes the verifier strictly happier"
        return raw, verdict, att, calls
    new_raw, new_verdict, chosen = found
    att.outcome = "accepted"
    att.applied = [p.describe(label) for p in chosen]
    return new_raw, new_verdict, att, calls


# ----------------------------------------------------------------------------- entry point
def repair(raw: RawDocumentTree, page: PdfPage, reader: Reader,
           config: Optional[RepairConfig] = None) -> RepairResult:
    cfg = config or RepairConfig()
    verdict = initial = verify_document(raw, cfg.verifier)
    if verdict.trusted:
        return RepairResult("already_trusted", raw, verdict, initial, [], 0)
    ncols = max([len(raw.metadata.periods)] + [len(r.values) for r in raw.rows])
    attempts: List[Attempt] = []
    calls = 0

    # Safety never comes from this check (every patch is confirmed by the verifier, and a layout the crops cannot
    # handle declines itself); it only avoids spending VLM calls on a page where essentially everything fails.
    if verdict.cell_status and _failed(verdict) / len(verdict.cell_status) > cfg.max_broken_fraction:
        attempts.append(Attempt("page", [], [], "too_broken",
                                f"{_failed(verdict)} of {len(verdict.cell_status)} cells fail: re-extract the page"))
        return RepairResult("needs_review", raw, verdict, initial, attempts, 0)

    for _ in range(cfg.max_rounds):
        windows = _merge_windows(raw, verdict.windows)[: cfg.max_windows]
        progressed = False
        for window in windows:
            try:
                raw, verdict, att, used = _repair_window(raw, verdict, window, page, reader, cfg, ncols)
            except Exception as exc:            # fail closed: leave the document as it was, say what happened
                label = {r.row_index: r.account_raw for r in raw.rows}
                att, used = Attempt(window.kind, [label.get(i, str(i)) for i in window.rows], window.cols, "error",
                                    f"{type(exc).__name__}: {str(exc)[:120]}"), 0
            attempts.append(att)
            calls += used
            progressed |= att.outcome == "accepted"
        if verdict.trusted or not progressed:
            break
    status = "needs_review" if not verdict.trusted else ("repaired" if any(a.applied for a in attempts) else "already_trusted")
    return RepairResult(status, raw, verdict, initial, attempts, calls)
