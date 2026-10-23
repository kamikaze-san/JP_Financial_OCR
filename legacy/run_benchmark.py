import os
import sys
import time
import json
from typing import Dict, Any, List, Optional, Tuple

# Ensure UTF-8 console output
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

import ollama
from solution1.models import RawDocumentTree, NormalizedDocumentTree
from solution1.baseline_data import get_baseline_raw_tree
from solution1.normalizer import normalize_tree
from solution1.validator import AccountingValidator
from solution1.vlm_interface import STAGE1_SYSTEM_PROMPT, parse_vlm_response

BENCHMARK_PROMPT = """You are an expert Japanese financial document transcription engine.
Transcribe every row of the financial table in this image into a JSON object matching this schema:

{
  "metadata": {
    "title": "string",
    "is_amended": boolean,
    "unit_raw": "string",
    "periods": [
      {"col_index": 1, "raw_label": "string", "raw_date": "string"},
      {"col_index": 2, "raw_label": "string", "raw_date": "string"}
    ]
  },
  "rows": [
    {
      "row_index": 1,
      "indent_level": 0,
      "account_raw": "資産の部",
      "role": "section_header",
      "values": []
    },
    {
      "row_index": 3,
      "indent_level": 2,
      "account_raw": "現金及び預金",
      "role": "item",
      "values": ["2,002,429", "608,281"]
    }
  ]
}

RULES:
1. Extract ALL 40+ rows completely from top to bottom (Current Assets, Tangible PPE, Intangible, Investments, Fixed Assets Total, Total Assets).
2. Transcribe exact Japanese text as printed. Do not translate.
3. Keep numbers exactly as printed (including commas, "△" for negative, "－" for nil).
4. Assign correct roles: "section_header", "group_header", "category_header", "item", "contra_item", "netted_item", "subtotal", "grand_total".
Return ONLY the raw JSON object.
"""

def evaluate_model_output(
    model_name: str,
    raw_output: str,
    gold_norm_tree: NormalizedDocumentTree
) -> Tuple[Optional[NormalizedDocumentTree], Dict[str, Any]]:
    # Parse JSON
    try:
        raw_tree = parse_vlm_response(raw_output)
    except Exception as e:
        return None, {"error": f"JSON parsing failed: {str(e)}", "equations_passed": 0, "total_equations": 20, "cell_accuracy": 0.0}

    # Normalize
    try:
        norm_tree = normalize_tree(raw_tree)
    except Exception as e:
        return None, {"error": f"Normalization failed: {str(e)}", "equations_passed": 0, "total_equations": 20, "cell_accuracy": 0.0}

    # Validate
    validator = AccountingValidator()
    report = validator.validate(norm_tree)

    # Compute Cell Accuracy vs Gold
    gold_items = {it.account_jp: it for it in gold_norm_tree.items if it.role not in ["section_header", "group_header", "category_header"]}
    dates = [p.date for p in gold_norm_tree.periods]
    
    total_cells = len(gold_items) * len(dates)
    matched_cells = 0
    mismatches = []
    
    for it in norm_tree.items:
        if it.account_jp in gold_items:
            gold_it = gold_items[it.account_jp]
            for d in dates:
                g_val = gold_it.normalized_values.get(d, 0.0)
                p_val = it.normalized_values.get(d, 0.0)
                if g_val == p_val:
                    matched_cells += 1
                else:
                    mismatches.append({
                        "account": it.account_jp,
                        "date": d,
                        "gold": g_val,
                        "predicted": p_val
                    })

    accuracy = (matched_cells / total_cells * 100.0) if total_cells > 0 else 0.0
    stats = {
        "is_valid": report.is_valid,
        "equations_passed": report.passed_equations,
        "total_equations": report.total_equations_tested,
        "cell_accuracy": round(accuracy, 2),
        "total_cells": total_cells,
        "matched_cells": matched_cells,
        "mismatches_sample": mismatches[:5],
        "failures": [
            f"{f.equation_name} [{f.period}]: Expected {f.expected_value:,.0f}, Got {f.computed_value:,.0f} (diff: {f.discrepancy:,.0f})"
            for f in report.failures
        ]
    }
    return norm_tree, stats

