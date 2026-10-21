import os
import sys
import time
import base64
import ollama

sys.stdout.reconfigure(encoding='utf-8')

image_path = "dataset/benchmark_suite/company_02_calbee/page_1.png"

with open(image_path, "rb") as f:
    img_b64 = base64.b64encode(f.read()).decode("utf-8")

prompt = (
    "You are an expert OCR table engine. Extract the entire balance sheet table from this Japanese financial document into an HTML <table>.\n"
    "Output ONLY the HTML <table> element starting with <table> and ending with </table>.\n"
    "Preserve all account names exactly as written, and keep negative symbols (such as △, ▲, or -) and numerals intact."
)

print("Testing qwen3-vl:2b-instruct on Calbee Page 1...")
start_time = time.time()
try:
    response = ollama.chat(
        model="qwen3-vl:2b-instruct",
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
    print(f"Output length: {len(output_text)} chars")
    lines = output_text.splitlines()
    for l in lines[:10]:
        print(l)
    print("...")
    for l in lines[-5:]:
        print(l)
        
    os.makedirs("benchmark_results/calbee", exist_ok=True)
    with open("benchmark_results/calbee/qwen2b_table_p1.html", "w", encoding="utf-8") as f:
        f.write(output_text)
except Exception as e:
    print("Error:", e)
