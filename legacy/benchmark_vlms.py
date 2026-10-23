import os
import sys
import time
import json
import base64
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

def load_image_bytes(image_path: str) -> bytes:
    with open(image_path, "rb") as f:
        return f.read()

def compute_accuracy_vs_gold(pred_tree: NormalizedDocumentTree, gold_tree: NormalizedDocumentTree) -> Dict[str, Any]:
    """Compares predicted normalized items against gold answer key."""
    gold_items = {it.account_jp: it for it in gold_tree.items}
    total_cells = 0
    matched_cells = 0
    missing_accounts = []
    mismatched_cells = []
    
    dates = [p.date for p in gold_tree.periods]
    
    for it in gold_tree.items:
        if it.role in ["section_header", "group_header", "category_header"]:
            continue
            
        pred_item = next((p for p in pred_tree.items if p.account_jp == it.account_jp), None)
        if not pred_item:
            missing_accounts.append(it.account_jp)
            total_cells += len(dates)
            continue
            
        for d in dates:
            total_cells += 1
            gold_val = it.normalized_values.get(d, 0.0)
            pred_val = pred_item.normalized_values.get(d, 0.0)
            
            # Exact match check
            if gold_val == pred_val:
                matched_cells += 1
            else:
                mismatched_cells.append({
                    "account": it.account_jp,
                    "date": d,
                    "gold": gold_val,
                    "pred": pred_val,
                    "diff": pred_val - gold_val
                })
                
    accuracy = (matched_cells / total_cells * 100.0) if total_cells > 0 else 0.0
    return {
        "total_cells": total_cells,
        "matched_cells": matched_cells,
        "accuracy_pct": round(accuracy, 2),
        "missing_accounts": missing_accounts,
        "mismatched_cells": mismatched_cells
    }

def run_single_vlm_benchmark(
    model_name: str,
    image_path: str,
    gold_raw_tree: RawDocumentTree,
    gold_norm_tree: NormalizedDocumentTree,
    enable_feedback_loop: bool = True
) -> Dict[str, Any]:
    print(f"\n{'='*65}")
    print(f"BENCHMARKING MODEL: {model_name}")
    print(f"{'='*65}")
    
    result = {
        "model_name": model_name,
        "pass1_latency_sec": 0.0,
        "pass1_equations_passed": 0,
        "pass1_total_equations": 20,
        "pass1_cell_accuracy_pct": 0.0,
        "feedback_applied": False,
        "pass2_latency_sec": 0.0,
        "pass2_equations_passed": 0,
        "pass2_cell_accuracy_pct": 0.0,
        "final_valid": False,
        "errors": []
    }
    
    # Read image
    if not os.path.exists(image_path):
        result["errors"].append(f"Image not found: {image_path}")
        return result
        
    messages = [
        {"role": "system", "content": STAGE1_SYSTEM_PROMPT},
        {"role": "user", "content": "Transcribe this Japanese balance sheet page exactly into the specified JSON schema.", "images": [image_path]}
    ]
    
    # -------------------------------------------------------------
    # Pass 1: Zero-Shot Initial Perception
    # -------------------------------------------------------------
    print(f"[{model_name}] Pass 1: Sending image to Ollama...")
    t0 = time.time()
    try:
        response = ollama.chat(
            model=model_name,
            messages=messages,
            format="json", # Request structured JSON from Ollama
            options={"temperature": 0.0}
        )
        pass1_latency = time.time() - t0
        result["pass1_latency_sec"] = round(pass1_latency, 2)
        print(f"[{model_name}] Pass 1 completed in {pass1_latency:.2f}s")
        raw_output_text = response['message']['content']
    except Exception as e:
        err_msg = f"Ollama execution failed: {str(e)}"
        print(f"[{model_name}] ERROR: {err_msg}")
        result["errors"].append(err_msg)
        return result
        
    # Parse Pass 1 JSON
    try:
        pred_raw_tree = parse_vlm_response(raw_output_text)
    except Exception as e:
        err_msg = f"Failed to parse model JSON output: {str(e)}"
        print(f"[{model_name}] ERROR: {err_msg}")
        result["errors"].append(err_msg)
        return result
        
    # Normalize & Validate Pass 1
    pred_norm_tree = normalize_tree(pred_raw_tree)
    validator = AccountingValidator()
    report1 = validator.validate(pred_norm_tree)
    
    acc1 = compute_accuracy_vs_gold(pred_norm_tree, gold_norm_tree)
    result["pass1_equations_passed"] = report1.passed_equations
    result["pass1_total_equations"] = report1.total_equations_tested
    result["pass1_cell_accuracy_pct"] = acc1["accuracy_pct"]
    result["final_valid"] = report1.is_valid
    
    print(f"[{model_name}] Pass 1 Results: {report1.passed_equations}/{report1.total_equations_tested} equations passed | Cell Accuracy: {acc1['accuracy_pct']}%")
    
    # -------------------------------------------------------------
    # Pass 2: Deterministic Python Accounting Feedback Loop
    # -------------------------------------------------------------
    if not report1.is_valid and enable_feedback_loop:
        result["feedback_applied"] = True
        print(f"\n[{model_name}] Triggering Python Accounting Feedback Loop...")
        
        # Build targeted feedback message
        feedback_lines = [
            "Your previous JSON transcription had accounting arithmetic discrepancies verified by our accounting engine:",
        ]
        for f in report1.failures[:5]: # Take top 5 failures
            feedback_lines.append(
                f"- In {f.period} for '{f.equation_name}': Expected {f.expected_value:,.0f}, but the sum of your extracted items was {f.computed_value:,.0f} (discrepancy: {f.discrepancy:,.0f}). Check the rows: {', '.join(f.involved_accounts[:4])}."
            )
        feedback_lines.append("Please carefully re-examine the image crop for these rows, check for negative signs (△/▲), nil dashes (－), and correct the JSON.")
        feedback_msg = "\n".join(feedback_lines)
        
        messages.append({"role": "assistant", "content": raw_output_text})
        messages.append({"role": "user", "content": feedback_msg})
        
        t1 = time.time()
        try:
            response2 = ollama.chat(
                model=model_name,
                messages=messages,
                format="json",
                options={"temperature": 0.0}
            )
            pass2_latency = time.time() - t1
            result["pass2_latency_sec"] = round(pass2_latency, 2)
            raw_output_text2 = response2['message']['content']
            
            pred_raw_tree2 = parse_vlm_response(raw_output_text2)
            pred_norm_tree2 = normalize_tree(pred_raw_tree2)
            report2 = validator.validate(pred_norm_tree2)
            acc2 = compute_accuracy_vs_gold(pred_norm_tree2, gold_norm_tree2)
            
            result["pass2_equations_passed"] = report2.passed_equations
            result["pass2_cell_accuracy_pct"] = acc2["accuracy_pct"]
            result["final_valid"] = report2.is_valid
            print(f"[{model_name}] Pass 2 (with feedback) Results: {report2.passed_equations}/{report2.total_equations_tested} equations passed | Cell Accuracy: {acc2['accuracy_pct']}%")
        except Exception as e:
            print(f"[{model_name}] Pass 2 Feedback failed: {str(e)}")
            result["errors"].append(f"Feedback pass failed: {str(e)}")
            
    return result

if __name__ == "__main__":
    test_models = [
        "openbmb/minicpm-v4.7:1b",
        "qwen3-vl:2b",
        "qwen3-vl:4b-instruct",
        "qwen3-vl:8b"
    ]
    print(f"Ready to benchmark models: {test_models}")