def run_benchmark_on_model(
    model_name: str,
    image_path: str,
    gold_norm_tree: NormalizedDocumentTree,
    output_dir: str
) -> Dict[str, Any]:
    print(f"\n{'='*70}")
    print(f"--> RUNNING BENCHMARK: {model_name}")
    print(f"{'='*70}")

    model_slug = model_name.replace("/", "_").replace(":", "_")
    model_out_dir = os.path.join(output_dir, model_slug)
    os.makedirs(model_out_dir, exist_ok=True)

    summary = {
        "model": model_name,
        "pass1_latency_sec": 0.0,
        "pass1_equations_passed": 0,
        "pass1_cell_accuracy": 0.0,
        "pass2_latency_sec": 0.0,
        "pass2_equations_passed": 0,
        "pass2_cell_accuracy": 0.0,
        "final_valid": False,
        "notes": ""
    }

    messages = [
        {"role": "user", "content": BENCHMARK_PROMPT, "images": [image_path]}
    ]

    # --- PASS 1 ---
    print(f"[{model_name}] Pass 1: Sending 300 DPI image crop (num_ctx=8192)...")
    t0 = time.time()
    try:
        resp1 = ollama.chat(
            model=model_name,
            messages=messages,
            format="json",
            options={"num_ctx": 16384, "num_predict": 4096, "temperature": 0.0}
        )
        t_pass1 = time.time() - t0
        summary["pass1_latency_sec"] = round(t_pass1, 2)
        out1 = resp1['message']['content']
        with open(os.path.join(model_out_dir, "pass1_raw_output.json"), "w", encoding="utf-8") as f:
            f.write(out1)
    except Exception as e:
        print(f"[{model_name}] Pass 1 failed: {str(e)}")
        summary["notes"] = f"Pass 1 failed: {str(e)}"
        return summary

    norm_tree1, stats1 = evaluate_model_output(model_name, out1, gold_norm_tree)
    if not norm_tree1:
        print(f"[{model_name}] Pass 1 parsing failed: {stats1.get('error')}")
        summary["notes"] = stats1.get("error", "Parsing failed")
        return summary

    summary["pass1_equations_passed"] = stats1["equations_passed"]
    summary["pass1_cell_accuracy"] = stats1["cell_accuracy"]
    summary["final_valid"] = stats1["is_valid"]
    print(f"[{model_name}] Pass 1 Results: {stats1['equations_passed']}/20 equations passed | Cell Accuracy: {stats1['cell_accuracy']}% ({t_pass1:.2f}s)")

    if stats1["failures"]:
        print(f"[{model_name}] First 3 Discrepancies:")
        for fail_str in stats1["failures"][:3]:
            print(f"   * {fail_str}")

    # --- PASS 2: DETERMINISTIC ACCOUNTING FEEDBACK LOOP ---
    if not stats1["is_valid"]:
        print(f"\n[{model_name}] Pass 2: Running Deterministic Python Feedback Loop...")
        feedback_lines = [
            "Your previous JSON output has mathematical discrepancies verified by our accounting engine:",
        ]
        for fail_str in stats1["failures"][:4]:
            feedback_lines.append(f"- {fail_str}")
        feedback_lines.append(
            "Please re-examine the image crop for these rows. Pay close attention to: "
            "1) negative deduction signs '△', "
            "2) nil dashes '－', "
            "3) and ensure the subtotal rows match the exact sum of individual items. "
            "Return the corrected full JSON."
        )
        feedback_prompt = "\n".join(feedback_lines)

        messages.append({"role": "assistant", "content": out1})
        messages.append({"role": "user", "content": feedback_prompt})

        t1 = time.time()
        try:
            resp2 = ollama.chat(
                model=model_name,
                messages=messages,
                format="json",
                options={"num_ctx": 16384, "num_predict": 4096, "temperature": 0.0}
            )
            t_pass2 = time.time() - t1
            summary["pass2_latency_sec"] = round(t_pass2, 2)
            out2 = resp2['message']['content']
            with open(os.path.join(model_out_dir, "pass2_raw_output.json"), "w", encoding="utf-8") as f:
                f.write(out2)

            norm_tree2, stats2 = evaluate_model_output(model_name, out2, gold_norm_tree)
            if norm_tree2:
                summary["pass2_equations_passed"] = stats2["equations_passed"]
                summary["pass2_cell_accuracy"] = stats2["cell_accuracy"]
                summary["final_valid"] = stats2["is_valid"]
                print(f"[{model_name}] Pass 2 (After Python Feedback) Results: {stats2['equations_passed']}/20 equations passed | Cell Accuracy: {stats2['cell_accuracy']}% ({t_pass2:.2f}s)")
        except Exception as e:
            print(f"[{model_name}] Pass 2 failed: {str(e)}")
            summary["notes"] += f" | Pass 2 failed: {str(e)}"

    return summary

