"""
Harness self-test: proves the harness itself is sound before it is used to judge a verifier.

  * every mutation really changes the document (no silent no-ops)
  * `oracle` (knows the truth)     -> every corruption caught, every equivalent form accepted
  * `always_pass` (vouches for all) -> catches nothing, accepts every equivalent form
  * `always_flag` (vouches for none)-> false alarm on every clean document
"""
from typing import Dict, List

from solution1.models import RawDocumentTree

from .adapters import AlwaysFlagVerifier, AlwaysPassVerifier, OracleVerifier
from .engine import evaluate_gate, run_harness
from .mutations import MutationConfig, generate
from .scoring import DETECTED, aggregate


def run_selftest(corpus: Dict[str, RawDocumentTree], stride: int = 3) -> List[str]:
    cfg = MutationConfig(stride=stride)
    failures: List[str] = []

    # 1. no-op mutations
    for doc, raw in corpus.items():
        clean = raw.model_dump()
        for m in generate(doc, raw, cfg):
            if m.raw.model_dump() == clean:
                failures.append(f"no-op mutation: {doc}/{m.cls}: {m.desc}")

    # 2. oracle must score perfectly
    oracle = run_harness(OracleVerifier(corpus), corpus, cfg)
    failures += [f"oracle: {p}" for p in evaluate_gate(oracle)]
    for cls, agg in aggregate(oracle.records).items():
        for kind, k in agg["by_kind"].items():
            if kind in ("detect", "detect_small", "gap", "flag") and k["rate"] < 1.0:
                failures.append(f"oracle missed {cls} [{kind}]: {k['outcomes']}")

    # 3. always_pass must catch nothing and never false-alarm
    ap = run_harness(AlwaysPassVerifier(), corpus, cfg)
    for r in ap.records:
        if r.outcome in DETECTED:
            failures.append(f"always_pass detected {r.cls}: {r.desc}")
            break
    if not any(r.kind == "invariant" and r.outcome == "ok" for r in ap.records):
        failures.append("always_pass: no invariant mutations were exercised")
    if any(r.kind == "invariant" and r.outcome != "ok" for r in ap.records):
        failures.append("always_pass failed an invariance check")

    # 4. always_flag must false-alarm on every clean document
    af = run_harness(AlwaysFlagVerifier(), corpus, cfg, classes=["degenerate"])
    if not all(v.flagged for v in af.clean.values()):
        failures.append("always_flag did not flag a clean document")
    if not any("FALSE ALARM" in p for p in evaluate_gate(af)):
        failures.append("gate did not report false alarms for always_flag")

    # 5. every class produced mutations on at least one document
    produced = {r.cls for r in oracle.records}
    from .mutations import OPERATORS
    for cls, _ in OPERATORS:
        if cls not in produced:
            failures.append(f"class {cls!r} generated no mutations on any corpus document")
    return failures
