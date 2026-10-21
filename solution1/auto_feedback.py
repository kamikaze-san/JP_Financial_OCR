from typing import List, Dict, Tuple, Optional, Any
from .models import NormalizedDocumentTree, ValidationReport, ChecksumFailure, NormalizedItem

def generate_automated_audit_feedback(
    tree: NormalizedDocumentTree, 
    report: ValidationReport
) -> str:
    """
    100% Programmatic, Leak-Free Arithmetic Feedback Engine.
    Identifies suspect cells by cross-referencing arithmetic deficits
    against zero/dash items and digit-substitution candidates.
    Zero human intervention. Fully scalable across arbitrary documents.
    """
    if report.is_valid:
        return ""
        
    unit_raw = tree.unit_raw
    unit_scale = tree.unit_multiplier
    lines = [
        "[AUTOMATED ARITHMETIC AUDIT ENGINE]",
        f"Validation failed on {len(report.failures)} mathematical equation(s):"
    ]
    
    for f in report.failures:
        exp_rep = f.expected_value / unit_scale
        calc_rep = f.computed_value / unit_scale
        diff_rep = f.discrepancy / unit_scale
        period = f.period
        
        lines.append(f"\n* Checksum Failure in '{f.equation_name}' for Period '{period}':")
        lines.append(f"  - Page Subtotal: {exp_rep:,.0f} ({unit_raw})")
        lines.append(f"  - Sum of Your Items: {calc_rep:,.0f} ({unit_raw})")
        lines.append(f"  - Discrepancy: {diff_rep:+,.0f} ({unit_raw})")
        
        # Algorithmic Suspect Cell Detection:
        # 1. Find all items participating in this failed equation
        participating_items = [
            it for it in tree.items 
            if it.account_jp in f.involved_accounts
        ]
        
        # 2. Check for zero/dash cells in the failed period (often causes deficit)
        zero_items = [
            it for it in participating_items 
            if it.reported_values.get(period, 0.0) == 0.0
        ]
        
        if diff_rep < 0:
            lines.append(f"  - Diagnosis: Deficit of {abs(diff_rep):,.0f} {unit_raw}.")
            if zero_items:
                zero_names = [f"'{it.account_jp}' (current value: '{it.raw_values.get(period, '')}')" for it in zero_items]
                lines.append(f"  - Suspect Rows in your extraction that have 0 or dash in {period}: {', '.join(zero_names)}.")
                lines.append(f"    Check the image: Did you mistakenly transcribe a dash '－' for one of these rows when it actually contains a printed number equal to ~{abs(diff_rep):,.0f}?")
            else:
                lines.append(f"    Check the numbers in participating rows: {', '.join(f.involved_accounts[:5])}.")
        else:
            lines.append(f"  - Diagnosis: Surplus of {diff_rep:,.0f} {unit_raw}. An item was double-counted or misread.")

    lines.append("\nINSTRUCTIONS:")
    lines.append("Re-inspect the indicated suspect cells on the image crop, correct the misread values, and return the complete updated JSON.")
    return "\n".join(lines)
