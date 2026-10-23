# solution2 results: accuracy and latency

Model for every VLM number below unless stated (section 1e compares 2B and GLM-OCR): `qwen3-vl:4b-instruct` via Ollama, 200 DPI pages, 8 GB GPU, one request at a time.
Latency is wall-clock seconds per page, measured after a warm-up call (cold start ~6 s, reported separately).
Everything here was produced by the code in this repo; the commands to reproduce each table are at the bottom.

Scope of the unseen-data run: **5 TDnet 決算短信 filings (11 balance-sheet pages) from the dev split.** The other 35 downloaded
filings and the whole test split have not been parsed. n = 11 pages is small: read the rates as "what happened", not as a
tight estimate.

---

## 1. Unseen filings, end to end (VLM -> verify -> join split pages -> repair)

Ground truth is the PDF text layer (no VLM); a page is only used when the verifier independently agrees with it.
Filings: AVANTIA (J-GAAP), サイプレスHD (IFRS), P-LOOPLACE (J-GAAP), G-ライズ (IFRS), SHIFT (J-GAAP).
3 of the 11 pages are halves of a balance sheet that continues across a page break (`gold_split`).

### 1a. Accuracy (final code, run 2)

| Check on the raw VLM output | v1 (solution1 validator) | v2 page by page | **v2 + joined split pages (pipeline)** |
| :--- | :---: | :---: | :---: |
| Correct page, trusted (true_trust) | 8 | 5 | **8** |
| Wrong page, flagged (true_flag) | 0 | 2 | **2** |
| Wrong page, **vouched for (SILENT_ERROR)** | **2** | 0 | **0** |
| Correct page, flagged (false_alarm) | 1 | 4 | **1** |

| After the retry loop (pages the pipeline flagged: 3) | Count |
| :--- | :---: |
| Fixed (verifier trusts it and every number matches the gold) | 2 |
| Unchanged (nothing to fix: the page was already correct, see 1d) | 1 |
| Wrong number accepted / repair made it worse | **0** |

Final page status: 5 trusted, 3 trusted_joined, 2 repaired, 1 needs_review.
Cell accuracy of the raw VLM output: 4 wrong cells in 11 pages (both wrong pages are IFRS, 2 wrong cells each), 0 after repair.
By standard (v2 pipeline verdicts): J-GAAP 7 true_trust + 1 false_alarm; IFRS 1 true_trust + 2 true_flag.

### 1b. Latency per page (steady state, n = 11)

| Stage | mean | median | p90 | max |
| :--- | :---: | :---: | :---: | :---: |
| Render PDF page (200 DPI) | 0.15 s | 0.13 s | 0.23 s | 0.45 s |
| **VLM extract (Stage 1)** | **39.2 s** | 39.1 s | 50.5 s | 60.5 s |
| Verify, v2 (CPU) | 1.3 ms | | | |
| Verify, v1 (CPU) | 1.9 ms | | | |
| Join check (5 pages that needed it) | < 1 ms | | | |
| Repair, pages that needed it (n = 3) | 26.9 s | 28.3 s | 30.7 s | 30.7 s |
| Repair per VLM crop read | 9.0 s | 9.4 s | 10.3 s | 10.3 s |
| Export (json + xlsx + md) | 0.20 s | 0.06 s | 0.08 s | 1.68 s |
| **Total per page** | **46.9 s** | 42.4 s | 61.3 s | 78.9 s |

The VLM read is 84% of the time of an average page; verification, joining and export together are under 0.2 s.
A page that verifies costs one read (30-60 s). A page that needs repair costs one read plus about 9 s per crop re-read (3 reads for a 2-cell error).
Whole run: 11 pages in 515.8 s wall.

### 1c. Per-page results (run 2)