def main():
    image_path = os.path.join(os.path.dirname(__file__), "solution1", "rendered_images_150dpi", "balance_sheet_after_correction_page_1.png")
    benchmark_out_dir = os.path.join(os.path.dirname(__file__), "benchmark_results")
    os.makedirs(benchmark_out_dir, exist_ok=True)

    # Gold standard
    gold_raw_tree = get_baseline_raw_tree()
    gold_norm_tree = normalize_tree(gold_raw_tree)

    models_to_test = [
        "openbmb/minicpm-v4.7:1b",
        "qwen3-vl:2b",
        "qwen3-vl:4b-instruct",
        "qwen3-vl:8b"
    ]

    all_results = []
    for model_name in models_to_test:
        res = run_benchmark_on_model(model_name, image_path, gold_norm_tree, benchmark_out_dir)
        all_results.append(res)

    # Print Summary Table
    print("\n" + "=" * 80)
    print("                     LOCAL VLM BENCHMARK SUMMARY TABLE                         ")
    print("=" * 80)
    print(f"{'Model':<26} | {'Pass 1 (Zero-Shot)':<20} | {'Pass 2 (With Loop)':<20} | {'Latency':<8}")
    print(f"{'':<26} | {'ACS':<8} {'Acc%':<10} | {'ACS':<8} {'Acc%':<10} | {'(s)':<8}")
    print("-" * 80)
    for r in all_results:
        p1_acs = f"{r['pass1_equations_passed']}/20"
        p1_acc = f"{r['pass1_cell_accuracy']}%"
        p2_acs = f"{r['pass2_equations_passed']}/20" if r['pass2_latency_sec'] > 0 else "-"
        p2_acc = f"{r['pass2_cell_accuracy']}%" if r['pass2_latency_sec'] > 0 else "-"
        total_lat = f"{r['pass1_latency_sec'] + r['pass2_latency_sec']:.1f}s"
        print(f"{r['model']:<26} | {p1_acs:<8} {p1_acc:<10} | {p2_acs:<8} {p2_acc:<10} | {total_lat:<8}")
    print("=" * 80)

    # Save summary report JSON and Markdown
    summary_json = os.path.join(benchmark_out_dir, "benchmark_summary.json")
    with open(summary_json, "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2, ensure_ascii=False)
        
    summary_md = os.path.join(benchmark_out_dir, "benchmark_summary.md")
    with open(summary_md, "w", encoding="utf-8") as f:
        f.write("# Local Small VLM Benchmark Results\n\n")
        f.write("| Model | Pass 1 ACS | Pass 1 Cell Acc | Pass 2 (With Python Loop) ACS | Pass 2 Cell Acc | Latency |\n")
        f.write("| :--- | :---: | :---: | :---: | :---: | :---: |\n")
        for r in all_results:
            p1_acs = f"{r['pass1_equations_passed']}/20"
            p1_acc = f"{r['pass1_cell_accuracy']}%"
            p2_acs = f"{r['pass2_equations_passed']}/20" if r['pass2_latency_sec'] > 0 else "-"
            p2_acc = f"{r['pass2_cell_accuracy']}%" if r['pass2_latency_sec'] > 0 else "-"
            total_lat = f"{r['pass1_latency_sec'] + r['pass2_latency_sec']:.1f}s"
            f.write(f"| `{r['model']}` | {p1_acs} | {p1_acc} | {p2_acs} | {p2_acc} | {total_lat} |\n")
    print(f"Benchmark summary saved to {summary_md}")

if __name__ == "__main__":
    main()
