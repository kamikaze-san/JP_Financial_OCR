import sys
import json
import time
import ollama

if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

image_path = "solution1/rendered_images_200dpi/balance_sheet_after_correction_page_1.png"

prompt = """Transcribe this table into a structured JSON object with the following schema:
{
  "metadata": {"title": "string", "unit": "string"},
  "rows": [{"account": "string", "fy2025": "string", "fy2026": "string"}]
}
Return ONLY valid JSON.
"""

print("Testing glm-ocr:latest with JSON prompt and format='json'...")
t0 = time.time()
try:
    resp = ollama.chat(
        model="glm-ocr:latest",
        messages=[{"role": "user", "content": prompt, "images": [image_path]}],
        format="json",
        options={"num_ctx": 16384, "num_predict": 4096, "temperature": 0.0}
    )
    latency = time.time() - t0
    content = resp['message']['content']
    print(f"GLM-OCR JSON Success ({latency:.1f}s) | Output Length: {len(content)} chars")
    print("Sample Output:")
    print(content[:400])
    
    # Try parsing
    data = json.loads(content)
    print("Parsed JSON successfully! Key count:", len(data))
    if "rows" in data:
        print(f"Row count: {len(data['rows'])}")
        print("First 3 rows:", data["rows"][:3])
except Exception as e:
    print(f"GLM-OCR JSON Failed: {str(e)}")
