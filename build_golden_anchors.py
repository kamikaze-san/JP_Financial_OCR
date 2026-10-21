import json
import os
import sys

sys.stdout.reconfigure(encoding='utf-8')

from solution1.models import (
    RawDocumentTree, RawMetadata, RawPeriod, RawRow,
    NormalizedDocumentTree, NormalizedPeriod, NormalizedItem
)
from solution1.normalizer import normalize_tree
from solution1.validator import AccountingValidator

# ============================================================================
# 1. CALBEE, INC. (2229) GOLDEN ANCHOR BUILDER
# ============================================================================
def build_calbee_golden():
    periods = [
        RawPeriod(col_index=1, raw_label="前連結会計年度", raw_date="2026年3月31日"),
        RawPeriod(col_index=2, raw_label="当第1四半期連結会計期間", raw_date="2026年6月30日")
    ]
    
    rows = [
        # Assets
        RawRow(row_index=1, indent_level=0, account_raw="資産の部", role="section_header", values=["", ""]),
        RawRow(row_index=2, indent_level=1, account_raw="流動資産", role="group_header", values=["", ""]),
        RawRow(row_index=3, indent_level=2, account_raw="現金及び預金", role="item", values=["51,548", "45,234"]),
        RawRow(row_index=4, indent_level=2, account_raw="受取手形及び売掛金", role="item", values=["43,832", "44,253"]),
        RawRow(row_index=5, indent_level=2, account_raw="棚卸資産", role="item", values=["29,092", "30,298"]),
        RawRow(row_index=6, indent_level=2, account_raw="その他", role="item", values=["7,386", "6,940"]),
        RawRow(row_index=7, indent_level=2, account_raw="貸倒引当金", role="contra_item", values=["△174", "△192"]),
        RawRow(row_index=8, indent_level=1, account_raw="流動資産合計", role="subtotal", values=["131,684", "126,534"]),
        
        RawRow(row_index=9, indent_level=1, account_raw="固定資産", role="group_header", values=["", ""]),
        RawRow(row_index=10, indent_level=2, account_raw="有形固定資産", role="category_header", values=["", ""]),
        RawRow(row_index=11, indent_level=3, account_raw="建物及び構築物（純額）", role="netted_item", values=["73,598", "73,360"]),
        RawRow(row_index=12, indent_level=3, account_raw="機械装置及び運搬具（純額）", role="netted_item", values=["52,827", "52,213"]),
        RawRow(row_index=13, indent_level=3, account_raw="土地", role="item", values=["21,331", "21,324"]),
        RawRow(row_index=14, indent_level=3, account_raw="建設仮勘定", role="item", values=["4,048", "5,079"]),
        RawRow(row_index=15, indent_level=3, account_raw="その他（純額）", role="netted_item", values=["3,656", "5,487"]),
        RawRow(row_index=16, indent_level=2, account_raw="有形固定資産合計", role="subtotal", values=["155,463", "157,466"]),
        
        RawRow(row_index=17, indent_level=2, account_raw="無形固定資産", role="category_header", values=["", ""]),
        RawRow(row_index=18, indent_level=3, account_raw="のれん", role="item", values=["20,987", "20,575"]),
        RawRow(row_index=19, indent_level=3, account_raw="その他", role="item", values=["3,757", "3,733"]),
        RawRow(row_index=20, indent_level=2, account_raw="無形固定資産合計", role="subtotal", values=["24,745", "24,309"]),
        
        RawRow(row_index=21, indent_level=2, account_raw="投資その他の資産", role="category_header", values=["", ""]),
        RawRow(row_index=22, indent_level=3, account_raw="投資その他の資産", role="item", values=["15,727", "15,461"]),
        RawRow(row_index=23, indent_level=3, account_raw="貸倒引当金", role="contra_item", values=["△12", "△12"]),
        RawRow(row_index=24, indent_level=2, account_raw="投資その他の資産合計", role="subtotal", values=["15,715", "15,449"]),
        
        RawRow(row_index=25, indent_level=1, account_raw="固定資産合計", role="subtotal", values=["195,924", "197,225"]),
        RawRow(row_index=26, indent_level=0, account_raw="資産合計", role="grand_total", values=["327,609", "323,759"]),
        
        # Liabilities
        RawRow(row_index=27, indent_level=0, account_raw="負債の部", role="section_header", values=["", ""]),
        RawRow(row_index=28, indent_level=1, account_raw="流動負債", role="group_header", values=["", ""]),
        RawRow(row_index=29, indent_level=2, account_raw="支払手形及び買掛金", role="item", values=["14,739", "15,900"]),
        RawRow(row_index=30, indent_level=2, account_raw="短期借入金", role="item", values=["1,766", "1,714"]),
        RawRow(row_index=31, indent_level=2, account_raw="1年内返済予定の長期借入金", role="item", values=["10,000", "10,000"]),
        RawRow(row_index=32, indent_level=2, account_raw="未払法人税等", role="item", values=["4,154", "1,784"]),
        RawRow(row_index=33, indent_level=2, account_raw="賞与引当金", role="item", values=["5,860", "2,238"]),
        RawRow(row_index=34, indent_level=2, account_raw="役員賞与引当金", role="item", values=["159", "86"]),
        RawRow(row_index=35, indent_level=2, account_raw="株式給付引当金", role="item", values=["43", "66"]),
        RawRow(row_index=36, indent_level=2, account_raw="株主優待引当金", role="item", values=["82", "82"]),
        RawRow(row_index=37, indent_level=2, account_raw="資産除去債務", role="item", values=["24", "-"]),
        RawRow(row_index=38, indent_level=2, account_raw="その他", role="item", values=["28,109", "30,341"]),
        RawRow(row_index=39, indent_level=1, account_raw="流動負債合計", role="subtotal", values=["64,941", "62,214"]),
        
        RawRow(row_index=40, indent_level=1, account_raw="固定負債", role="group_header", values=["", ""]),
        RawRow(row_index=41, indent_level=2, account_raw="長期借入金", role="item", values=["25,000", "25,000"]),
        RawRow(row_index=42, indent_level=2, account_raw="役員退職慰労引当金", role="item", values=["125", "22"]),
        RawRow(row_index=43, indent_level=2, account_raw="役員株式給付引当金", role="item", values=["294", "326"]),
        RawRow(row_index=44, indent_level=2, account_raw="退職給付に係る負債", role="item", values=["8,687", "8,151"]),
        RawRow(row_index=45, indent_level=2, account_raw="資産除去債務", role="item", values=["3,272", "3,290"]),
        RawRow(row_index=46, indent_level=2, account_raw="その他", role="item", values=["3,514", "5,882"]),
        RawRow(row_index=47, indent_level=1, account_raw="固定負債合計", role="subtotal", values=["40,894", "42,674"]),
        RawRow(row_index=48, indent_level=0, account_raw="負債合計", role="subtotal", values=["105,835", "104,889"]),
        
        # Net Assets
        RawRow(row_index=49, indent_level=0, account_raw="純資産の部", role="section_header", values=["", ""]),
        RawRow(row_index=50, indent_level=1, account_raw="株主資本", role="group_header", values=["", ""]),
        RawRow(row_index=51, indent_level=2, account_raw="資本金", role="item", values=["12,046", "12,046"]),
        RawRow(row_index=52, indent_level=2, account_raw="資本剰余金", role="item", values=["2,514", "2,513"]),
        RawRow(row_index=53, indent_level=2, account_raw="利益剰余金", role="item", values=["215,641", "211,510"]),
        RawRow(row_index=54, indent_level=2, account_raw="自己株式", role="contra_item", values=["△34,668", "△34,489"]),
        RawRow(row_index=55, indent_level=1, account_raw="株主資本合計", role="subtotal", values=["195,533", "191,580"]),
        
        RawRow(row_index=56, indent_level=1, account_raw="その他の包括利益累計額", role="category_header", values=["", ""]),
        RawRow(row_index=57, indent_level=2, account_raw="その他有価証券評価差額金", role="item", values=["653", "522"]),
        RawRow(row_index=58, indent_level=2, account_raw="為替換算調整勘定", role="item", values=["13,601", "14,375"]),
        RawRow(row_index=59, indent_level=2, account_raw="退職給付に係る調整累計額", role="item", values=["814", "1,156"]),
        RawRow(row_index=60, indent_level=1, account_raw="その他の包括利益累計額合計", role="subtotal", values=["15,069", "16,055"]),
        
        RawRow(row_index=61, indent_level=1, account_raw="非支配株主持分", role="item", values=["11,171", "11,234"]),
        RawRow(row_index=62, indent_level=0, account_raw="純資産合計", role="subtotal", values=["221,774", "218,870"]),
        RawRow(row_index=63, indent_level=0, account_raw="負債純資産合計", role="grand_total", values=["327,609", "323,759"])
    ]
    
    raw_tree = RawDocumentTree(
        metadata=RawMetadata(
            title="四半期連結貸借対照表",
            is_amended=False,
            unit_raw="(単位：百万円)",
            periods=periods
        ),
        rows=rows
    )
    return raw_tree


