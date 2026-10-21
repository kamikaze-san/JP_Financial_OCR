from typing import List, Dict, Optional, Any
from pydantic import BaseModel, Field

# ============================================================================
# STAGE 1 MODELS (Raw As-Reported Perception)
# ============================================================================

class RawPeriod(BaseModel):
    col_index: int
    raw_label: str
    raw_date: str

class RawMetadata(BaseModel):
    title: str
    is_amended: bool = False
    unit_raw: str
    periods: List[RawPeriod]

class RawRow(BaseModel):
    row_index: int
    indent_level: int
    account_raw: str
    role: str  # "section_header", "group_header", "category_header", "item", "contra_item", "netted_item", "subtotal", "grand_total"
    values: List[str]  # Raw strings exactly as printed, e.g. ["2,002,429", "608,281"]

class RawDocumentTree(BaseModel):
    metadata: RawMetadata
    rows: List[RawRow]


# ============================================================================
# STAGE 2 MODELS (Normalized & Canonical Data)
# ============================================================================

class NormalizedPeriod(BaseModel):
    col_index: int
    raw_label: str
    date: str  # ISO-8601 YYYY-MM-DD
    fiscal_year: int
    is_consolidated: bool = True

class NormalizedItem(BaseModel):
    row_index: int
    indent_level: int
    account_jp: str
    account_en: str
    xbrl_element: Optional[str] = None
    ma_bucket: str  # e.g. "cash_and_equivalents", "trade_receivables", "ppe_tangible"
    role: str       # "item", "contra_item", "netted_item", "subtotal", "grand_total"
    raw_values: Dict[str, str]        # date -> raw string
    reported_values: Dict[str, float]   # date -> float as reported on page
    normalized_values: Dict[str, float] # date -> standard base currency (JPY)
    parent_account: Optional[str] = None # For contra/net items, points to parent asset

class ChecksumFailure(BaseModel):
    equation_name: str
    period: str
    expected_value: float
    computed_value: float
    discrepancy: float
    involved_accounts: List[str]
    tolerance_applied: float

class ValidationReport(BaseModel):
    is_valid: bool
    total_equations_tested: int
    passed_equations: int
    failed_equations: int
    failures: List[ChecksumFailure] = []

class NormalizedDocumentTree(BaseModel):
    document_title: str
    is_amended: bool
    currency: str = "JPY"
    unit_multiplier: int
    unit_raw: str
    periods: List[NormalizedPeriod]
    items: List[NormalizedItem]
    validation_report: Optional[ValidationReport] = None
