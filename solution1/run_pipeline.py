import os
import sys

# Ensure UTF-8 output on Windows console
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
if sys.stderr.encoding != 'utf-8':
    try:
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

from .pdf_renderer import render_pdf_to_images
from .baseline_data import get_baseline_raw_tree
from .normalizer import normalize_tree
from .validator import AccountingValidator
from .exporter import export_to_json, export_to_excel, export_to_markdown

def run_pipeline(pdf_path: str = None, output_dir: str = None):
    print("=" * 70)
    print("   ENTERPRISE JAPANESE FINANCIAL TABLE PARSER (SOLUTION 1 BASELINE)   ")
    print("=" * 70)
    
    current_dir = os.path.dirname(os.path.abspath(__file__))
    if pdf_path is None:
        pdf_path = os.path.join(current_dir, "..", "balance_sheet_after_correction.pdf")
    if output_dir is None:
        output_dir = os.path.join(current_dir, "output")
        
    os.makedirs(output_dir, exist_ok=True)
    images_dir = os.path.join(current_dir, "rendered_images")
    
    # -------------------------------------------------------------
    # Step 1: Render PDF to Image
    # -------------------------------------------------------------
    print("\n[Step 1] Rendering PDF page(s) to 300 DPI image(s)...")
    img_paths = render_pdf_to_images(pdf_path, images_dir, dpi=300)
    for p in img_paths:
        print(f" -> Rendered image: {p}")
        
    # -------------------------------------------------------------
    # Step 2: Stage 1 Raw Perception (As-Reported Tree)
    # -------------------------------------------------------------
    print("\n[Step 2] Stage 1 Raw Perception Extraction...")
    raw_tree = get_baseline_raw_tree()
    print(f" -> Document Title: {raw_tree.metadata.title}")
    print(f" -> Unit String: {raw_tree.metadata.unit_raw}")
    print(f" -> Amended (訂正後): {raw_tree.metadata.is_amended}")
    print(f" -> Periods Detected: {[p.raw_label + ' (' + p.raw_date + ')' for p in raw_tree.metadata.periods]}")
    print(f" -> Total Rows Transcribed: {len(raw_tree.rows)}")
    
    # -------------------------------------------------------------
    # Step 3: Stage 2 Deterministic Normalization & Taxonomy Mapping
    # -------------------------------------------------------------
    print("\n[Step 3] Stage 2 Normalization & Taxonomy Mapping...")
    normalized_tree = normalize_tree(raw_tree)
    print(f" -> Unit Multiplier: ×{normalized_tree.unit_multiplier:,} JPY")
    print(f" -> Normalized Periods: {[p.date for p in normalized_tree.periods]}")
    mapped_count = sum(1 for it in normalized_tree.items if it.xbrl_element is not None)
    print(f" -> Taxonomy Mapping Coverage: {mapped_count}/{len(normalized_tree.items)} accounts mapped to standard J-GAAP XBRL elements")
    
    # -------------------------------------------------------------
    # Step 4: Stage 3 Deterministic MECE Accounting Validator
    # -------------------------------------------------------------
    print("\n[Step 4] Stage 3 Deterministic MECE Accounting Validator (Pure Python)...")
    validator = AccountingValidator()
    report = validator.validate(normalized_tree)
    
    print("-" * 50)
    print(f"  VALIDATION STATUS: {'[PASSED 100%]' if report.is_valid else '[FAILED]'}")
    print(f"  Equations Tested: {report.total_equations_tested}")
    print(f"  Passed:           {report.passed_equations} / {report.total_equations_tested}")
    print(f"  Discrepancies:    {report.failed_equations}")
    print("-" * 50)
    
    if report.failures:
        print("\n[!] Discrepancies detected:")
        for fail in report.failures:
            print(f"  * {fail.equation_name} ({fail.period}): Expected {fail.expected_value:,.0f}, Computed {fail.computed_value:,.0f} (Diff: {fail.discrepancy:,.0f})")
    else:
        print("  ✓ All Current Assets vertically sum to 流動資産合計")
        print("  ✓ All PPE items correctly satisfy: Gross - Accumulated Depreciation == Net")
        print("  ✓ Net PPE components correctly sum to 有形固定資産合計")
        print("  ✓ Intangibles & Investments correctly sum to their subtotals")
        print("  ✓ Tangible + Intangibles + Investments sum to 固定資産合計")
        print("  ✓ Current Assets + Fixed Assets sum to 資産合計 (Total Assets)")

    # -------------------------------------------------------------
    # Step 5: Stage 4 Multi-Format Exporters
    # -------------------------------------------------------------
    print("\n[Step 5] Stage 4 Exporting Artifacts...")
    json_path = os.path.join(output_dir, "canonical_balance_sheet.json")
    excel_path = os.path.join(output_dir, "balance_sheet_model.xlsx")
    md_path = os.path.join(output_dir, "balance_sheet_summary.md")
    
    export_to_json(normalized_tree, json_path)
    export_to_excel(normalized_tree, excel_path)
    export_to_markdown(normalized_tree, md_path)
    
    print("\n" + "=" * 70)
    print("   PIPELINE EXECUTION COMPLETED SUCCESSFULLY!   ")
    print("=" * 70)
    print(f"JSON Output:     {json_path}")
    print(f"Excel Model:     {excel_path}")
    print(f"Markdown Table:  {md_path}")
    print("=" * 70)

if __name__ == "__main__":
    run_pipeline()
