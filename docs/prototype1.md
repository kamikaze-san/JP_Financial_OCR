> **Historical document.** This is the specification written for the first prototype. The current design and results are in `solution2/README.md` and `solution2/RESULTS.md`; the links below point to files on the original author's machine.

# Enterprise Japanese Financial Table Parser — Prototype 1 Specification
**Document Version:** 1.0  
**Target Domain:** Enterprise Document Intelligence for M&A Due Diligence & Financial Modeling  
**Primary Language/Jurisdiction:** Japanese Corporate Filings (J-GAAP / EDINET)  
**Sample Reference Document:** [balance_sheet_after_correction.pdf](file:///c:/Users/NewGr/Downloads/JP_Parser/balance_sheet_after_correction.pdf)  
**Reference Taxonomy:** [j_gaap_bs_taxonomy.json](file:///c:/Users/NewGr/Downloads/JP_Parser/references/j_gaap_bs_taxonomy.json)

---

## 1. Executive Summary & Problem Definition

In enterprise M&A due diligence, financial analysts manually extract, normalize, and reconcile financial statements from Japanese corporate disclosures (e.g., *Yukashoken Hokokusho* / Annual Securities Reports). 

### The Core Challenges:
1. **Unstructured & Varied Layouts**: Reports range from clean digital PDFs to scanned/skewed documents with borderless tables, colored alternating row stripes, and implicit whitespace grids.
2. **Japanese Financial Conventions**:
   * Negative indicators: `△` or `▲` for subtractions/contra accounts.
   * Nil/Zero values: `－` denoting zero or not applicable.
   * Unit multipliers: Header declarations like `（単位：千円）` (thousands of JPY) or `（単位：百万円）` (millions of JPY).
   * Reporting state: Revisions marked by `（訂正後）` (amended/revised filing).
   * Hierarchy and netting: Inline accumulated depreciation (`減価償却累計額`) under gross assets.
3. **The Failure of Traditional OCR / Heuristic Extractors**:
   Traditional table extractors (Tabula, Camelot, heuristic bounding-box parsers) fail on borderless grids, misread negative signs as character noise, and flatten nested parent-child hierarchies into misleading rows.
4. **The Failure of Naive End-to-End LLMs**:
   Prompting a single LLM to OCR, translate, standardize, and calculate all at once leads to high numeric hallucination rates and unverified data.

---

## 2. Defining "Optimal" in Enterprise Document Intelligence

"Optimal" is not merely character recognition accuracy. For enterprise M&A and financial modeling, an optimal parser must satisfy **6 core dimensions**:

| Dimension | Definition & Metric |
| :--- | :--- |
| **1. Semantic Hierarchy Fidelity** | Preserves parent-child trees (`Section` $\to$ `Group` $\to$ `Category` $\to$ `Line Item` $\to$ `Contra`). Prevents double-counting in downstream financial models. |
| **2. Contextual Normalization** | Canonically resolves units (`千円` $\to \times 1,000$), signs (`△` $\to$ `-`), and dates (`2025年3月31日` $\to$ `2025-03-31`). |
| **3. Deterministic Arithmetic Verification** | Every extracted page is validated by mathematical accounting invariants. Evaluated by **ACS (Arithmetic Consistency Score)**: % of tables where internal sums balance $100\%$. |
| **4. Zero-Shot Generalizability** | Operates across unknown layouts (striped backgrounds, borderless, scans, multi-page) without hardcoded bounding box templates. |
| **5. Auditability & Provenance** | Every extracted cell maintains page number and bounding-box coordinates (`[x0, y0, x1, y1]`) for click-to-source deal room audit trails. |
| **6. Scale & Throughput Economics** | Discards non-financial pages, using expensive VLM calls only for visual table extraction, while arithmetic and normalization execute in pure Python. |

---

## 3. Two-Stage Decoupled System Architecture

The fundamental design rule of this system is:  
**The LLM/VLM handles Perception (vision & text); Deterministic Python handles Arithmetic & Verification.**

```
                      ┌──────────────────────────────────────────────┐
                      │              Source PDF Document             │
                      └──────────────────────┬───────────────────────┘
                                             │
                                             ▼
                                 [ PDF Page-to-Image ]
                              (PyMuPDF / pdf2image @ 300 DPI)
                                             │
                                             ▼
                      ┌──────────────────────────────────────────────┐
                      │          STAGE 1: VLM PERCEPTION             │
                      │  (Extracts As-Reported Tree & Exact Strings) │
                      └──────────────────────┬───────────────────────┘
                                             │
                                             ▼ Raw JSON
                      ┌──────────────────────────────────────────────┐
                      │    STAGE 2: NORMALIZATION & TAXONOMY         │
                      │                                              │
                      │  2A. Deterministic Normalizer (Pure Python): │
                      │      • Unit multiplier: '千円' -> * 1,000    │
                      │      • Signs: '△' -> negative, '－' -> 0     │
                      │      • Dates: '2025年3月31日' -> '2025-03-31'│
                      │                                              │
                      │  2B. Semantic Taxonomy Mapping:              │
                      │      • 85-90% Standard J-GAAP Dict Lookup    │
                      │      • 10-15% Text LLM for Custom Accounts   │
                      └──────────────────────┬───────────────────────┘
                                             │
                                             ▼ Normalized Tree
                      ┌──────────────────────────────────────────────┐
                      │   STAGE 3: DETERMINISTIC MECE VALIDATOR      │
                      │        (Pure Python Arithmetic Engine)       │
                      └──────────────────────┬───────────────────────┘
                                             │
                         ┌───────────────────┴───────────────────┐
                         │ All Invariants Balance?               │
                         ├───────────────────┬───────────────────┤
                        YES                  NO                  │
                         │                   │                   │
                         ▼                   ▼                   ▼
              ┌─────────────────────┐   [ Targeted Feedback Loop ]
              │   STAGE 4: OUTPUTS  │   "FY2026 Current Assets sum
              │                     │    differs by -7. Crop rows 5-9
              │  1. Canonical JSON  │    and re-read digits."
              │  2. Excel (.xlsx)   │            │
              │     (Live Formulas) │            └─► (Max 1 targeted retry)
              │  3. Markdown Table  │
              └─────────────────────┘
```

---

## 4. Stage Specifications & Prompts

### Stage 1: Vision Perception Prompt (VLM)
* **Goal**: Faithful transcription of the visual layout into an as-reported JSON tree.
* **Input**: High-resolution image crop of the table page.
* **Output Schema**:
```json
{
  "metadata": {
    "title": "string",
    "is_amended": boolean,
    "unit_raw": "string",
    "periods": [
      {"col_index": 1, "raw_label": "string", "raw_date": "string"}
    ]
  },
  "rows": [
    {
      "row_index": integer,
      "indent_level": integer,
      "account_raw": "string",
      "role": "section_header | group_header | item | contra_item | netted_item | subtotal | grand_total",
      "values": ["string", "string"]
    }
  ]
}
```

### Stage 2: Normalization & Taxonomy
1. **Numeric Scaling**: Multiplies string numbers by the detected unit (`千円` = 1,000; `百万円` = 1,000,000; `円` = 1).
2. **Taxonomy Anchor**: Maps extracted Japanese names against [references/j_gaap_bs_taxonomy.json](file:///c:/Users/NewGr/Downloads/JP_Parser/references/j_gaap_bs_taxonomy.json):
   * `現金及び預金` $\to$ `cash_and_deposits` (M&A Bucket: `cash_and_equivalents`)
   * `売掛金` $\to$ `accounts_receivable_trade` (M&A Bucket: `trade_receivables`)
   * `建設仮勘定` $\to$ `construction_in_progress` (M&A Bucket: `ppe_tangible`)

### Stage 3: MECE Accounting Arithmetic Invariants
Balance sheets are mathematically structured as **trees of partitions** (Mutually Exclusive, Collectively Exhaustive):
* **Section Partitions**:
  $$\text{Total Assets} = \text{Total Current Assets} + \text{Total Fixed Assets} + [\text{Deferred Assets}]$$
* **Group Partitions**:
  $$\text{Total Fixed Assets} = \text{Total PPE} + \text{Total Intangibles} + \text{Total Investments \& Other}$$
* **Component Rollups**:
  $$\text{Total Current Assets} = \sum (\text{Current Asset items}) - \text{Allowance for doubtful accounts}$$
* **PPE Netting (Indirect Method)**:
  $$\text{Net PPE item} = \text{Gross PPE item} - \text{Accumulated Depreciation}$$
  $$\text{Total PPE} = \sum (\text{Net PPE items}) + \text{Land} + \text{Construction in progress}$$

#### Edge-Case Tolerances:
* **Rounding Slack**: Allows $\pm 1$ unit step per component block to account for legal rounding/truncation (`切捨て` / `四捨五入`).
* **Multi-Page Handling**: Evaluates section rollups per page; checks grand identity ($\text{Assets} = \text{Liab} + \text{Equity}$) after all pages parse.

---

## 5. Downstream Flow & End-User Experience

| Consumer | Primary Format | Value Delivered |
| :--- | :--- | :--- |
| **M&A Financial Analyst** | **Excel (`.xlsx`)** | Subtotals and totals use native `=SUM(...)` and `=C12-C13` formulas rather than static values, allowing analysts to immediately run DCF and LBO sensitivity models. |
| **Data Pipelines / AI Agents** | **Hierarchical JSON** | Lossless tree structure with normalized JPY values, ISO dates, and M&A taxonomy tags. |
| **Due Diligence Deal Room** | **Interactive Web UI** | Every cell connects to bounding box `[x0, y0, x1, y1]` for click-to-highlight source auditability. |

---

## 6. GEPA (Genetic-Pareto) Prompt Evolution Framework

Instead of manual trial-and-error prompt engineering, the parser utilizes **GEPA** (*Genetic-Pareto Evolutionary Prompt Adaptation*):

```
Candidate Prompts Population
        │
        ▼ Evaluate on Seed Dataset (EDINET Filings)
Pareto Frontier Evaluation:
  • Objective 1: Arithmetic Consistency Score (ACS) (100% mathematical pass rate)
  • Objective 2: Numeric Exact Match (EM)
  • Objective 3: Structural Indentation Similarity (TEDS)
        │
        ▼ On Equality Failure
Natural Language Reflection:
  "Discrepancy in Current Assets: sum was 5,863,683; reported 5,863,684 (diff: -1).
   Row 3 '受取手形' parsed 20,658 instead of 20,659 on striped background."
        │
        ▼ Crossover & Mutation
Next-Generation Evolved Prompt
```
This continuous feedback loop allows the system to autonomously adapt to edge cases (striped rows, font weights, footnote attachments) across unknown documents.
