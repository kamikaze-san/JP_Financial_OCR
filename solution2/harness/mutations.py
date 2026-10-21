"""
Mutation operators: realistic corruptions of a clean Stage-1 (VLM output) document.

Every mutation declares what a good verifier is expected to do with it (`kind`):

  detect        corruption large enough that arithmetic must catch it (>= material_delta)
  detect_small  same class, but below material_delta: reported, not gated (rounding zone)
  gap           arithmetic alone cannot see it (swaps, units, wrong column, label flips,
                coherent plugs). Needs a second signal; tracked so progress is measurable
  no_degrade    surface noise that must not silently reduce what the verifier checks
                (fail-open tests: footnote on a total label, Chinese glyph drift, ...)
  flag          degenerate output (no rows, nothing parseable) that must never be vouched for
  invariant     equivalent re-spelling of the same content; verdict must not change

Mutations are generated lazily and share unmodified rows with the clean document.
"""
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterator, List, Optional, Tuple

from solution1.models import RawDocumentTree, RawRow

DIGITS = "0123456789"
ITEMISH = {"item", "contra_item", "netted_item"}
TOTALISH = {"subtotal", "grand_total"}
NIL_CHARS = {"-", "－", "―", "ー", "—", "–", "−"}

# Digit pairs a VLM plausibly confuses (assumption, used only to tag `realistic`).
CONFUSABLE = {frozenset(p) for p in [("3", "8"), ("1", "7"), ("0", "6"), ("0", "8"), ("5", "6"),
                                      ("5", "8"), ("6", "8"), ("4", "9"), ("2", "7"), ("3", "5"),
                                      ("8", "9"), ("0", "9")]}

# Visually/semantically confusable account vocabulary (asset <-> liability etc.).
CONFUSABLE_LABELS = [
    ("債権", "債務"), ("売掛金", "買掛金"), ("貸付金", "借入金"), ("前払", "前受"),
    ("仮払", "仮受"), ("未収", "未払"), ("繰延税金資産", "繰延税金負債"),
    ("流動資産", "流動負債"), ("固定資産", "固定負債"),
]

# Simplified-Chinese glyph drift actually observed in GLM-OCR output.
SIMPLIFIED = [("流動", "流动"), ("資産", "资产"), ("負債", "负债"), ("剰余", "剩余"),
              ("金額", "金额"), ("債権", "债权"), ("債務", "债务"), ("純額", "纯额"),
              ("累計額", "累计额")]


@dataclass
class MutationConfig:
    material_delta: float = 100.0   # |change| in reported units that arithmetic must catch
    stride: int = 1                 # keep every n-th mutation per class (speed)
    plug_delta: float = 123.0


@dataclass
class Mutation:
    doc: str
    cls: str
    kind: str
    desc: str
    raw: RawDocumentTree
    targets: Tuple[Tuple[int, int], ...] = ()
    delta: Optional[float] = None
    meta: Dict = field(default_factory=dict)


# ----------------------------------------------------------------------------- helpers
def parse_val(s: str) -> Optional[float]:
    """Signed value of a printed number, or None if it is not a plain number."""
    s = unicodedata.normalize("NFKC", s or "").strip()
    if not s:
        return None
    neg = s.startswith(("△", "▲")) or (s.startswith("(") and s.endswith(")"))
    t = s.lstrip("△▲").strip("()").replace(",", "").replace(" ", "")
    if not re.fullmatch(r"\d+(\.\d+)?", t):
        return None
    v = float(t)
    return -v if neg else v


def is_nil(s: str) -> bool:
    return (s or "").strip() in NIL_CHARS


def fmt_like(v: float) -> str:
    r = round(abs(v))
    return ("△" if v < 0 else "") + f"{r:,}"


def regroup(digits: str, with_commas: bool) -> str:
    return f"{int(digits):,}" if with_commas else digits


