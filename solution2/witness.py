"""
The PDF text layer as a witness for the VLM's rows (digital PDFs only).

A native digital PDF carries the characters the filer typed, so a model's misread kanji, a value that slid onto the next row, a dropped
cell or an invented one can be compared against it, row by row, without arithmetic and without another model. The text layer is used
as a witness, not as the answer:

  * every VLM row is matched to a text line of the page by its label (order-preserving, tolerant of a garbled character)
  * the numbers on that line are compared with the numbers the model gave the row
  * the characters of that line's label are compared with the model's label
  * anything it cannot decide is "unknown", never "agree": two-sided (T-account) pages, pages whose text layer looks like OCR output or has unmapped
    glyphs, rows whose label cannot be located

Row verdicts: agree | label_variant (same numbers, label differs in a few characters; `proposed` is the printed text) | conflict (the numbers differ;
`kind` says how: shifted / spurious / dropped / wrong) | unknown. `missing` lists text lines with numbers that no model row was matched to (a dropped row).

Nothing here changes a table; `snap_labels` returns a corrected copy for the label_variant rows only and records every change.
"""
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Sequence, Tuple

from solution1.models import RawDocumentTree
from solution2.repair.page import Line, PdfPage
from solution2.verify.parsing import BLANK, NIL, NUMBER, clean_label, parse_cell

OCR_PRODUCER = re.compile(r"ocr|abbyy|finereader|tesseract|readiris|paddle|omnipage|paper ?capture", re.I)
LABEL_SIMILAR = 0.6              # below this a label is not "the same label with a slip"


@dataclass
class RowCheck:
    row_index: int
    verdict: str                 # agree | label_variant | conflict | unknown
    kind: str = ""               # conflict: shifted | spurious | dropped | wrong ; unknown: why
    pdf_label: str = ""
    pdf_numbers: Tuple = ()
    proposed: str = ""           # label_variant: the label as printed


@dataclass
class PageCheck:
    usable: bool
    reason: str
    rows: List[RowCheck] = field(default_factory=list)
    missing: List[Tuple[str, Tuple]] = field(default_factory=list)      # (label, numbers) of text lines no row was matched to


def text_layer_status(pdf_path, page_index: int) -> Tuple[bool, str]:
    """Is there a native text layer worth comparing against? (A text layer added by OCR carries OCR's own errors.)"""
    import fitz
    with fitz.open(pdf_path) as doc:
        meta = doc.metadata or {}
        producer = f"{meta.get('producer', '')} {meta.get('creator', '')}"
        page = doc[page_index]
        words = page.get_text("words")
        fonts = [str(f[3]) for f in page.get_fonts()]
    if not words:
        return False, "no text layer"
    if OCR_PRODUCER.search(producer):
        return False, f"the PDF was produced by OCR software ({producer.strip()})"
    if any("glyphless" in f.lower() for f in fonts):
        return False, "invisible OCR font"
    text = "".join(w[4] for w in words)
    unmapped = sum(1 for ch in text if ch == "�" or "" <= ch <= "")
    if text and unmapped / len(text) > 0.02:
        return False, "glyphs without a unicode mapping"
    if sum(parse_cell(w[4]).kind in (NUMBER, NIL) for w in words) < 4:
        return False, "too few numbers on the page"
    return True, ""


def line_numbers(line: Line) -> Tuple:
    return tuple((c.kind, c.value) for c in (parse_cell(t) for t in line.tokens) if c.kind in (NUMBER, NIL))


def row_numbers(values: Sequence[str]) -> Tuple:
    return tuple((c.kind, c.value) for c in (parse_cell(v) for v in values) if c.kind in (NUMBER, NIL))


def _norm(label: str) -> str:
    return clean_label(label)[0].replace("　", "").replace(" ", "")


