"""
Strict parsers for Stage-1 (VLM output) strings.

The v1 parsers return a default (0, x1000, 2025-03-31) when they cannot parse, which is
indistinguishable from real data. Here every parser either returns a value or says why it could
not, and the caller decides what to do about it.
"""
import datetime
import re
import unicodedata
from dataclasses import dataclass
from typing import Optional, Tuple

# ----------------------------------------------------------------------------- cells
NUMBER, NIL, BLANK, BAD = "number", "nil", "blank", "bad"

_NIL = {"-", "―", "ー", "—", "–", "−", "─"}            # after NFKC ("－" has become "-")
_NEGATIVE_PREFIXES = "△▲-−"
_GROUPED_INT = re.compile(r"\d{1,3}(?:,\d{3})+|\d+")   # commas only at thousands boundaries
# a footnote reference printed next to a number ("※1 110,588", "△17,221 ※3"): not part of the value. The digit must be
# separated from the number by a space, otherwise "※1110,588" is ambiguous and stays a bad cell.
_FOOTNOTE_LEAD = re.compile(r"^\s*[※*]\d{0,2}\s+")
_FOOTNOTE_TRAIL = re.compile(r"(?<=\d)\s+[※*]\d{0,2}\s*$")


@dataclass(frozen=True)
class Cell:
    kind: str                  # number | nil | blank | bad
    value: Optional[int]       # integer in the table's printed unit; nil -> 0; blank/bad -> None
    raw: str
    reason: str = ""           # why a cell is bad


def parse_cell(raw: str) -> Cell:
    """Strict: anything that is not an unambiguous integer, nil dash or blank is `bad`."""
    text = unicodedata.normalize("NFKC", raw or "")
    text = _FOOTNOTE_TRAIL.sub("", _FOOTNOTE_LEAD.sub("", text))
    s = re.sub(r"\s+", "", text)
    if not s:
        return Cell(BLANK, None, raw)
    if s in _NIL:
        return Cell(NIL, 0, raw)
    negative = False
    if s[0] in _NEGATIVE_PREFIXES:
        negative, s = True, s[1:]
    elif s.startswith("(") and s.endswith(")"):
        negative, s = True, s[1:-1]
    if not s:
        return Cell(BAD, None, raw, "sign without digits")
    if "." in s:
        return Cell(BAD, None, raw, "decimal point in an integer statement")
    if not _GROUPED_INT.fullmatch(s):
        return Cell(BAD, None, raw, "not a plain number (stray characters or misplaced commas)")
    value = int(s.replace(",", ""))
    return Cell(NUMBER, -value if negative else value, raw)


# ----------------------------------------------------------------------------- units
_UNITS = [("十億円", 10**9), ("百万円", 10**6), ("千円", 10**3), ("億円", 10**8), ("万円", 10**4), ("円", 1)]


def parse_unit(raw: str) -> Optional[int]:
    """Multiplier for a unit declaration such as '（単位：千円）', or None if unrecognised."""
    s = unicodedata.normalize("NFKC", raw or "")
    for token, multiplier in _UNITS:       # longest tokens first: 百万円 contains 万円
        if token in s:
            return multiplier
    return None


# ----------------------------------------------------------------------------- dates
_ERA_OFFSET = {"令和": 2018, "平成": 1988, "昭和": 1925, "大正": 1911}
_ERA_DATE = re.compile(r"(令和|平成|昭和|大正)\s*(元|\d+)\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
_WESTERN_DATE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")


def parse_date(raw: str) -> Optional[str]:
    """ISO date for Western or imperial-era dates; None when it cannot be resolved."""
    s = unicodedata.normalize("NFKC", raw or "")
    m = _ERA_DATE.search(s)
    if m:
        era_year = 1 if m.group(2) == "元" else int(m.group(2))
        year, month, day = _ERA_OFFSET[m.group(1)] + era_year, int(m.group(3)), int(m.group(4))
    else:
        m = _WESTERN_DATE.search(s)
        if not m:
            return None
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        return datetime.date(year, month, day).isoformat()
    except ValueError:
        return None


# ----------------------------------------------------------------------------- labels
# Simplified-Chinese forms seen in GLM-OCR output (and their Japanese originals). These characters
# never occur in Japanese text, so mapping them back is safe. The repair is reported, not hidden.
_SIMPLIFIED = {
    "动": "動", "资": "資", "产": "産", "负": "負", "债": "債", "权": "権", "务": "務", "额": "額",
    "剩": "剰", "计": "計", "纯": "純", "贷": "貸", "减": "減", "价": "価", "偿": "償", "积": "積",
    "损": "損", "总": "総", "营": "営", "业": "業", "关": "関", "现": "現", "买": "買", "卖": "売",
    "库": "庫", "货": "貨", "证": "証", "备": "備", "发": "発", "订": "訂", "还": "還", "转": "転",
    "应": "応", "费": "費", "员": "員", "变": "変", "类": "類", "实": "実", "终": "終",
}
_SIMPLIFIED_TABLE = str.maketrans(_SIMPLIFIED)
_FOOTNOTE = re.compile(r"[※*]\d*|[(（]注\s*\d+[)）]")


def clean_label(raw: str) -> Tuple[str, bool]:
    """Matching form of an account label: NFKC, no whitespace, footnote markers stripped,
    Simplified-Chinese glyphs mapped back. Returns (label, glyphs_were_repaired)."""
    s = re.sub(r"\s+", "", unicodedata.normalize("NFKC", raw or ""))
    s = _FOOTNOTE.sub("", s)
    repaired = s.translate(_SIMPLIFIED_TABLE)
    return repaired, repaired != s