def numeric_cells(raw: RawDocumentTree, include_zero: bool = False) -> Iterator[Tuple[int, int, str, float]]:
    for ri, row in enumerate(raw.rows):
        for ci, s in enumerate(row.values):
            v = parse_val(s)
            if v is not None and (v != 0 or include_zero):
                yield ri, ci, s, v


def is_total_row(row: RawRow) -> bool:
    return row.role in TOTALISH or row.account_raw.endswith("合計")


def _set_values(raw, ri, values):
    rows = list(raw.rows)
    rows[ri] = rows[ri].model_copy(update={"values": values})
    return raw.model_copy(update={"rows": rows})


def _set_cell(raw, ri, ci, s):
    vals = list(raw.rows[ri].values)
    vals[ci] = s
    return _set_values(raw, ri, vals)


def _set_label(raw, ri, label):
    rows = list(raw.rows)
    rows[ri] = rows[ri].model_copy(update={"account_raw": label})
    return raw.model_copy(update={"rows": rows})


def _set_meta(raw, **upd):
    return raw.model_copy(update={"metadata": raw.metadata.model_copy(update=upd)})


def _with_rows(raw, rows):
    return raw.model_copy(update={"rows": rows})


def _ncols(raw) -> int:
    return len(raw.metadata.periods)


def _cell_desc(raw, ri, ci, old, new):
    return f"{raw.rows[ri].account_raw} [col {ci}]: {old!r} -> {new!r}"


def _detect_kind(delta: float, cfg: MutationConfig) -> str:
    return "detect" if abs(delta) >= cfg.material_delta else "detect_small"


# ----------------------------------------------------------------------------- numeric corruptions
def op_digit_sub(doc, raw, cfg):
    for ri, ci, s, v in numeric_cells(raw, include_zero=True):  # a printed 0 misread as 8 is realistic
        idxs = [k for k, ch in enumerate(s) if ch in DIGITS]
        for n, k in enumerate(idxs):
            for nd in DIGITS:
                if nd == s[k] or (n == 0 and nd == "0" and len(idxs) > 1):
                    continue
                ns = s[:k] + nd + s[k + 1:]
                delta = parse_val(ns) - v
                yield Mutation(doc, "digit_sub", _detect_kind(delta, cfg), _cell_desc(raw, ri, ci, s, ns),
                               _set_cell(raw, ri, ci, ns), ((raw.rows[ri].row_index, ci),), delta,
                               {"pos_from_right": len(idxs) - 1 - n, "abs_delta": abs(delta),
                                "realistic": frozenset((s[k], nd)) in CONFUSABLE,
                                "row_role": raw.rows[ri].role})


def op_digit_drop(doc, raw, cfg):
    for ri, ci, s, v in numeric_cells(raw):
        digits = "".join(ch for ch in s if ch in DIGITS)
        if len(digits) < 2:
            continue
        for n in range(len(digits)):
            nd = digits[:n] + digits[n + 1:]
            ns = ("△" if s.startswith("△") else "") + regroup(nd, "," in s)
            delta = parse_val(ns) - v
            yield Mutation(doc, "digit_drop", _detect_kind(delta, cfg), _cell_desc(raw, ri, ci, s, ns),
                           _set_cell(raw, ri, ci, ns), ((raw.rows[ri].row_index, ci),), delta,
                           {"abs_delta": abs(delta)})


def op_digit_transpose(doc, raw, cfg):
    for ri, ci, s, v in numeric_cells(raw):
        idxs = [k for k, ch in enumerate(s) if ch in DIGITS]
        for a, b in zip(idxs, idxs[1:]):
            if s[a] == s[b] or (a == idxs[0] and s[b] == "0"):
                continue
            chars = list(s)
            chars[a], chars[b] = chars[b], chars[a]
            ns = "".join(chars)
            delta = parse_val(ns) - v
            yield Mutation(doc, "digit_transpose", _detect_kind(delta, cfg), _cell_desc(raw, ri, ci, s, ns),
                           _set_cell(raw, ri, ci, ns), ((raw.rows[ri].row_index, ci),), delta,
                           {"abs_delta": abs(delta)})


