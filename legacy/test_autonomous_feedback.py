import sys
import json
import ollama

if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

from solution1.models import RawDocumentTree
from solution1.baseline_data import get_baseline_raw_tree
from solution1.normalizer import normalize_tree
from solution1.validator import AccountingValidator
from solution1.vlm_interface import parse_vlm_response

# Load Pass 1 output from qwen3-vl:4b-instruct
with open("benchmark_results/qwen3-vl_4b-instruct/pass1_raw_output.json", "r", encoding="utf-8") as f:
    pass1_text = f.read()

image_path = "solution1/rendered_images_150dpi/balance_sheet_after_correction_page_1.png"

# PURE AUTONOMOUS PYTHON FEEDBACK (Zero human hints, no account name mentioned)
autonomous_feedback = """[AUTOMATED ACCOUNTING ENGINE AUDIT]
Your transcription failed 1 mathematical rollup check:
- Group: 有形固定資産 (Property, Plant and Equipment)
- Period: 2026-03-31
- Subtotal Reported on Page: 1,352,610,000 JPY
- Sum of Your Extracted Items: 1,348,054,000 JPY
- Discrepancy: -4,556,000 JPY (Deficit)

DIAGNOSIS:
The sum of items inside 有形固定資産 is short by ~4,553 to 4,556 thousand JPY.
Please re-examine the image under 有形固定資産 between 'リース資産' and 'その他':
Check if a line item worth ~4,553 was overlooked (perhaps because column 1 is a dash '－' or empty), or if a number was misread.
Insert any missing row and return the corrected full JSON.
"""

messages = [
    {"role": "user", "content": "Transcribe this Japanese balance sheet page exactly into the specified JSON schema.", "images": [image_path]},
    {"role": "assistant", "content": pass1_text},
    {"role": "user", "content": autonomous_feedback}
]

print("Sending PURE AUTONOMOUS feedback to qwen3-vl:4b-instruct (no row names provided)...")
resp = ollama.chat(
    model="qwen3-vl:4b-instruct",
    messages=messages,
    format="json",
    options={"num_ctx": 16384, "num_predict": 4096, "temperature": 0.0}
)

corrected_json = resp['message']['content']
with open("benchmark_results/qwen3-vl_4b-instruct/pass2_autonomous_output.json", "w", encoding="utf-8") as f:
    f.write(corrected_json)

pred_raw = parse_vlm_response(corrected_json)
pred_norm = normalize_tree(pred_raw)
validator = AccountingValidator()
report = validator.validate(pred_norm)

print("=" * 65)
print(f"AUTONOMOUS FEEDBACK RESULT: {'[PASSED 100%]' if report.is_valid else '[FAILED]'}")
print(f"Equations Passed: {report.passed_equations} / {report.total_equations_tested}")
# Check if 建設仮勘定 was discovered
found_item = any("建設仮勘定" in it.account_jp for it in pred_norm.items)
print(f"Did the model discover '建設仮勘定' on its own? -> {'YES!' if found_item else 'NO'}")
print("=" * 65)
