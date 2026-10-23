"""
Alternative first-read formats for the VLM: the page as Qwen's own document-parsing output (HTML or Markdown tables)
instead of solution1's nested JSON with an indent level and a role on every row.

    HTML      prompt "Parse this document page into QwenVL HTML format."   -> <table><tr><td>label</td><td>1,234</td>...
    MARKDOWN  prompt "QwenVL Markdown"                                      -> | label | 1,234 | ... |

(The bare prompt "QwenVL HTML" makes qwen3-vl:4b-instruct answer with a single image box, so the HTML prompt is spelled out.)

`parse_table_text` turns either into the same RawDocumentTree the rest of the pipeline uses, so verify / join / repair are
unchanged. These formats carry no indent or role: a row without numbers is a heading, a label ending in 計 is a subtotal,
and the indent comes from leading ideographic spaces if the model kept them. The verifier is label-driven, so this is
what it mostly uses anyway.
"""
import json
import re
import unicodedata
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

from solution1.models import RawDocumentTree
from solution2.verify.parsing import NIL, NUMBER, clean_label, parse_cell

PROMPTS = {
    "html": "Parse this document page into QwenVL HTML format.",
    "markdown": "QwenVL Markdown",
}

_DATE = re.compile(r"[（(][^()（）]*\d+\s*年\s*\d+\s*月(?:\s*\d+\s*日)?[^()（）]*[）)]|\d{4}\s*年\s*\d+\s*月(?:\s*\d+\s*日)?")
_UNIT = re.compile(r"[（(]?\s*単位\s*[:：]\s*[^)）\s<|]+\s*[）)]?")


