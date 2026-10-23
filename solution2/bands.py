"""
Band reading: instead of transcribing a whole page in one long answer, cut the rendered page into bands of a dozen text
lines and have the model read each band on its own, then stitch the rows back together.

Why: the whole-page failures seen so far (a value that slides onto the next row, a dropped cell, a heading echoed as a line
item) are all "lost track of which row this number belongs to" over 30-50 rows. A band keeps that problem small, and the
repair crops already show the model reads a band accurately.

No text layer and no table detector is needed (so it also works on scans):
  1. the page image is scanned for text lines (horizontal ink profile, vertical rules ignored)
  2. lines are grouped into bands of `per_band` lines with `overlap` lines shared between neighbours; band edges therefore
     fall in the white space between lines and never cut a row in half
  3. one short call on a reduced copy of the whole page reads only the metadata: unit and the numeric column headings
  4. each band is read with the same row-reading prompt the repair loop uses (label + one value per numeric column)
  5. the rows are concatenated, dropping the rows a band repeats from its neighbour's overlap
The result is the same Stage-1 tree as every other first read, so verify / join / repair are unchanged.
"""
import json
import re
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

import numpy as np
from PIL import Image, ImageFilter

from solution1.models import RawDocumentTree
from solution2.repair.reader import Crop, OllamaReader, ReadRow
from solution2.verify.parsing import NUMBER, NIL, clean_label, parse_cell

META_PROMPT = (
    "This is one page of a Japanese financial statement. Do not transcribe the table. Return only JSON: "
    '{"unit": "<the unit exactly as printed, e.g. 単位：千円>", '
    '"columns": [{"label": "<column heading>", "date": "<the date or period printed under or in it, or empty>"}]} '
    "with one entry for every column that holds numbers, from left to right. The leftmost column of account names is not "
    "a numeric column and must not be listed."
)


def _lines(gray: np.ndarray, dpi: int, denoise: bool) -> List[Tuple[int, int]]:
    if denoise:                                                 # speckle noise would otherwise fill the gaps between lines
        gray = np.asarray(Image.fromarray(gray).filter(ImageFilter.MedianFilter(3)))
    dark = gray < 160
    run = np.zeros(dark.shape[1], dtype=np.int32)
    longest = np.zeros(dark.shape[1], dtype=np.int32)
    for row in dark:                                            # longest unbroken vertical ink run per column
        run = (run + 1) * row
        np.maximum(longest, run, out=longest)
    dark[:, longest > 0.1 * dark.shape[0]] = False              # vertical table rules and page borders (text has gaps between lines)
    ink = dark.sum(axis=1)
    active = ink[ink > 0]
    threshold = max(2, int(0.02 * np.percentile(active, 99))) if denoise and active.size else 2
    on = ink >= threshold
    min_gap, min_height = max(2, round(dpi * 0.02)), max(6, round(dpi * 0.04))
    lines, start, last = [], None, None
    for y, v in enumerate(on):
        if v:
            if start is None:
                start = y
            elif y - last > min_gap:                            # a blank stretch ends the line
                lines.append((start, last))
                start = y
            last = y
    if start is not None:
        lines.append((start, last))
    return [(a, b) for a, b in lines if b - a + 1 >= min_height]


def text_lines(gray: np.ndarray, dpi: int = 200) -> List[Tuple[int, int]]:
    """(top, bottom) pixel rows of each text line. Tall vertical rules are ignored; hairlines are not text."""
    return _lines(gray, dpi, False)


def ran_together(lines: Sequence[Tuple[int, int]], dpi: int) -> bool:
    """Far fewer lines than the inked height can hold (no statement row is taller than 0.22 inch): the lines ran together."""
    if not lines:
        return True
    return len(lines) < 0.5 * (lines[-1][1] - lines[0][0]) / round(dpi * 0.22)