| Page | Std | Status | v1 | v2 page-only | v2 pipeline | Wrong cells before -> after | Repair reads | Extract | Repair | Total |
| :--- | :-: | :--- | :--- | :--- | :--- | :-: | :-: | :-: | :-: | :-: |
| AVANTIA p9 | J-GAAP | trusted | true_trust | true_trust | true_trust | 0 -> 0 | - | 45.4 s | - | 47.5 s |
| AVANTIA p10 | J-GAAP | trusted | true_trust | true_trust | true_trust | 0 -> 0 | - | 40.4 s | - | 40.5 s |
| AVANTIA p26 (split) | J-GAAP | trusted_joined (p27) | true_trust | false_alarm | true_trust | 0 -> 0 | - | 34.5 s | - | 34.7 s |
| AVANTIA p27 (split) | J-GAAP | trusted_joined (p26) | true_trust | false_alarm | true_trust | 0 -> 0 | - | 39.1 s | - | 39.2 s |
| サイプレスHD p7 | IFRS | **repaired** | SILENT_ERROR | true_flag | true_flag | 2 -> 0 | 3 | 50.5 s | 28.3 s | 78.9 s |
| P-LOOPLACE p7 | J-GAAP | trusted | true_trust | true_trust | true_trust | 0 -> 0 | - | 42.1 s | - | 42.4 s |
| P-LOOPLACE p8 | J-GAAP | needs_review | true_trust | false_alarm | **false_alarm** | 0 -> 0 | 3 | 35.9 s | 21.7 s | 57.8 s |
| G-ライズ p6 | IFRS | **repaired** | SILENT_ERROR | true_flag | true_flag | 2 -> 0 | 3 | 30.3 s | 30.7 s | 61.2 s |
| G-ライズ p7 | IFRS | trusted | true_trust | true_trust | true_trust | 0 -> 0 | - | 30.1 s | - | 30.3 s |
| SHIFT p17 | J-GAAP | trusted | false_alarm | true_trust | true_trust | 0 -> 0 | - | 60.5 s | - | 60.7 s |
| SHIFT p18 (split) | J-GAAP | trusted_joined (p17) | true_trust | false_alarm | true_trust | 0 -> 0 | - | 22.1 s | - | 22.2 s |

### 1d. What the run showed

* **Split statements work.** All three pages whose total sits on the next page (AVANTIA p26/p27, SHIFT p18) are flagged when
  checked alone and trusted once joined with the neighbour: v2 page-only has 4 false alarms, the pipeline has 1. Joining is gated:
  both pages need the same unit and period columns and the pair has to verify with no errors.
* **v1 vouched for two wrong IFRS pages** (SILENT_ERROR: 2 wrong cells each). v2 flagged both and the retry loop fixed both.
* **One false alarm remains (P-LOOPLACE p8), fail-closed.** The VLM read the page correctly but put その他利益剰余金 and its only child
  繰越利益剰余金 (same amount) at the same indent, so 利益剰余金合計 sums them twice. The pipeline cannot tell that from a hallucinated
  duplicate row, so it keeps the page for review and the repair loop (correctly) changes nothing. This costs one review, not a wrong number.
* **Run 1 found a verifier bug, fixed in this session.** サイプレスHD (IFRS) has a title row `(1) 連結財政状態計算書` above the table and an
  IFRS grand total labelled 負債及び資本合計. The verifier treated the title as an unclosed section and did not recognise 資本 as the equity
  side of the balance tie, so a perfectly repaired page stayed at 94% coverage and was refused (the loop spent 12 reads, 144.6 s, to no avail).
  After the fix (leading statement titles are not sections; the tie accepts 純資産 or 資本) the same page is repaired in 3 reads, 28.3 s.
  The mutation gate still passes (below) and two regression tests were added.

| Run | Code | Pipeline status | Repair outcomes | Wall | Mean total / page |
| :--- | :--- | :--- | :--- | :-: | :-: |
| Run 1 (before the title/資本 fix) | `09d8608bad84` | 5 trusted, 3 joined, 1 repaired, 2 needs_review | 1 fixed, 1 improved, 1 unchanged | 630.8 s | 57.3 s |
| **Run 2 (final)** | `a1d673868644` | 5 trusted, 3 joined, 2 repaired, 1 needs_review | 2 fixed, 1 unchanged | **515.8 s** | **46.9 s** |

Both runs have 0 SILENT_ERROR and 0 worse. The two runs also differ in VLM output (SHIFT p17 extraction took 60 s in both, other pages vary by
a few seconds), so the run-to-run difference in total time is mostly the repaired page, not noise in the model.

### 1e. Three models on the same 11 pages (same pipeline, same code `a1d673868644`, each model also does its own repair reads)

