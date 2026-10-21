import os
import re
import json
import unicodedata
from typing import Dict, Any, Optional, Tuple, List
from .models import (
    RawDocumentTree,
    RawRow,
    NormalizedDocumentTree,
    NormalizedItem,
    NormalizedPeriod
)

def normalize_japanese_text(text: str) -> str:
    """Converts full-width numbers/characters to half-width and strips excess whitespace."""
    if not text:
        return ""
    # Normalize unicode (NFKC converts full-width ０-９ to 0-9 and full-width spaces)
    normalized = unicodedata.normalize("NFKC", text)
    return normalized.strip()

def parse_unit_multiplier(unit_raw: str) -> int:
    """Parses unit declarations like '単位：千円' into integer scale multiplier."""
    norm = normalize_japanese_text(unit_raw)
    if "百万円" in norm:
        return 1_000_000
    elif "十億円" in norm:
        return 1_000_000_000
    elif "千円" in norm:
        return 1_000
    elif "円" in norm:
        return 1
    # Default fallback
    return 1_000

def parse_numeric_value(val_str: str) -> float:
    """
    Parses financial string into float.
    Handles '△', '▲', '()', '－', '―', commas, and whitespace.
    """
    if not val_str:
        return 0.0
        
    s = normalize_japanese_text(val_str)
    
    # Nil / Dash indicators
    if s in ["-", "―", "－", "―", "ー", "nil", "null", ""]:
        return 0.0
        
    is_negative = False
    if s.startswith("△") or s.startswith("▲") or (s.startswith("(") and s.endswith(")")) or s.startswith("-"):
        is_negative = True
        s = s.replace("△", "").replace("▲", "").replace("(", "").replace(")", "").replace("-", "")
        
    # Remove commas and spaces
    s = s.replace(",", "").replace(" ", "")
    
    try:
        val = float(s)
        return -val if is_negative else val
    except ValueError:
        return 0.0

def parse_japanese_date(date_raw: str) -> Tuple[str, int]:
    """
    Parses strings like '2025 年３月 31 日' or '2026年3月31日' into ('2025-03-31', 2025).
    Supports Western years and Japanese imperial eras (Reiwa).
    """
    s = normalize_japanese_text(date_raw)
    
    # Check for Reiwa (令和)
    reiwa_match = re.search(r"令和\s*(\d+|元)\s*年\s*(\d+)\s*月\s*(\d+)\s*日", s)
    if reiwa_match:
        era_yr = 1 if reiwa_match.group(1) == "元" else int(reiwa_match.group(1))
        western_yr = 2018 + era_yr
        month = int(reiwa_match.group(2))
        day = int(reiwa_match.group(3))
        iso_date = f"{western_yr:04d}-{month:02d}-{day:02d}"
        return iso_date, western_yr
        
    # Western year match
    western_match = re.search(r"(\d{4})\s*年\s*(\d+)\s*月\s*(\d+)\s*日", s)
    if western_match:
        yr = int(western_match.group(1))
        month = int(western_match.group(2))
        day = int(western_match.group(3))
        iso_date = f"{yr:04d}-{month:02d}-{day:02d}"
        return iso_date, yr
        
    # Fallback search for 4 digit year
    yr_match = re.search(r"(\d{4})", s)
    yr = int(yr_match.group(1)) if yr_match else 2025
    return f"{yr}-03-31", yr


