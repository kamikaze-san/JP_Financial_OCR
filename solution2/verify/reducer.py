"""
Hierarchy-driven deterministic verification of a Stage-1 table, with a verdict for every cell.

Unlike solution1's AccountingValidator, no equation is tied to a particular account name. The
equations are discovered from the table itself by reducing it bottom-up with a stack:

  * a value-less header row opens a section; a total row (label ends in 計) closes the innermost open
    section whose rows actually sum to it (children-then-total layout). Sections are bracket-matched,
    not indent-matched, because real VLM output indents subtotals inconsistently
  * a valued parent followed by deeper valued rows is the sum of those rows (total-then-children
    layout, e.g. the Yamato T-account); this is the one place indentation is still used
  * a 純額 row directly after a contra (累計額) run is gross + contra (signed), so netting is just
    another sum
  * a grand total over closed sections (負債純資産合計) is the sum of those section totals, and the
    balance tie (assets = liabilities + net assets) is checked when both sides are present

The account *label* drives what a row is; the VLM's role tags are only a hint, because they are noisy
(real output tags subtotals as 'netted_item').

Once an equation is evaluated its components collapse into the total, so higher levels check the
*reported* totals and a single bad cell fails only the equations it touches (which localises it).

Verdicts per cell: exact | rounding | unverified | failed | structure_ambiguous. A document is
`trusted` only if nothing failed, nothing was unparseable/unresolved, and coverage is high enough.
Nothing is ever silently defaulted: a check that cannot run leaves cells `unverified` and lowers coverage.
"""
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from solution1.models import RawDocumentTree

from .parsing import BAD, BLANK, NIL, NUMBER, Cell, clean_label, parse_cell, parse_date, parse_unit

EXACT, ROUNDING = "exact", "rounding"
UNVERIFIED, FAILED, STRUCTURE_AMBIGUOUS = "unverified", "failed", "structure_ambiguous"
VOUCHED = (EXACT, ROUNDING)
TOTALISH = {"subtotal", "grand_total"}
ITEMISH = {"item", "contra_item", "netted_item"}
_NETTED_CONTRA = ("減価償却累計額", "減損損失累計額")
HEADER_ROLES = {"section_header", "group_header", "category_header"}
DELTA_WORDS = ("増減", "増加", "減少", "変動", "前期比", "前年比", "差異")


@dataclass
class VerifierConfig:
    min_coverage: float = 0.95     # share of numeric cells that must sit in a balanced equation
    require_dates: bool = True     # unresolved period dates make the document untrusted


@dataclass
class Finding:
    kind: str
    severity: str                  # "error" makes the document untrusted; "warning" is informational
    message: str
    key: Tuple = ()                # stable, comparable identity used by the test harness


@dataclass
class EquationReport:
    kind: str                      # sum | net | tie
    total: Tuple[int, str]         # (row_index, label)
    components: List[Tuple[int, str]]
    structure_ok: bool
    results: Dict[int, Tuple[Optional[int], str]]   # column -> (diff = sum(components) - total, status)


@dataclass
class Window:
    """A region of the table that needs another look (what the retry loop re-reads)."""
    kind: str                      # failed | unclosed | unparseable
    rows: List[int]                # RawRow.row_index values, in document order
    cols: List[int]                # columns involved


@dataclass
class DocumentVerdict:
    trusted: bool
    coverage: Optional[float]
    cell_status: Dict[Tuple[int, int], str]
    findings: List[Finding]
    equations: List[EquationReport]
    equations_evaluated: int
    unit_multiplier: Optional[int]
    period_dates: List[Optional[str]]
    convention: str
    windows: List[Window] = field(default_factory=list)

    @property
    def errors(self) -> List[Finding]:
        return [f for f in self.findings if f.severity == "error"]


# ----------------------------------------------------------------------------- internal model
@dataclass
class _Row:
    pos: int
    row_index: int
    label: str
    indent: int
    role: str
    cells: List[Cell]
    has_value: bool          # any number / nil / unparseable cell (header rows have none)
    total_strong: bool       # label says total
    total_weak: bool         # only the VLM role says total
    net_like: bool
    contra_like: bool


@dataclass
class _Eq:
    id: int
    kind: str
    total: int               # row position
    comps: List[int]
    structure_ok: bool = True


