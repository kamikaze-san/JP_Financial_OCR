"""
The retry loop, tested with scripted readers over the real baseline PDF (no GPU).

`TextLayerReader` stands in for a perfect VLM; wrappers below make it noisy, wrong, or inconsistent.
"""
import copy
import sys
import types
from pathlib import Path

import pytest

from solution1.models import RawDocumentTree
from solution2.harness.corpus import ROOT, load_corpus
from solution2.repair import PdfPage, RepairConfig, TextLayerReader, build_prompt, repair
from solution2.repair.reader import Crop, OllamaReader, ReadRow
from solution2.verify import parse_stage1, verify_document
from solution2.verify.parsing import parse_cell

PDF = ROOT / "balance_sheet_after_correction.pdf"
pytestmark = pytest.mark.skipif(not PDF.exists(), reason="baseline PDF not available")


@pytest.fixture(scope="module")
def gold():
    return load_corpus()["baseline"]


@pytest.fixture()
def page():
    return PdfPage(PDF, 0)


def row(doc, label, nth=0):
    return [r for r in doc.rows if r.account_raw == label][nth]


def cell(r, c):
    return (parse_cell(r.values[c]).kind, parse_cell(r.values[c]).value) if c < len(r.values) else None


def same_numbers(a: RawDocumentTree, b: RawDocumentTree) -> bool:
    return len(a.rows) == len(b.rows) and all(
        [cell(x, c) for c in range(2)] == [cell(y, c) for c in range(2)] for x, y in zip(a.rows, b.rows))


class Scripted:
    """A reader that wraps another and rewrites what it returns, read by read."""
    def __init__(self, inner, transform):
        self.inner, self.transform, self.calls = inner, transform, 0

    def read(self, crop, ncols):
        rows = self.inner.read(crop, ncols)
        k, self.calls = self.calls, self.calls + 1
        return self.transform(k, rows)


def bump_first_number(rows):
    out = [ReadRow(r.label, list(r.values)) for r in rows]
    for r in out:
        if any(v for v in r.values):
            r.values[0] = "9,999"
            break
    return out


# ----------------------------------------------------------------------------- repairs
def test_missed_value_is_repaired_and_only_that_cell_changes(gold, page):
    doc = copy.deepcopy(gold)
    row(doc, "建設仮勘定").values[1] = "－"
    assert not verify_document(doc).trusted
    res = repair(doc, page, TextLayerReader(page))
    assert res.status == "repaired" and res.verdict.trusted
    assert same_numbers(res.raw, gold)
    assert len(res.applied) == 1 and "建設仮勘定" in res.applied[0]


def test_errors_in_different_sections_are_both_repaired(gold, page):
    doc = copy.deepcopy(gold)
    row(doc, "受取手形").values[0] = "20,859"
    row(doc, "土地").values[1] = "705,408"
    res = repair(doc, page, TextLayerReader(page))
    assert res.status == "repaired" and same_numbers(res.raw, gold)
    assert len(res.applied) == 2


def test_one_bad_read_is_outvoted(gold, page):
    doc = copy.deepcopy(gold)
    row(doc, "受取手形").values[0] = "20,859"
    noisy = Scripted(TextLayerReader(page), lambda k, rows: bump_first_number(rows) if k == 1 else rows)
    res = repair(doc, page, noisy)
    assert res.status == "repaired" and same_numbers(res.raw, gold)


def test_omitted_row_is_recovered(gold, page):
    doc = copy.deepcopy(gold)
    doc.rows = [r for r in doc.rows if r.account_raw != "建設仮勘定"]
    assert not verify_document(doc).trusted
    res = repair(doc, page, TextLayerReader(page))
    assert res.status == "repaired"
    inserted = [r for r in res.raw.rows if r.account_raw == "建設仮勘定"]
    assert len(inserted) == 1 and [cell(inserted[0], c) for c in range(2)] == [cell(row(gold, "建設仮勘定"), c) for c in range(2)]


def test_a_line_item_turned_into_a_heading_gets_its_numbers_back(gold, page):
    doc = copy.deepcopy(gold)
    r = row(doc, "建設仮勘定")
    r.role, r.values = "category_header", []                  # seen in real output: 有形固定資産 became a heading
    assert not verify_document(doc).trusted
    res = repair(doc, page, TextLayerReader(page))
    assert res.status == "repaired" and same_numbers(res.raw, gold)
    assert row(res.raw, "建設仮勘定").role == "item"


def test_duplicated_row_is_removed(gold, page):
    doc = copy.deepcopy(gold)
    i = [r.account_raw for r in doc.rows].index("土地")
    dup = copy.deepcopy(doc.rows[i])
    dup.row_index = 999
    doc.rows.insert(i + 1, dup)
    res = repair(doc, page, TextLayerReader(page))
    assert res.status == "repaired" and same_numbers(res.raw, gold)


# ----------------------------------------------------------------------------- it must not fabricate
def test_agreeing_but_wrong_reads_are_rejected_by_the_verifier(gold, page):
    doc = copy.deepcopy(gold)
    row(doc, "建設仮勘定").values[1] = "－"

    def always_9999(k, rows):
        return [ReadRow(r.label, ["9,999" if r.label == "建設仮勘定" and i == 1 else v for i, v in enumerate(r.values)])
                for r in rows]
    res = repair(doc, page, Scripted(TextLayerReader(page), always_9999))
    assert res.status == "needs_review" and res.raw == doc
    assert any(a.outcome == "rejected" for a in res.attempts)


