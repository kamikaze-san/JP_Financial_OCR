import pytest

from solution2.verify.parsing import BAD, BLANK, NIL, NUMBER, clean_label, parse_cell, parse_date, parse_unit


@pytest.mark.parametrize("raw,value", [
    ("2,002,429", 2002429), ("△1,923,585", -1923585), ("▲174", -174), ("(174)", -174),
    ("−174", -174), ("-174", -174), ("１，２３４", 1234), (" 4,048 ", 4048), ("△0", 0), ("0", 0),
])
def test_numbers(raw, value):
    c = parse_cell(raw)
    assert (c.kind, c.value) == (NUMBER, value)


@pytest.mark.parametrize("raw", ["－", "-", "―", "ー", "—", "−"])
def test_nil_dashes(raw):
    assert parse_cell(raw).kind == NIL and parse_cell(raw).value == 0


def test_blank():
    assert parse_cell("").kind == BLANK and parse_cell("   ").value is None


@pytest.mark.parametrize("raw", ["4,048※1", "4.048", "2,02,429", "2,O02", "N/A", "△", "12 abc", "1,23"])
def test_nothing_is_silently_zeroed(raw):
    c = parse_cell(raw)
    assert c.kind == BAD and c.value is None and c.reason


@pytest.mark.parametrize("raw,mult", [
    ("（単位：千円）", 1_000), ("(単位：百万円)", 1_000_000), ("単位：円", 1), ("（単位：万円）", 10_000),
    ("（単位：億円）", 100_000_000), ("（単位：十億円）", 10**9),
])
def test_units(raw, mult):
    assert parse_unit(raw) == mult


def test_unknown_unit_is_not_defaulted():
    assert parse_unit("") is None and parse_unit("US$ thousands") is None


@pytest.mark.parametrize("raw,iso", [
    ("2026年3月31日", "2026-03-31"), ("2025 年３月 31 日", "2025-03-31"), ("令和8年3月31日", "2026-03-31"),
    ("令和元年5月1日", "2019-05-01"), ("平成30年3月31日", "2018-03-31"), ("昭和63年3月31日", "1988-03-31"),
])
def test_dates(raw, iso):
    assert parse_date(raw) == iso


@pytest.mark.parametrize("raw", ["", "FY2026", "当連結会計年度末", "2026年13月31日", "2026年2月30日"])
def test_unresolved_date_is_not_defaulted(raw):
    assert parse_date(raw) is None


def test_label_cleaning():
    assert clean_label("流动资产合计") == ("流動資産合計", True)
    assert clean_label("流動資産 合計") == ("流動資産合計", False)
    assert clean_label("流動資産合計※1") == ("流動資産合計", False)
    assert clean_label("1年内返済予定の長期借入金")[0] == "1年内返済予定の長期借入金"


def test_tolerant_loader_reports_its_repairs():
    from solution2.verify.loader import parse_stage1
    text = ('```json\n{"metadata": {"title": "t", "unit_raw": "（単位：千円）", "periods": ['
            '{"col_index": 1, "raw_label": "a", "raw_date": "2026年3月31日"}]}, "rows": ['
            '{"row_index": 1, "indent_level": 0, "account_raw": "資産の部", "role": "section_header"},'
            '{"row_index": 2, "indent_level": 1, "account_raw": "現金", "role": "item", "values": [1234]}]}\n```')
    raw, notes = parse_stage1(text)
    assert raw.rows[0].values == [] and raw.rows[1].values == ["1234"]
    assert len(notes) == 2
    with pytest.raises(ValueError):
        parse_stage1("not json at all")


def test_a_footnote_reference_next_to_a_number_is_not_part_of_the_value():
    from solution2.verify.parsing import NUMBER, BAD, parse_cell
    for raw, value in [("※1 110,588", 110588), ("※3 △17,221", -17221), ("△17,221 ※3", -17221), ("110,588 ※1", 110588), ("※ 5", 5), ("*2 9,000", 9000)]:
        cell = parse_cell(raw)
        assert cell.kind == NUMBER and cell.value == value, raw
    assert parse_cell("※1110,588").kind == BAD            # no space: the footnote digit cannot be told from the number
    assert parse_cell("※1").kind == BAD
