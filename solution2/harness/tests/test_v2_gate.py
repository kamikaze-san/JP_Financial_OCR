"""The harness gate is the acceptance test for the v2 verifier."""
from solution2.harness.adapters import V2Verifier
from solution2.harness.corpus import load_corpus
from solution2.harness.engine import evaluate_gate, run_harness
from solution2.harness.mutations import MutationConfig


def test_v2_passes_the_gate():
    results = run_harness(V2Verifier(), load_corpus(), MutationConfig(stride=4))
    assert evaluate_gate(results) == []
