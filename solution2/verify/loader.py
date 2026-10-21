"""
Tolerant loader for Stage-1 VLM output.

solution1's schema rejects a whole page when a model omits `values` on a header row or emits numbers
as JSON numbers. Those gaps are unambiguous, so they are repaired here, and every repair is reported
(never silent). Anything that is not valid JSON, or still violates the schema afterwards, raises.
"""
import json
from typing import List, Tuple

from solution1.models import RawDocumentTree


def parse_stage1(text: str) -> Tuple[RawDocumentTree, List[str]]:
    s = text.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s[3:]
        s = s[:-3] if s.rstrip().endswith("```") else s
    data = json.loads(s)
    notes: List[str] = []
    missing = coerced = 0
    for row in data.get("rows", []):
        if "values" not in row:
            row["values"] = []
            missing += 1
        elif any(not isinstance(v, str) for v in row["values"]):
            row["values"] = ["" if v is None else str(v) for v in row["values"]]
            coerced += 1
    if missing:
        notes.append(f"{missing} row(s) had no `values`; treated as value-less")
    if coerced:
        notes.append(f"{coerced} row(s) had non-string values; converted to text")
    return RawDocumentTree(**data), notes
