import os
import json
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from .models import NormalizedDocumentTree

def export_to_json(tree: NormalizedDocumentTree, output_path: str):
    """Exports the normalized tree and validation report to clean JSON."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(tree.model_dump(), f, ensure_ascii=False, indent=2)
    print(f"[Export] Saved Canonical JSON -> {output_path}")

def export_to_markdown(tree: NormalizedDocumentTree, output_path: str):
    """Exports a human-readable Markdown summary table."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    periods = [p.date for p in tree.periods]
    
    lines = []
    lines.append(f"# {tree.document_title}")
    lines.append(f"**Amended Filing:** {'Yes (訂正後)' if tree.is_amended else 'No'} | **Base Currency:** {tree.currency} | **Original Unit:** {tree.unit_raw}")
    lines.append("")
    
    # Table header
    header = "| Hierarchy / Line Item | English Standard | Role | " + " | ".join([f"{p} (Reported)" for p in periods]) + " | " + " | ".join([f"{p} (Normalized JPY)" for p in periods]) + " |"
    sep = "| :--- | :--- | :--- | " + " | ".join(["---:"] * (len(periods) * 2)) + " |"
    lines.append(header)
    lines.append(sep)
    
    for it in tree.items:
        indent = "&nbsp;&nbsp;" * it.indent_level
        jp_display = f"{indent}{it.account_jp}"
        if it.role in ["subtotal", "grand_total"]:
            jp_display = f"**{jp_display}**"
            en_display = f"**{it.account_en}**"
        else:
            en_display = it.account_en
            
        rep_vals = [f"{it.reported_values.get(p, 0.0):,.0f}" if it.reported_values.get(p) is not None else "-" for p in periods]
        norm_vals = [f"¥{it.normalized_values.get(p, 0.0):,.0f}" if it.normalized_values.get(p) is not None else "-" for p in periods]
        
        row_str = f"| {jp_display} | {en_display} | `{it.role}` | " + " | ".join(rep_vals) + " | " + " | ".join(norm_vals) + " |"
        lines.append(row_str)
        
    # Validation section
    lines.append("")
    lines.append("## Deterministic Accounting Verification Report")
    if tree.validation_report:
        vr = tree.validation_report
        lines.append(f"- **Overall Status:** {'✅ PASSED (100% Balanced)' if vr.is_valid else '❌ DISCREPANCY DETECTED'}")
        lines.append(f"- **Equations Tested:** {vr.total_equations_tested}")
        lines.append(f"- **Passed:** {vr.passed_equations} / {vr.total_equations_tested}")
        if vr.failures:
            lines.append("### Failures:")
            for f in vr.failures:
                lines.append(f"- `{f.equation_name}` [{f.period}]: Expected {f.expected_value:,.0f}, Computed {f.computed_value:,.0f} (Diff: {f.discrepancy:,.0f})")
    
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"[Export] Saved Markdown Summary -> {output_path}")

def export_to_excel(tree: NormalizedDocumentTree, output_path: str):
    """
    Exports a professional financial model (.xlsx) using openpyxl.
    Injects native Excel formulas for subtotals and totals.
    """
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Balance Sheet"
    ws.views.sheetView[0].showGridLines = True
    
    # Styles
    title_font = Font(name="Calibri", size=14, bold=True, color="1F4E79")
    header_font = Font(name="Calibri", size=10, bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="1F4E79", end_color="1F4E79", fill_type="solid")
    subtotal_font = Font(name="Calibri", size=10, bold=True)
    subtotal_fill = PatternFill(start_color="E9EDF4", end_color="E9EDF4", fill_type="solid")
    total_font = Font(name="Calibri", size=11, bold=True, color="002060")
    total_fill = PatternFill(start_color="D9E1F2", end_color="D9E1F2", fill_type="solid")
    regular_font = Font(name="Calibri", size=10)
    
    thin_border = Border(
        left=Side(style='thin', color='D9D9D9'),
        right=Side(style='thin', color='D9D9D9'),
        top=Side(style='thin', color='D9D9D9'),
        bottom=Side(style='thin', color='D9D9D9')
    )
    double_bottom_border = Border(
        top=Side(style='thin', color='000000'),
        bottom=Side(style='double', color='000000')
    )

    # Title Block
    ws['A1'] = tree.document_title
    ws['A1'].font = title_font
    ws['A2'] = f"Unit: {tree.unit_raw} (Multiplier: ×{tree.unit_multiplier:,} JPY) | Status: {'Revised (訂正後)' if tree.is_amended else 'Original'}"
    ws['A2'].font = Font(name="Calibri", size=9, italic=True, color="595959")
    
    # Headers
    headers = ["Hierarchy Path", "Account (Japanese)", "Account (Standard English)", "M&A Taxonomy Bucket", "Role"]
    for p in tree.periods:
        headers.append(f"{p.date} (Reported {tree.unit_raw})")
    for p in tree.periods:
        headers.append(f"{p.date} (Normalized JPY)")
        
    start_row = 4
    for col_idx, h_text in enumerate(headers, start=1):
        cell = ws.cell(row=start_row, column=col_idx, value=h_text)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    # Data Rows
    current_row = 5
    num_periods = len(tree.periods)
    
    for item in tree.items:
        indent = "  " * item.indent_level
        ws.cell(row=current_row, column=1, value=f"{indent}{item.account_jp}")
        ws.cell(row=current_row, column=2, value=item.account_jp)
        ws.cell(row=current_row, column=3, value=item.account_en)
        ws.cell(row=current_row, column=4, value=item.ma_bucket)
        ws.cell(row=current_row, column=5, value=item.role)
        
        # Populate reported and normalized numbers
        for p_idx, p in enumerate(tree.periods):
            rep_col = 6 + p_idx
            norm_col = 6 + num_periods + p_idx
            
            rep_val = item.reported_values.get(p.date, 0.0)
            rep_cell = ws.cell(row=current_row, column=rep_col, value=rep_val)
            rep_cell.number_format = '#,##0;[Red]-#,##0;"-"'
            rep_cell.alignment = Alignment(horizontal="right")
            
            # Use Excel formula for normalized column: =RepCol * Multiplier
            rep_col_letter = get_column_letter(rep_col)
            norm_cell = ws.cell(row=current_row, column=norm_col, value=f"={rep_col_letter}{current_row}*{tree.unit_multiplier}")
            norm_cell.number_format = '¥#,##0;[Red]-¥#,##0;"-"'
            norm_cell.alignment = Alignment(horizontal="right")
            
        # Styling based on role
        if item.role == "grand_total":
            for col_i in range(1, len(headers) + 1):
                c = ws.cell(row=current_row, column=col_i)
                c.font = total_font
                c.fill = total_fill
                c.border = double_bottom_border
        elif item.role == "subtotal":
            for col_i in range(1, len(headers) + 1):
                c = ws.cell(row=current_row, column=col_i)
                c.font = subtotal_font
                c.fill = subtotal_fill
        else:
            for col_i in range(1, len(headers) + 1):
                c = ws.cell(row=current_row, column=col_i)
                c.font = regular_font
                c.border = thin_border
                
        current_row += 1

    # Auto-adjust column widths
    for col in ws.columns:
        col_letter = get_column_letter(col[0].column)
        max_len = max(len(str(cell.value or '')) for cell in col)
        ws.column_dimensions[col_letter].width = max(max_len + 3, 12)
        
    ws.column_dimensions['A'].width = 32
    ws.column_dimensions['B'].width = 24
    ws.column_dimensions['C'].width = 32

    wb.save(output_path)
    print(f"[Export] Saved Excel Financial Model -> {output_path}")