def _make_rows(raw: RawDocumentTree, ncols: int) -> Tuple[List[_Row], List[str]]:
    rows, repaired = [], []
    for pos, r in enumerate(raw.rows):
        label, was_repaired = clean_label(r.account_raw)
        if was_repaired:
            repaired.append(r.account_raw)
        cells = [parse_cell(r.values[c]) if c < len(r.values) else Cell(BLANK, None, "") for c in range(ncols)]
        strong = label.endswith("計")
        rows.append(_Row(
            pos=pos, row_index=r.row_index, label=label, indent=r.indent_level, role=r.role, cells=cells,
            has_value=any(c.kind in (NUMBER, NIL, BAD) for c in cells),
            total_strong=strong,
            total_weak=(r.role in TOTALISH) and not strong,
            net_like="純額" in label,                     # the label is reliable, the role is not (real VLMs
                                                           # tag subtotals as 'netted_item')
            contra_like=(r.role == "contra_item" or "累計額" in label) and not strong,
        ))
    return rows, repaired


# ----------------------------------------------------------------------------- structure
def _build_equations(rows: List[_Row], ncols: int) -> Tuple[List[_Eq], List[Tuple[int, List[int]]]]:
    """Discover the equations of a table by reducing it bottom-up.

    Sections are bracket-matched, not indent-matched: a value-less header row opens a section, a
    total row closes the innermost open section whose rows actually sum to it. Indentation proved
    unreliable in real VLM output (subtotals indented like their items), so it is only used for the
    total-then-children layout, where a valued parent is followed by deeper valued rows."""
    stack: List[Tuple[str, int, int]] = []     # (node | hdr | frame, row position, indent)
    eqs: List[_Eq] = []
    closed = set()                              # rows that are the total of an equation (not plain line items)

    def val(pos: int, c: int) -> int:
        return rows[pos].cells[c].value or 0

    def diffs(total: int, comps: List[int]) -> List[Tuple[int, int]]:
        out = []
        for c in range(ncols):
            if all(rows[q].cells[c].kind == BLANK for q in [total] + comps):
                continue
            out.append((sum(val(q, c) for q in comps) - val(total, c), max(1, sum(1 for q in comps if val(q, c)))))
        return out

    def loosely_balanced(total: int, comps: List[int]) -> bool:
        d = diffs(total, comps)
        return bool(d) and all(abs(x) <= n for x, n in d)      # within the widest plausible rounding slack

    def cost(total: int, comps: List[int]) -> int:
        return sum(abs(x) for x, _ in diffs(total, comps))

    def add_eq(kind: str, total: int, comps: List[int], skipped: int = 0) -> None:
        # not structure_ok when the window swallowed a section header that never got its own total, or
        # holds an accumulated-depreciation row that should have been netted into a 純額 row (arithmetic
        # cannot see a missing 純額 row: gross + contra = net)
        if kind == "sum":
            skipped += sum(1 for q in comps if any(t in rows[q].label for t in _NETTED_CONTRA))
        eqs.append(_Eq(len(eqs), kind, total, list(comps), structure_ok=(skipped == 0)))

    def close_frames(indent: int) -> None:
        while True:
            frames = [i for i, it in enumerate(stack) if it[0] == "frame"]
            if not frames or stack[frames[-1]][2] < indent:
                return
            f = frames[-1]
            total, ind = stack[f][1], stack[f][2]
            add_eq("sum", total, [it[1] for it in stack[f + 1:] if it[0] == "node"])
            del stack[f:]
            closed.add(total)
            stack.append(("node", total, ind))

    def net_window() -> Optional[Tuple[int, List[int]]]:
        i, contras = len(stack) - 1, []
        while i >= 0 and stack[i][0] == "node" and rows[stack[i][1]].contra_like:
            contras.append(i)
            i -= 1
        if not contras or i < 0 or stack[i][0] != "node":
            return None
        return i, contras[::-1]

    def frame_candidate(p: int) -> bool:
        """Total-then-children layout: a valued parent followed by deeper valued rows whose direct children (the
        shallowest deeper rows) sum to it. A single child is enough when it carries the parent's amount (a sub-group
        line with one account: その他利益剰余金 -> 繰越利益剰余金), otherwise counting both would double count.
        A role-only 'subtotal' tag is not enough on its own (a gross line item tagged subtotal is followed by its contra
        and net rows, which do not sum to it); the one exception is a parent whose number vanished into a dash, which
        must still fail."""
        r = rows[p]
        if not r.has_value:
            return False
        deeper, j = [], p + 1
        while j < len(rows) and rows[j].indent > r.indent:
            if rows[j].has_value:
                deeper.append(j)
            j += 1
        if not deeper:
            return False
        top_level = min(rows[q].indent for q in deeper)
        if loosely_balanced(p, [q for q in deeper if rows[q].indent == top_level]):
            return True
        return len(deeper) >= 2 and r.role in TOTALISH and not any(c.kind == NUMBER for c in r.cells)

    def skipped_headers(tail) -> int:
        """Headers a window swallowed without their own total. That is suspicious (a dropped total row) only
        when the window also holds closed section totals, i.e. the swallowed header had siblings with totals.
        A sub-group header inside a list of plain line items (その他利益剰余金 under 利益剰余金) is normal."""
        headers = sum(1 for it in tail if it[0] == "hdr")
        return headers if any(it[0] == "node" and it[1] in closed for it in tail) else 0

    def inline_subtotal(p: int) -> Optional[Tuple[int, List[int]]]:
        """A plain row whose value equals the sum of the run of rows directly above it is a subtotal
        (IFRS equity: 親会社の所有者に帰属する持分), even without a 計 label. Needs >= 2 rows, a real
        amount, and every column to balance, so a coincidence is very unlikely."""
        r = rows[p]
        if not r.has_value or r.contra_like or not any(abs(val(p, c)) >= 100 for c in range(ncols)):
            return None
        run = []
        for i in range(len(stack) - 1, -1, -1):
            if stack[i][0] != "node":
                break
            run.append(i)
        run.reverse()
        for size in range(len(run), 1, -1):                    # the largest balancing group first
            comps = [stack[i][1] for i in run[-size:]]
            if loosely_balanced(p, comps):
                return run[-size], comps
        return None

    def total_window(p: int, strong: bool) -> Optional[Tuple[int, List[int], int]]:
        """Components of total row p: (stack index to cut from, component rows, skipped headers)."""
        floor = max((i for i, it in enumerate(stack) if it[0] == "frame"), default=-1)
        cuts = []
        for i in range(len(stack) - 1, floor, -1):             # innermost open section first
            if stack[i][0] == "hdr":
                tail = stack[i + 1:]
                cuts.append((i, [it[1] for it in tail if it[0] == "node"], skipped_headers(tail)))
        floor_size = 1 if strong else 2          # a role-only total must total at least two rows
        for i, nodes, skipped in cuts:
            if len(nodes) >= floor_size and loosely_balanced(p, nodes):
                return i, nodes, skipped
        node_is = [i for i in range(floor + 1, len(stack)) if stack[i][0] == "node"]
        if cuts:
            # open headers delimit the sections, so a loose line item must not be dropped to make a sum
            # balance (that would hide a duplicated or omitted row). Only a run of already-closed section
            # totals may be re-grouped: a grand total over sections whose own header was never closed.
            run = []
            for i in range(len(stack) - 1, floor, -1):
                if stack[i][0] != "node" or stack[i][1] not in closed:
                    break
                run.append(i)
            node_is = run[::-1]
        best = None
        if node_is:        # no section window balances: try a balancing suffix of nodes
            n = min(range(1, len(node_is) + 1),
                    key=lambda k: (cost(p, [stack[i][1] for i in node_is[-k:]]), k))
            comps = [stack[i][1] for i in node_is[-n:]]
            best = (node_is[-n], comps, skipped_headers(stack[node_is[-n]:]))
            if len(comps) >= floor_size and loosely_balanced(p, comps):
                return best
        if not strong:
            return None
        for i, nodes, skipped in cuts:                         # report the failure on the innermost section
            if nodes:
                return i, nodes, skipped
        return best

    # a section name that some later total is named after (流動負債 <- 流動負債合計)
    stems = {r.label[:-2] if r.label.endswith("合計") else r.label[:-1] for r in rows if r.total_strong}

    def heading_echo(p: int) -> bool:
        """A value-less heading immediately followed by a valued row with the same label is the VLM echoing the
        line item as a heading, unless a later total is named after it (Calbee's real 投資その他の資産 heading,
        item and 投資その他の資産合計). Treating an echo as a section would steal its total's pairing."""
        nxt = rows[p + 1] if p + 1 < len(rows) else None
        return bool(nxt and nxt.has_value and nxt.label == rows[p].label and rows[p].label not in stems)

    for p, r in enumerate(rows):
        close_frames(r.indent)
        if not r.has_value:
            if r.role in ITEMISH or r.role in TOTALISH or heading_echo(p):
                continue                                       # an empty line item / an echoed heading: nothing to check
            stack.append(("hdr", p, r.indent))
            continue
        if r.net_like:
            win = net_window()
            if win:
                gross_i, contra_is = win
                add_eq("net", p, [stack[gross_i][1]] + [stack[i][1] for i in contra_is])
                del stack[gross_i:]
                closed.add(p)
            stack.append(("node", p, r.indent))
            continue
        if r.total_strong:
            # only a row whose label says 計 closes a section; a role-only subtotal (no 計 in its label) is a
            # subtotal of the rows just above it or a parent of the rows below, handled by the generic path
            win = total_window(p, True)
            if win:
                cut, comps, skipped = win
                add_eq("sum", p, comps, skipped)
                del stack[cut:]
                closed.add(p)
                stack.append(("node", p, r.indent))
                continue
            if frame_candidate(p):
                stack.append(("frame", p, r.indent))
                continue
            if r.total_strong:
                add_eq("sum", p, [])                           # a printed total with nothing under it
            stack.append(("node", p, r.indent))
            continue
        if frame_candidate(p):
            stack.append(("frame", p, r.indent))
            continue
        inline = inline_subtotal(p)
        if inline:
            cut, comps = inline
            add_eq("sum", p, comps)
            del stack[cut:]
            closed.add(p)
        stack.append(("node", p, r.indent))
    close_frames(-1)

    hdr_is = [i for i, it in enumerate(stack) if it[0] == "hdr"]

    # the balance identity: assets = liabilities + net assets (only when every section closed cleanly)
    top = [it[1] for it in stack if it[0] == "node"]
    first_node = next((i for i, it in enumerate(stack) if it[0] == "node"), len(stack))
    # page / statement titles above the first section are not open sections (a header that names a side of the balance sheet is)
    titles_only = all(i < first_node and not any(k in rows[stack[i][1]].label for k in ("資産", "負債", "資本", "純資産"))
                      for i in hdr_is)
    if titles_only and len(top) >= 2:
        last = rows[top[-1]]
        if (last.total_strong and "負債" in last.label and ("純資産" in last.label or "資本" in last.label)
                and "資産" in rows[top[0]].label and "負債" not in rows[top[0]].label):
            add_eq("tie", top[-1], [top[0]])
    if not hdr_is and len(top) == 3 and not any(e.kind == "tie" for e in eqs):
        a, l, n = (rows[p].label for p in top)                 # no combined 負債純資産合計 row printed
        if ("負債" not in a and "純" not in a and "負債" in l and "純資産" not in l
                and "純資産" in n and "負債" not in n):
            add_eq("tie", top[0], [top[1], top[2]])

    # sections that were opened but never closed by a total (e.g. the total row was dropped)
    unclosed: List[Tuple[int, List[int]]] = []
    tied = titles_only and any(e.kind == "tie" for e in eqs)
    for n, i in enumerate(hdr_is):
        end = hdr_is[n + 1] if n + 1 < len(hdr_is) else len(stack)
        left = [it[1] for it in stack[i + 1:end] if it[0] == "node"]
        if left and not (tied and i < first_node):             # a statement title whose sections the balance tie combines
            unclosed.append((stack[i][1], left))
    return eqs, unclosed