def check_page(pdf_path, page_index: int, raw: RawDocumentTree, page: Optional[PdfPage] = None) -> PageCheck:
    def unknown(reason: str) -> PageCheck:
        return PageCheck(False, reason, [RowCheck(r.row_index, "unknown", reason) for r in raw.rows])

    ok, why = text_layer_status(pdf_path, page_index)
    if not ok:
        return unknown(why)
    page = page or PdfPage(pdf_path, page_index)
    if page.is_two_column():
        return unknown("two-sided (T-account) page")
    lines = page.lines()
    mapping = page.map_rows(raw)
    with_numbers = [i for i, l in enumerate(lines) if line_numbers(l)]
    all_numbers = {line_numbers(l) for l in lines if line_numbers(l)}
    rows: List[RowCheck] = []
    used = set()
    mapped = {r.row_index: mapping[r.row_index] for r in raw.rows if r.row_index in mapping}
    for pos, r in enumerate(raw.rows):
        j = mapping.get(r.row_index)
        my_nums = row_numbers(r.values)
        if j is None:
            if not my_nums:
                rows.append(RowCheck(r.row_index, "unknown", "label not found on the page"))
                continue
            # the label could not be matched (a slip too large, or no label at all): look for a line with these numbers between the
            # lines of the neighbouring rows, in order
            prev_j = max([mapped[x.row_index] for x in raw.rows[:pos] if x.row_index in mapped], default=-1)
            next_j = min([mapped[x.row_index] for x in raw.rows[pos + 1:] if x.row_index in mapped], default=len(lines))
            gap = [k for k in range(prev_j + 1, next_j) if line_numbers(lines[k]) == my_nums and k not in used]
            if gap:
                line = lines[gap[0]]
                used.add(gap[0])
                same = SequenceMatcher(None, _norm(r.account_raw), _norm(line.label)).ratio() >= LABEL_SIMILAR
                rows.append(RowCheck(r.row_index, "label_variant" if same else "conflict", "" if same else "relabelled",
                                     line.label, my_nums, proposed=line.label))
            elif my_nums in {line_numbers(lines[k]) for k in used}:
                rows.append(RowCheck(r.row_index, "conflict", "duplicate", "", my_nums))      # the same numbers as a row already matched
            elif my_nums in all_numbers:
                rows.append(RowCheck(r.row_index, "conflict", "elsewhere", "", my_nums))
            else:
                rows.append(RowCheck(r.row_index, "conflict", "absent", "", my_nums))
            continue
        used.add(j)
        line = lines[j]
        pdf_nums = line_numbers(line)
        if my_nums != pdf_nums:
            if my_nums and not pdf_nums:
                kind = "spurious"
            elif pdf_nums and not my_nums:
                kind = "dropped"
            elif any(line_numbers(lines[k]) == my_nums for k in range(max(0, j - 3), min(len(lines), j + 4)) if k != j):
                kind = "shifted"
            else:
                kind = "wrong"
            rows.append(RowCheck(r.row_index, "conflict", kind, line.label, pdf_nums))
            continue
        mine, printed = _norm(r.account_raw), _norm(line.label)
        if mine == printed or not printed:
            rows.append(RowCheck(r.row_index, "agree", "", line.label, pdf_nums))
        elif SequenceMatcher(None, mine, printed).ratio() >= LABEL_SIMILAR:
            rows.append(RowCheck(r.row_index, "label_variant", "", line.label, pdf_nums, proposed=line.label))
        else:
            rows.append(RowCheck(r.row_index, "unknown", "label differs too much to trust the match", line.label, pdf_nums))
    # a dropped row: a labelled line with numbers, inside the span of the matched lines, that no row was matched to
    if used:
        lo, hi = min(used), max(used)
        missing = [(lines[i].label, line_numbers(lines[i])) for i in with_numbers
                   if lo < i < hi and i not in used and len(_norm(lines[i].label)) >= 2]
    else:
        missing = []
    return PageCheck(True, "", rows, missing)


def snap_labels(raw: RawDocumentTree, check: PageCheck) -> Tuple[RawDocumentTree, List[Tuple[int, str, str]]]:
    """A copy of the table with the label of every label_variant row replaced by the printed one, and the list of changes
    (row_index, model label, printed label). Numbers are never touched."""
    changes = {c.row_index: c.proposed for c in check.rows if c.verdict == "label_variant" and c.proposed}
    rows, done = [], []
    for r in raw.rows:
        if r.row_index in changes:
            done.append((r.row_index, r.account_raw, changes[r.row_index]))
            r = r.model_copy(update={"account_raw": changes[r.row_index]})
        rows.append(r)
    return raw.model_copy(update={"rows": rows}), done
