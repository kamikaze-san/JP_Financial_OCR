import sys
from solution1.vlm_interface import parse_vlm_response
from solution1.normalizer import normalize_tree
from solution1.baseline_data import get_baseline_raw_tree

with open("benchmark_results/qwen3-vl_4b-instruct/pass2_targeted_output.json", "r", encoding="utf-8") as f:
    pred = normalize_tree(parse_vlm_response(f.read()))

gold = normalize_tree(get_baseline_raw_tree())

dates = ["2025-03-31", "2026-03-31"]
total = 0
matched = 0
mismatches = []

print(f"Predicted item count: {len(pred.items)} | Gold item count: {len(gold.items)}")

for p_it, g_it in zip(pred.items, gold.items):
    for d in dates:
        total += 1
        p_val = p_it.normalized_values.get(d)
        g_val = g_it.normalized_values.get(d)
        if p_val == g_val:
            matched += 1
        else:
            mismatches.append({
                "pred_name": p_it.account_jp,
                "gold_name": g_it.account_jp,
                "date": d,
                "pred": p_val,
                "gold": g_val
            })

acc = (matched / total) * 100
print(f"TRUE EXACT MATCH BY SEQUENCE: {matched}/{total} ({acc:.2f}%)")
if mismatches:
    print("Mismatches count:", len(mismatches))
    for m in mismatches:
        print(f"  * {m['pred_name']} ({m['date']}): Pred={m['pred']}, Gold={m['gold']}")
else:
    print("ALL 100% OF EXTRACTED NUMBERS MATCH GOLD ANSWER KEY EXACTLY!")
