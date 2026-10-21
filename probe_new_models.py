import sys
import time
import re
import json

if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

import ollama

image_path = "solution1/rendered_images_200dpi/balance_sheet_after_correction_page_1.png"

models = [
    "MedAIBase/PaddleOCR-VL:0.9b",
    "glm-ocr:latest",
    "deepseek-ocr:latest",
    "qwen3.5:4b"
]

def clean_thinking(text: str) -> str:
    """Strips <think>...</think> reasoning blocks if present."""
    return re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()

print("Testing initial probe on all 4 models...")
for m in models:
    print(f"\n{'='*60}\nPROBING MODEL: {m}\n{'='*60}")
    t0 = time.time()
    try:
        # Prompt explicitly with no-think instruction
        res = ollama.chat(
            model=m,
            messages=[{
                'role': 'user',
                'content': 'Transcribe the first 5 rows of the financial table in this image to markdown or JSON. Do not output any thinking process or <think> tags. Output only the data.',
                'images': [image_path]
            }],
            options={'num_ctx': 8192, 'temperature': 0.0}
        )
        latency = time.time() - t0
        content = res['message']['content']
        cleaned = clean_thinking(content)
        print(f"[{m}] SUCCESS ({latency:.1f}s) | Raw Length: {len(content)} chars | Cleaned Length: {len(cleaned)} chars")
        print(f"Sample Output:\n{cleaned[:300]}")
    except Exception as e:
        latency = time.time() - t0
        print(f"[{m}] FAILED ({latency:.1f}s): {str(e)}")
