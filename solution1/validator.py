from typing import List, Dict, Tuple, Optional
from .models import NormalizedDocumentTree, ValidationReport, ChecksumFailure, NormalizedItem

class AccountingValidator:
    """
    Deterministic Accounting Verification Engine.
    Executes MECE financial invariants on normalized balance sheet trees.
    """
    
    def __init__(self, tolerance_multiplier: float = 1.0):
        # Tolerance per equation step (e.g., 1.0 unit step for legal rounding differences)
        self.tolerance_multiplier = tolerance_multiplier

    def validate(self, tree: NormalizedDocumentTree) -> ValidationReport:
        failures: List[ChecksumFailure] = []
        tested_count = 0
        passed_count = 0
        
        # Build quick item lookups by clean account name and row
        items = tree.items
        dates = [p.date for p in tree.periods]
        unit_scale = tree.unit_multiplier

        for date in dates:
            # -------------------------------------------------------------
            # Equation 1: Current Assets Summation
            # -------------------------------------------------------------
            tested_count += 1
            ca_items = []
            ca_subtotal = None
            
            # Find all items under Current Assets up to 流動資産合計
            in_ca = False
            for it in items:
                if it.account_jp == "流動資産" or it.role == "group_header":
                    if "流動" in it.account_jp:
                        in_ca = True
                        continue
                if in_ca:
                    if it.account_jp == "流動資産合計":
                        ca_subtotal = it
                        in_ca = False
                        break
                    elif it.role in ["item", "contra_item"]:
                        ca_items.append(it)
                        
            if ca_subtotal:
                calc_sum = sum(it.reported_values.get(date, 0.0) for it in ca_items)
                exp_sum = ca_subtotal.reported_values.get(date, 0.0)
                diff = round(calc_sum - exp_sum, 2)
                
                # Check within rounding tolerance (1 unit step * number of components)
                tol = len(ca_items) * 1.0
                if abs(diff) <= tol:
                    passed_count += 1
                else:
                    failures.append(ChecksumFailure(
                        equation_name="Current Assets Vertical Sum (流動資産合計)",
                        period=date,
                        expected_value=exp_sum * unit_scale,
                        computed_value=calc_sum * unit_scale,
                        discrepancy=diff * unit_scale,
                        involved_accounts=[it.account_jp for it in ca_items],
                        tolerance_applied=tol * unit_scale
                    ))

            # -------------------------------------------------------------
            # Equation 2: PPE Horizontal Netting (Gross - AccDep = Net)
            # -------------------------------------------------------------
            for idx, it in enumerate(items):
                if it.role == "netted_item" or "（純額）" in it.account_jp:
                    parent_acc = it.parent_account
                    # Find previous gross and contra items
                    gross_item = None
                    dep_item = None
                    for prev_idx in range(idx - 1, -1, -1):
                        prev = items[prev_idx]
                        if prev.role == "contra_item" or "減価償却累計額" in prev.account_jp:
                            if dep_item is None:
                                dep_item = prev
                        elif prev.account_jp == parent_acc or (parent_acc is None and gross_item is None):
                            gross_item = prev
                            break
                            
                    if gross_item and dep_item:
                        tested_count += 1
                        g_val = gross_item.reported_values.get(date, 0.0)
                        d_val = dep_item.reported_values.get(date, 0.0) # usually negative
                        net_val = it.reported_values.get(date, 0.0)
                        
                        calc_net = g_val + d_val if d_val < 0 else g_val - d_val
                        diff = round(calc_net - net_val, 2)
                        if abs(diff) <= 1.0:
                            passed_count += 1
                        else:
                            failures.append(ChecksumFailure(
                                equation_name=f"PPE Netting ({it.account_jp})",
                                period=date,
                                expected_value=net_val * unit_scale,
                                computed_value=calc_net * unit_scale,
                                discrepancy=diff * unit_scale,
                                involved_accounts=[gross_item.account_jp, dep_item.account_jp, it.account_jp],
                                tolerance_applied=1.0 * unit_scale
                            ))

            # -------------------------------------------------------------
            # Equation 3: Tangible Fixed Assets Summation (有形固定資産合計)
            # -------------------------------------------------------------
            ppe_subtotal = next((it for it in items if it.account_jp == "有形固定資産合計"), None)
            if ppe_subtotal:
                tested_count += 1
                # Tangible components: Netted rows + Land + Construction in progress
                ppe_components = [
                    it for it in items
                    if ("有形" in it.ma_bucket or it.role == "netted_item" or it.account_jp in ["土地", "建設仮勘定"])
                    and it.account_jp != "有形固定資産合計"
                    and it.role in ["netted_item", "item"]
                    and not ("減価償却累計額" in it.account_jp)
                    # Exclude gross rows that already have a net row
                    and not (any(other.parent_account == it.account_jp for other in items))
                ]
                calc_sum = sum(it.reported_values.get(date, 0.0) for it in ppe_components)
                exp_sum = ppe_subtotal.reported_values.get(date, 0.0)
                diff = round(calc_sum - exp_sum, 2)
                tol = len(ppe_components) * 1.0
                if abs(diff) <= tol:
                    passed_count += 1
                else:
                    failures.append(ChecksumFailure(
                        equation_name="Tangible PPE Sum (有形固定資産合計)",
                        period=date,
                        expected_value=exp_sum * unit_scale,
                        computed_value=calc_sum * unit_scale,
                        discrepancy=diff * unit_scale,
                        involved_accounts=[it.account_jp for it in ppe_components],
                        tolerance_applied=tol * unit_scale
                    ))

            # -------------------------------------------------------------
            # Equation 4: Intangibles Summation (無形固定資産合計)
            # -------------------------------------------------------------
            int_subtotal = next((it for it in items if it.account_jp == "無形固定資産合計"), None)
            if int_subtotal:
                tested_count += 1
                # Find rows between 有形固定資産合計 (or 無形固定資産) and 無形固定資産合計
                int_components = []
                in_int = False
                for it in items:
                    if "無形固定資産" in it.account_jp:
                        if it.account_jp == "無形固定資産合計":
                            in_int = False
                            break
                        in_int = True
                        continue
                    if in_int and it.role == "item":
                        int_components.append(it)
                        
                calc_sum = sum(it.reported_values.get(date, 0.0) for it in int_components)
                exp_sum = int_subtotal.reported_values.get(date, 0.0)
                diff = round(calc_sum - exp_sum, 2)
                if abs(diff) <= len(int_components) * 1.0:
                    passed_count += 1
                else:
                    failures.append(ChecksumFailure(
                        equation_name="Intangibles Sum (無形固定資産合計)",
                        period=date,
                        expected_value=exp_sum * unit_scale,
                        computed_value=calc_sum * unit_scale,
                        discrepancy=diff * unit_scale,
                        involved_accounts=[it.account_jp for it in int_components],
                        tolerance_applied=len(int_components) * 1.0 * unit_scale
                    ))

            # -------------------------------------------------------------
            # Equation 5: Investments & Other Summation (投資その他の資産合計)
            # -------------------------------------------------------------
            inv_subtotal = next((it for it in items if it.account_jp == "投資その他の資産合計"), None)
            if inv_subtotal:
                tested_count += 1
                inv_components = []
                in_inv = False
                for it in items:
                    if "投資その他の資産" in it.account_jp:
                        if it.account_jp == "投資その他の資産合計":
                            in_inv = False
                            break
                        in_inv = True
                        continue
                    if in_inv and it.role in ["item", "contra_item"]:
                        inv_components.append(it)
                        
                calc_sum = sum(it.reported_values.get(date, 0.0) for it in inv_components)
                exp_sum = inv_subtotal.reported_values.get(date, 0.0)
                diff = round(calc_sum - exp_sum, 2)
                if abs(diff) <= len(inv_components) * 1.0:
                    passed_count += 1
                else:
                    failures.append(ChecksumFailure(
                        equation_name="Investments & Other Sum (投資その他の資産合計)",
                        period=date,
                        expected_value=exp_sum * unit_scale,
                        computed_value=calc_sum * unit_scale,
                        discrepancy=diff * unit_scale,
                        involved_accounts=[it.account_jp for it in inv_components],
                        tolerance_applied=len(inv_components) * 1.0 * unit_scale
                    ))

            # -------------------------------------------------------------
            # Equation 6: Fixed Assets Total (固定資産合計)
            # -------------------------------------------------------------
            fixed_total = next((it for it in items if it.account_jp == "固定資産合計"), None)
            if fixed_total and ppe_subtotal and int_subtotal and inv_subtotal:
                tested_count += 1
                calc_fixed = (
                    ppe_subtotal.reported_values.get(date, 0.0) +
                    int_subtotal.reported_values.get(date, 0.0) +
                    inv_subtotal.reported_values.get(date, 0.0)
                )
                exp_fixed = fixed_total.reported_values.get(date, 0.0)
                diff = round(calc_fixed - exp_fixed, 2)
                if abs(diff) <= 3.0:
                    passed_count += 1
                else:
                    failures.append(ChecksumFailure(
                        equation_name="Fixed Assets Total (固定資産合計 = 有形 + 無形 + 投資)",
                        period=date,
                        expected_value=exp_fixed * unit_scale,
                        computed_value=calc_fixed * unit_scale,
                        discrepancy=diff * unit_scale,
                        involved_accounts=["有形固定資産合計", "無形固定資産合計", "投資その他の資産合計"],
                        tolerance_applied=3.0 * unit_scale
                    ))

            # -------------------------------------------------------------
            # Equation 7: Total Assets (資産合計 = 流動資産合計 + 固定資産合計)
            # -------------------------------------------------------------
            total_assets = next((it for it in items if it.account_jp == "資産合計"), None)
            if total_assets and ca_subtotal and fixed_total:
                tested_count += 1
                calc_assets = (
                    ca_subtotal.reported_values.get(date, 0.0) +
                    fixed_total.reported_values.get(date, 0.0)
                )
                exp_assets = total_assets.reported_values.get(date, 0.0)
                diff = round(calc_assets - exp_assets, 2)
                if abs(diff) <= 4.0:
                    passed_count += 1
                else:
                    failures.append(ChecksumFailure(
                        equation_name="Total Assets Balance (資産合計 = 流動資産合計 + 固定資産合計)",
                        period=date,
                        expected_value=exp_assets * unit_scale,
                        computed_value=calc_assets * unit_scale,
                        discrepancy=diff * unit_scale,
                        involved_accounts=["流動資産合計", "固定資産合計"],
                        tolerance_applied=4.0 * unit_scale
                    ))

        report = ValidationReport(
            is_valid=(len(failures) == 0),
            total_equations_tested=tested_count,
            passed_equations=passed_count,
            failed_equations=len(failures),
            failures=failures
        )
        tree.validation_report = report
        return report

    def generate_arithmetic_feedback(self, report: ValidationReport, unit_raw: str, unit_scale: int) -> str:
        """
        Pure deterministic feedback generation.
        Synthesizes an actionable error prompt strictly from mathematical validation failure objects.
        NO human hints, NO hardcoded row names, completely scalable to any document.
        """
        if report.is_valid:
            return ""
            
        lines = [
            "[AUTOMATED DETERMINISTIC ACCOUNTING ENGINE AUDIT]",
            f"Your previous transcription failed {len(report.failures)} mathematical checksum(s):"
        ]
        
        for f in report.failures:
            exp_rep = f.expected_value / unit_scale
            calc_rep = f.computed_value / unit_scale
            diff_rep = f.discrepancy / unit_scale
            
            lines.append(f"\n* Checksum Failure: '{f.equation_name}' (Period: {f.period})")
            lines.append(f"  - Reported Subtotal cell on page: {exp_rep:,.0f} ({unit_raw})")
            lines.append(f"  - Sum of your extracted component items: {calc_rep:,.0f} ({unit_raw})")
            lines.append(f"  - Discrepancy: {diff_rep:+,.0f} ({unit_raw})")
            
            if f.involved_accounts:
                start_acc = f.involved_accounts[0]
                end_acc = f.involved_accounts[-1]
                if diff_rep < 0:
                    lines.append(f"  - Mathematical Diagnosis: Deficit of {abs(diff_rep):,.0f}. Either an item was misread, OR an entire row was omitted between '{start_acc}' and '{end_acc}'.")
                else:
                    lines.append(f"  - Mathematical Diagnosis: Surplus of {diff_rep:,.0f}. An item was double-counted or misread.")
                    
        lines.append("\nINSTRUCTIONS FOR AUTONOMOUS CORRECTION:")
        lines.append("1. Look at the image specifically in the section between the first and last accounts indicated above.")
        lines.append("2. Count the rows in that section on the image and check if a row was skipped (e.g., a row where one column has a dash '－' or nil).")
        lines.append("3. Verify the digits of each row so that the sum of components matches the reported subtotal.")
        lines.append("4. Return the complete corrected JSON matching the schema.")
        return "\n".join(lines)
