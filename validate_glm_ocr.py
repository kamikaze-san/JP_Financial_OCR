import sys
from bs4 import BeautifulSoup

if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

from solution1.models import RawDocumentTree, RawMetadata, RawPeriod, RawRow
from solution1.normalizer import normalize_tree
from solution1.validator import AccountingValidator
from solution1.baseline_data import get_baseline_raw_tree

html_file = "benchmark_results/glm_ocr/table.html"
with open(html_file, "r", encoding="utf-8") as f:
    soup = BeautifulSoup(f.read(), "html.parser")

table = soup.find("table")
rows = table.find_all("tr")

# Header row
headers = [c.get_text(strip=True) for c in rows[0].find_all(["th", "td"])]
periods = [
    RawPeriod(col_index=1, raw_label="前連結会計年度", raw_date="2025年3月31日"),
    RawPeriod(col_index=2, raw_label="当連結会計年度", raw_date="2026年3月31日")
]

raw_rows = []
for idx, r in enumerate(rows[1:], start=1):
    cells = [c.get_text(strip=True) for c in r.find_all(["th", "td"])]
    acc_name = cells[0] if len(cells) > 0 else ""
    vals = cells[1:] if len(cells) > 1 else []
    
    # Assign role
    if acc_name in ["資産の部"]:
        role = "section_header"
        indent = 0
    elif acc_name in ["流動資産", "固定資産"]:
        role = "group_header"
        indent = 1
    elif acc_name in ["有形固定資産", "無形固定資産", "投資その他の資産"]:
        role = "category_header"
        indent = 2
    elif "（純額）" in acc_name:
        role = "netted_item"
        indent = 3
    elif "減価償却累計額" in acc_name or "引当金" in acc_name:
        role = "contra_item"
        indent = 4
    elif acc_name == "資産合計":
        role = "grand_total"
        indent = 0
    elif "合計" in acc_name:
        role = "subtotal"
        indent = 1 if "資産合計" in acc_name or "固定資産合計" in acc_name else 2
    else:
        role = "item"
        indent = 3

    raw_rows.append(RawRow(
        row_index=idx,
        indent_level=indent,
        account_raw=acc_name,
        role=role,
        values=vals
    ))

raw_tree = RawDocumentTree(
    metadata=RawMetadata(
        title="連結貸借対照表",
        is_amended=True,
        unit_raw="（単位：千円）",
        periods=periods
    ),
    rows=raw_rows
)

norm_tree = normalize_tree(raw_tree)
validator = AccountingValidator()
report = validator.validate(norm_tree)

print("=" * 65)
print(f"GLM-OCR VALIDATION RESULT: {'[100% PASSED]' if report.is_valid else '[FAILED]'}")
print(f"Equations Passed: {report.passed_equations} / {report.total_equations_tested}")
print(f"Failed Equations: {report.failed_equations}")
if report.failures:
    for f in report.failures:
        print(f"  * {f.equation_name} ({f.period}): Expected {f.expected_value:,.0f}, Computed {f.computed_value:,.0f} (Diff: {f.discrepancy:,.0f})")

# Compare with Gold Key
gold = normalize_tree(get_baseline_raw_tree())
dates = ["2025-03-31", "2026-03-31"]
matched = 0
total = 0
for p_it, g_it in zip(norm_tree.items, gold.items):
    for d in dates:
        total += 1
        if p_it.normalized_values.get(d) == g_it.normalized_values.get(d):
            matched += 1
        else:
            print(f"Mismatched Cell: {p_it.account_jp} ({d}) -> GLM={p_it.normalized_values.get(d)}, Gold={g_it.normalized_values.get(d)}")

print(f"Exact Cell Match vs Gold Key: {matched} / {total} ({matched/total*100:.2f}%)")
print("=" * 65)