def test_no_majority_means_no_change(gold, page):
    doc = copy.deepcopy(gold)
    row(doc, "建設仮勘定").values[1] = "－"

    def different_each_time(k, rows):
        return [ReadRow(r.label, [f"{k + 1}00" if r.label == "建設仮勘定" and i == 1 else v for i, v in enumerate(r.values)])
                for r in rows]
    res = repair(doc, page, Scripted(TextLayerReader(page), different_each_time))
    assert res.status == "needs_review" and res.raw == doc


def test_too_few_usable_reads_is_a_failure_not_a_guess(gold, page):
    doc = copy.deepcopy(gold)
    row(doc, "建設仮勘定").values[1] = "－"
    res = repair(doc, page, Scripted(TextLayerReader(page), lambda k, rows: rows if k == 0 else None))
    assert res.status == "needs_review" and res.raw == doc
    assert res.attempts[0].outcome == "reads_failed"


def test_only_untrusted_cells_in_the_failing_window_are_touched(gold, page):
    doc = copy.deepcopy(gold)
    row(doc, "現金及び預金").values[0] = "2,502,429"                 # fails the current-assets equation
    a, b = row(doc, "投資有価証券"), row(doc, "関係会社株式")
    a.values, b.values = b.values, a.values                        # wrong but invisible to arithmetic (vouched)
    res = repair(doc, page, TextLayerReader(page))                  # a perfect reader would 'fix' the swap too
    assert res.status == "repaired"
    assert cell(row(res.raw, "現金及び預金"), 0) == cell(row(gold, "現金及び預金"), 0)
    assert row(res.raw, "投資有価証券").values == a.values and row(res.raw, "関係会社株式").values == b.values
    assert len(res.applied) == 1


def test_page_that_is_too_broken_is_sent_back_not_repaired(gold, page):
    doc = copy.deepcopy(gold)
    for k, r in enumerate(doc.rows):                       # every number replaced: nothing is left to anchor on
        r.values = [f"{(k * 7919 + c * 104729) % 9000000 + 100000:,}" if v else v for c, v in enumerate(r.values)]
    res = repair(doc, page, TextLayerReader(page))
    assert res.status == "needs_review" and res.vlm_calls == 0 and res.attempts[0].outcome == "too_broken"


def test_rows_that_cannot_be_found_on_the_page_are_not_guessed(gold, page):
    doc = copy.deepcopy(gold)
    row(doc, "建設仮勘定").values[1] = "－"
    for r in doc.rows[15:31]:                                   # the VLM garbled the names in the failing section
        suffix = next((t for t in ("（純額）", "合計") if r.account_raw.endswith(t)), "")   # structure stays readable
        r.account_raw = "".join(chr(0x3041 + (ord(ch) % 40)) for ch in r.account_raw[: len(r.account_raw) - len(suffix)]) + suffix
    res = repair(doc, page, TextLayerReader(page))
    assert res.status == "needs_review" and res.vlm_calls == 0 and res.attempts[0].outcome == "no_region"


def test_reading_the_wrong_page_never_changes_the_document(gold):
    other = PdfPage(ROOT / "dataset/benchmark_suite/company_02_calbee/balance_sheet.pdf", 0)   # shares many labels
    doc = copy.deepcopy(gold)
    row(doc, "建設仮勘定").values[1] = "－"
    res = repair(doc, other, TextLayerReader(other))
    assert res.status == "needs_review" and res.raw == doc and not res.applied


def test_trusted_document_is_left_alone(gold, page):
    res = repair(gold, page, TextLayerReader(page))
    assert res.status == "already_trusted" and res.vlm_calls == 0 and res.raw is gold


# ----------------------------------------------------------------------------- leak-free and fresh
def test_prompt_depends_only_on_the_column_count():
    p2, p1 = build_prompt(2), build_prompt(1)
    assert p2 != p1
    assert not any(ch.isdigit() for ch in p2.replace("2", "")) and "△" in p2   # no numbers, only the column count


def test_reader_sends_one_fresh_single_image_turn_with_no_document_content(monkeypatch, tmp_path):
    sent = []
    fake = types.ModuleType("ollama")
    fake.chat = lambda **kw: sent.append(kw) or {"message": {"content": '{"rows": []}'}}
    monkeypatch.setitem(sys.modules, "ollama", fake)
    img = tmp_path / "crop.png"
    img.write_bytes(b"x")
    reader = OllamaReader("m")
    reader.read(Crop(img, (0, 0, 1, 1), 300), 2)
    reader.read(Crop(img, (5, 5, 9, 9), 400), 2)
    assert len(sent) == 2
    for kw in sent:
        assert len(kw["messages"]) == 1 and kw["messages"][0]["role"] == "user"       # no history to anchor on
        assert kw["messages"][0]["images"] == [str(img)]
        assert kw["messages"][0]["content"] == build_prompt(2)                      # identical, document-independent
        assert kw["options"]["temperature"] == 0.0


# ----------------------------------------------------------------------------- real saved outputs
def test_real_qwen_output_with_the_missed_row_is_repaired(page):
    path = ROOT / "benchmark_results/qwen3-vl_4b-instruct/pass1_raw_output.json"
    if not path.exists():
        pytest.skip("saved Qwen output not available")
    raw, _ = parse_stage1(path.read_text(encoding="utf-8"))
    res = repair(raw, page, TextLayerReader(page))
    assert res.status == "repaired" and res.verdict.trusted
    assert [a for a in res.applied] == ["'建設仮勘定' col 1: '－' -> '4,553' (3 reads)"]