def op_comma_decimal(doc, raw, cfg):
    for ri, ci, s, v in numeric_cells(raw):
        if "," not in s:
            continue
        for which, pos in (("last", s.rfind(",")), ("first", s.find(","))):
            if which == "first" and s.count(",") < 2:
                continue
            ns = s[:pos] + "." + s[pos + 1:]
            delta = (parse_val(ns) or 0.0) - v
            yield Mutation(doc, "comma_decimal", _detect_kind(delta, cfg), _cell_desc(raw, ri, ci, s, ns),
                           _set_cell(raw, ri, ci, ns), ((raw.rows[ri].row_index, ci),), delta,
                           {"abs_delta": abs(delta)})


def op_sign_drop(doc, raw, cfg):
    for ri, ci, s, v in numeric_cells(raw):
        if v < 0 and s.startswith(("△", "▲")):
            ns = s[1:]
            delta = parse_val(ns) - v
            yield Mutation(doc, "sign_drop", _detect_kind(delta, cfg), _cell_desc(raw, ri, ci, s, ns),
                           _set_cell(raw, ri, ci, ns), ((raw.rows[ri].row_index, ci),), delta,
                           {"abs_delta": abs(delta), "row_role": raw.rows[ri].role})


def op_sign_add(doc, raw, cfg):
    for ri, ci, s, v in numeric_cells(raw):
        if v > 0 and raw.rows[ri].role in (ITEMISH | TOTALISH):
            ns = "△" + s
            delta = parse_val(ns) - v
            yield Mutation(doc, "sign_add", _detect_kind(delta, cfg), _cell_desc(raw, ri, ci, s, ns),
                           _set_cell(raw, ri, ci, ns), ((raw.rows[ri].row_index, ci),), delta,
                           {"abs_delta": abs(delta)})


def op_nil_to_number(doc, raw, cfg):
    for ri, row in enumerate(raw.rows):
        if row.role not in ITEMISH:
            continue
        for ci, s in enumerate(row.values):
            if not is_nil(s):
                continue
            for ns in ("7", "1,234", "123,456"):
                delta = parse_val(ns)
                yield Mutation(doc, "nil_to_number", _detect_kind(delta, cfg), _cell_desc(raw, ri, ci, s, ns),
                               _set_cell(raw, ri, ci, ns), ((row.row_index, ci),), delta,
                               {"abs_delta": abs(delta)})


def op_number_to_nil(doc, raw, cfg):
    for ri, ci, s, v in numeric_cells(raw):
        nil = "－"
        yield Mutation(doc, "number_to_nil", _detect_kind(v, cfg), _cell_desc(raw, ri, ci, s, nil),
                       _set_cell(raw, ri, ci, nil), ((raw.rows[ri].row_index, ci),), -v,
                       {"abs_delta": abs(v), "row_role": raw.rows[ri].role})


def op_value_unparseable(doc, raw, cfg):
    """Strings a strict parser must reject (v1 silently turns them into 0)."""
    for ri, ci, s, v in numeric_cells(raw):
        variants = []
        if "0" in s:
            variants.append(("letter_O", s.replace("0", "O", 1)))
        variants.append(("footnote", s + "※1"))
        for tag, ns in variants:
            yield Mutation(doc, "value_unparseable", "detect", _cell_desc(raw, ri, ci, s, ns),
                           _set_cell(raw, ri, ci, ns), ((raw.rows[ri].row_index, ci),), None,
                           {"variant": tag, "abs_delta": abs(v)})


# ----------------------------------------------------------------------------- structural corruptions
def op_row_omit(doc, raw, cfg):
    for ri, row in enumerate(raw.rows):
        if row.role not in ITEMISH:
            continue
        mags = [abs(parse_val(s)) for s in row.values if parse_val(s)]
        if not mags:
            continue
        rows = raw.rows[:ri] + raw.rows[ri + 1:]
        yield Mutation(doc, "row_omit", "detect" if max(mags) >= cfg.material_delta else "detect_small",
                       f"drop row {row.account_raw!r} {row.values}", _with_rows(raw, rows), (), None,
                       {"abs_delta": max(mags)})


