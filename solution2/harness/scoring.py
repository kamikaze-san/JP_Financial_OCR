"""
Scoring: turns (mutation, verdict, clean verdict) into an outcome, then aggregates.

Outcomes
  caught           verifier flagged the document and its findings changed vs the clean document
  not_vouched      not flagged, but it reports the corrupted cell(s) as unverified/failed/...
  weak             corrupted cell labelled 'rounding' (vouched, but only through slack)
  silent           corruption went through with the verifier vouching for it
  surfaced         (no_degrade) verifier lost coverage but reports it at cell level
  silent_degraded  (no_degrade) verifier quietly checks less than before and still vouches
  benign           (no_degrade) verdict unchanged: noise was absorbed
  ok / false_alarm (invariant) verdict unchanged / changed on an equivalent re-spelling
  crash            the verifier raised
"""
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Dict, List

from .contract import NOT_VOUCHED, ROUNDING, Verdict
from .mutations import Mutation

# outcomes that count as a failure for gating, per kind
BAD = {
    "detect": {"silent", "weak", "crash"},
    "detect_small": {"crash"},
    "gap": {"crash"},
    "no_degrade": {"silent_degraded", "crash"},
    "flag": {"silent", "crash"},
    "invariant": {"false_alarm", "crash"},
}
DETECTED = {"caught", "not_vouched"}


@dataclass
class Record:
    doc: str
    cls: str
    kind: str
    outcome: str
    desc: str
    meta: Dict


def _unvouched(v: Verdict) -> int:
    return sum(1 for s in v.cell_status.values() if s in NOT_VOUCHED) if v.cell_status is not None else 0


def degraded(v: Verdict, clean: Verdict) -> bool:
    """Did the verifier quietly check less? Coverage is authoritative when reported: equation counts
    drop whenever a row is legitimately removed, even though every remaining cell is still verified.
    Without coverage (v1) the equation count is the only signal there is."""
    if v.coverage is not None and clean.coverage is not None:
        return v.coverage < clean.coverage - 1e-9
    return (v.evaluated_equations is not None and clean.evaluated_equations is not None
            and v.evaluated_equations < clean.evaluated_equations)


def surfaced(v: Verdict, clean: Verdict) -> bool:
    return (v.cell_status is not None and clean.cell_status is not None
            and _unvouched(v) > _unvouched(clean))


def score(m: Mutation, v: Verdict, clean: Verdict) -> str:
    if v.crashed:
        return "crash"
    changed = v.flagged and v.signature != clean.signature
    if m.kind in ("detect", "detect_small", "gap"):
        if changed:
            return "caught"
        statuses = [v.cell_status.get(t) for t in m.targets] if v.cell_status is not None else []
        if any(s in NOT_VOUCHED for s in statuses):
            return "not_vouched"
        if not m.targets and surfaced(v, clean):
            return "not_vouched"
        if any(s == ROUNDING for s in statuses):
            return "weak"
        return "silent"
    if m.kind == "no_degrade":
        if changed:
            return "caught"
        if surfaced(v, clean):
            return "surfaced"
        return "silent_degraded" if degraded(v, clean) else "benign"
    if m.kind == "flag":
        return "caught" if v.flagged else "silent"
    if m.kind == "invariant":
        same = v.flagged == clean.flagged and v.signature == clean.signature and not degraded(v, clean)
        return "ok" if same else "false_alarm"
    raise ValueError(m.kind)


def aggregate(records: List[Record]) -> Dict[str, Dict]:
    """class -> {kind, n, outcomes, rate, bad}"""
    by_cls: Dict[str, List[Record]] = defaultdict(list)
    for r in records:
        by_cls[r.cls].append(r)
    out = {}
    for cls, rs in by_cls.items():
        # a class can mix detect / detect_small (split by magnitude), so aggregate per kind
        by_kind = {}
        for kind in sorted({r.kind for r in rs}):
            sub = [r for r in rs if r.kind == kind]
            oc = Counter(r.outcome for r in sub)
            bad = sum(oc[o] for o in BAD[kind])
            if kind in ("detect", "detect_small", "gap", "flag"):
                good = sum(oc[o] for o in DETECTED)       # share caught
            else:
                good = len(sub) - bad                     # share that behaved
            by_kind[kind] = {"n": len(sub), "outcomes": dict(oc), "bad": bad, "rate": good / len(sub)}
        out[cls] = {"n": len(rs), "by_kind": by_kind}
    return out


def by_position(records: List[Record]) -> Dict[int, Counter]:
    res: Dict[int, Counter] = defaultdict(Counter)
    for r in records:
        if r.cls == "digit_sub":
            res[min(r.meta["pos_from_right"], 6)][r.outcome] += 1
    return res


def by_magnitude(records: List[Record]) -> Dict[str, Counter]:
    res: Dict[str, Counter] = defaultdict(Counter)
    for r in records:
        if r.cls != "digit_sub":
            continue
        d = r.meta["abs_delta"]
        label = "1-9" if d < 10 else "10-99" if d < 100 else "100-999" if d < 1000 else "1000+"
        res[label][r.outcome] += 1
    return res