def find_lines(page: Image.Image, dpi: int) -> Tuple[Image.Image, List[Tuple[int, int]]]:
    """The page (rotated upright if it had to be) and its text lines. A clean render takes the plain path and is never altered.
    If the lines run together (noise, skew), try again with the speckle filtered out, then with the page rotated by up to
    2 degrees, and as a last resort cut the inked span into evenly spaced pseudo-lines so that bands can still be formed."""
    lines = text_lines(np.asarray(page.convert("L")), dpi)
    if not ran_together(lines, dpi):
        return page, lines
    best_page, best = page, _lines(np.asarray(page.convert("L")), dpi, True)
    if not ran_together(best, dpi):
        return best_page, best
    for angle in (0.5, -0.5, 1.0, -1.0, 1.5, -1.5, 2.0, -2.0):
        rotated = page.rotate(angle, resample=Image.BICUBIC, fillcolor=(255, 255, 255))
        found = _lines(np.asarray(rotated.convert("L")), dpi, True)
        if len(found) > len(best):
            best_page, best = rotated, found
        if not ran_together(best, dpi):
            return best_page, best
    if best:
        top, bottom = best[0][0], best[-1][1]
        n = max(1, round((bottom - top) / (dpi * 0.16)))
        edges = np.linspace(top, bottom, n + 1).astype(int)
        best = [(int(edges[i]), int(edges[i + 1]) - 1) for i in range(n)]
    return best_page, best


def read_with_split(read: Callable[[int, int], Optional[List[ReadRow]]], a: int, b: int,
                    min_lines: int = 4) -> Optional[List[ReadRow]]:
    """Read lines a..b; if the model fails on the band (e.g. an Ollama repetition abort), read its two halves (sharing one
    line) and stitch them, recursively, instead of losing the whole page. None only if a piece too small to split fails."""
    rows = read(a, b)
    if rows is not None or b - a + 1 < 2 * min_lines:
        return rows
    mid = (a + b) // 2
    first, second = read_with_split(read, a, mid, min_lines), read_with_split(read, mid, b, min_lines)
    if first is None or second is None:
        return None
    return stitch([first, second])


def band_plan(n_lines: int, per_band: int = 12, overlap: int = 2) -> List[Tuple[int, int]]:
    """[first line, last line] (inclusive) of each band, consecutive bands sharing `overlap` lines."""
    if n_lines <= 0:
        return []
    step = max(1, per_band - overlap)
    plan, s = [], 0
    while True:
        e = min(n_lines - 1, s + per_band - 1)
        plan.append((s, e))
        if e >= n_lines - 1:
            return plan
        s += step


def _key(row: ReadRow) -> Tuple[str, Tuple[str, ...]]:
    values = [re.sub(r"\s+", "", v) for v in row.values]
    while values and not values[-1]:
        values.pop()
    return clean_label(row.label)[0], tuple(values)


def stitch(bands: Sequence[List[ReadRow]], max_overlap_rows: int = 6) -> List[ReadRow]:
    """Concatenate band reads; rows a band repeats from the end of the previous one are dropped (longest exact match
    of the previous rows' tail with the new rows' head, at most `max_overlap_rows`)."""
    out: List[ReadRow] = []
    for rows in bands:
        k = 0
        for n in range(min(max_overlap_rows, len(out), len(rows)), 0, -1):
            if [_key(r) for r in out[-n:]] == [_key(r) for r in rows[:n]]:
                k = n
                break
        out.extend(rows[k:])
    return out


def _json_obj(text: str) -> Optional[dict]:
    s = text.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s[3:]
        s = s[:-3] if s.rstrip().endswith("```") else s
    try:
        data = json.loads(s)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def build_tree(meta: Optional[dict], rows: Sequence[ReadRow]) -> RawDocumentTree:
    meta = meta or {}
    columns = [c for c in (meta.get("columns") or []) if isinstance(c, dict)]
    counts = Counter(sum(parse_cell(v).kind in (NUMBER, NIL) and bool(v.strip()) for v in r.values) for r in rows)
    counts.pop(0, None)
    seen = counts.most_common(1)[0][0] if counts else 0          # the numeric cells a data row usually has
    if seen and len(columns) > seen:                              # the model listed a column too many (e.g. the label column)
        dated = [c for c in columns if c.get("date")]
        columns = dated if len(dated) == seen else columns[-seen:]
    ncols = max(len(columns), seen)
    periods = []
    for j in range(ncols):
        c = columns[j] if j < len(columns) else {}
        periods.append({"col_index": j + 1, "raw_label": str(c.get("label") or f"column {j + 1}"),
                        "raw_date": str(c.get("date") or "")})
    out = []
    for r in rows:
        values = list(r.values)
        if len(values) > ncols and not any(v.strip() for v in values[ncols:]):
            values = values[:ncols]                               # padding the model added beyond the numeric columns
        has_numbers = any(parse_cell(v).kind in (NUMBER, NIL) and v.strip() for v in values)
        if not has_numbers:
            values = []
        label = r.label.strip()
        role = "group_header" if not has_numbers else ("subtotal" if clean_label(label)[0].endswith("計") else "item")
        out.append({"row_index": len(out) + 1, "indent_level": 0, "account_raw": label, "role": role, "values": values})
    if not any(r["values"] for r in out):
        raise ValueError("no numeric rows were read")
    return RawDocumentTree.model_validate({
        "metadata": {"title": "", "is_amended": False, "unit_raw": str(meta.get("unit") or ""), "periods": periods},
        "rows": out})