def op_subtotal_omit(doc, raw, cfg):
    for ri, row in enumerate(raw.rows):
        if row.role in TOTALISH and any(parse_val(s) for s in row.values):
            rows = raw.rows[:ri] + raw.rows[ri + 1:]
            yield Mutation(doc, "subtotal_omit", "no_degrade", f"drop total row {row.account_raw!r}",
                           _with_rows(raw, rows), (), None, {})


def op_row_dup(doc, raw, cfg):
    new_index = max(r.row_index for r in raw.rows) + 1
    for ri, row in enumerate(raw.rows):
        if row.role not in ITEMISH:
            continue
        mags = [abs(parse_val(s)) for s in row.values if parse_val(s)]
        if not mags:
            continue
        rows = list(raw.rows)
        rows.insert(ri + 1, row.model_copy(update={"row_index": new_index}))
        yield Mutation(doc, "row_dup", "detect" if max(mags) >= cfg.material_delta else "detect_small",
                       f"duplicate row {row.account_raw!r}", _with_rows(raw, rows),
                       tuple((new_index, ci) for ci, s in enumerate(row.values) if parse_val(s)), None,
                       {"abs_delta": max(mags)})


def op_heading_value_shift(doc, raw, cfg):
    """Seen in real Qwen3-VL-2B output: the first line item of a section is lost and its numbers land on
    the heading row above it. The sums still balance, so only the structure gives it away."""
    for ri in range(len(raw.rows) - 1):
        head, item = raw.rows[ri], raw.rows[ri + 1]
        if head.role not in ("section_header", "group_header", "category_header") or any(head.values):
            continue
        if item.role != "item" or not any(parse_val(s) for s in item.values):
            continue
        rows = list(raw.rows)
        rows[ri] = head.model_copy(update={"values": list(item.values)})
        del rows[ri + 1]
        yield Mutation(doc, "heading_value_shift", "detect", f"{item.account_raw!r}'s numbers moved onto the heading "
                       f"{head.account_raw!r}", _with_rows(raw, rows),
                       tuple((head.row_index, ci) for ci in range(len(item.values))), None,
                       {"abs_delta": max(abs(parse_val(s) or 0) for s in item.values)})


# ----------------------------------------------------------------------------- arithmetic-invisible (gaps)
def op_row_swap_values(doc, raw, cfg):
    for ri in range(len(raw.rows) - 1):
        a, b = raw.rows[ri], raw.rows[ri + 1]
        if a.role not in ITEMISH or b.role not in ITEMISH:
            continue
        if not any(parse_val(s) for s in a.values) or not any(parse_val(s) for s in b.values):
            continue
        if a.values == b.values:
            continue
        rows = list(raw.rows)
        rows[ri] = a.model_copy(update={"values": list(b.values)})
        rows[ri + 1] = b.model_copy(update={"values": list(a.values)})
        targets = tuple((r.row_index, ci) for r in (a, b) for ci in range(len(r.values)))
        yield Mutation(doc, "row_swap_values", "gap", f"swap values of {a.account_raw!r} and {b.account_raw!r}",
                       _with_rows(raw, rows), targets, None, {})


def op_column_swap(doc, raw, cfg):
    if _ncols(raw) < 2:
        return
    rows = [r.model_copy(update={"values": list(reversed(r.values))}) if len(r.values) == _ncols(raw) else r
            for r in raw.rows]
    yield Mutation(doc, "column_swap", "gap", "reverse the value order of every row", _with_rows(raw, rows),
                   tuple((r.row_index, ci) for r in raw.rows for ci in range(len(r.values))), None, {})


def op_period_header_swap(doc, raw, cfg):
    if _ncols(raw) < 2:
        return
    ps = list(raw.metadata.periods)
    swapped = [ps[0].model_copy(update={"raw_date": ps[1].raw_date}),
               ps[1].model_copy(update={"raw_date": ps[0].raw_date})] + ps[2:]
    yield Mutation(doc, "period_header_swap", "gap", "swap the two period dates", _set_meta(raw, periods=swapped),
                   (), None, {})


