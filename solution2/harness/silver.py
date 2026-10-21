"""
"Silver gold": ground truth for a digital PDF table taken from its text layer, with no VLM involved.

    from solution2.harness.silver import silver_gold
    gold = silver_gold("statement.pdf", page_index=0)

The digits come straight from the PDF's text layer and the indentation from the label x-positions, so this
is independent of everything the VLM and the verifier do. It is only *silver*: it can still be wrong
(a split cell, a footnote glued on), so a page is only used as gold if the verifier also trusts it, i.e.
two independent paths (text-layer digits and accounting identities) agree. Rows it cannot read cleanly make the
page unusable rather than approximately usable.
"""
import re
import unicodedata
from typing import List, Optional

from solution1.models import RawDocumentTree, RawMetadata, RawPeriod, RawRow
from solution2.repair.page import PdfPage, _is_number_token
from solution2.verify.parsing import NIL, NUMBER, parse_cell

_DATE = re.compile(r"\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日|(?:令和|平成)\s*(?:元|\d+)\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日")
INDENT_STEP = 13.0


def silver_gold(pdf_path, page_index: int = 0, title: Optional[str] = None) -> RawDocumentTree:
    page = PdfPage(pdf_path, page_index)
    lines = page.lines()
    texts = [unicodedata.normalize("NFKC", "".join(l.tokens)) for l in lines]

    unit_raw = next((" ".join(l.tokens) for l in lines if "単位" in "".join(l.tokens)), "")
    header_idx = [i for i, t in enumerate(texts) if _DATE.search(t)]
    if not header_idx:
        raise ValueError("no column header with dates found on the page")
    periods: List[RawPeriod] = []
    # dates sit on one line (or a few) under the column titles, left to right
    for i in header_idx:
        for m in _DATE.finditer("".join(lines[i].tokens)):
            periods.append(RawPeriod(col_index=len(periods) + 1, raw_label=f"column {len(periods) + 1}", raw_date=m.group(0)))
    height = max(l.bbox[3] for l in lines)

    body = []
    for l in lines[header_idx[-1] + 1:]:
        nums = [t for t in l.tokens if _is_number_token(t)]
        if not l.label and (l.bbox[1] > 0.93 * height or len(nums) <= 1):
            continue                                               # page number / stray figure
        head = []
        for t in l.tokens:
            if _is_number_token(t):
                break
            head.append(t)
        body.append((l, "".join(head), nums))
    ncols = max(len(p) for _, _, p in body) if body else 0
    if ncols == len(periods) + 1 and any("増減" in "".join(lines[i].tokens) for i in range(max(header_idx[0] - 3, 0), header_idx[-1] + 1)):
        periods.append(RawPeriod(col_index=len(periods) + 1, raw_label="増減", raw_date=""))   # a change column has no date
    if ncols != len(periods):
        raise ValueError(f"{len(periods)} period headers but rows carry up to {ncols} numbers")
    x_min = min(l.bbox[0] for l, label, _ in body if label)
    rows: List[RawRow] = []
    for k, (l, label, nums) in enumerate(body, start=1):
        if not label:
            raise ValueError(f"a numbers-only line at y={l.bbox[1]:.0f}")
        negative = any(parse_cell(t).value and parse_cell(t).value < 0 for t in nums)
        if not nums:
            role = "group_header"
        elif label.endswith("計"):
            role = "grand_total" if label in ("資産合計", "負債純資産合計", "負債及び純資産合計") else "subtotal"
        elif "純額" in label:
            role = "netted_item"
        elif ("累計額" in label or "引当金" in label) and negative:
            role = "contra_item"
        else:
            role = "item"
        rows.append(RawRow(row_index=k, indent_level=round((l.bbox[0] - x_min) / INDENT_STEP), account_raw=label,
                           role=role, values=nums if nums else []))
    # a plain-labelled row that equals the sum of the rows directly above it (IFRS 親会社の所有者に帰属する持分)
    # is a subtotal; tag it so, using only the extracted numbers
    def num(r, c):
        return parse_cell(r.values[c]).value or 0
    for i, r in enumerate(rows):
        if r.role != "item" or not r.values or max(abs(num(r, c)) for c in range(len(r.values))) < 100:
            continue
        for size in range(i, 1, -1):
            above = rows[i - size:i]
            if all(a.values for a in above) and all(
                    abs(sum(num(a, c) for a in above) - num(r, c)) <= 1 for c in range(len(r.values))):
                rows[i] = r.model_copy(update={"role": "subtotal"})
                break
    return RawDocumentTree(metadata=RawMetadata(title=title or "", is_amended=False, unit_raw=unit_raw, periods=periods),
                           rows=rows)