| | `qwen3-vl:4b-instruct` | `qwen3-vl:2b-instruct` | `glm-ocr` |
| :--- | :-: | :-: | :-: |
| Pages with a usable Stage-1 table | 11 / 11 | 11 / 11 | **0 / 11** (6 extraction_failed, 5 no_table) |
| Raw output fully correct (0 wrong cells) | 9 / 11 | 5 / 11 | - |
| Pipeline verdict, v2 + join: true_trust / true_flag / false_alarm / label_gap | 8 / 2 / 1 / 0 | 5 / 5 / 0 / 1 | - |
| v1 on the raw output: **SILENT_ERROR** | 2 | 3 | - |
| v2 on the raw output: **SILENT_ERROR** | **0** | **0** | - |
| Final status: trusted / joined / repaired / needs_review | 5 / 3 / 2 / 1 | 4 / 2 / 0 / 5 | - |
| Pages ending trusted **and** fully correct | 10 / 11 (the 11th is correct but held) | 5 / 11 (+1 trusted with values on the wrong rows, see below) | 0 / 11 |
| Repair outcomes on flagged pages | 2 fixed, 1 unchanged | 3 improved, 2 unchanged, 0 fixed | - |
| Wrong number accepted / repair made it worse | 0 | 0 | 0 |
| VLM extract per page (mean / p90) | 39.2 s / 50.5 s | **21.5 s** / 28.0 s | 41.3 s / 67.0 s |
| Repair per crop read (mean) | 9.0 s | 8.9 s | - |
| Total per page (mean / p90 / max) | 46.9 s / 61.3 s / 78.9 s | 42.6 s / 75.0 s / 112.5 s | 41.4 s / 67.1 s / 68.2 s |
| Whole run (wall) | 515.8 s | 468.4 s | 455.4 s |

* **2B is twice as fast per read but wrong far more often.** Five of its 11 pages have wrong cells after the first read (3 to 45 each; one page with
  45 wrong cells), and repair reduced them on 3 pages without reaching a trusted state on any. It spends its saved extraction time on repair reads
  (mean 46 s on the 5 pages that needed it), so the total per page is only 9% lower than 4B. The verifier never vouched for a wrong *total*.