def op_unit_change(doc, raw, cfg):
    m = re.search(r"(十億円|百万円|千円|円)", unicodedata.normalize("NFKC", raw.metadata.unit_raw))
    current = m.group(1) if m else ""
    for unit in ("円", "千円", "百万円", "万円", "億円"):
        if unit != current:
            yield Mutation(doc, "unit_change", "gap", f"unit {current!r} -> {unit!r}",
                           _set_meta(raw, unit_raw=f"（単位：{unit}）"), (), None, {})


def op_label_confusable_flip(doc, raw, cfg):
    for ri, row in enumerate(raw.rows):
        for a, b in CONFUSABLE_LABELS:
            for x, y in ((a, b), (b, a)):
                if x in row.account_raw:
                    new = row.account_raw.replace(x, y, 1)
                    yield Mutation(doc, "label_confusable_flip", "gap", f"{row.account_raw!r} -> {new!r}",
                                   _set_label(raw, ri, new),
                                   tuple((row.row_index, ci) for ci in range(len(row.values))), None,
                                   {"touches_total_label": is_total_row(row)})


def _parent_chain(rows, i):
    """Rows that sum row i, assuming children-then-total layout. Empty if layout is unsuitable."""
    chain, j, k = [], i, rows[i].indent_level
    while k > 0:
        target, found = k - 1, None
        for t in range(j + 1, len(rows)):
            r = rows[t]
            if r.indent_level == target and (
                    (r.role in TOTALISH and r.account_raw.endswith("計")) or
                    (r.role == "netted_item" and "純額" in r.account_raw)):
                found = t
                break
            if r.indent_level < target:
                break
        if found is None:
            break
        chain.append(found)
        j, k = found, target
    return chain


def op_plug_subtotals(doc, raw, cfg):
    """Coherence hallucination: an item is misread AND every total above it is 'fixed' to match.
    Section sums balance perfectly; only a cross-side tie (assets = liabilities + net assets)
    or an independent second reader can still see it."""
    rows = raw.rows
    for ri, row in enumerate(rows):
        if row.role not in ITEMISH:
            continue
        if row.role == "item" and ri + 1 < len(rows) and rows[ri + 1].role in {"contra_item", "netted_item"}:
            continue  # gross row feeding a net row: the plug would need to touch the net row too
        chain = _parent_chain(rows, ri)
        if not chain:
            continue
        for ci, s in enumerate(row.values):
            v = parse_val(s)
            chain_ok = all(ci < len(rows[t].values) and parse_val(rows[t].values[ci]) is not None
                           for t in chain)
            if not v or not chain_ok:
                continue
            new_rows = list(rows)
            for t in [ri] + chain:
                cur = parse_val(new_rows[t].values[ci])
                vals = list(new_rows[t].values)
                vals[ci] = fmt_like(cur + cfg.plug_delta)
                new_rows[t] = new_rows[t].model_copy(update={"values": vals})
            top = rows[chain[-1]].account_raw
            yield Mutation(doc, "plug_subtotals", "gap",
                           f"{row.account_raw!r} [col {ci}] +{cfg.plug_delta:g} and totals up to {top!r}",
                           _with_rows(raw, new_rows), ((row.row_index, ci),), cfg.plug_delta,
                           {"chain": len(chain)})


# ----------------------------------------------------------------------------- fail-open / noise
def op_label_script_drift(doc, raw, cfg):
    for ri, row in enumerate(raw.rows):
        for jp, zh in SIMPLIFIED:
            if jp in row.account_raw:
                new = row.account_raw.replace(jp, zh)
                yield Mutation(doc, "label_script_drift", "no_degrade", f"{row.account_raw!r} -> {new!r}",
                               _set_label(raw, ri, new), (), None, {"touches_total_label": is_total_row(row)})


