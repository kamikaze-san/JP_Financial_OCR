"""
XBRL-derived CSV (EDINET type=5) -> balance-sheet facts: exact yen values, independent of the PDF and of any VLM.

Per the EDINET viewing guide the CSV has one fact per row with these columns (Japanese headers):
    要素ID, 項目名, コンテキストID, 相対年度, 連結・個別, 期間・時点, ユニットID, 単位, 値
and is commonly UTF-16 with tab separators. That description came from secondary sources, so the reader sniffs the
encoding and delimiter, and falls back to column positions if the headers are not the expected ones. Validate it on
the first real file (`python -m solution2.edinet inspect <csv>` prints what was understood).

A balance-sheet fact is an instant-in-time JPY fact whose context carries no dimension other than the optional
NonConsolidatedMember (the members used for equity-statement detail, segments, etc. are excluded).
"""
import csv
import io
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Dict, Iterable, List, Optional

ALIASES = {"要素ID": "element", "項目名": "label", "コンテキストID": "context", "相対年度": "relative",
           "連結・個別": "scope", "期間・時点": "period_type", "ユニットID": "unit_id", "単位": "unit", "値": "value"}
POSITIONAL = ["element", "label", "context", "relative", "scope", "period_type", "unit_id", "unit", "value"]
SIMPLE_INSTANT = re.compile(r"^([A-Za-z0-9]+)Instant(_NonConsolidatedMember)?$")
NON_FINANCIAL_PREFIXES = ("jpdei_cor",)


@dataclass
class Fact:
    element: str
    label: str
    context: str
    value: int          # yen


@dataclass
class BsGold:
    scope: str                                   # consolidated | individual
    standard: str                                # J-GAAP | IFRS | unknown
    by_context: Dict[str, List[Fact]] = field(default_factory=dict)

    def contexts_by_recency(self) -> List[str]:
        """Current period first, then Prior1, Prior2 ... (from the context ids)."""
        def rank(ctx: str) -> int:
            m = re.match(r"Prior(\d)", ctx)
            return int(m.group(1)) if m else 0
        return sorted(self.by_context, key=rank)

    def values(self, context: str) -> List[int]:
        return [f.value for f in self.by_context.get(context, [])]


def decode(raw: bytes) -> str:
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16")
    for enc in ("utf-8-sig", "cp932", "utf-16"):        # strictest first: Shift-JIS bytes also "decode" as UTF-16 garbage
        try:
            text = raw.decode(enc)
        except UnicodeError:
            continue
        if "\x00" not in text[:200]:
            return text
    return raw.decode("utf-8", errors="replace")


def read_rows(path) -> List[Dict[str, str]]:
    text = decode(Path(path).read_bytes())
    first = text.split("\n", 1)[0]
    delimiter = "\t" if first.count("\t") >= first.count(",") else ","
    rows = list(csv.reader(io.StringIO(text), delimiter=delimiter, quotechar='"'))
    if not rows:
        return []
    header = [h.strip().strip('"').lstrip("﻿") for h in rows[0]]
    if any(h in ALIASES for h in header):
        names = [ALIASES.get(h, h) for h in header]
        body = rows[1:]
    else:
        names, body = POSITIONAL, rows
    return [dict(zip(names, r)) for r in body if len(r) >= 3]


def _to_int(text: str) -> Optional[int]:
    s = (text or "").strip().replace(",", "")
    if not s or s in ("－", "-"):
        return None
    try:
        return int(Decimal(s))
    except (InvalidOperation, ValueError):
        return None


def balance_sheet_golds(rows: Iterable[Dict[str, str]]) -> List[BsGold]:
    """One gold per scope (consolidated / individual) that has balance-sheet facts."""
    buckets: Dict[str, Dict[str, List[Fact]]] = {"consolidated": {}, "individual": {}}
    standards = {"consolidated": set(), "individual": set()}
    for r in rows:
        element = r.get("element", "")
        if element.startswith(NON_FINANCIAL_PREFIXES) or r.get("unit_id", "") not in ("JPY", "jpy") or "時点" not in r.get("period_type", ""):
            continue
        m = SIMPLE_INSTANT.match(r.get("context", ""))
        value = _to_int(r.get("value", ""))
        if not m or value is None:
            continue
        scope = "individual" if (m.group(2) or r.get("scope", "").startswith("個別")) else "consolidated"
        buckets[scope].setdefault(r["context"], []).append(Fact(element, r.get("label", ""), r["context"], value))
        prefix = element.split(":")[0]
        if prefix in ("jppfs_cor", "jpigp_cor"):
            standards[scope].add("IFRS" if prefix == "jpigp_cor" else "J-GAAP")
    out = []
    for scope, by_context in buckets.items():
        if by_context and standards[scope]:                   # needs real statement elements, not just notes
            std = "IFRS" if "IFRS" in standards[scope] else "J-GAAP"
            out.append(BsGold(scope, std, by_context))
    return out


def golds_from_dir(csv_dir) -> List[BsGold]:
    rows: List[Dict[str, str]] = []
    for path in sorted(Path(csv_dir).glob("*.csv")):
        rows += read_rows(path)
    return balance_sheet_golds(rows)


def describe(path) -> str:
    """What the reader understood from a file (for validating the format on a real download)."""
    raw = Path(path).read_bytes()
    text = decode(raw)
    first = text.split("\n", 1)[0]
    rows = read_rows(path)
    golds = balance_sheet_golds(rows)
    lines = [f"{Path(path).name}: {len(raw):,} bytes, BOM={raw[:2].hex()}, "
             f"delimiter={'TAB' if first.count(chr(9)) >= first.count(',') else 'COMMA'}, rows={len(rows):,}",
             f"header: {first.strip()[:200]!r}"]
    for g in golds:
        n = sum(len(v) for v in g.by_context.values())
        lines.append(f"  {g.scope}: {g.standard}, {n} balance-sheet facts over contexts {g.contexts_by_recency()}")
        for f in g.by_context[g.contexts_by_recency()[0]][:3]:
            lines.append(f"     {f.element:<48} {f.label[:14]:<14} {f.value:>18,}")
    if not golds:
        lines.append("  no balance-sheet facts found (check column names / encoding above)")
    return "\n".join(lines)