# ----------------------------------------------------------------------------- evaluation
def _tolerance_ok(diff: int, n: int, mode: str) -> bool:
    if mode == "exact":
        return diff == 0
    if mode == "truncation":                       # components truncated: sum <= total (a single +1 unit is allowed: it is
        return -(n - 1) <= diff <= 1 if n >= 1 else diff == 0     # indistinguishable from rounding noise, e.g. IFRS in thousands)
    if mode == "either":                           # not enough evidence to choose: allow what either could produce
        return -(n - 1) <= diff <= math.ceil(n / 2)
    return abs(diff) <= math.ceil(n / 2)           # rounding to nearest


def _combine(statuses: List[str]) -> str:
    for s in (FAILED, UNVERIFIED, STRUCTURE_AMBIGUOUS, ROUNDING):
        if s in statuses:
            return s
    return EXACT


def verify_document(raw: RawDocumentTree, config: Optional[VerifierConfig] = None) -> DocumentVerdict:
    cfg = config or VerifierConfig()
    findings: List[Finding] = []
    periods = raw.metadata.periods
    ncols = max([len(periods)] + [len(r.values) for r in raw.rows])
    rows, repaired = _make_rows(raw, ncols)

    # ---- metadata, never defaulted
    unit = parse_unit(raw.metadata.unit_raw)
    if unit is None:
        findings.append(Finding("unit_unresolved", "error", f"cannot read unit from {raw.metadata.unit_raw!r}",
                                ("unit_unresolved",)))
    dates = [parse_date(p.raw_date) for p in periods]
    # a column titled 増減 (change) legitimately has no date
    delta_cols = [c for c, p in enumerate(periods) if dates[c] is None and any(w in p.raw_label for w in DELTA_WORDS)]
    date_cols = [c for c in range(len(periods)) if c not in delta_cols]
    # a trailing column the VLM did not list in `periods` is still recognisable as a change column when
    # current - prior = value holds on nearly every row (checked here once the cells are parsed)
    inferred_delta = False
    if not delta_cols and len(date_cols) >= 2 and ncols == len(periods) + 1:
        a_, b_, x_ = date_cols[-2], date_cols[-1], ncols - 1
        trios = [(r.cells[a_], r.cells[b_], r.cells[x_]) for r in rows
                 if all(t.kind in (NUMBER, NIL) for t in (r.cells[a_], r.cells[b_], r.cells[x_]))]
        good = sum(1 for p0, p1, d0 in trios if abs((p1.value or 0) - (p0.value or 0) - (d0.value or 0)) <= 1)
        if len(trios) >= 3 and good / len(trios) >= 0.8:
            delta_cols, inferred_delta = [x_], True
            findings.append(Finding("inferred_change_column", "warning",
                                    "a trailing column that is not listed in `periods` behaves as current - prior "
                                    "on nearly every row; treated as a change column", ()))
    if not periods:
        findings.append(Finding("no_periods", "error", "no period columns declared", ("no_periods",)))
    for c, (p, d) in enumerate(zip(periods, dates)):
        if d is None and cfg.require_dates and c not in delta_cols:
            findings.append(Finding("date_unresolved", "error", f"cannot read date {p.raw_date!r}",
                                    ("date_unresolved", c)))
    known = [d for d in dates if d]
    if len(set(known)) != len(known):
        findings.append(Finding("period_collision", "error", "two columns resolve to the same date",
                                ("period_collision",)))
    for r in rows:
        if r.has_value and periods and len(raw.rows[r.pos].values) != len(periods) and not (
                inferred_delta and len(raw.rows[r.pos].values) == len(periods) + 1):
            findings.append(Finding("column_count_mismatch", "error",
                                    f"{raw.rows[r.pos].account_raw!r} has {len(raw.rows[r.pos].values)} values "
                                    f"for {len(periods)} period(s)", ("column_count_mismatch", r.row_index)))
    if repaired:
        findings.append(Finding("glyph_repaired", "warning",
                                f"{len(repaired)} label(s) contained Simplified-Chinese glyphs", ()))

    # ---- cells
    numeric_cells = [(r.row_index, c) for r in rows for c in range(ncols) if r.cells[c].kind in (NUMBER, NIL, BAD)]
    for r in rows:
        for c in range(ncols):
            cell = r.cells[c]
            if cell.kind == BAD:
                findings.append(Finding("unparseable_cell", "error",
                                        f"{raw.rows[r.pos].account_raw!r} col {c}: {cell.raw!r} ({cell.reason})",
                                        ("unparseable_cell", r.row_index, c)))
    if not numeric_cells:
        findings.append(Finding("no_numeric_cells", "error", "the table contains no numbers", ("no_numeric_cells",)))

    # ---- equations
    eqs, unclosed = _build_equations(rows, ncols)

    def cell(pos: int, c: int) -> Cell:
        return rows[pos].cells[c]

    raw_results: Dict[Tuple[int, int], Optional[Tuple[int, int]]] = {}   # (eq, col) -> (diff, n_eff) | None
    for eq in eqs:
        involved = [eq.total] + eq.comps
        for c in range(ncols):
            kinds = [cell(p, c).kind for p in involved]
            if all(k == BLANK for k in kinds):
                continue
            if BAD in kinds:
                raw_results[(eq.id, c)] = None
                continue
            total_v = cell(eq.total, c).value or 0
            comp_vs = [cell(p, c).value or 0 for p in eq.comps]
            raw_results[(eq.id, c)] = (sum(comp_vs) - total_v, max(1, sum(1 for v in comp_vs if v)))

    if unit == 1:
        mode = "exact"                              # amounts printed in yen are not rounded to a unit
    else:
        # Rounding convention is inferred from the section sums only: net rows (gross + contra = net)
        # round three independent figures, so they can legitimately differ in either direction.
        # A convention needs evidence. Truncation is inferred from >= 3 negative diffs with positives under 25%;
        # round-to-nearest from >= 2 positive diffs making up >= 25% of them; a statement that balances to the
        # unit everywhere is held to exactness. With too little evidence either convention is allowed (a
        # small IFRS statement can show a +2 that proves nothing about the others).
        diffs_ = [res[0] for (key, res) in raw_results.items()
                  if res and eqs[key[0]].kind == "sum" and abs(res[0]) <= res[1] and res[0] != 0]
        n_sums = sum(1 for (key, res) in raw_results.items() if res and eqs[key[0]].kind == "sum")
        positives = sum(1 for d in diffs_ if d > 0)
        negatives = len(diffs_) - positives
        if positives >= 2 and positives / len(diffs_) >= 0.25:
            mode = "rounding"
        elif negatives >= 3 and positives / len(diffs_) < 0.25:     # an isolated positive is an outlier, not a convention
            mode = "truncation"
        elif not diffs_ and n_sums >= 3:
            mode = "exact"
        else:
            mode = "either"

    memberships: Dict[Tuple[int, int], List[Tuple[str, str]]] = {}   # cell -> [(role, status)]
    reports: List[EquationReport] = []
    windows: List[Window] = []
    evaluated = 0
    for eq in eqs:
        results: Dict[int, Tuple[Optional[int], str]] = {}
        for c in range(ncols):
            if (eq.id, c) not in raw_results:
                continue
            res = raw_results[(eq.id, c)]
            involved = [(eq.total, "total")] + [(p, "comp") for p in eq.comps]
            if res is None:
                status = UNVERIFIED
                results[c] = (None, status)
                for p, role in involved:
                    memberships.setdefault((p, c), []).append((role, UNVERIFIED))
                continue
            evaluated += 1
            diff, n = res
            if diff == 0:
                status = EXACT
            elif eq.kind == "net" and unit != 1 and abs(diff) <= n:
                status = ROUNDING                    # three independently rounded figures
            elif eq.kind != "net" and eq.kind != "tie" and _tolerance_ok(diff, n, mode):
                status = ROUNDING
            else:
                status = FAILED
            results[c] = (diff, status)
            if status == FAILED:
                total_row = rows[eq.total]
                findings.append(Finding(
                    "equation_failed", "error",
                    f"{raw.rows[total_row.pos].account_raw!r} col {c}: components sum to "
                    f"{sum(cell(p, c).value or 0 for p in eq.comps):,} but {total_row.cells[c].value or 0:,} is printed "
                    f"(diff {diff:+,})", ("equation_failed", total_row.row_index, c, diff)))
            for p, role in involved:
                if status in VOUCHED and role == "comp" and not eq.structure_ok:
                    memberships.setdefault((p, c), []).append((role, STRUCTURE_AMBIGUOUS))
                else:
                    memberships.setdefault((p, c), []).append((role, status))
        failed_cols = [c for c, (_, st) in results.items() if st == FAILED]
        if failed_cols:
            windows.append(Window("failed", [rows[q].row_index for q in sorted({eq.total, *eq.comps})], failed_cols))
        reports.append(EquationReport(
            eq.kind, (rows[eq.total].row_index, raw.rows[eq.total].account_raw),
            [(rows[p].row_index, raw.rows[p].account_raw) for p in eq.comps], eq.structure_ok, results))
        if not eq.structure_ok:
            findings.append(Finding("structure_ambiguous", "warning",
                                    f"{raw.rows[eq.total].account_raw!r} contains a section that never got its own total, or an "
                                    f"accumulated-depreciation row without its 純額 row (a row may be missing)", ()))

    # a row the VLM tagged as a heading but gave numbers, which is not the total of anything: the numbers
    # probably belong to a line item it skipped (real 2B output does this). Arithmetic cannot see it, since
    # the value still sits in the right sum, so the cells are simply not vouched.
    totals = {eq.total for eq in eqs}
    for r in rows:
        cols = [c for c in range(ncols) if r.cells[c].kind in (NUMBER, NIL)]
        if cols and r.role in HEADER_ROLES and r.pos not in totals:
            findings.append(Finding("heading_with_values", "error",
                                    f"{raw.rows[r.pos].account_raw!r} is tagged as a heading but carries numbers and "
                                    f"totals nothing (they may belong to a line item the VLM skipped)",
                                    ("heading_with_values", r.row_index)))
            windows.append(Window("heading", [r.row_index], cols))
            for c in cols:
                memberships.setdefault((r.pos, c), []).append(("comp", STRUCTURE_AMBIGUOUS))

    for header_pos, left in unclosed:
        findings.append(Finding("unclosed_section", "warning",
                                f"{len(left)} row(s) under {raw.rows[header_pos].account_raw!r} are never combined "
                                f"into a total (a total row may be missing)", ()))
        for p in left:
            for c in range(ncols):
                memberships.setdefault((p, c), []).append(("comp", STRUCTURE_AMBIGUOUS))
        windows.append(Window("unclosed", [rows[q].row_index for q in range(header_pos, max(left) + 1)],
                              list(range(ncols))))
    for r in rows:
        bad_cols = [c for c in range(ncols) if r.cells[c].kind == BAD]
        if bad_cols:
            windows.append(Window("unparseable", [r.row_index], bad_cols))

    # ---- per-cell verdicts
    cell_status: Dict[Tuple[int, int], str] = {}
    for r in rows:
        for c in range(ncols):
            cl = r.cells[c]
            if cl.kind not in (NUMBER, NIL, BAD):
                continue
            if cl.kind == BAD:
                cell_status[(r.row_index, c)] = FAILED
                continue
            ms = memberships.get((r.pos, c), [])
            comp = [s for role, s in ms if role == "comp"]
            tot = [s for role, s in ms if role == "total"]
            if not ms:
                status = UNVERIFIED
            elif FAILED in comp:
                status = FAILED
            elif FAILED in tot and not any(s in VOUCHED for s in comp):
                status = FAILED                      # its own equation fails and no parent corroborates it
            else:
                status = _combine(comp + [s for s in tot if s != FAILED])
            cell_status[(r.row_index, c)] = status

    # cross-column identity: change = current - prior, checked on every row (it also vouches for rows no sum covers)
    if len(delta_cols) == 1 and len(date_cols) >= 2:
        dc, a, b = delta_cols[0], date_cols[-2], date_cols[-1]
        for r in rows:
            trio = [r.cells[c] for c in (a, b, dc)]
            if not all(t.kind in (NUMBER, NIL) for t in trio):
                continue
            d = (trio[1].value or 0) - (trio[0].value or 0) - (trio[2].value or 0)
            verdict_ = EXACT if d == 0 else ROUNDING if abs(d) <= 1 else FAILED
            for c in (a, b, dc):
                key = (r.row_index, c)
                if verdict_ == FAILED:
                    if c == dc or cell_status.get(key) not in VOUCHED:
                        cell_status[key] = FAILED          # a vouched column keeps its verdict: the change cell is blamed
                elif cell_status.get(key) == UNVERIFIED:
                    cell_status[key] = verdict_
            if verdict_ == FAILED:
                findings.append(Finding("delta_mismatch", "error",
                                        f"{raw.rows[r.pos].account_raw!r}: {trio[1].raw} - {trio[0].raw} is not the printed "
                                        f"change {trio[2].raw} (diff {d:+,})", ("delta_mismatch", r.row_index, d)))
                windows.append(Window("failed", [r.row_index], [a, b, dc]))

    coverage = None
    if cell_status:
        coverage = sum(1 for s in cell_status.values() if s in VOUCHED) / len(cell_status)
        if coverage < cfg.min_coverage:
            findings.append(Finding("low_coverage", "error",
                                    f"only {coverage:.0%} of numeric cells are covered by a balanced equation "
                                    f"(need {cfg.min_coverage:.0%})", ("low_coverage", round(coverage, 2))))
    if any(s == ROUNDING for s in cell_status.values()):
        findings.append(Finding("tolerance_used", "warning",
                                f"some equations balance only within rounding slack (convention: {mode})", ()))

    return DocumentVerdict(
        trusted=not any(f.severity == "error" for f in findings),
        coverage=coverage, cell_status=cell_status, findings=findings, equations=reports,
        equations_evaluated=evaluated, unit_multiplier=unit, period_dates=dates, convention=mode,
        windows=windows)
