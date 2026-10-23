import numpy as np
import pytest
from PIL import Image, ImageDraw

from solution2.bands import band_plan, build_tree, stitch, text_lines
from solution2.repair.reader import ReadRow


def page_with_lines(n, pitch=40, height=24, width=800, rule=False):
    img = Image.new("L", (width, n * pitch + 100), 255)
    d = ImageDraw.Draw(img)
    for i in range(n):
        y = 50 + i * pitch
        d.rectangle((60, y, 360, y + height), fill=0)           # a text line (a solid block is enough for the profile)
        d.rectangle((560, y, 700, y + height), fill=0)
    if rule:
        d.line((30, 0, 30, img.height), fill=0, width=3)        # a vertical table rule through every line
    return np.asarray(img)


def test_each_text_line_is_found_and_vertical_rules_are_ignored():
    assert len(text_lines(page_with_lines(17))) == 17
    assert len(text_lines(page_with_lines(17, rule=True))) == 17


def test_band_plan_overlaps_and_covers_every_line():
    plan = band_plan(30, per_band=12, overlap=2)
    assert plan[0] == (0, 11) and plan[1][0] == 10 and plan[-1][1] == 29
    covered = {i for a, b in plan for i in range(a, b + 1)}
    assert covered == set(range(30))
    assert band_plan(5) == [(0, 4)] and band_plan(0) == []


def rows(*labels):
    return [ReadRow(l, ["1", "2"]) for l in labels]


def test_stitch_drops_the_rows_a_band_repeats():
    out = stitch([rows("a", "b", "c", "d"), rows("c", "d", "e", "f"), rows("f", "g")])
    assert [r.label for r in out] == list("abcdefg")


def test_stitch_keeps_rows_when_nothing_overlaps():
    assert [r.label for r in stitch([rows("a", "b"), rows("c", "d")])] == list("abcd")


def test_build_tree_uses_the_metadata_and_marks_headings():
    tree = build_tree({"unit": "単位：千円", "columns": [{"label": "前期", "date": "(2025年3月31日)"}, {"label": "当期", "date": "(2026年3月31日)"}]},
                      [ReadRow("資産の部", []), ReadRow("現金", ["100", "110"]), ReadRow("資産合計", ["100", "110"])])
    assert tree.metadata.unit_raw == "単位：千円" and [p.raw_date for p in tree.metadata.periods] == ["(2025年3月31日)", "(2026年3月31日)"]
    assert [r.role for r in tree.rows] == ["group_header", "item", "subtotal"] and tree.rows[0].values == []


def test_no_numeric_rows_is_an_error():
    with pytest.raises(ValueError):
        build_tree({}, [ReadRow("資産の部", [])])


def test_a_failed_band_is_read_in_halves_instead_of_losing_the_page():
    from solution2.bands import read_with_split
    calls = []

    def read(a, b):
        calls.append((a, b))
        if b - a + 1 > 8:                                    # the model fails on anything longer than 8 lines
            return None
        return [ReadRow(f"line{i}", ["1", "2"]) for i in range(a, b + 1)]

    rows = read_with_split(read, 0, 11)
    assert [r.label for r in rows] == [f"line{i}" for i in range(12)]          # the shared line is not duplicated
    assert calls[0] == (0, 11) and len(calls) == 3
    assert read_with_split(lambda a, b: None, 0, 5) is None                     # too small to split: unreadable


def test_padding_values_beyond_the_numeric_columns_are_trimmed():
    tree = build_tree({"columns": [{"label": "a", "date": ""}, {"label": "b", "date": ""}]},
                      [ReadRow("現金", ["1", "2", "", ""]), ReadRow("計", ["1", "2", "", ""])])
    assert all(len(r.values) == 2 for r in tree.rows)


def test_markdown_band_rows_drop_column_headings_and_keep_headings_as_rows():
    from solution2.formats import rows_from_markdown
    text = ("```markdown\n|  | 前連結会計年度 (2026年2月28日) | 当中間連結会計期間 (2026年8月31日) |\n|---|---|---|\n"
            "| **流動資産** |  |  |\n| 現金及び預金 | 1,000 | 1,100 | | |\n| 流動資産合計 | 1,000 | 1,100 |\n```")
    rows = rows_from_markdown(text, 2)
    assert [(r.label, r.values) for r in rows] == [("流動資産", []), ("現金及び預金", ["1,000", "1,100"]), ("流動資産合計", ["1,000", "1,100"])]


def test_markdown_band_with_digits_but_no_table_is_a_failed_read():
    from solution2.formats import rows_from_markdown
    assert rows_from_markdown("現金 1,000 1,100", 2) is None
    assert rows_from_markdown("資産の部", 2) == []