* **Correction: 2B's one trusted-but-imperfect page is a real silent error, not a cosmetic one.** サイプレスHD p7 (`label_gap` in the table: every number
  exists on the page, so the number-level scorer calls it a label gap) has the whole 流動負債 block shifted up by one row: the 流動負債 heading carries
  976,246 (that is 借入金's value), 借入金 carries リース負債's, and so on down to a blank その他の流動負債. The items still add up to 流動負債合計
  within rounding slack, so v2 vouched for the page (as did v1). A sum cannot see values moved between rows of the same block; this is the
  "row shift" gap in section 2, and 1g shows the same pattern on 4 of 5 wrong 2B pages, where v2 did flag it. Read the "0 silent errors" rows in this
  section as "0 wrong totals / numbers vouched for", with this one misattribution as the exception.
* **GLM-OCR does not follow the Stage-1 JSON prompt.** On a probe it copied the schema's placeholder `"string"` into the fields and wrote numbers into the
  account-name field; across the 11 pages every response failed (6 extraction_failed after ~67 s, probably the 8,192-token cap, and 5 with no numeric cell).
  This is a prompt-format failure, not an OCR-quality verdict: the earlier benchmark (section 5 context, `matrix_summary.md`) used GLM-OCR's own table output
  (100% on the Yamato baseline page, 17.7 s) and a separate parser. Using it here would need a GLM-native adapter (table HTML -> Stage-1 rows), which is not built.

### 1f. Resolution: 200 DPI vs 300 DPI for the first full-page read (same 11 pages, full pipeline, repair crops are 250-400 DPI in both)

| | 4B @ 200 | 4B @ 300 | 2B @ 200 | 2B @ 300 |
| :--- | :-: | :-: | :-: | :-: |
| Raw output fully correct (0 wrong cells) | 9 / 11 | 9 / 11 | 5 / 11 | **7 / 11** |
| Pipeline verdict (v2 + join): true_trust / true_flag / false_alarm / label_gap | 8 / 2 / 1 / 0 | 6 / 2 / 3 / 0 | 5 / 5 / 0 / 1 | **7 / 4 / 0 / 0** |
| v1 on the raw output: SILENT_ERROR | 2 | 2 | 3 | 3 |
| v2 on the raw output: SILENT_ERROR | **0** | **0** | **0** | **0** |
| Final status: trusted / joined / repaired / needs_review | 5 / 3 / 2 / 1 | 5 / 1 / 2 / 3 | 4 / 2 / 0 / 5 | 5 / 2 / 1 / 3 |
| Trusted with every number correct | **10 / 11** | 8 / 11 | 5 / 11 | 8 / 11 |
| Wrong number accepted / repair made it worse | 0 | 0 | 0 | 0 |
| VLM extract per page, mean / p90 | **39.2 s** / 50.5 s | 55.1 s / 73.5 s | 21.5 s / 28.0 s | 35.5 s / 42.7 s |
| Repair per crop read, mean | 9.0 s | 13.5 s | 8.9 s | 7.2 s |
| Total per page, mean / p90 / max | **46.9 s** / 61.3 s / 78.9 s | 73.8 s / 106.1 s / 122.4 s | 42.6 s / 75.0 s / 112.5 s | 49.7 s / 78.9 s / 79.6 s |
| Whole run (wall) | **515.8 s** | 811.6 s | 468.4 s | 547.5 s |

* **4B is not resolution-limited.** 300 DPI leaves the raw accuracy unchanged (9 / 11 either way; the same two IFRS pages still drop the
  有形固定資産 values, now with 5 wrong cells each instead of 2) and costs 57% more per page (46.9 s -> 73.8 s). The final result is *worse*:
  - the two AVANTIA split halves (p26/p27) read correctly but no longer verify when joined (coverage 94% against the 95% needed, a section is
    missing its own total at the page break), so they stay in review: a join-boundary fragility, fail-closed, no wrong number involved;
  - both IFRS repairs end as `fixed_label` (numbers right, a row label differs) instead of `fixed`.
  Run-to-run noise of the model is part of this, so read the 4B difference as "no gain", not as "300 DPI hurts".
* **2B gains from 300 DPI**: raw accuracy 5 -> 7 of 11 pages, trusted-and-correct 5 -> 8 of 11, the 45-wrong-cell page and the 12-wrong-cell page become exact,
  and the repair loop fixes a third page. It still does not reach 4B at 200 DPI (10 / 11), and the extra resolution costs it 65% more extract time
  (21.5 s -> 35.5 s), though its total per page rises only 17% (42.6 s -> 49.7 s) because fewer pages need repair.
* **Best accuracy per second on this sample:** 4B @ 200 DPI (10 / 11 trusted-correct at 46.9 s). If speed matters more, 2B @ 300 DPI reaches 8 / 11
  at 49.7 s, which is not faster than 4B @ 200 DPI, so there is currently no reason to prefer 2B.
* Sample is 11 pages and one run per cell, so differences of one page are within noise. The safety result is unaffected: 0 silent errors in all four configurations.

### 1g. Second random sample: 5 more filings, 8 pages (dev split, 200 DPI, never seen while building the verifier)

Filings: 丸東産, あさひ, タキヒヨー (J-GAAP, 百万円), G-ラストワンマイル, G-トリプルアイズ (IFRS, the latter with a 3-column 移行日 balance sheet).
Pages: 5 gold, 2 split-statement halves, 1 "ambiguous" (the gold is right but the verifier flags even the gold; see below). Three of the filings have another balance-sheet
page the text-layer gold reader could not parse (no dated header, or a numbers-only line), so those pages are not scored.
4B ran on the final code (`bf90aec177d0`, which re-joins a split half after its neighbour was repaired; see "Found in this run"); 2B ran one commit earlier
(`a1d673868644`), which does not matter for it since none of its wrong pages could verify jointly anyway.

| | `qwen3-vl:4b-instruct` | `qwen3-vl:2b-instruct` |
| :--- | :-: | :-: |
| Pages with a usable table | 8 / 8 | 7 / 8 (G-トリプルアイズ p10, the 3-column page, returned no numbers) |
| Raw output with every number on its correct row | 4 / 8 | 2 / 8 |
| Raw output with correct numbers but an echoed heading row (harmless) | 3 / 8 | 0 / 8 |
| Raw output with a real error | 1 / 8 (2 dropped values) | 5 / 8 (2 to 14 wrong cells each) |
| v1 on the raw output: false alarm / wrong page flagged / echo-or-shift passed | 2 / 1 / 3 | 0 / 3 / 2 (both of these 2 are shifted blocks) |
| v2 + join: wrong pages flagged / **wrong numbers vouched** | 1 of 1 / **0** | 5 of 5 / **0** |
| v2 false alarms | 2: a split half held at first sight (later trusted by joining) and the ambiguous page (scored as flagged because its echo rows differ from the gold) | 1 (the ambiguous page) |
| Final status: trusted / joined / repaired / needs_review / no_table | 4 / 3 / 0 / 1 / 0 | 1 / 0 / 0 / 6 / 1 |
| Trusted with every number correct | **7 / 8** (the 8th is correct but held) | 1 / 8 |
| Repair outcome on flagged pages | 1 fixed, 2 unchanged (nothing to fix) | 1 improved, 5 unchanged |
| VLM extract per page, mean / p90 | 38.6 s / 59.1 s | **22.7 s** / 38.3 s |
| Repair per crop read, mean | 12.6 s | 7.5 s |
| Total per page, mean / p90 / max | 70.5 s / 220 s* / 220 s | **54.2 s** / 111 s / 111 s |
| Whole run (wall) | 564.2 s | 434.0 s |

\* one page needed 9 crop reads (175.8 s) to recover two dropped values, which lifts the mean; the median page took 56 s.

Both samples together, `qwen3-vl:4b-instruct` @ 200 DPI: **17 of 19 pages trusted with every number correct, 2 more correct but held for review (verifier false alarms),
0 wrong numbers vouched for.** `qwen3-vl:2b-instruct`: 6 of 19 trusted and correct, one trusted with shifted values (sample 1), 0 wrong totals vouched for.

**Failure modes seen**

1. **Value shift under a group heading (2B, 4 of its 5 wrong pages).** The heading takes its first child's value and every child below slides up one row
   (有形固定資産, 投資その他の資産, その他の包括利益累計額, 流動負債). The sums are unchanged, so only the heading-with-numbers check and the rounding
   limits catch it. v2 flagged all four here; v1 passed two of them; v2 passed one in sample 1 (above).
2. **Dropped line value (4B, タキヒヨー p5: 無形固定資産; sample 1: 有形固定資産 on two IFRS pages).** The row exists but its cells are empty. The total no longer
   balances, the verifier flags it and repair re-reads it, but this page needed 9 crop reads and 176 s because the first attempts were rejected.
3. **Heading echo (4B, 3 IFRS pages).** The model writes a category heading and then the same label again as a line item (有形固定資産 -> 有形固定資産,
   投資不動産 -> 投資不動産). The numbers are right and the verifier is unaffected; the scorer calls these `label_gap`. They are the "equivalent re-spellings" class from section 2.
4. **Split statements with an error in one half (タキヒヨー p5/p6).** Joining before repair failed because p5 had the dropped value; once p5 was repaired, p6 (a clean half) could
   not be trusted alone and was left in review. **Pipeline bug, fixed in this session:** pages are now joined again after repair. With the fix both halves are `trusted_joined`
   (before the fix: 1 page repaired but held, 1 half held). A regression test covers it.
5. **Verifier false alarm on a half statement that starts at 負債及び資本 (G-ラストワンマイル p7, both models).** The gold is right (all totals tie), but 資本合計 is evaluated over a window that
   swallows 負債合計 because an outer heading is still open, so the page is refused by the verifier and by both models' outputs. Fail-closed, no wrong number involved; not fixed yet.
6. **Extraction failure on an unusual layout (2B on the 3-column IFRS page with a 移行日 transition-date column):** no numeric cells at all, so the pipeline reports `no_table`. 4B read the same page correctly.
7. **Wasted repair work when the first read is badly wrong (2B).** 21 crop reads and 111 s on あさひ p6 changed nothing; the loop declines to accept anything that does not improve the verifier, so it is safe, but it is slow.
8. **Gold coverage:** 3 of the 5 filings contain a balance-sheet page the text-layer gold could not parse, so the sample under-represents those layouts.

Evaluation-harness fix found in this run: a page the pipeline flagged at first sight, repaired, and then trusted after joining was scored as if the pipeline had vouched for
the wrong raw page (a false SILENT_ERROR in the report). The flag now counts whenever repair ran (`eval.py`), and the saved report was re-scored from its stored rows.

---

## 2. Verifier: v1 vs v2 on 28,280 synthetic corruptions (7 documents, no VLM)

Every digit substitution / drop / transposition, sign flip, dropped or duplicated row, nil/number swap, unparseable value, degenerate output, etc.
(`solution2/harness`, reports in `solution2/harness/reports/`). "Silent" means a corruption the verifier did not flag.

| Verifier | Gate | Violations | Must-catch corruptions silent | Time for all 28,280 |
| :--- | :-: | :-: | :--- | :-: |
| v1 (solution1 validator) | **FAIL** | 19 | 56% silent overall; 0% caught on 3 of 7 documents (Yamato, FastRetailing p2, Nikon p2) | 16.9 s |
| **v2 (hierarchy-driven reducer)** | **PASS** | **0** | 0 silent in every must-catch class (12 classes, 100% each) | 44.5 s |

* Degenerate output: v1 vouches for 28/28, v2 catches 28/28.
* Equivalent re-spellings (heading echo, structure noise, NFKC forms): v2 never changes the verdict; v1 false-alarms on 9 of 289.
* Known gaps, not detectable by arithmetic alone and reported as informational: label confusable flips (e.g. 債権/債務), column swap, period-header swap,
  unit change, swapping two values inside a section. These need a second signal (text-layer cross-check, dual reads).
* Cost: v2 is ~2.6x slower than v1 on the corpus (about 1.6 ms per mutation) and still under 2 ms per page on real tables.

---

## 3. Real VLM outputs, verifier scorecard (24 saved Qwen outputs, 2B and 4B, several DPIs)

Page-level, against hand-checked ground truth (`solution2/harness/reports/realcheck.txt`).

| Verifier | true_trust | true_flag | **SILENT_ERROR** | label_gap | false_alarm | unparseable |
| :--- | :-: | :-: | :-: | :-: | :-: | :-: |
| v1 | 4 | 3 | **10** | 3 | 3 | 1 |
| **v2** | 7 | 15 | **0** | 1 | 0 | 1 |

---

## 4. Retry loop on real VLM outputs (location-only re-reads of failing windows)

Crop re-reads by `qwen3-vl:4b-instruct`, 3 reads per cell, no deficit leaked to the model. 24 saved outputs
(`solution2/harness/reports/repair_eval_ollama_4b.txt`).

| Outcome | Count |
| :--- | :-: |
| Fixed | 9 |
| Fixed (label differs only) | 1 |
| Improved (fewer wrong cells, not yet trusted) | 1 |
| Unchanged (T-account pages declined, nothing safe to crop) | 6 |
| Wrong number accepted / worse | **0** |

Cost: 78 crop reads in 699 s = **9.0 s per read**, in line with 9.0 s per read on the unseen run above.
A text-layer reader (digital PDFs only, no model) repaired the same pages with no GPU time at all (`repair_eval_text.txt`).

---

## 5. Earlier benchmark numbers, same VLM (for context; `benchmark_results/`)

| Run | Model | Latency |
| :--- | :--- | :-: |
| Yamato baseline page, solution1 benchmark | `qwen3-vl:4b-instruct` | 55.0 s |
| Calbee 2 pages, solution1 benchmark | `qwen3-vl:4b-instruct` | 62.6 s |
| solution2 pipeline, baseline page (200 DPI) | `qwen3-vl:4b-instruct` | 59 s |
| solution2 pipeline, Calbee 2 pages | `qwen3-vl:4b-instruct` | 82 s |

---

## Limits of these results

* 11 + 8 pages from 10 filings (two independent random samples), dev split only; no test-split number exists yet. The test split must stay unseen until the verifier is frozen.
* The text-layer gold covers digital PDFs only. Scans would need the XBRL gold (EDINET path is built but has never been run with a key).
* Arithmetic identities do not see a consistent mislabel, a swapped period, or values moved between rows of one block (seen once in 1e, 2B @200 DPI);
  these show up as "known gaps" in section 2. A per-row cross-check against the PDF text layer is the planned second signal for digital PDFs.
* One P-LOOPLACE-style false alarm per ~11 pages is the current cost of failing closed.

## Reproduce

```bash
python -m pytest solution2 -q                                                            # 149 tests
python -m solution2.harness.run --verifier v2 --gate                                     # section 2
python -m solution2.tdnet eval --data-dir solution2/data_sample/tdnet --split dev --include-split   # section 1 (needs Ollama + qwen3-vl:4b-instruct)
python -m solution2.tdnet eval --data-dir solution2/data_sample/tdnet --split dev --include-split --no-join   # same run, page by page
python -m solution2.tdnet eval --data-dir solution2/data_sample/tdnet --split dev --include-split --model qwen3-vl:2b-instruct --run-name qwen2b   # 1e (also glm-ocr:latest)
python -m solution2.tdnet eval --data-dir solution2/data_sample/tdnet --split dev --include-split --model qwen3-vl:4b-instruct --dpi 300 --run-name qwen4b_300   # 1f
```

`solution2/data_sample/tdnet` holds all 40 downloaded filings (about 20 MB), the plan (token `b174e2e274`), and the 11 prepared gold pages
(relative paths). TDnet's viewer only keeps about 31 days, so these copies are the only way to reproduce the run later.
