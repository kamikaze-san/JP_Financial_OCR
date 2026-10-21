import os
import sys
import time
import base64
import ollama

sys.stdout.reconfigure(encoding='utf-8')

image_path = "dataset/benchmark_suite/company_01_yamato/page_1.png"

with open(image_path, "rb") as f:
    img_b64 = base64.b64encode(f.read()).decode("utf-8")

prompt = (
    "You are an expert OCR table engine. Extract the entire balance sheet table from this Japanese financial document into an HTML <table>.\n"
    "This document has a two-column balance sheet format (Assets on the left, Liabilities and Net Assets on the right).\n"
    "Output ONLY the HTML <table> element starting with <table> and ending with </table>.\n"
    "Preserve all account names exactly as written, and keep negative symbols (such as △, ▲, or -) and numerals intact."
)

print(f"Running glm-ocr on Yamato T-Account ({image_path})...")
start_time = time.time()

try:
    response = ollama.chat(
        model="glm-ocr:latest",
        messages=[
            {
                "role": "user",
                "content": prompt,
                "images": [img_b64]
            }
        ],
        options={
            "temperature": 0.0,
            "num_predict": 4096,
            "num_ctx": 16384
        }
    )
    elapsed = time.time() - start_time
    output_text = response['message']['content']
    print(f"Completed in {elapsed:.2f} seconds!")
    
    os.makedirs("benchmark_results/yamato", exist_ok=True)
    with open("benchmark_results/yamato/glm_table.html", "w", encoding="utf-8") as f:
        f.write(output_text)
    
    print("\n--- Output Preview (first 12 lines) ---")
    lines = output_text.splitlines()
    for l in lines[:12]:
        print(l)
    print("...")
    for l in lines[-10:]:
        print(l)

except Exception as e:
    print("Error:", e)
