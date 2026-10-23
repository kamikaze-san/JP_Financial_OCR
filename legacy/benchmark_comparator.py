import json
import os
import re
import sys
import unicodedata
from bs4 import BeautifulSoup

sys.stdout.reconfigure(encoding='utf-8')

from solution1.models import NormalizedDocumentTree
from solution1.normalizer import parse_numeric_value

KANJI_VARIANTS = {
    '流动': '流動',
    '资产': '資産',
    '负债': '負債',
    '剩余': '剰余',
    '金额': '金額'
}

def clean_account_name(name: str) -> str:
    name = unicodedata.normalize('NFKC', name)
    name = re.sub(r'[\s\u3000()（）]', '', name)
    for k, v in KANJI_VARIANTS.items():
        name = name.replace(k, v)
    return name

def parse_html_table_to_rows(html_path):
    with open(html_path, "r", encoding="utf-8") as f:
        soup = BeautifulSoup(f.read(), "html.parser")
        
    table = soup.find("table")
    if not table:
        return []
        
    extracted_rows = []
    tr_elements = table.find_all("tr")
    
    for tr in tr_elements:
        tds = [c.get_text(strip=True) for c in tr.find_all(["th", "td"])]
        if not tds:
            continue
            
        # Check if 4-column T-Account format: [Acc_L, Val_L, Acc_R, Val_R]
        if len(tds) == 4:
            acc_l = clean_account_name(tds[0])
            val_l = tds[1]
            acc_r = clean_account_name(tds[2])
            val_r = tds[3]
            
            if acc_l and acc_l not in ["科目", "資産の部"]:
                extracted_rows.append((acc_l, [val_l]))
            if acc_r and acc_r not in ["科目", "負債の部", "純資産の部"]:
                extracted_rows.append((acc_r, [val_r]))
        else:
            acc = clean_account_name(tds[0])
            vals = tds[1:]
            if acc and acc not in ["科目", "資産の部", "負債の部", "純資産の部"]:
                extracted_rows.append((acc, vals))
                
    return extracted_rows

def evaluate_against_golden(html_path, golden_path, company_name):
    print("=" * 80)
    print(f"BENCHMARK COMPARATOR: {company_name}")
    print(f"Golden Anchor: {golden_path}")
    print(f"Model Output:  {html_path}")
    print("=" * 80)
    
    with open(golden_path, "r", encoding="utf-8") as f:
        golden_tree = NormalizedDocumentTree.model_validate_json(f.read())
        
    golden_dates = [p.date for p in golden_tree.periods]
    print(f"Reporting Periods: {golden_dates} (Multiplier: {golden_tree.unit_multiplier}x)")
    
    model_rows = parse_html_table_to_rows(html_path)
    print(f"Parsed Model Rows: {len(model_rows)} | Golden Items: {len(golden_tree.items)}")
    
    # Filter golden items that have expected numbers
    eval_golden = [it for it in golden_tree.items if it.role not in ["section_header", "group_header", "category_header"]]
    
    total_cells = 0
    exact_matches = 0
    mismatches = []
    
    # Sequential alignment tracking
    model_idx = 0
    
    for g_idx, g_item in enumerate(eval_golden):
        g_acc = clean_account_name(g_item.account_jp)
        
        # Search forward in model_rows for matching account name
        found_row = None
        for i in range(model_idx, min(model_idx + 4, len(model_rows))):
            m_acc, m_vals = model_rows[i]
            if g_acc in m_acc or m_acc in g_acc:
                found_row = m_vals
                model_idx = i + 1
                break
                
        # If not found immediately ahead, search globally
        if found_row is None:
            for i, (m_acc, m_vals) in enumerate(model_rows):
                if g_acc == m_acc:
                    found_row = m_vals
                    break

        for d_idx, date in enumerate(golden_dates):
            total_cells += 1
            expected_val = g_item.reported_values.get(date)
            
            if found_row is not None and d_idx < len(found_row):
                pred_raw = found_row[d_idx]
                pred_val = parse_numeric_value(pred_raw)
            else:
                pred_raw = "MISSING"
                pred_val = None
                
            if expected_val is None:
                if pred_val is None or pred_val == 0.0:
                    exact_matches += 1
                else:
                    mismatches.append({
                        "account": g_item.account_jp,
                        "date": date,
                        "expected": "None",
                        "predicted": pred_raw
                    })
            else:
                if pred_val == expected_val:
                    exact_matches += 1
                else:
                    mismatches.append({
                        "account": g_item.account_jp,
                        "date": date,
                        "expected": f"{expected_val:,.0f}",
                        "predicted": pred_raw
                    })

    accuracy = (exact_matches / total_cells * 100) if total_cells > 0 else 0.0
    print(f"\nSCORECARD:")
    print(f"  * Total Evaluated Cells: {total_cells}")
    print(f"  * Exact Cell Matches:    {exact_matches}")
    print(f"  * Exact Match Rate (EM): {accuracy:.2f}%")
    
    if mismatches:
        print(f"\n  [!] Cell Discrepancies ({len(mismatches)}):")
        for m in mismatches:
            print(f"      - {m['account']:<24} ({m['date']}): Expected {m['expected']:>10} vs Model {m['predicted']:>10}")
    else:
        print("\n  >>> [100.0% PERFECT EXACT MATCH ANCHOR VALIDATION] <<<")
    print()

if __name__ == "__main__":
    evaluate_against_golden(
        html_path="benchmark_results/calbee/glm_combined.html",
        golden_path="dataset/benchmark_suite/company_02_calbee/golden_anchor.json",
        company_name="Calbee, Inc. (2229)"
    )
    
    evaluate_against_golden(
        html_path="benchmark_results/yamato/glm_table.html",
        golden_path="dataset/benchmark_suite/company_01_yamato/golden_anchor.json",
        company_name="Yamato Mobility & Mfg. (7886)"
    )
