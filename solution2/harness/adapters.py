"""
Verifier adapters.

  v1           the existing solution1 normalizer + AccountingValidator (the baseline to beat)
  oracle       flags iff the document is semantically different from a clean one (harness self-test)
  always_pass  vouches for everything (harness self-test: must score ~0 detection)
  always_flag  vouches for nothing   (harness self-test: must show 100% false alarms)
  pkg.mod:Cls  any class with a no-arg constructor implementing the Verifier protocol
"""
import importlib
from typing import Dict

from solution1.models import RawDocumentTree
from solution1.normalizer import normalize_tree
from solution1.validator import AccountingValidator

from .contract import Verdict


class V1Verifier:
    """Wraps solution1 as-is. It only reports document-level pass/fail, no per-cell status."""
    name = "v1"

    def verify(self, raw: RawDocumentTree) -> Verdict:
        try:
            tree = normalize_tree(raw)
            report = AccountingValidator().validate(tree)
        except Exception as exc:  # a crash is neither a pass nor a clean detection
            return Verdict(flagged=True, crashed=True,
                           signature=frozenset({("crash", type(exc).__name__)}),
                           notes=(repr(exc)[:200],))
        signature = frozenset((f.equation_name, f.period, round(f.discrepancy, 2))
                              for f in report.failures)
        return Verdict(flagged=not report.is_valid,
                       signature=signature,
                       # v1 increments total_equations_tested before checking the equation
                       # exists, so passed+failed is the number actually evaluated.
                       evaluated_equations=report.passed_equations + report.failed_equations)


def _canonical(raw: RawDocumentTree):
    """Semantic content of a document, independent of both verifiers: strict value/unit/date
    parsing, labels compared as printed (NFKC + whitespace only) so label damage counts as a change."""
    import re
    import unicodedata
    from solution2.verify.parsing import parse_cell, parse_date, parse_unit
    return (
        parse_unit(raw.metadata.unit_raw),
        tuple(parse_date(p.raw_date) for p in raw.metadata.periods),
        tuple((re.sub(r"\s+", "", unicodedata.normalize("NFKC", r.account_raw)),
               tuple((c.kind, c.value) for c in map(parse_cell, r.values)))
              for i, r in enumerate(raw.rows) if not _is_echo(raw.rows, i)),
    )


def _is_echo(rows, i) -> bool:
    """A value-less row repeating the label of the valued row right after it is the same table (an echoed heading)."""
    from solution2.verify.parsing import clean_label
    nxt = rows[i + 1] if i + 1 < len(rows) else None
    return bool(nxt and not any(v.strip() for v in rows[i].values) and any(v.strip() for v in nxt.values)
                and clean_label(nxt.account_raw)[0] == clean_label(rows[i].account_raw)[0])


class OracleVerifier:
    """Flags iff the document differs semantically from every clean corpus document."""
    name = "oracle"

    def __init__(self, corpus: Dict[str, RawDocumentTree]):
        self._clean = {_canonical(d) for d in corpus.values()}

    def verify(self, raw: RawDocumentTree) -> Verdict:
        try:
            canon = _canonical(raw)
        except Exception as exc:
            return Verdict(flagged=True, crashed=True, signature=frozenset({("crash",)}),
                           notes=(repr(exc)[:200],))
        if canon in self._clean:
            return Verdict(flagged=False)
        return Verdict(flagged=True, signature=frozenset({("oracle", hash(canon))}))


class V2Verifier:
    """solution2's hierarchy-driven verifier with per-cell verdicts."""
    name = "v2"

    def __init__(self, config=None):
        self.config = config

    def verify(self, raw: RawDocumentTree) -> Verdict:
        from solution2.verify import verify_document
        try:
            v = verify_document(raw, self.config)
        except Exception as exc:
            return Verdict(flagged=True, crashed=True, signature=frozenset({("crash", type(exc).__name__)}),
                           notes=(repr(exc)[:200],))
        return Verdict(
            flagged=not v.trusted,
            signature=frozenset(f.key for f in v.errors),
            evaluated_equations=v.equations_evaluated,
            coverage=v.coverage,
            cell_status=dict(v.cell_status),
            notes=tuple(f.message for f in v.findings if f.severity == "warning")[:5],
        )


class AlwaysPassVerifier:
    name = "always_pass"

    def verify(self, raw: RawDocumentTree) -> Verdict:
        return Verdict(flagged=False)


class AlwaysFlagVerifier:
    name = "always_flag"

    def verify(self, raw: RawDocumentTree) -> Verdict:
        return Verdict(flagged=True, signature=frozenset({("always",)}))


def make_verifier(spec: str, corpus: Dict[str, RawDocumentTree]):
    if spec == "v1":
        return V1Verifier()
    if spec == "v2":
        return V2Verifier()
    if spec == "oracle":
        return OracleVerifier(corpus)
    if spec == "always_pass":
        return AlwaysPassVerifier()
    if spec == "always_flag":
        return AlwaysFlagVerifier()
    module_name, _, class_name = spec.partition(":")
    if not class_name:
        raise ValueError(f"Unknown verifier {spec!r}; use v1|oracle|always_pass|always_flag|pkg.mod:Class")
    return getattr(importlib.import_module(module_name), class_name)()
