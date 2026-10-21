import os
import sys
import time
import json
from typing import Dict, Any, List, Optional, Tuple

if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

import ollama
from solution1.pdf_renderer import render_pdf_to_images
from solution1.models import RawDocumentTree, NormalizedDocumentTree
from solution1.baseline_data import get_baseline_raw_tree
from solution1.normalizer import normalize_tree
from solution1.validator import AccountingValidator
from solution1.vlm_interface import STAGE1_SYSTEM_PROMPT, parse_vlm_response
from check_pass2_accuracy import compute_sequence_accuracy if "compute_sequence_accuracy" in dir() else None

def evaluate_pred_vs_gold(pred_norm: NormalizedDocumentTree, gold_norm: NormalizedDocumentTree) -> float:
    dates = ["2025-03-31", "2026-03-31"]
    total = 0
    matched = 0
    for p_it, g_it in zip(pred_norm.items, gold_norm.items):
        for d in dates:
            total += 1
            if p_it.normalized_values.get(d) == g_it.normalized_values.get(d):
                matched += 1
    return round((matched / total * 100), 2) if total > 0 else 0.0

def run_experiment(model_name: str, image_path: str, gold_norm: NormalizedDocumentTree) -> Dict[str, Any]:
    print(f"\n--- Testing Model: {model_name} on Image: {os.path.basename(image_path)} ---")
    file_size_kb = os.path.getsize(image_path) / 1024
    
    messages = [
        {"role": "user", "content": "Transcribe this Japanese balance sheet page exactly into the specified JSON schema.", "images": [image_path]}
    ]
    
    t0 = time.time()
    try:
        resp = ollama.chat(
            model=model_name,
            messages=messages,
            format="json",
            options={"num_ctx": 16384, "num_predict": 4096, "temperature": 0.0}
        )
        latency = time.time() - t0
        raw_text = resp['message']['content']
        
        pred_raw = parse_vlm_response(raw_text)
        pred_norm = normalize_tree(pred_raw)
        validator = AccountingValidator()
        report = validator.validate(pred_norm)
        acc = evaluate_pred_vs_gold(pred_norm, gold_norm)
        
        print(f"[{model_name}] Equations Passed: {report.passed_equations}/20 | Exact Match: {acc}% | Latency: {latency:.1f}s")
        return {
            "model": model_name,
            "image": os.path.basename(image_path),
            "file_size_kb": round(file_size_kb, 1),
            "latency_sec": round(latency, 1),
            "equations_passed": report.passed_equations,
            "is_valid": report.is_valid,
            "accuracy_pct": acc,
            "error": None
        }
    except Exception as e:
        latency = time.time() - t0
        print(f"[{model_name}] FAILED: {str(e)} ({latency:.1f}s)")
        return {
            "model": model_name,
            "image": os.path.basename(image_path),
            "file_size_kb": round(file_size_kb, 1),
            "latency_sec": round(latency, 1),
            "equations_passed": 0,
            "is_valid": False,
            "accuracy_pct": 0.0,
            "error": str(e)
        }
