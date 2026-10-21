"""pytest entry point:  python -m pytest solution2/harness/tests -q"""
import pytest

from solution2.harness.corpus import load_corpus
from solution2.harness.mutations import MutationConfig, generate, parse_val
from solution2.harness.selftest import run_selftest


@pytest.fixture(scope="module")
def corpus():
    return load_corpus()


def test_harness_selftest(corpus):
    assert run_selftest(corpus, stride=3) == []


def test_every_digit_substitution_is_enumerated(corpus):
    raw = corpus["baseline"]
    digit_cells = sum(sum(ch.isdigit() for ch in s) for r in raw.rows for s in r.values if parse_val(s))
    muts = [m for m in generate("baseline", raw, MutationConfig(), classes=["digit_sub"])]
    # 9 substitutions per digit, minus the forbidden leading-zero ones
    assert 8 * digit_cells < len(muts) <= 9 * digit_cells


def test_parse_val_handles_triangles_and_commas():
    assert parse_val("△1,923,585") == -1923585.0
    assert parse_val("2,002,429") == 2002429.0
    assert parse_val("－") is None
    assert parse_val("") is None