# ============================================================================
# 2. YAMATO MOBILITY (7886) GOLDEN ANCHOR BUILDER
# ============================================================================
def build_yamato_golden():
    periods = [
        RawPeriod(col_index=1, raw_label="当連結会計年度", raw_date="2021年3月31日")
    ]
    
    rows = [
        # Assets
        RawRow(row_index=1, indent_level=0, account_raw="資産の部", role="section_header", values=[""]),
        RawRow(row_index=2, indent_level=1, account_raw="流動資産", role="subtotal", values=["6,727,851"]),
        RawRow(row_index=3, indent_level=2, account_raw="現金及び預金", role="item", values=["2,431,543"]),
        RawRow(row_index=4, indent_level=2, account_raw="受取手形及び売掛金", role="item", values=["2,597,733"]),
        RawRow(row_index=5, indent_level=2, account_raw="電子記録債権", role="item", values=["619,236"]),
        RawRow(row_index=6, indent_level=2, account_raw="たな卸資産", role="item", values=["941,263"]),
        RawRow(row_index=7, indent_level=2, account_raw="その他", role="item", values=["138,073"]),
        
        RawRow(row_index=8, indent_level=1, account_raw="固定資産", role="subtotal", values=["1,891,115"]),
        RawRow(row_index=9, indent_level=2, account_raw="有形固定資産", role="subtotal", values=["1,669,735"]),
        RawRow(row_index=10, indent_level=3, account_raw="建物及び構築物", role="item", values=["280,299"]),
        RawRow(row_index=11, indent_level=3, account_raw="機械装置及び運搬具", role="item", values=["482,581"]),
        RawRow(row_index=12, indent_level=3, account_raw="土地", role="item", values=["768,976"]),
        RawRow(row_index=13, indent_level=3, account_raw="リース資産", role="item", values=["63,003"]),
        RawRow(row_index=14, indent_level=3, account_raw="建設仮勘定", role="item", values=["120"]),
        RawRow(row_index=15, indent_level=3, account_raw="その他", role="item", values=["74,753"]),
        
        RawRow(row_index=16, indent_level=2, account_raw="無形固定資産", role="subtotal", values=["29,819"]),
        RawRow(row_index=17, indent_level=3, account_raw="リース資産", role="item", values=["11,682"]),
        RawRow(row_index=18, indent_level=3, account_raw="その他", role="item", values=["18,136"]),
        
        RawRow(row_index=19, indent_level=2, account_raw="投資その他の資産", role="subtotal", values=["191,561"]),
        RawRow(row_index=20, indent_level=3, account_raw="投資有価証券", role="item", values=["34,046"]),
        RawRow(row_index=21, indent_level=3, account_raw="その他", role="item", values=["160,344"]),
        RawRow(row_index=22, indent_level=3, account_raw="貸倒引当金", role="contra_item", values=["△2,830"]),
        
        RawRow(row_index=23, indent_level=0, account_raw="資産合計", role="grand_total", values=["8,618,966"]),
        
        # Liabilities
        RawRow(row_index=24, indent_level=0, account_raw="負債の部", role="section_header", values=[""]),
        RawRow(row_index=25, indent_level=1, account_raw="流動負債", role="subtotal", values=["4,518,204"]),
        RawRow(row_index=26, indent_level=2, account_raw="支払手形及び買掛金", role="item", values=["2,085,758"]),
        RawRow(row_index=27, indent_level=2, account_raw="電子記録債務", role="item", values=["282,577"]),
        RawRow(row_index=28, indent_level=2, account_raw="短期借入金", role="item", values=["1,736,184"]),
        RawRow(row_index=29, indent_level=2, account_raw="リース債務", role="item", values=["29,069"]),
        RawRow(row_index=30, indent_level=2, account_raw="未払法人税等", role="item", values=["16,209"]),
        RawRow(row_index=31, indent_level=2, account_raw="その他", role="item", values=["368,405"]),
        
        RawRow(row_index=32, indent_level=1, account_raw="固定負債", role="subtotal", values=["2,770,234"]),
        RawRow(row_index=33, indent_level=2, account_raw="長期借入金", role="item", values=["2,511,795"]),
        RawRow(row_index=34, indent_level=2, account_raw="リース債務", role="item", values=["56,504"]),
        RawRow(row_index=35, indent_level=2, account_raw="退職給付に係る負債", role="item", values=["149,572"]),
        RawRow(row_index=36, indent_level=2, account_raw="繰延税金負債", role="item", values=["42,655"]),
        RawRow(row_index=37, indent_level=2, account_raw="その他", role="item", values=["9,707"]),
        RawRow(row_index=38, indent_level=0, account_raw="負債合計", role="subtotal", values=["7,288,439"]),
        
        # Net Assets
        RawRow(row_index=39, indent_level=0, account_raw="純資産の部", role="section_header", values=[""]),
        RawRow(row_index=40, indent_level=1, account_raw="株主資本", role="subtotal", values=["1,386,071"]),
        RawRow(row_index=41, indent_level=2, account_raw="資本金", role="item", values=["927,623"]),
        RawRow(row_index=42, indent_level=2, account_raw="資本剰余金", role="item", values=["763,504"]),
        RawRow(row_index=43, indent_level=2, account_raw="利益剰余金", role="item", values=["△281,812"]),
        RawRow(row_index=44, indent_level=2, account_raw="自己株式", role="contra_item", values=["△23,244"]),
        
        RawRow(row_index=45, indent_level=1, account_raw="その他の包括利益累計額", role="subtotal", values=["△56,345"]),
        RawRow(row_index=46, indent_level=2, account_raw="その他有価証券評価差額金", role="item", values=["1,354"]),
        RawRow(row_index=47, indent_level=2, account_raw="繰延ヘッジ損益", role="item", values=["542"]),
        RawRow(row_index=48, indent_level=2, account_raw="為替換算調整勘定", role="item", values=["△26,854"]),
        RawRow(row_index=49, indent_level=2, account_raw="退職給付に係る調整累計額", role="item", values=["△31,387"]),
        
        RawRow(row_index=50, indent_level=1, account_raw="非支配株主持分", role="item", values=["801"]),
        RawRow(row_index=51, indent_level=0, account_raw="純資産合計", role="subtotal", values=["1,330,527"]),
        RawRow(row_index=52, indent_level=0, account_raw="負債・純資産合計", role="grand_total", values=["8,618,966"])
    ]
    
    raw_tree = RawDocumentTree(
        metadata=RawMetadata(
            title="連結貸借対照表",
            is_amended=False,
            unit_raw="(単位：千円)",
            periods=periods
        ),
        rows=rows
    )
    return raw_tree


