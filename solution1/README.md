# Solution 1: Baseline Japanese Balance Sheet Parser & Validator

This folder contains the complete reference implementation of **Solution 1 Baseline** for enterprise Japanese financial statement extraction.

---

## 1. Directory Structure

```
solution1/
├── __init__.py
├── models.py             # Pydantic models for Stage 1 (Raw) & Stage 2 (Normalized)
├── pdf_renderer.py       # PyMuPDF (fitz) 300 DPI page-to-image renderer
├── baseline_data.py      # Stage 1 reference perception data for sample PDF
├── normalizer.py         # Deterministic JPY scaling, sign cleaner, context-aware J-GAAP mapper
├── validator.py          # Deterministic MECE Accounting Validator (pure Python)
├── exporter.py           # Multi-format exporter: Canonical JSON, Excel (.xlsx), Markdown
├── vlm_interface.py      # Standardized VLM prompt, schema, and API integration hook
├── run_pipeline.py       # Master pipeline runner
├── rendered_images/      # 300 DPI rendered PNG pages
└── output/               # Generated output artifacts:
    ├── canonical_balance_sheet.json
    ├── balance_sheet_model.xlsx
    └── balance_sheet_summary.md
```

---

## 2. Quickstart: Running the Baseline Pipeline

From the root of the repository, execute:

```bash
python -m solution1.run_pipeline
```

### What Happens:
1. **Rendering**: Converts [balance_sheet_after_correction.pdf](file:///c:/Users/NewGr/Downloads/JP_Parser/balance_sheet_after_correction.pdf) to `rendered_images/balance_sheet_after_correction_page_1.png` at 300 DPI.
2. **Perception**: Loads as-reported hierarchy, preserving exact Japanese strings and signs (`△`, `－`).
3. **Normalization**:
   * Scales numbers by unit multiplier (`千円` $\to \times 1,000$ base JPY).
   * Context-aware taxonomy mapping disambiguates shared account names (e.g., `その他` under Current Assets vs. PPE vs. Intangibles).
4. **MECE Verification**: Executes 20 distinct accounting equations across both fiscal periods.
5. **Multi-Format Export**: Generates:
   * [canonical_balance_sheet.json](file:///c:/Users/NewGr/Downloads/JP_Parser/solution1/output/canonical_balance_sheet.json): Full hierarchical data with validation status.
   * [balance_sheet_model.xlsx](file:///c:/Users/NewGr/Downloads/JP_Parser/solution1/output/balance_sheet_model.xlsx): Styled financial model with native Excel formulas (`=SUM(...)`, `=Gross - AccDep`).
   * [balance_sheet_summary.md](file:///c:/Users/NewGr/Downloads/JP_Parser/solution1/output/balance_sheet_summary.md): Markdown report.

---

## 3. How to Benchmark Other Smaller VLMs

To test smaller VLMs (e.g., Qwen2-VL, Ollama, Llama-3.2-Vision):

1. Inspect [solution1/vlm_interface.py](file:///c:/Users/NewGr/Downloads/JP_Parser/solution1/vlm_interface.py) for the standardized extraction prompt and output JSON schema.
2. Send `rendered_images/balance_sheet_after_correction_page_1.png` + `STAGE1_SYSTEM_PROMPT` to your target VLM.
3. Pass the resulting JSON string to `parse_vlm_response(raw_output)`.
4. Run `normalize_tree(raw_vlm_tree)` and `AccountingValidator().validate(norm_tree)`.
5. Score the model on:
   * **ACS (Arithmetic Consistency Score)**: Did it pass all 20 checksums?
   * **Exact Match (EM)**: Character/digit differences vs. `baseline_data.py`.
