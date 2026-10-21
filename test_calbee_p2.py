import os
import sys
import time
import base64
import ollama

sys.stdout.reconfigure(encoding='utf-8')

image_path = "dataset/benchmark_suite/company_02_calbee/page_2.png"

with open(image_path, "rb") as f:
    img_b64 = base64.b64encode(f.read()).decode("utf-8")

prompt = (
    "You are an expert OCR table engine. Extract the entire balance sheet table from this Japanese financial document into an HTML <table>.\n"
    "Output ONLY the HTML <table> element starting with <table> and ending with </table>.\n"
    "Preserve all account names exactly as written, and keep negative symbols (such as △, ▲, or -) and numerals intact."
)

print(f"Running glm-ocr on {image_path}...")
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
    
    with open("benchmark_results/calbee/glm_table_p2.html", "w", encoding="utf-8") as f:
        f.write(output_text)
    
    print("\n--- Output Preview (first 10 lines) ---")
    lines = output_text.splitlines()
    for l in lines[:10]:
        print(l)
    print("...")
    for l in lines[-10:]:
        print(l)

except Exception as e:
    print("Error:", e)
