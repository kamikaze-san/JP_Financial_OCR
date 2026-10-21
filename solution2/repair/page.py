"""
Page layout for the retry loop: where on the page is each Stage-1 row, and what should be cropped.

For a digital PDF the text layer gives word boxes. They are used for LAYOUT ONLY (which line is which row,
where to crop); the values are re-read from the rendered crop by a reader, never copied from here.

A failing window is turned into one or more crops: the lines of the window's rows, merged into runs, with
a little context above and below so a row the VLM skipped (which has no Stage-1 row to point at) falls
inside the crop. If too few of a window's rows can be found on the page, no crop is produced and the
window is left for review: the loop never guesses a region.
"""
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from solution1.models import RawDocumentTree
from solution2.verify.parsing import NIL, NUMBER, clean_label, parse_cell

from .align import align_labels

Rect = Tuple[float, float, float, float]


@dataclass
class Line:
    index: int
    bbox: Rect
    tokens: List[str]
    label: str                 # cleaned text before the first number


@dataclass
class Region:
    rect: Rect                 # crop rectangle in PDF points
    first_line: int
    last_line: int
    row_indexes: List[int]     # Stage-1 rows whose line falls inside the crop


class PdfPage:
    def __init__(self, pdf_path, page_index: int = 0, workdir: Optional[Path] = None):
        self.pdf_path, self.page_index = str(pdf_path), page_index
        self._lines: Optional[List[Line]] = None
        self._workdir = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="jp_repair_"))

    # ---- layout
    def lines(self) -> List[Line]:
        if self._lines is None:
            import fitz
            with fitz.open(self.pdf_path) as doc:
                words = doc[self.page_index].get_text("words")
            self._lines = _cluster_lines(words)
        return self._lines

    def table_x_range(self) -> Tuple[float, float]:
        lines = [l for l in self.lines() if l.label]
        return min(l.bbox[0] for l in lines), max(l.bbox[2] for l in lines)

    def is_two_column(self) -> bool:
        """A T-account page: one text line holds a left AND a right statement (label, numbers, label, numbers).
        Crops of such a page mix both sides, so the loop declines them rather than guessing."""
        with_numbers = [l for l in self.lines() if any(_is_number_token(t) for t in l.tokens)]
        if not with_numbers:
            return False
        def second_label(l: Line) -> bool:
            seen_number = False
            for t in l.tokens:
                if _is_number_token(t):
                    seen_number = True
                elif seen_number and any("぀" <= ch <= "ヿ" or "一" <= ch <= "鿿" for ch in t)                         and not t.startswith(("※", "(", "（")):
                    return True                    # a label after a number (labels may be split into single characters)
            return False
        return sum(second_label(l) for l in with_numbers) / len(with_numbers) > 0.3

    def map_rows(self, raw: RawDocumentTree) -> Dict[int, int]:
        """Stage-1 row_index -> line index, by label sequence."""
        labels = [clean_label(r.account_raw)[0] for r in raw.rows]
        found = align_labels(labels, [l.label for l in self.lines()])
        return {raw.rows[i].row_index: j for i, j in found.items() if labels[i]}

    def locate(self, raw: RawDocumentTree, row_indexes: Sequence[int], context_above: int = 2,
               context_below: int = 1, merge_gap: int = 3, min_found: float = 0.6) -> List[Region]:
        """Crop regions covering the given Stage-1 rows; [] if the rows cannot be found on the page."""
        if self.is_two_column():
            return []
        row_to_line = self.map_rows(raw)
        wanted = [row_to_line[i] for i in row_indexes if i in row_to_line]
        if not row_indexes or len(wanted) < min_found * len(row_indexes):
            return []
        runs: List[List[int]] = []
        for j in sorted(set(wanted)):
            if runs and j - runs[-1][-1] <= merge_gap:
                runs[-1].append(j)
            else:
                runs.append([j])
        lines, x0, x1 = self.lines(), *self.table_x_range()
        regions = []
        for run in runs:
            first = max(0, run[0] - context_above)
            last = min(len(lines) - 1, run[-1] + context_below)
            rect = (max(0.0, x0 - 12), lines[first].bbox[1] - 3, x1 + 12, lines[last].bbox[3] + 3)
            inside = [i for i, j in row_to_line.items() if first <= j <= last]
            regions.append(Region(rect, first, last, inside))
        return regions

    # ---- rendering
    def render(self, rect: Rect, dpi: int, pad: float = 0.0, tag: str = "crop") -> Path:
        import fitz
        clip = fitz.Rect(rect[0] - pad, rect[1] - pad, rect[2] + pad, rect[3] + pad)
        with fitz.open(self.pdf_path) as doc:
            page = doc[self.page_index]
            clip = clip & page.rect
            out = self._workdir / f"{tag}_{int(clip.y0)}_{int(clip.y1)}_{dpi}.png"
            page.get_pixmap(clip=clip, dpi=dpi).save(out)
        return out


def _is_number_token(token: str) -> bool:
    return parse_cell(token).kind in (NUMBER, NIL)


def _cluster_lines(words) -> List[Line]:
    """Group PDF words into text lines by vertical position."""
    if not words:
        return []
    heights = sorted(w[3] - w[1] for w in words)
    tol = 0.5 * heights[len(heights) // 2]
    ordered = sorted(words, key=lambda w: (w[1] + w[3]) / 2)
    groups: List[List] = []
    for w in ordered:
        yc = (w[1] + w[3]) / 2
        if groups and abs(yc - sum((x[1] + x[3]) / 2 for x in groups[-1]) / len(groups[-1])) <= tol:
            groups[-1].append(w)
        else:
            groups.append([w])
    lines = []
    for idx, g in enumerate(groups):
        g.sort(key=lambda w: w[0])
        tokens = [w[4] for w in g]
        head = []
        for t in tokens:
            if _is_number_token(t):
                break
            head.append(t)
        bbox = (min(w[0] for w in g), min(w[1] for w in g), max(w[2] for w in g), max(w[3] for w in g))
        lines.append(Line(idx, bbox, tokens, clean_label("".join(head))[0]))
    return lines