class _Rows(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows: List[List[str]] = []
        self.header_flags: List[bool] = []
        self._row: Optional[List[str]] = None
        self._cell: Optional[List[str]] = None
        self._th = False
        self.outside: List[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row, self._th = [], False
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
            self._th = self._th or tag == "th"
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._row is not None and self._cell is not None:
            self._row.append(re.sub(r"\s+", " ", "".join(self._cell)).strip())
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self.header_flags.append(self._th)
            self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)
        elif self._row is None and data.strip():
            self.outside.append(data.strip())


def _markdown_rows(text: str) -> Tuple[List[List[str]], List[str]]:
    rows, outside = [], []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("|"):
            cells = [c.strip() for c in s.strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                continue
            rows.append([re.sub(r"\s+", " ", c.replace("**", "")).strip() for c in cells])
        elif s and not s.startswith("```"):
            outside.append(s)
    return rows, outside


def _split_rows(text: str) -> Tuple[List[List[str]], List[str]]:
    if "<tr" in text.lower():
        p = _Rows()
        p.feed(text)
        outside = list(p.outside)
        return p.rows, outside + [c for r in p.rows[:6] for c in r if "単位" in c]
    return _markdown_rows(text)


def _is_numeric(cell: str) -> bool:
    return parse_cell(cell).kind in (NUMBER, NIL) and bool(cell.strip())


def parse_table_text(text: str) -> RawDocumentTree:
    """Qwen HTML / Markdown table output -> Stage-1 tree. Raises ValueError if there is no table."""
    rows, outside = _split_rows(text)
    if not rows:
        raise ValueError("no table in the model output")
    counts = Counter(sum(_is_numeric(c) for c in r[1:]) for r in rows if any(_is_numeric(c) for c in r[1:]))
    ncols = max([n for n, k in counts.items() if k >= 2] or [max(counts, default=0)])
    first_data = next((i for i, r in enumerate(rows) if sum(_is_numeric(c) for c in r[1:]) >= 1), len(rows))

    # period columns from the header rows (rows above the data that carry a year / period / date)
    def is_header(r: List[str]) -> bool:
        return bool(ncols) and len(r) in (ncols, ncols + 1) and any(re.search(r"年|期|月|日", c) for c in r)             and not any(_is_numeric(c) for c in r[1:])
    header_idx = [i for i in range(first_data) if is_header(rows[i])]
    periods = []
    for j in range(ncols):
        parts = []
        for i in header_idx:
            r = rows[i]
            cell = r[j + (len(r) - ncols)]
            if cell:
                parts.append(cell)
        text_j = " ".join(parts)
        date = _DATE.search(text_j)
        periods.append({"col_index": j + 1, "raw_date": date.group(0).strip() if date else "",
                        "raw_label": (_DATE.sub("", text_j).strip() if date else text_j.strip()) or f"column {j + 1}"})

    blob = " ".join(outside + [c for r in rows[:first_data] for c in r])
    last_header = max(header_idx, default=-1)
    unit = _UNIT.search(blob)
    title = next((o for o in outside if o and not _UNIT.search(o)), "")

    out_rows = []
    for i, r in enumerate(rows):
        if i <= last_header or (i < first_data and (len(r) < 2 or any('単位' in c for c in r))):
            continue                                # titles, unit line and period headers are metadata, not rows
        label_raw, cells = (r[0] if r else ""), r[1:]
        label = label_raw.replace("**", "")
        lead = len(label) - len(label.lstrip("　  "))
        label = label.strip("　  ").strip()
        values = [c for c in cells]
        while values and not values[-1].strip():
            values.pop()
        has_numbers = any(_is_numeric(c) for c in values)
        if not has_numbers:
            values = []
        clean, _ = clean_label(label)
        role = "group_header" if not has_numbers else ("subtotal" if clean.endswith("計") else "item")
        out_rows.append({"row_index": len(out_rows) + 1, "indent_level": min(3, lead), "account_raw": label,
                         "role": role, "values": values})
    if not any(r["values"] for r in out_rows):
        raise ValueError("the table has no numeric rows")
    return RawDocumentTree.model_validate({
        "metadata": {"title": title, "is_amended": False, "unit_raw": unit.group(0).strip() if unit else "",
                     "periods": periods},
        "rows": out_rows})


def format_extractor(model: str, kind: str, num_ctx: int = 16384, num_predict: int = 8192, temperature: float = 0.0,
                     keep_raw: bool = True) -> Callable[[str], Optional[str]]:
    """image path -> Stage-1 JSON text built from the model's HTML / Markdown answer (None if the call or parse fails).
    The model's own text is kept next to the image as <image>.<kind>.txt."""
    prompt = PROMPTS[kind]

    def extract(image_path: str) -> Optional[str]:
        import ollama
        try:
            resp = ollama.chat(model=model, messages=[{"role": "user", "content": prompt, "images": [str(image_path)]}],
                               options={"num_ctx": num_ctx, "num_predict": num_predict, "temperature": temperature})
            text = resp["message"]["content"]
        except Exception:
            return None
        if keep_raw:
            Path(str(image_path) + f".{kind}.txt").write_text(text, encoding="utf-8")
        try:
            return parse_table_text(text).model_dump_json(indent=2)
        except Exception:
            return None

    return extract


# ----------------------------------------------------------------------------- scoring that ignores structure
def _norm_label(label: str) -> str:
    return unicodedata.normalize("NFKC", clean_label(label)[0]).replace(" ", "")


def numeric_rows(tree: RawDocumentTree) -> List[Tuple[str, Tuple]]:
    out = []
    for r in tree.rows:
        cells = [parse_cell(v) for v in r.values]
        if any(c.kind in (NUMBER, NIL) for c in cells):
            out.append((_norm_label(r.account_raw), tuple((c.kind, c.value) for c in cells if c.kind != "blank")))
    return out


def compare_numeric_rows(pred: RawDocumentTree, gold: RawDocumentTree) -> dict:
    """Row-level, structure-blind comparison: only rows with numbers count (headings, roles and indents are ignored).

    exact       a gold row found in the output with the same label and the same numbers
    misplaced   same numbers found, but under another label (the value-shift signature)
    missing     a gold row whose numbers appear nowhere in the output
    extra       output rows with numbers that match no gold row
    """
    g = Counter(numeric_rows(gold))
    p = Counter(numeric_rows(pred))
    exact = sum((g & p).values())
    g_left, p_left = g - (g & p), p - (g & p)
    g_vals, p_vals = Counter(v for _, v in g_left.elements()), Counter(v for _, v in p_left.elements())
    misplaced = sum((g_vals & p_vals).values())
    return {"gold_rows": sum(g.values()), "exact": exact, "misplaced": misplaced,
            "missing": sum(g_left.values()) - misplaced, "extra": sum(p_left.values()) - misplaced}