def op_label_total_noise(doc, raw, cfg):
    for ri, row in enumerate(raw.rows):
        if not is_total_row(row):
            continue
        label = row.account_raw
        variants = [("footnote", label + "※1")]
        if "合計" in label:
            variants.append(("space", label.replace("合計", " 合計")))
        if label.endswith("合計"):
            variants.append(("short_form", label[:-2] + "計"))
        for tag, new in variants:
            yield Mutation(doc, "label_total_noise", "no_degrade", f"{label!r} -> {new!r}",
                           _set_label(raw, ri, new), (), None, {"variant": tag})


def op_label_item_noise(doc, raw, cfg):
    for ri, row in enumerate(raw.rows):
        if row.role in ITEMISH and any(row.values):
            new = row.account_raw + "※2"
            yield Mutation(doc, "label_item_noise", "no_degrade", f"{row.account_raw!r} -> {new!r}",
                           _set_label(raw, ri, new), (), None, {})


def op_degenerate(doc, raw, cfg):
    yield Mutation(doc, "degenerate", "flag", "zero rows", _with_rows(raw, []), (), None, {})
    blank = [r.model_copy(update={"values": ["" for _ in r.values]}) for r in raw.rows]
    yield Mutation(doc, "degenerate", "flag", "every value blank", _with_rows(raw, blank), (), None, {})
    junk = [r.model_copy(update={"values": ["x" for _ in r.values]}) for r in raw.rows]
    yield Mutation(doc, "degenerate", "flag", "every value unparseable", _with_rows(raw, junk), (), None, {})
    yield Mutation(doc, "degenerate", "flag", "single row only", _with_rows(raw, raw.rows[:1]), (), None, {})


# ----------------------------------------------------------------------------- invariance (must NOT change verdict)
def _map_values(raw, fn):
    return _with_rows(raw, [r.model_copy(update={"values": [fn(s) for s in r.values]}) for r in raw.rows])


def op_invariant(doc, raw, cfg):
    full = {ord(str(d)): ord("０") + d for d in range(10)}
    yield Mutation(doc, "invariant_forms", "invariant", "full-width digits",
                   _map_values(raw, lambda s: s.translate(full)), (), None, {})
    if any(s.startswith("△") for r in raw.rows for s in r.values):
        yield Mutation(doc, "invariant_forms", "invariant", "△ written as ▲",
                       _map_values(raw, lambda s: s.replace("△", "▲")), (), None, {})
        yield Mutation(doc, "invariant_forms", "invariant", "negatives in parentheses",
                       _map_values(raw, lambda s: f"({s[1:]})" if s.startswith("△") else s), (), None, {})
        # an unambiguous minus written with another glyph is the same value, not a corruption
        for glyph, label in (("−", "U+2212 minus"), ("-", "hyphen-minus")):
            yield Mutation(doc, "invariant_forms", "invariant", f"negatives written with {label}",
                           _map_values(raw, lambda s, g=glyph: g + s[1:] if s.startswith("△") else s),
                           (), None, {})
    if any(is_nil(s) for r in raw.rows for s in r.values):
        for nil in ("-", "―", "ー", "—", "–", "−"):
            if all(s == nil for r in raw.rows for s in r.values if is_nil(s)):
                continue  # document already spells nil this way
            yield Mutation(doc, "invariant_forms", "invariant", f"nil dash written as {nil!r}",
                           _map_values(raw, lambda s, n=nil: n if is_nil(s) else s), (), None, {})
    yield Mutation(doc, "invariant_forms", "invariant", "padding spaces around values",
                   _map_values(raw, lambda s: f" {s} " if s else s), (), None, {})
    yield Mutation(doc, "invariant_forms", "invariant", "trailing full-width space on labels",
                   _with_rows(raw, [r.model_copy(update={"account_raw": r.account_raw + "　"}) for r in raw.rows]),
                   (), None, {})
    periods, ok = [], True
    for p in raw.metadata.periods:
        m = re.search(r"(\d{4})\s*年\s*([０-９\d]+)\s*月\s*([０-９\d]+)\s*日", unicodedata.normalize("NFKC", p.raw_date))
        if not m or int(m.group(1)) < 2019:
            ok = False
            break
        periods.append(p.model_copy(update={"raw_date": f"令和{int(m.group(1)) - 2018}年{m.group(2)}月{m.group(3)}日"}))
    if ok and periods:
        yield Mutation(doc, "invariant_forms", "invariant", "Reiwa era dates", _set_meta(raw, periods=periods), (), None, {})
    m = re.search(r"(十億円|百万円|千円|円)", unicodedata.normalize("NFKC", raw.metadata.unit_raw))
    if m:
        for form in (f"(単位：{m.group(1)})", f"単位：{m.group(1)}", f"（単位:{m.group(1)}）"):
            if form == raw.metadata.unit_raw:
                continue
            yield Mutation(doc, "invariant_forms", "invariant", f"unit string {form!r}",
                           _set_meta(raw, unit_raw=form), (), None, {})


