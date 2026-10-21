"""
Readers: fresh, independent transcriptions of a cropped region.

Leak-free by construction. `build_prompt` takes only the number of numeric columns; it is not given the
row labels, the numbers read the first time, the failing equation, or any deficit. Each read is a single
fresh chat turn with one image, so there is no earlier answer to anchor on or to copy.
"""
import json
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Protocol

from .page import PdfPage, Rect, _is_number_token


@dataclass
class Crop:
    image_path: Path
    rect: Rect                     # PDF-point rectangle (used by readers that do not look at pixels)
    dpi: int


@dataclass
class ReadRow:
    label: str
    values: List[str]


class Reader(Protocol):
    def read(self, crop: Crop, ncols: int) -> Optional[List[ReadRow]]: ...


def build_prompt(ncols: int) -> str:
    return (
        "This image is a cropped part of a Japanese financial statement table. "
        "Transcribe every table row visible in the image, from top to bottom, as JSON: "
        '{"rows": [{"label": "<account name as printed>", "values": [<one string per numeric column, left to right>]}]}. '
        f"There are {ncols} numeric column(s). Copy each number exactly as printed: keep the commas, write a "
        'negative number with the printed △ sign, write a printed dash as "－", and use "" for an empty cell. '
        "A heading row that has no numbers has values []. "
        "Do not calculate, correct or translate anything, and do not add rows that are not visible. "
        "Return only the JSON."
    )


def parse_read(text: str) -> Optional[List[ReadRow]]:
    """Tolerant parser for a reader's JSON; None if it is not usable."""
    s = text.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s[3:]
        s = s[:-3] if s.rstrip().endswith("```") else s
    try:
        data = json.loads(s)
    except ValueError:
        return None
    rows = data.get("rows") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        return None
    out = []
    for r in rows:
        if not isinstance(r, dict):
            return None
        label = r.get("label", r.get("account_raw", ""))
        values = r.get("values", [])
        if not isinstance(label, str) or not isinstance(values, list):
            return None
        out.append(ReadRow(label, ["" if v is None else str(v) for v in values]))
    return out


class OllamaReader:
    """A local vision-language model reading the rendered crop."""

    def __init__(self, model: str = "qwen3-vl:4b-instruct", num_ctx: int = 12288, num_predict: int = 3072):
        self.model, self.num_ctx, self.num_predict = model, num_ctx, num_predict

    def read(self, crop: Crop, ncols: int) -> Optional[List[ReadRow]]:
        import ollama
        try:
            resp = ollama.chat(
                model=self.model, format="json",
                messages=[{"role": "user", "content": build_prompt(ncols), "images": [str(crop.image_path)]}],
                options={"num_ctx": self.num_ctx, "num_predict": self.num_predict, "temperature": 0.0})
        except Exception:
            return None
        return parse_read(resp["message"]["content"])


class TextLayerReader:
    """Exact digits straight from a digital PDF's text layer, for the rows inside the crop rectangle.

    It is an independent reader (no VLM), used to exercise the loop deterministically and as a possible
    second signal for digital PDFs. Blank cells cannot be recovered from a token stream, so a short row is
    left-padded with blanks."""

    def __init__(self, page: PdfPage):
        self.page = page

    def read(self, crop: Crop, ncols: int) -> Optional[List[ReadRow]]:
        x0, y0, x1, y1 = crop.rect
        rows = []
        for line in self.page.lines():
            cy = (line.bbox[1] + line.bbox[3]) / 2
            if not (y0 <= cy <= y1) or not line.label and not any(_is_number_token(t) for t in line.tokens):
                continue
            head = []
            for t in line.tokens:
                if _is_number_token(t):
                    break
                head.append(t)
            nums = [t for t in line.tokens if _is_number_token(t)]
            if len(nums) < ncols:
                nums = [""] * (ncols - len(nums)) + nums if nums else []
            rows.append(ReadRow("".join(head), nums[:ncols]))
        return rows
