import sys
import json
import time
import ollama

if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

from solution1.models import RawDocumentTree
from solution1.normalizer import normalize_tree
from solution1.validator import AccountingValidator
from solution1.vlm_interface import parse_vlm_response
from solution1.baseline_data import get_baseline_raw_tree
from run_benchmark import BENCHMARK_PROMPT

image_path_200dpi = "solution1/rendered_images_200dpi/balance_sheet_after_correction_page_1.png"

print("Running qwen3-vl:2b-instruct on 200 DPI image...")
t0 = time.time()
resp = ollama.chat(
    model="qwen3-vl:2b-instruct",
    messages=[{"role": "user", "content": BENCHMARK_PROMPT, "images": [image_path_200dpi]}],
    format="json",
    options={"num_ctx": 16384, "num_predict": 4096, "temperature": 0.0}
)
latency = time.time() - t0
print(f"Generation completed in {latency:.1f}s")

raw_json = resp['message']['content']
with open("benchmark_results/qwen3-vl_2b-instruct/pass1_200dpi_output.json", "w", encoding="utf-8") as f:
    f.write(raw_json)

try:
    pred_raw = parse_vlm_response(raw_json)
    pred_norm = normalize_tree(pred_raw)
    validator = AccountingValidator()
    report = validator.validate(pred_norm)

    print("=" * 60)
    print(f"2B-INSTRUCT 200 DPI RESULT: {'[100% PASSED]' if report.is_valid else '[FAILED]'}")
    print(f"Equations Passed: {report.passed_equations} / {report.total_equations_tested}")
    gold = normalize_tree(get_baseline_raw_tree())
    matched = sum(1 for p_it, g_it in zip(pred_norm.items, gold.items) for d in ["2025-03-31", "2026-03-31"] if p_it.normalized_values.get(d) == g_it.normalized_values.get(d))
    print(f"Exact Match: {matched}/86 ({matched/86*100:.2f}%)")
    print("=" * 60)
except Exception as e:
    print(f"Failed to parse 2B output: {str(e)}")