def _has_numbers(row) -> bool:
    return any(parse_val(s) is not None for s in row.values)


def _total_first(raw) -> bool:
    """Total-then-children layout (e.g. the Yamato T-account): totals carry no 合計 label and
    their components are recognised by indentation, so indent noise is not an invariant there."""
    return any(r.role in TOTALISH and _has_numbers(r) and not r.account_raw.endswith("合計") for r in raw.rows)


def op_structure_noise(doc, raw, cfg):
    """Real VLM output (saved Qwen3-VL runs) indents subtotals like their items and is loose with
    roles. Verdicts must not depend on it."""
    def restyle(desc, fn):
        rows = [fn(i, r) for i, r in enumerate(raw.rows)]
        if rows == list(raw.rows):
            return []                                          # the document already looks like this: not a mutation
        return [Mutation(doc, "structure_noise", "invariant", desc, _with_rows(raw, rows), (), None, {})]

    if not _total_first(raw):
        prev = {"indent": None}

        def like_rows_above(i, r):
            out = r
            if is_total_row(r) and prev["indent"] is not None:
                out = r.model_copy(update={"indent_level": prev["indent"]})
            if _has_numbers(r):
                prev["indent"] = r.indent_level
            return out
        yield from restyle("subtotals indented like the rows above them", like_rows_above)
        yield from restyle("every row at indent 0", lambda i, r: r.model_copy(update={"indent_level": 0}))
        yield from restyle("indent jittered by -1/0/+1",
                           lambda i, r: r.model_copy(update={"indent_level": max(0, r.indent_level + (i % 3) - 1)}))
    yield from restyle("every line-item role written as 'item'",
                       lambda i, r: r.model_copy(update={"role": "item"}) if r.role in ITEMISH else r)
    yield from restyle("every plain item labelled 'subtotal'",
                       lambda i, r: r.model_copy(update={"role": "subtotal"}) if r.role == "item" else r)
    # seen in real Qwen3-VL-2B output: subtotals tagged 'netted_item'
    yield from restyle("every total row labelled 'netted_item'",
                       lambda i, r: r.model_copy(update={"role": "netted_item"}) if is_total_row(r) else r)
    if any("純額" in r.account_raw for r in raw.rows):
        yield from restyle("every 純額 row labelled 'subtotal'",
                           lambda i, r: r.model_copy(update={"role": "subtotal"}) if "純額" in r.account_raw else r)


def op_heading_echo(doc, raw, cfg):
    """Seen in real Qwen3-VL-4B output: the model repeats a line item as a value-less heading just above it
    (資本金 heading, then the 資本金 line). Verdicts must not change. Skipped where the document itself has a
    heading + item with that label (a later total is named after it)."""
    stems = {r.account_raw[:-2] if r.account_raw.endswith("合計") else r.account_raw[:-1]
             for r in raw.rows if r.account_raw.endswith("計")}
    new_index = max(r.row_index for r in raw.rows) + 1
    for ri, row in enumerate(raw.rows):
        if row.role != "item" or not any(parse_val(s) for s in row.values) or row.account_raw in stems:
            continue
        if ri and raw.rows[ri - 1].account_raw == row.account_raw:
            continue
        echo = row.model_copy(update={"row_index": new_index, "role": "category_header", "values": [""] * len(row.values)})
        rows = list(raw.rows)
        rows.insert(ri, echo)
        yield Mutation(doc, "heading_echo", "invariant", f"{row.account_raw!r} echoed as a heading above itself",
                       _with_rows(raw, rows), (), None, {})


