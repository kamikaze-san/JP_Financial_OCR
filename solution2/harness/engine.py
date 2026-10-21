"""Runs a verifier over every mutation of every clean document and evaluates the gate."""
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from solution1.models import RawDocumentTree

from .contract import Verdict, Verifier
from .mutations import MutationConfig, generate
from .scoring import BAD, Record, aggregate, score

GATED_KINDS = ("detect", "no_degrade", "flag", "invariant")


@dataclass
class Results:
    verifier: str
    cfg: MutationConfig
    clean: Dict[str, Verdict]
    records: List[Record] = field(default_factory=list)
    seconds: float = 0.0


def run_harness(verifier: Verifier, corpus: Dict[str, RawDocumentTree], cfg: MutationConfig,
                classes: Optional[List[str]] = None, docs: Optional[List[str]] = None,
                progress: Optional[Callable[[str], None]] = None) -> Results:
    t0 = time.time()
    names = [d for d in corpus if not docs or d in docs]
    results = Results(verifier=getattr(verifier, "name", type(verifier).__name__), cfg=cfg,
                      clean={d: verifier.verify(corpus[d]) for d in names})
    for d in names:
        n_before = len(results.records)
        for m in generate(d, corpus[d], cfg, classes):
            outcome = score(m, verifier.verify(m.raw), results.clean[d])
            results.records.append(Record(d, m.cls, m.kind, outcome, m.desc, {**m.meta, "delta": m.delta}))
        if progress:
            progress(f"  {d}: {len(results.records) - n_before} mutations")
    results.seconds = time.time() - t0
    return results


def evaluate_gate(results: Results) -> List[str]:
    """Violations that should fail CI. Gaps and small-delta digit errors are informational."""
    problems = []
    for doc, v in results.clean.items():
        if v.crashed:
            problems.append(f"clean document {doc!r}: verifier crashed {v.notes}")
        elif v.flagged:
            problems.append(f"FALSE ALARM on clean document {doc!r} ({len(v.signature)} finding(s))")
    for cls, agg in aggregate(results.records).items():
        for kind, k in agg["by_kind"].items():
            if kind in GATED_KINDS and k["bad"]:
                bad = {o: c for o, c in k["outcomes"].items() if o in BAD[kind]}
                problems.append(f"{cls} [{kind}]: {k['bad']}/{k['n']} bad {bad}")
    return problems
