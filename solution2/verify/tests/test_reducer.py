import copy
from pathlib import Path

import pytest

from solution1.vlm_interface import parse_vlm_response
from solution2.harness.corpus import load_corpus
from solution2.verify import verify_document

ROOT = Path(__file__).resolve().parents[3]
QWEN = ROOT / "benchmark_results" / "qwen3-vl_4b-instruct"


@pytest.fixture(scope="module")
def corpus():
    return load_corpus()


def row(doc, label, nth=0):
    return [r for r in doc.rows if r.account_raw == label][nth]


@pytest.mark.parametrize("name", ["baseline", "calbee", "yamato"])
def test_clean_documents_are_fully_verified(corpus, name):
    v = verify_document(corpus[name])
    assert v.trusted and v.coverage == 1.0 and not v.errors


def test_balance_tie_and_grand_totals_are_found(corpus):
    kinds = [(e.kind, e.total[1]) for e in verify_document(corpus["calbee"]).equations]
    assert ("tie", "負債純資産合計") in kinds and ("sum", "負債純資産合計") in kinds


def test_total_then_children_layout(corpus):
    names = [e.total[1] for e in verify_document(corpus["yamato"]).equations]
    assert "流動資産" in names and "資産合計" in names


def test_error_is_localised_to_its_section(corpus):
    doc = copy.deepcopy(corpus["baseline"])
    row(doc, "受取手形").values[0] = "1,020,659"
    v = verify_document(doc)
    assert not v.trusted
    by_index = {r.row_index: r.account_raw for r in doc.rows}
    failed = {by_index[idx] for (idx, c), s in v.cell_status.items() if s == "failed"}
    # the window that does not add up is blamed (the culprit is among its leaves) ...
    assert "受取手形" in failed and "現金及び預金" in failed
    # ... but its total is corroborated by the level above, and nothing outside the section is blamed
    assert "流動資産合計" not in failed and "資産合計" not in failed and "有形固定資産合計" not in failed
    assert [f.message.split("'")[1] for f in v.errors if f.kind == "equation_failed"] == ["流動資産合計"]


def test_dropped_sign_on_accumulated_depreciation_is_not_absorbed(corpus):
    doc = copy.deepcopy(corpus["baseline"])
    row(doc, "減価償却累計額").values[0] = "1,923,585"      # v1 silently accepted this
    assert not verify_document(doc).trusted


def test_dash_for_number_is_caught(corpus):
    doc = copy.deepcopy(corpus["calbee"])
    row(doc, "建設仮勘定").values[0] = "－"
    assert not verify_document(doc).trusted


def test_fail_open_cases(corpus):
    doc = copy.deepcopy(corpus["baseline"])
    doc.rows = []
    assert not verify_document(doc).trusted                 # zero rows must never be vouched for
    doc = copy.deepcopy(corpus["baseline"])
    row(doc, "流動資産合計").account_raw = "流動資産計"
    assert verify_document(doc).trusted                      # alternate total label still verified
    doc = copy.deepcopy(corpus["baseline"])
    for r in doc.rows:
        r.account_raw += "※1"
    assert verify_document(doc).trusted                      # footnotes on every label


def test_chinese_glyphs_are_repaired_and_reported(corpus):
    doc = copy.deepcopy(corpus["baseline"])
    row(doc, "流動資産合計").account_raw = "流动资产合计"
    v = verify_document(doc)
    assert v.trusted and any(f.kind == "glyph_repaired" for f in v.findings)


def test_unit_and_dates_are_never_defaulted(corpus):
    doc = copy.deepcopy(corpus["baseline"])
    doc.metadata.unit_raw = "???"
    assert {f.kind for f in verify_document(doc).errors} == {"unit_unresolved"}
    doc = copy.deepcopy(corpus["baseline"])
    doc.metadata.periods[0].raw_date = "前期"
    assert "date_unresolved" in {f.kind for f in verify_document(doc).errors}


def test_missing_total_row_is_surfaced(corpus):
    doc = copy.deepcopy(corpus["calbee"])
    doc.rows = [r for r in doc.rows if r.account_raw != "資産合計"]
    v = verify_document(doc)
    assert any(s == "structure_ambiguous" for s in v.cell_status.values())


def with_titles(doc, titles):
    rows = [r.model_copy(update={"row_index": r.row_index + len(titles)}) for r in doc.rows]
    heads = [rows[0].model_copy(update={"row_index": i + 1, "indent_level": 0, "role": "section_header",
                                        "account_raw": t, "values": []}) for i, t in enumerate(titles)]
    return doc.model_copy(update={"rows": heads + rows})


@pytest.mark.parametrize("name", ["calbee"])
def test_statement_titles_above_the_table_do_not_change_the_verdict(corpus, name):
    plain = verify_document(corpus[name])
    titled = verify_document(with_titles(corpus[name], ["3．連結財務諸表及び主な注記", "(1) 連結財政状態計算書"]))
    assert titled.trusted == plain.trusted and titled.coverage == plain.coverage
    assert not any(s == "structure_ambiguous" for s in titled.cell_status.values())


def test_a_title_does_not_hide_a_dropped_total(corpus):
    doc = copy.deepcopy(corpus["calbee"])
    doc.rows = [r for r in doc.rows if r.account_raw != "資産合計"]
    v = verify_document(with_titles(doc, ["(1) 連結財政状態計算書"]))
    assert any(s == "structure_ambiguous" for s in v.cell_status.values())


# ---- real VLM output saved by the earlier benchmarks (indentation and roles as the model wrote them)
def _real(name):
    path = QWEN / name
    if not path.exists() or not path.read_text(encoding="utf-8").strip():
        pytest.skip(f"{name} not available")
    return parse_vlm_response(path.read_text(encoding="utf-8"))


def test_real_correct_output_is_trusted():
    for name in ("pass1_200dpi_output.json", "pass2_targeted_output.json"):
        assert verify_document(_real(name)).trusted


def test_real_missed_row_is_flagged_and_localised():
    v = verify_document(_real("pass1_raw_output.json"))          # 建設仮勘定 4,553 read as '－'
    assert not v.trusted
    bad = [f for f in v.errors if f.kind == "equation_failed"]
    assert any("有形固定資産合計" in f.message and "-4,556" in f.message for f in bad)


def test_real_fabricated_value_is_flagged():
    v = verify_document(_real("pass2_rerun_clean_feedback.json"))    # 44,556 invented for 建設仮勘定
    assert not v.trusted
