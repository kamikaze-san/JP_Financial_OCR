import os
import sys
import time
import base64
import json
import unicodedata
import re
from bs4 import BeautifulSoup
import ollama

sys.stdout.reconfigure(encoding='utf-8')

from solution1.models import NormalizedDocumentTree
from solution1.normalizer import parse_numeric_value
from legacy.benchmark_comparator import clean_account_name, parse_html_table_to_rows

MODELS = [
    "glm-ocr:latest",
    "qwen3-vl:2b-instruct",
    "qwen3-vl:4b-instruct"
]

COMPANIES = [
    {
        "id": "company_02_calbee",
        "name": "Calbee, Inc. (2229)",
        "golden_anchor": "dataset/benchmark_suite/company_02_calbee/golden_anchor.json",
        "pages": [
            "dataset/benchmark_suite/company_02_calbee/page_1.png",
            "dataset/benchmark_suite/company_02_calbee/page_2.png"
        ],
        "is_t_account": False
    },
    {
        "id": "company_01_yamato",
        "name": "Yamato Mobility & Mfg. (7886)",
        "golden_anchor": "dataset/benchmark_suite/company_01_yamato/golden_anchor.json",
        "pages": [
            "dataset/benchmark_suite/company_01_yamato/page_1.png"
        ],
        "is_t_account": True
    }
]