@dataclass
class BandConfig:
    per_band: int = 12
    overlap: int = 2
    pad_px: int = 10
    dpi: int = 200                  # DPI of the page image the extractor is handed
    meta_max_side: int = 1700       # the metadata call sees a reduced copy of the page
    num_ctx: int = 12288            # context for the band reads (a smaller one keeps a big model on the GPU)
    reader: str = "json"            # how a band is read: "json" (row prompt, JSON-constrained) or "markdown" (Qwen's Markdown mode)


def band_extractor(model: str, config: Optional[BandConfig] = None, keep_log: bool = True) -> Callable[[str], Optional[str]]:
    """image path -> Stage-1 JSON text (None if nothing could be read). A log of the calls (band count, seconds per call)
    is written next to the image as <image>.bands.json."""
    cfg = config or BandConfig()
    if cfg.reader == "markdown":
        from solution2.formats import MarkdownBandReader
        reader = MarkdownBandReader(model, num_ctx=cfg.num_ctx)
    else:
        reader = OllamaReader(model, num_ctx=cfg.num_ctx)

    def extract(image_path: str) -> Optional[str]:
        import ollama
        t_all = time.perf_counter()
        page, lines = find_lines(Image.open(image_path).convert("RGB"), cfg.dpi)
        image = page.convert("L")
        if not lines:
            return None
        log = {"lines": len(lines), "calls": []}

        # 1. metadata from a reduced whole-page view
        small = page
        scale = cfg.meta_max_side / max(small.size)
        if scale < 1:
            small = small.resize((round(small.width * scale), round(small.height * scale)))
        meta_path = Path(str(image_path) + ".meta.png")
        small.save(meta_path)
        t0 = time.perf_counter()
        meta = None
        try:
            resp = ollama.chat(model=model, format="json",
                               messages=[{"role": "user", "content": META_PROMPT, "images": [str(meta_path)]}],
                               options={"num_ctx": 8192, "num_predict": 600, "temperature": 0.0})
            meta = _json_obj(resp["message"]["content"])
        except Exception:
            meta = None
        log["calls"].append({"what": "meta", "s": round(time.perf_counter() - t0, 2)})
        ncols = len((meta or {}).get("columns") or []) or 2

        # 2. bands
        rows_per_band: List[List[ReadRow]] = []
        cols = (np.asarray(image) < 160).any(axis=0)
        left = max(0, int(cols.argmax()) - cfg.pad_px)
        right = min(image.width, image.width - int(cols[::-1].argmax()) + cfg.pad_px)
        full = page
        counter = {"n": 0}

        def read_band(a: int, b: int) -> Optional[List[ReadRow]]:
            k = counter["n"]
            counter["n"] += 1
            y0, y1 = max(0, lines[a][0] - cfg.pad_px), min(image.height, lines[b][1] + cfg.pad_px)
            crop_path = Path(str(image_path) + f".band{k}.png")
            full.crop((left, y0, right, y1)).save(crop_path)
            t0 = time.perf_counter()
            read = reader.read(Crop(crop_path, (0, 0, 0, 0), cfg.dpi), ncols)
            log["calls"].append({"what": f"band{k}", "lines": [a, b], "s": round(time.perf_counter() - t0, 2),
                                 "rows": None if read is None else len(read)})
            return read

        rows_per_band: List[List[ReadRow]] = []
        for a, b in band_plan(len(lines), cfg.per_band, cfg.overlap):
            read = read_with_split(read_band, a, b)
            if read is None:
                return None                                     # a band that cannot be read even in halves: the page is unreadable
            rows_per_band.append(read)

        log["total_s"] = round(time.perf_counter() - t_all, 2)
        if keep_log:
            Path(str(image_path) + ".bands.json").write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
        try:
            return build_tree(meta, stitch(rows_per_band)).model_dump_json(indent=2)
        except Exception:
            return None

    return extract