# ----------------------------------------------------------------------------- registry
OPERATORS: List[Tuple[str, Callable]] = [
    ("digit_sub", op_digit_sub), ("digit_drop", op_digit_drop), ("digit_transpose", op_digit_transpose),
    ("comma_decimal", op_comma_decimal), ("sign_drop", op_sign_drop), ("sign_add", op_sign_add),
    ("nil_to_number", op_nil_to_number), ("number_to_nil", op_number_to_nil),
    ("value_unparseable", op_value_unparseable), ("row_omit", op_row_omit), ("row_dup", op_row_dup),
    ("subtotal_omit", op_subtotal_omit), ("heading_value_shift", op_heading_value_shift),
    ("row_swap_values", op_row_swap_values), ("column_swap", op_column_swap),
    ("period_header_swap", op_period_header_swap), ("unit_change", op_unit_change),
    ("label_confusable_flip", op_label_confusable_flip), ("plug_subtotals", op_plug_subtotals),
    ("label_script_drift", op_label_script_drift), ("label_total_noise", op_label_total_noise),
    ("label_item_noise", op_label_item_noise), ("degenerate", op_degenerate),
    ("invariant_forms", op_invariant), ("structure_noise", op_structure_noise), ("heading_echo", op_heading_echo),
]

CLASS_INFO: Dict[str, str] = {
    "digit_sub": "one digit replaced by another (every position, every cell)",
    "digit_drop": "one digit missing from a number",
    "digit_transpose": "two adjacent digits swapped",
    "comma_decimal": "thousands comma read as a decimal point (1,234 -> 1.234)",
    "sign_drop": "△ lost on a negative value",
    "sign_add": "△ invented on a positive value",
    "nil_to_number": "printed dash read as a number",
    "number_to_nil": "printed number read as a dash (the observed 建設仮勘定 failure)",
    "value_unparseable": "letter O for 0, footnote glued to a number (v1 turns these into 0)",
    "row_omit": "a line item is skipped",
    "row_dup": "a line item is duplicated",
    "subtotal_omit": "a total row is skipped (equation can no longer run)",
    "heading_value_shift": "a section's first line item is lost and its numbers land on the heading above it",
    "row_swap_values": "adjacent rows exchange values (label/value misalignment)",
    "column_swap": "both period columns exchanged",
    "period_header_swap": "period dates exchanged",
    "unit_change": "unit misread (千円 <-> 百万円 ...)",
    "label_confusable_flip": "債権/債務, 売掛/買掛, 貸付/借入 ... flips",
    "plug_subtotals": "item misread and every total above it adjusted to match",
    "label_script_drift": "Simplified-Chinese glyphs in labels (流动资产合计)",
    "label_total_noise": "footnote / space / short form on a total label",
    "label_item_noise": "footnote marker on an item label",
    "degenerate": "no rows, blank or unparseable output",
    "invariant_forms": "equivalent spellings (full-width digits, ▲, 令和, nil variants, unit strings)",
    "heading_echo": "a line item echoed as a value-less heading just above itself (seen in real Qwen3-VL-4B output)",
    "structure_noise": "VLM indent/role noise seen in real Qwen output (subtotals indented like items, flat roles)",
}


def generate(doc: str, raw: RawDocumentTree, cfg: MutationConfig,
             classes: Optional[List[str]] = None) -> Iterator[Mutation]:
    for cls, fn in OPERATORS:
        if classes and cls not in classes:
            continue
        for i, m in enumerate(fn(doc, raw, cfg)):
            if i % max(cfg.stride, 1) == 0:
                yield m