def run_vlm_extraction(model_name, image_path, is_t_account=False):
    with open(image_path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode("utf-8")
        
    if is_t_account:
        prompt = (
            "You are an expert OCR table engine. Extract the entire balance sheet table from this Japanese financial document into an HTML <table>.\n"
            "This document has a two-column balance sheet format (Assets on the left, Liabilities and Net Assets on the right).\n"
            "Output ONLY the HTML <table> element starting with <table> and ending with </table>.\n"
            "Preserve all account names exactly as written, and keep negative symbols (such as △, ▲, or -) and numerals intact."
        )
    else:
        prompt = (
            "You are an expert OCR table engine. Extract the entire balance sheet table from this Japanese financial document into an HTML <table>.\n"
            "Output ONLY the HTML <table> element starting with <table> and ending with </table>.\n"
            "Preserve all account names exactly as written, and keep negative symbols (such as △, ▲, or -) and numerals intact."
        )
        
    start_t = time.time()
    response = ollama.chat(
        model=model_name,
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
    elapsed = time.time() - start_t
    return response['message']['content'], elapsed

def evaluate_model_on_company(model_name, company_meta):
    comp_id = company_meta["id"]
    comp_name = company_meta["name"]
    golden_path = company_meta["golden_anchor"]
    pages = company_meta["pages"]
    is_t_account = company_meta["is_t_account"]
    
    out_dir = f"benchmark_results/{comp_id}/{model_name.replace(':', '_')}"
    os.makedirs(out_dir, exist_ok=True)
    
    total_time = 0.0
    combined_tbody = ""
    
    for p_idx, p_path in enumerate(pages):
        cached_file = os.path.join(out_dir, f"page_{p_idx+1}.html")
        time_file = os.path.join(out_dir, f"page_{p_idx+1}_time.txt")
        
        if os.path.exists(cached_file) and os.path.exists(time_file):
            print(f"[{model_name}] Loading cached {comp_name} Page {p_idx+1}...")
            with open(cached_file, "r", encoding="utf-8") as f:
                html_content = f.read()
            with open(time_file, "r", encoding="utf-8") as f:
                elapsed = float(f.read().strip())
        else:
            print(f"[{model_name}] Running on {comp_name} Page {p_idx+1} ({p_path})...")
            html_content, elapsed = run_vlm_extraction(model_name, p_path, is_t_account)
            with open(cached_file, "w", encoding="utf-8") as f:
                f.write(html_content)
            with open(time_file, "w", encoding="utf-8") as f:
                f.write(str(elapsed))
            print(f"   -> Finished in {elapsed:.2f}s")
            
        total_time += elapsed
        soup = BeautifulSoup(html_content, "html.parser")
        tbody = soup.find("tbody")
        if tbody:
            combined_tbody += str(tbody)
        else:
            table = soup.find("table")
            if table:
                combined_tbody += "".join(str(tr) for tr in table.find_all("tr"))
                
    combined_html_path = os.path.join(out_dir, "combined.html")
    with open(combined_html_path, "w", encoding="utf-8") as f:
        f.write(f"<table>{combined_tbody}</table>")
        
    # Evaluate against golden
    with open(golden_path, "r", encoding="utf-8") as f:
        golden_tree = NormalizedDocumentTree.model_validate_json(f.read())
        
    golden_dates = [p.date for p in golden_tree.periods]
    model_rows = parse_html_table_to_rows(combined_html_path)
    eval_golden = [it for it in golden_tree.items if it.role not in ["section_header", "group_header", "category_header"]]
    
    total_cells = 0
    exact_matches = 0
    mismatches = []
    model_idx = 0
    
    for g_idx, g_item in enumerate(eval_golden):
        g_acc = clean_account_name(g_item.account_jp)
        found_row = None
        for i in range(model_idx, min(model_idx + 4, len(model_rows))):
            m_acc, m_vals = model_rows[i]
            if g_acc in m_acc or m_acc in g_acc:
                found_row = m_vals
                model_idx = i + 1
                break
                
        if found_row is None:
            for i, (m_acc, m_vals) in enumerate(model_rows):
                if g_acc == m_acc:
                    found_row = m_vals
                    break

        for d_idx, date in enumerate(golden_dates):
            total_cells += 1
            expected_val = g_item.reported_values.get(date)
            
            if found_row is not None and d_idx < len(found_row):
                pred_raw = found_row[d_idx]
                pred_val = parse_numeric_value(pred_raw)
            else:
                pred_raw = "MISSING"
                pred_val = None
                
            if expected_val is None:
                if pred_val is None or pred_val == 0.0:
                    exact_matches += 1
                else:
                    mismatches.append({"account": g_item.account_jp, "date": date, "exp": "None", "pred": pred_raw})
            else:
                if pred_val == expected_val:
                    exact_matches += 1
                else:
                    mismatches.append({"account": g_item.account_jp, "date": date, "exp": f"{expected_val:,.0f}", "pred": pred_raw})

    em_rate = (exact_matches / total_cells * 100) if total_cells > 0 else 0.0
    return {
        "model": model_name,
        "company": comp_name,
        "total_time": total_time,
        "total_cells": total_cells,
        "exact_matches": exact_matches,
        "em_rate": em_rate,
        "mismatches": mismatches
    }

def main():
    print("=" * 80)
    print("STARTING COMPREHENSIVE VLM BENCHMARK MATRIX")
    print(f"Models:    {MODELS}")
    print(f"Companies: {[c['name'] for c in COMPANIES]}")
    print("=" * 80)
    
    # Pre-cache known GLM runs
    os.makedirs("benchmark_results/company_02_calbee/glm-ocr_latest", exist_ok=True)
    if os.path.exists("benchmark_results/calbee/glm_table.html"):
        with open("benchmark_results/calbee/glm_table.html", "r", encoding="utf-8") as f:
            t1 = f.read()
        with open("benchmark_results/company_02_calbee/glm-ocr_latest/page_1.html", "w", encoding="utf-8") as f:
            f.write(t1)
        with open("benchmark_results/company_02_calbee/glm-ocr_latest/page_1_time.txt", "w") as f:
            f.write("15.60")
            
    if os.path.exists("benchmark_results/calbee/glm_table_p2.html"):
        with open("benchmark_results/calbee/glm_table_p2.html", "r", encoding="utf-8") as f:
            t2 = f.read()
        with open("benchmark_results/company_02_calbee/glm-ocr_latest/page_2.html", "w", encoding="utf-8") as f:
            f.write(t2)
        with open("benchmark_results/company_02_calbee/glm-ocr_latest/page_2_time.txt", "w") as f:
            f.write("14.99")
            
    os.makedirs("benchmark_results/company_01_yamato/glm-ocr_latest", exist_ok=True)
    if os.path.exists("benchmark_results/yamato/glm_table.html"):
        with open("benchmark_results/yamato/glm_table.html", "r", encoding="utf-8") as f:
            t3 = f.read()
        with open("benchmark_results/company_01_yamato/glm-ocr_latest/page_1.html", "w", encoding="utf-8") as f:
            f.write(t3)
        with open("benchmark_results/company_01_yamato/glm-ocr_latest/page_1_time.txt", "w") as f:
            f.write("13.80")

    # Pre-cache known Qwen 2B on Calbee P1
    os.makedirs("benchmark_results/company_02_calbee/qwen3-vl_2b-instruct", exist_ok=True)
    if os.path.exists("benchmark_results/calbee/qwen2b_table_p1.html"):
        with open("benchmark_results/calbee/qwen2b_table_p1.html", "r", encoding="utf-8") as f:
            t4 = f.read()
        with open("benchmark_results/company_02_calbee/qwen3-vl_2b-instruct/page_1.html", "w", encoding="utf-8") as f:
            f.write(t4)
        with open("benchmark_results/company_02_calbee/qwen3-vl_2b-instruct/page_1_time.txt", "w") as f:
            f.write("17.35")

    results = []
    for comp in COMPANIES:
        print(f"\n>>> EVALUATING {comp['name']} <<<")
        for model in MODELS:
            try:
                res = evaluate_model_on_company(model, comp)
                results.append(res)
                print(f"[{model} on {comp['name']}] Latency: {res['total_time']:.2f}s | EM: {res['exact_matches']}/{res['total_cells']} ({res['em_rate']:.2f}%)")
            except Exception as e:
                print(f"[{model} on {comp['name']}] Error: {e}")
                
    summary_path = "benchmark_results/matrix_summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\nAll benchmark evaluations complete! Saved matrix summary to {summary_path}")

if __name__ == "__main__":
    main()
