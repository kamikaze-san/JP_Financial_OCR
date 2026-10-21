import os
import sys
import time
import re
from bs4 import BeautifulSoup

if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

import ollama
from solution1.models import RawDocumentTree, RawMetadata, RawPeriod, RawRow
from solution1.normalizer import normalize_tree
from solution1.validator import AccountingValidator
from solution1.baseline_data import get_baseline_raw_tree

image_path = "solution1/rendered_images_200dpi/balance_sheet_after_correction_page_1.png"
out_dir = "benchmark_results/glm_ocr"
os.makedirs(out_dir, exist_ok=True)

print("Running glm-ocr:latest on 200 DPI image...")
t0 = time.time()
resp = ollama.chat(
    model="glm-ocr:latest",
    messages=[{
        'role': 'user',
        'content': 'Transcribe the complete table from this image into an HTML table with all rows and columns.',
        'images': [image_path]
    }],
    options={'num_ctx': 16384, 'num_predict': 4096, 'temperature': 0.0}
)
latency = time.time() - t0
print(f"GLM-OCR finished in {latency:.1f}s")

html_content = resp['message']['content']
html_path = os.path.join(out_dir, "table.html")
with open(html_path, "w", encoding="utf-8") as f:
    f.write(html_content)

print(f"Saved HTML table -> {html_path} ({len(html_content)} chars)")

# Parse HTML Table into rows
soup = BeautifulSoup(html_content, "html.parser")
table = soup.find("table")

if not table:
    print("No <table> element found in output! Raw start:")
    print(html_content[:300])
else:
    rows = table.find_all("tr")
    print(f"Extracted {len(rows)} HTML table rows!")
    for idx, r in enumerate(rows[:10]):
        cells = [c.get_text(strip=True) for c in r.find_all(["th", "td"])]
        print(f"Row {idx+1}: {cells}")

    # Check for specific difficult items
    all_text = table.get_text()
    print("\n--- Key Check Items ---")
    print("Contains '建設仮勘定':", "建設仮勘定" in all_text)
    print("Contains '4,553':", "4,553" in all_text)
    print("Contains '△1,923,585':", "1,923,585" in all_text)
    print("Contains '流動資産合計':", "流動資産合計" in all_text)
    print("Contains '資産合計':", "資産合計" in all_text)