class TaxonomyMapper:
    """Maps extracted Japanese account names to official J-GAAP taxonomy & M&A buckets with context awareness."""
    
    def __init__(self, taxonomy_path: Optional[str] = None):
        if taxonomy_path is None:
            taxonomy_path = os.path.join(
                os.path.dirname(__file__), "..", "references", "j_gaap_bs_taxonomy.json"
            )
            
        self.context_map: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self.flat_map: Dict[str, Dict[str, Any]] = {}
        if os.path.exists(taxonomy_path):
            with open(taxonomy_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                self._build_index(data)
                
    def _build_index(self, data: Dict[str, Any]):
        for section in data.get("sections", []):
            sec_name = section.get("section_jp", "")
            if "total_element" in section and section["total_element"]:
                tot = section["total_element"]
                entry = {
                    "account_en": tot["account_en"],
                    "xbrl_element": tot["xbrl_element"],
                    "ma_bucket": "grand_total"
                }
                self.flat_map[tot["account_jp"]] = entry
                self.context_map[(tot["account_jp"], sec_name)] = entry
                
            for group in section.get("groups", []):
                grp_name = group.get("group_jp", "")
                if "total_element" in group and group["total_element"]:
                    tot = group["total_element"]
                    entry = {
                        "account_en": tot["account_en"],
                        "xbrl_element": tot["xbrl_element"],
                        "ma_bucket": "subtotal"
                    }
                    self.flat_map[tot["account_jp"]] = entry
                    self.context_map[(tot["account_jp"], grp_name)] = entry
                    
                for item in group.get("standard_items", []):
                    entry = {
                        "account_en": item["account_en"],
                        "xbrl_element": item["xbrl_element"],
                        "ma_bucket": item.get("ma_bucket", "general")
                    }
                    self.context_map[(item["account_jp"], grp_name)] = entry
                    if item["account_jp"] not in self.flat_map:
                        self.flat_map[item["account_jp"]] = entry
                        
                for cat in group.get("categories", []):
                    cat_name = cat.get("category_jp", "")
                    if "total_element" in cat and cat["total_element"]:
                        tot = cat["total_element"]
                        entry = {
                            "account_en": tot["account_en"],
                            "xbrl_element": tot["xbrl_element"],
                            "ma_bucket": "subtotal"
                        }
                        self.flat_map[tot["account_jp"]] = entry
                        self.context_map[(tot["account_jp"], cat_name)] = entry
                        
                    for item in cat.get("standard_items", []):
                        entry = {
                            "account_en": item["account_en"],
                            "xbrl_element": item["xbrl_element"],
                            "ma_bucket": item.get("ma_bucket", "general")
                        }
                        self.context_map[(item["account_jp"], cat_name)] = entry
                        self.context_map[(item["account_jp"], grp_name)] = entry
                        if item["account_jp"] not in self.flat_map:
                            self.flat_map[item["account_jp"]] = entry

    def map_account(self, account_raw: str, role: str, context: Optional[str] = None) -> Tuple[str, Optional[str], str]:
        """Returns (account_en, xbrl_element, ma_bucket) considering parent context."""
        clean = normalize_japanese_text(account_raw)
        
        # 1. Try context-specific match
        if context and (clean, context) in self.context_map:
            match = self.context_map[(clean, context)]
            return match["account_en"], match["xbrl_element"], match["ma_bucket"]
            
        # 2. Try flat match
        if clean in self.flat_map:
            match = self.flat_map[clean]
            return match["account_en"], match["xbrl_element"], match["ma_bucket"]
            
        # 3. Clean stripped bracket variations like 建物及び構築物（純額）
        base_name = re.sub(r"[（\(].*?[）\)]", "", clean).strip()
        if context and (base_name, context) in self.context_map:
            match = self.context_map[(base_name, context)]
            suffix = " (Net)" if "純額" in clean else ""
            return f"{match['account_en']}{suffix}", match["xbrl_element"], match["ma_bucket"]
        if base_name in self.flat_map:
            match = self.flat_map[base_name]
            suffix = " (Net)" if "純額" in clean else ""
            return f"{match['account_en']}{suffix}", match["xbrl_element"], match["ma_bucket"]
            
        # Fallback mappings for known patterns
        if "合計" in clean:
            return clean, None, "subtotal"
        if "減価償却累計額" in clean:
            return "Accumulated depreciation", "AccumulatedDepreciationPPE", "contra_asset"
            
        return clean, None, "other_unclassified"


def normalize_tree(raw_tree: RawDocumentTree) -> NormalizedDocumentTree:
    """Converts Stage 1 RawDocumentTree into Stage 2 NormalizedDocumentTree."""
    unit_multiplier = parse_unit_multiplier(raw_tree.metadata.unit_raw)
    mapper = TaxonomyMapper()
    
    # 1. Parse periods
    normalized_periods: List[NormalizedPeriod] = []
    for p in raw_tree.metadata.periods:
        iso_date, fy = parse_japanese_date(p.raw_date)
        is_cons = "連結" in raw_tree.metadata.title or "連結" in p.raw_label
        normalized_periods.append(NormalizedPeriod(
            col_index=p.col_index,
            raw_label=p.raw_label,
            date=iso_date,
            fiscal_year=fy,
            is_consolidated=is_cons
        ))
        
    date_cols = [p.date for p in normalized_periods]
    
    # 2. Parse items and track parent assets & context group
    normalized_items: List[NormalizedItem] = []
    current_context: Optional[str] = None
    current_parent_asset: Optional[str] = None
    
    for r in raw_tree.rows:
        clean_name = normalize_japanese_text(r.account_raw)
        role = r.role
        
        # Deterministic accounting role canonicalization:
        # Don't rely on small LLMs to guess technical role labels when the Japanese string is unambiguous
        if "純額" in clean_name and role != "netted_item":
            role = "netted_item"
        elif "合計" in clean_name:
            if clean_name in ["資産合計", "負債及び純資産合計"]:
                role = "grand_total"
            else:
                role = "subtotal"
        elif "減価償却累計額" in clean_name or "引当金" in clean_name and "△" in str(r.values):
            role = "contra_item"
        
        # Track active category / group context
        if role in ["group_header", "category_header"]:
            current_context = clean_name
        elif "合計" in clean_name and role == "subtotal":
            # Keep context until subtotal row is mapped
            pass
            
        # Track parent asset for indirect depreciation
        if role == "item" and not ("減価償却累計額" in clean_name or "純額" in clean_name):
            current_parent_asset = clean_name
            parent_link = None
        elif role in ["contra_item", "netted_item"] or "減価償却累計額" in clean_name or "純額" in clean_name:
            parent_link = current_parent_asset
        else:
            parent_link = None
            if role in ["subtotal", "grand_total"]:
                current_parent_asset = None
                
        acc_en, xbrl_elem, ma_bucket = mapper.map_account(clean_name, role, context=current_context)
        
        raw_vals: Dict[str, str] = {}
        rep_vals: Dict[str, float] = {}
        norm_vals: Dict[str, float] = {}
        
        for idx, date in enumerate(date_cols):
            raw_str = r.values[idx] if idx < len(r.values) else ""
            rep_float = parse_numeric_value(raw_str)
            raw_vals[date] = raw_str
            rep_vals[date] = rep_float
            norm_vals[date] = rep_float * unit_multiplier
            
        normalized_items.append(NormalizedItem(
            row_index=r.row_index,
            indent_level=r.indent_level,
            account_jp=clean_name,
            account_en=acc_en,
            xbrl_element=xbrl_elem,
            ma_bucket=ma_bucket,
            role=role,
            raw_values=raw_vals,
            reported_values=rep_vals,
            normalized_values=norm_vals,
            parent_account=parent_link
        ))
        
    return NormalizedDocumentTree(
        document_title=raw_tree.metadata.title,
        is_amended=raw_tree.metadata.is_amended,
        currency="JPY",
        unit_multiplier=unit_multiplier,
        unit_raw=raw_tree.metadata.unit_raw,
        periods=normalized_periods,
        items=normalized_items
    )
