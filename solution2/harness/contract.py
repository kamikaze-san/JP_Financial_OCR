"""
Verifier contract for the v2 verification test harness.

Any verifier (the v1 AccountingValidator via an adapter, the future v2 reducer, a
text-layer cross-check, ...) plugs in by implementing `Verifier.verify`. The harness
only ever sees the `Verdict`, so verifiers can be swapped without touching the tests.
"""
from dataclasses import dataclass
from typing import Dict, FrozenSet, Optional, Protocol, Tuple

from solution1.models import RawDocumentTree

# (RawRow.row_index, position in RawRow.values)
CellKey = Tuple[int, int]

# Per-cell verification statuses a verifier may report (all optional; v1 reports none).
EXACT = "exact"                              # sits in an equation that balances exactly
ROUNDING = "rounding"                        # balances only through rounding slack (weak)
UNVERIFIED = "unverified"                    # no equation covers it
FAILED = "failed"                            # covered by an equation that does not balance
STRUCTURE_AMBIGUOUS = "structure_ambiguous"  # balances only under an alternative structure
CONFLICT = "conflict"                        # independent readers disagree

# Statuses meaning "the verifier does NOT vouch for this cell".
NOT_VOUCHED = frozenset({UNVERIFIED, FAILED, STRUCTURE_AMBIGUOUS, CONFLICT})


@dataclass
class Verdict:
    # True when the verifier does not vouch for the document (v1: not is_valid).
    flagged: bool
    # What the verifier found wrong, as comparable keys. Used for *differential*
    # detection: a corrupted document only counts as caught if its signature differs
    # from the clean document's (so a verifier that already false-alarms on the clean
    # document does not get credit for flagging every mutation of it).
    signature: FrozenSet[Tuple] = frozenset()
    # Number of equations/checks that were actually evaluated (not merely counted).
    evaluated_equations: Optional[int] = None
    # Fraction of numeric cells covered by a balanced check, if the verifier knows it.
    coverage: Optional[float] = None
    # Optional per-cell status. When present the harness scores corrupted cells directly.
    cell_status: Optional[Dict[CellKey, str]] = None
    crashed: bool = False
    notes: Tuple[str, ...] = ()


class Verifier(Protocol):
    name: str

    def verify(self, raw: RawDocumentTree) -> Verdict: ...
