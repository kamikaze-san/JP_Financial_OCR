import json
from typing import Optional, Dict, Any
from .models import RawDocumentTree

STAGE1_SYSTEM_PROMPT = """You are an expert Japanese financial document transcription engine.
Your task is to transcribe the financial table on this page with 100% fidelity into a structured JSON tree.

CRITICAL RULES:
1. DO NOT translate Japanese account names. Transcribe the exact characters as printed.
2. DO NOT modify, round, or calculate numbers. Extract raw strings exactly as they appear (including commas, "△", "▲", and "－").
3. CAPTURE HIERARCHY: Observe visual indentation, font weight, and grouping to assign each row:
   - "indent_level": 0 for section title (e.g., 資産の部), 1 for major group (e.g., 流動資産, 固定資産), 2 for category (e.g., 有形固定資産), 3 for individual line items.
   - "role": One of ["section_header", "group_header", "category_header", "item", "contra_item", "netted_item", "subtotal", "grand_total"].
4. METADATA: Accurately capture:
   - "title": Table/document title.
   - "unit_raw": The exact unit string (e.g., "単位：千円", "単位：百万円", "単位：円").
   - "is_amended": true if "(訂正後)" or similar correction indicator is present, else false.
   - "periods": Column headers with exact period labels and dates.

Return ONLY valid JSON matching this schema:
{
  "metadata": {
    "title": "string",
    "is_amended": boolean,
    "unit_raw": "string",
    "periods": [
      {"col_index": 1, "raw_label": "string", "raw_date": "string"},
      {"col_index": 2, "raw_label": "string", "raw_date": "string"}
    ]
  },
  "rows": [
    {
      "row_index": integer,
      "indent_level": integer,
      "account_raw": "string",
      "role": "section_header" | "group_header" | "category_header" | "item" | "contra_item" | "netted_item" | "subtotal" | "grand_total",
      "values": ["string", "string"]
    }
  ]
}
"""

def get_vlm_prompt() -> str:
    """Returns the standardized Stage 1 VLM extraction prompt."""
    return STAGE1_SYSTEM_PROMPT

def parse_vlm_response(response_text: str) -> RawDocumentTree:
    """Parses raw VLM JSON response into validated RawDocumentTree."""
    # Strip markdown code fences if present
    clean_text = response_text.strip()
    if clean_text.startswith("```json"):
        clean_text = clean_text[7:]
    elif clean_text.startswith("```"):
        clean_text = clean_text[3:]
    if clean_text.endswith("```"):
        clean_text = clean_text[:-3]
    clean_text = clean_text.strip()
    
    data = json.loads(clean_text)
    return RawDocumentTree(**data)
