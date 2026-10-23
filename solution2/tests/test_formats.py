import pytest

from solution1.models import RawDocumentTree
from solution2.formats import compare_numeric_rows, parse_table_text
from solution2.verify import verify_document

HTML = ("<html><body><p>決算短信</p><p>(単位：千円)</p><table><thead><tr><th>前連結会計年度</th><th>当連結会計年度</th></tr>"
        "<tr><th>(2025年3月31日)</th><th>(2026年3月31日)</th></tr></thead><tbody>"
        "<tr><td>資産の部</td><td></td></tr><tr><td>流動資産</td><td></td></tr>"
        "<tr><td>現金及び預金</td><td>1,000</td><td>1,100</td></tr>"
        "<tr><td>売掛金</td><td>500</td><td>－</td></tr>"
        "<tr><td>流動資産合計</td><td>1,500</td><td>1,100</td></tr></tbody></table></body></html>")

MARKDOWN = """```markdown
(単位：千円)

|  | 前連結会計年度 (2025年3月31日) | 当連結会計年度 (2026年3月31日) |
|---|---|---|
| **資産の部** |  |  |
| **流動資産** |  |  |
| 現金及び預金 | 1,000 | 1,100 |
| 売掛金 | 500 | - |
| 流動資産合計 | 1,500 | 1,100 |
```"""


@pytest.mark.parametrize("text", [HTML, MARKDOWN], ids=["html", "markdown"])
def test_table_text_becomes_a_verified_stage1_tree(text):
    tree = parse_table_text(text)
    assert tree.metadata.unit_raw.replace("(", "").replace(")", "") == "単位：千円"
    assert [p.raw_date for p in tree.metadata.periods] == ["(2025年3月31日)", "(2026年3月31日)"]
    labels = [(r.account_raw, r.values) for r in tree.rows]
    assert labels[0] == ("資産の部", []) and labels[1] == ("流動資産", [])          # headings are rows without values, not metadata
    assert ("現金及び預金", ["1,000", "1,100"]) in labels
    assert not [f for f in verify_document(tree).findings if f.kind == "equation_failed"]      # 流動資産合計 balances (a half statement is not trusted as a whole)


def test_no_table_is_an_error():
    with pytest.raises(ValueError):
        parse_table_text("<html><body><div class='image' data-bbox='0 0 1000 1000'></div></body></html>")


def test_row_comparison_ignores_structure_and_sees_a_shift():
    gold = parse_table_text(HTML)
    assert compare_numeric_rows(parse_table_text(MARKDOWN), gold) == {"gold_rows": 3, "exact": 3, "misplaced": 0, "missing": 0, "extra": 0}
    shifted = RawDocumentTree.model_validate_json(gold.model_dump_json())
    shifted.rows[2].account_raw, shifted.rows[3].account_raw = shifted.rows[3].account_raw, shifted.rows[2].account_raw
    res = compare_numeric_rows(shifted, gold)
    assert res["exact"] == 1 and res["misplaced"] == 2
