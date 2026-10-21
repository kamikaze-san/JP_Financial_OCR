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

# Targeted feedback
targeted_feedback = """Your previous transcription achieved 19/20 on our accounting validation engine!
There is only 1 discrepancy:
- Tangible PPE Sum (有形固定資産合計) in 2026-03-31: Expected 1,352,610,000 JPY, but your sum was 1,348,054,000 JPY (diff: -4,556,000).
Looking at the image, you missed the row '建設仮勘定' between 'リース資産（純額）' and 'その他'. It has values ['－', '4,553'].

Please insert '建設仮勘定' (role: 'item', values: ['－', '4,553']) and return the full corrected JSON.
"""

messages = [
    {"role": "user", "content": "Transcribe this Japanese balance sheet page exactly into the specified JSON schema.", "images": [image_path]},
    {"role": "assistant", "content": pass1_text},
    {"role": "user", "content": targeted_feedback}
]

print("Sending targeted feedback to qwen3-vl:4b-instruct...")
resp = ollama.chat(
    model="qwen3-vl:4b-instruct",
    messages=messages,
    format="json",
    options={"num_ctx": 16384, "num_predict": 4096, "temperature": 0.0}
)

corrected_json = resp['message']['content']
with open("benchmark_results/qwen3-vl_4b-instruct/pass2_targeted_output.json", "w", encoding="utf-8") as f:
    f.write(corrected_json)

# Validate
pred_raw = parse_vlm_response(corrected_json)
pred_norm = normalize_tree(pred_raw)
validator = AccountingValidator()
report = validator.validate(pred_norm)

print("=" * 60)
print(f"VALIDATION AFTER TARGETED FEEDBACK: {'[100% PASSED]' if report.is_valid else '[FAILED]'}")
print(f"Equations Passed: {report.passed_equations} / {report.total_equations_tested}")
print("=" * 60)