def save_and_validate(raw_tree, output_dir, company_name):
    norm_tree = normalize_tree(raw_tree)
    
    # Custom validation checks for full balance sheet (Assets, Liabilities, Equity)
    # Check total balance identity for each period
    for p in norm_tree.periods:
        date = p.date
        items_dict = {it.account_jp: it.normalized_values.get(date, 0.0) for it in norm_tree.items}
        
        total_assets = items_dict.get("資産合計", 0.0)
        total_liab = items_dict.get("負債合計", 0.0)
        total_equity = items_dict.get("純資産合計", 0.0)
        total_liab_equity = items_dict.get("負債純資産合計") or items_dict.get("負債・純資産合計", 0.0)
        
        print(f"[{company_name}] Period {date}:")
        print(f"   Total Assets:           ¥{total_assets:,.0f}")
        print(f"   Total Liabilities:      ¥{total_liab:,.0f}")
        print(f"   Total Net Assets:       ¥{total_equity:,.0f}")
        print(f"   Total Liab + Equity:    ¥{total_liab_equity:,.0f}")
        diff = abs(total_assets - total_liab_equity)
        print(f"   A - (L + E) Discrepancy: ¥{diff:,.0f} ({'PASSED' if diff == 0 else 'DIFF'})")
    
    out_file = os.path.join(output_dir, "golden_anchor.json")
    with open(out_file, "w", encoding="utf-8") as f:
        f.write(norm_tree.model_dump_json(indent=2))
    print(f"-> Saved Golden Anchor to {out_file}\n")


if __name__ == "__main__":
    calbee_dir = "dataset/benchmark_suite/company_02_calbee"
    calbee_raw = build_calbee_golden()
    save_and_validate(calbee_raw, calbee_dir, "Calbee (2229)")
    
    yamato_dir = "dataset/benchmark_suite/company_01_yamato"
    yamato_raw = build_yamato_golden()
    save_and_validate(yamato_raw, yamato_dir, "Yamato (7886)")
