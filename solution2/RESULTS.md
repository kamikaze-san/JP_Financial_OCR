# solution2 results: accuracy and latency

Every number here was produced by the code in this repository (commands at the bottom). Hardware: one 8 GB GPU, Ollama, Q4 models, one request at a time.
Model for the numbers below unless stated: `qwen3-vl:4b-instruct`. Latency is wall-clock seconds per page, measured after a warm-up call (cold start about 6 s, reported separately).
The sections are a chronological log (what was tried, what it showed, what was corrected afterwards), so later sections can refine or correct earlier ones; this summary is the current state.

## Summary (read this first)

**The data used.** 50 real TDnet 決算短信 filings (92 balance-sheet pages; gold = the PDF text layer, kept only where the verifier independently agrees), used in separate roles, plus the 5 original benchmark documents (8 pages):

| Set | Filings / pages | Used for |
| :-- | :-: | :-- |
| dev A, dev B | 5 + 5 / 11 + 8 | building, and choosing the configurations by a written rule (sections 1a-1k) |
| sealed test | 21 / 38 | run once, with the configurations frozen beforehand (1m) |
| dev C, dev D | 5 + 4 / 9 + 7 | the PDF text-layer check, run after it was frozen (1p) |
| fresh set E | 10 / 19 | downloaded afterwards on request; the frozen pipelines and the text-layer check (1p) |
| benchmark documents | 5 / 8 | Yamato (T-account), Calbee, FastRetailing, Nikon, baseline; clean, scan-like and skewed renders (1l, 1o) |

**First read compared on the dev pages (19 pages, 477 gold rows with numbers, repair off):**

| First read | Model | Rows exact | Pages with a wrong or missing number | Read per page | Section |
| :-- | :-- | :-: | :-: | :-: | :-: |
| solution1 JSON, whole page | 4B | 98.5% | 3 | 38.9 s | 1h |
| Qwen HTML, whole page | 4B | 98.7% | 1 | 22.1 s | 1h |
| Qwen Markdown, whole page | 4B | 97.9% | 1 | 18.0 s | 1h (corrected) |
| **Bands (12 text lines per read)** | **4B** | **99.6%** (99.4% re-run with the final code, 1n) | **0** | **40.5 s** | 1i |
| solution1 JSON, whole page | 2B | 84.1% | 6 | 22.0 s | 1h |
| Qwen Markdown, whole page | 2B | 97.1% | 2 | 9.9 s | 1h |
| Bands | 2B | 84.3% | 15 | 24.7 s | 1i |
| Qwen Markdown, whole page | 8B (20% on CPU) | 99.6% | 0 | 56.1 s | 1j |

**Full pipeline (first read -> verify -> join split pages -> retry loop) on filings no model had seen:**

| Configuration | Set | First-read rows exact | Trusted / left for review | Trusted pages with a wrong cell | Total per page |
| :-- | :-: | :-: | :-: | :-: | :-: |
| **4B, bands** | sealed test (38 pages) | **98.9%** | 33 / 5 | **0** | 48.4 s |
| 2B, whole-page Markdown | sealed test | 96.5% | 25 / 13 | 0 | **17.0 s** |
| 4B, whole-page JSON (previous default) | sealed test | 97.9% | 35 / 3 | 1 | 58.0 s |
| 4B, bands | fresh set E (19 pages) | 98.6% | 14 / 5 | 0 | 47.6 s |
| 2B, whole-page Markdown | fresh set E | 95.9% | 8 / 11 | 0 | 16.8 s |

**The PDF text layer as a witness for the model's rows (1p):** on native digital PDFs it flagged **315 of 315** wrong rows (label slips, values moved onto another row, wrong or invented numbers) across 7 samples, with 5 false alarms in 9,590 correct rows (0.05%); 200 of 200 on the filings it was not developed on. The gold there is built from the same text layer, and the PDFs come from two main generators and none is scanned, so this measures the matching, not other producers or scans.

* **Safety held everywhere:** the verifier vouched for no wrong number in any configuration on the sealed test or the fresh set. The one trusted misattribution seen anywhere (2B, values shifted one row inside a block, JSON) is in 1e; the old validator (v1) vouched for 9, 3 and 2 wrong pages after the sealed-test JSON, bands and Markdown reads.
* **Best accuracy per second: 4B in bands** (about 99%, 40 s), or **2B in Markdown** (about 96-97%, 10 s) when throughput matters more than coverage; 8B matches 4B bands on accuracy at 1.4x the time and is not worth it as a first reader.
* **Not in the main pipeline yet:** bands, HTML and Markdown are only selectable in the evaluation command (`--format`); `python -m solution2.pipeline` still uses the JSON read. The text-layer witness is an evaluation tool, not a pipeline stage.
* **Known weak spots:** two-sided T-account pages (the 4B Markdown read is largely right but the converter and retry loop are one-sided: 1o), real scans (never run), label slips that arithmetic cannot see (the witness covers digital PDFs only), IFRS and other layouts are a small part of the sample, and some correct pages are held for review because the verifier is strict (false alarms on the sealed test: 2 of 38 pages for 4B bands, 9 of 38 for 2B Markdown).

## Contents of the log

| Section | What it covers |
| :-- | :-- |
| 1a-1d | First unseen-data run: 5 filings, accuracy, latency, what it showed |
| 1e | Three models (4B, 2B, GLM-OCR) on the same pages |
| 1f | 200 vs 300 DPI |
| 1g | Second random sample and its failure modes |
| 1h | First-read format: solution1 JSON vs Qwen HTML vs Qwen Markdown |
| 1i | Reading in bands |
| 1j | The 8B-instruct model (CPU offload) |
| 1k | Bands with the Markdown prompt, and **the protocol for the sealed test** |
| 1l | The original benchmark documents, scan-like and skewed renders, two pipeline bugs found (footnote markers, the band cutter) |
| 1m | **Sealed test result** |
| 1n | Check that later code changes did not move the dev numbers |
| 1o | T-account pages: model or converter? |
| 1p | The PDF text layer as a witness (seven samples) |
| 2-5 | Verifier v1 vs v2 on 28,280 synthetic corruptions; real-output scorecard; retry loop on real outputs; earlier benchmark numbers |
| Limits, Reproduce, Appendix | Limits of these results, the commands, and the bugs the first unseen-data pass exposed |

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

### 1h. First-read format: solution1 JSON vs Qwen HTML vs Qwen Markdown (all 19 pages of both samples, 200 DPI, repair off, first read only)

The same page read three ways, then converted into the pipeline's table (`solution2/formats.py`) so the verifier, joining and scoring are unchanged:
**JSON** = solution1's Stage-1 schema (nested rows, indent and role per row; the numbers in 1a-1g come from this);
**HTML** = prompt "Parse this document page into QwenVL HTML format." (the bare prompt "QwenVL HTML" makes qwen3-vl:4b-instruct answer with one image box, so the instruction is spelled out);
**Markdown** = prompt "QwenVL Markdown" (Qwen's own mode; in practice it writes tables as LaTeX tabular with the indent as \hspace, and sometimes as a pipe table).
Accuracy is scored row by row on the 477 gold rows that carry numbers, ignoring headings, roles and indents, so the formats are comparable.
`typo` = the numbers are right and the label differs by a character or two; `misattributed` = the numbers are right but sit under an unrelated label (the row-shift error).

| Model | Format | Usable pages | Rows exact (of 477) | Pages fully exact | Label typo rows | **Misattributed rows** | Pages with a wrong or missing number | VLM read per page, mean / p90 |
| :-- | :-- | :-: | :-: | :-: | :-: | :-: | :-: | :-: |
| 4B | JSON | 19 | 470 (98.5%) | 13 | 4 | 0 | 3 | 38.9 s / 59.1 s |
| 4B | **HTML** | 19 | **471 (98.7%)** | **15** | 3 | 0 | 1 | **22.1 s** / 33.5 s |
| 4B | Markdown | 19 | 447 (93.7%) | 9 | 13 | 15 (one page) | 1 | **18.0 s** / 24.8 s |
| 2B | JSON | 18 | 401 (84.1%) | 5 | 2 | **28** (7 pages) | 6 | 22.0 s / 33.7 s |
| 2B | HTML | 15 | 358 (75.1%) | 10 | 2 | 1 | 3 | 11.7 s / 17.2 s |
| 2B | **Markdown** | 19 | **463 (97.1%)** | 8 | 12 | **0** | 2 | **9.9 s** / 14.1 s |

How the verifier treated each page's first read (v2 with joining; pages with a wrong or missing number = wrong-number pages):

| Model | Format | Wrong-number pages flagged / **trusted** | Misattributed-row pages flagged / **trusted** | Correct pages held (false alarm) |
| :-- | :-- | :-: | :-: | :-: |
| 4B | JSON | 3 / **0** | 0 / 0 | 2 |
| 4B | HTML | 1 / **0** | 0 / 0 | 3 |
| 4B | Markdown | 1 / **0** | 1 / **0** | 2 |
| 2B | JSON | 6 / **0** | 6 / **1** (サイプレスHD p7, see 1e) | 1 |
| 2B | HTML | 3 / **0** | 1 / **0** | 7 |
| 2B | Markdown | 2 / **0** | 0 / 0 | 5 |

No wrong number was vouched for in any of the six configurations; the only trusted misattribution is the one already reported in 1e.

What this shows:
* **4B: HTML matches JSON on accuracy (98.7% vs 98.5%, 15 vs 13 fully exact pages) and is 43% faster to read** (22.1 s vs 38.9 s). That is the best first-read format for 4B.
* **4B Markdown is faster still (18.0 s) but less accurate** (93.7%): 13 label typo rows and one page with 20 rows shifted (P-LOOPLACE p8; flagged). The typos are real vision slips and repeat across filings:
  繰延税金資産 read as 練延税金資産 (3 pages), 賞与引当金 read as 貸与引当金 (2 filings), plus the harmless ソフトウェア / ソフトウエア spelling variant. Every one of those is a non-word or a rare variant that a term lexicon or the PDF text layer would snap back (see the label-checking discussion); none changes a number.
* **2B Markdown is the big change for 2B**: 97.1% of rows exact (JSON: 84.1%), the row-shift error disappears (28 misattributed rows -> 0), and the read is 2.2x faster (9.9 s vs 22.0 s). Its remaining errors are label typos (12 rows). 2B in Markdown is close to 4B in JSON on rows (97.1% vs 98.5%) at about a quarter of the read time.
* **2B HTML is unreliable**: on 4 of 19 pages it did not produce a table at all (text paragraphs or an image box), so only 15 pages are usable.
* Format choice interacts with model size: the table-native formats help 2B a lot and 4B a little in accuracy, but all of them cut latency by 43-55%.

Cross-format label check (computed offline from the saved reads of the same pages; no extra model calls): where the three formats read the same number row under different labels, the label is suspect.
Of the label errors (right number, wrong label) across the three formats, 4B has 35 and the cross-format disagreement flags 29 (83%); 2B has 51 and flags 42 (82%).
All 6 misses for 4B are one case: the page prints ソフトウエア and every format, in every read, writes the commoner ソフトウェア. Agreement between reads cannot catch an error that all reads share (a language-prior autocorrect);
only the PDF text layer (digital PDFs) or a lexicon with alias handling can. The check also fires on correct labels (52 times for 4B, mostly spacing and indent variants), so it should trigger a cheap crop re-read, not a correction.

How to read it:
* Single runs at temperature 0, 19 pages, repair off, so these are first-read numbers; the retry loop (and the label checks discussed) would improve every row of the table. The HTML prompt was chosen by looking at one page (G-ライズ p6, in sample A); the Markdown prompt was used as-is.
* The converter adds structure the formats do not carry: no role or indent from HTML; an indent from \hspace in Markdown. The verifier is mostly label-driven, which is why it still works, but the tuned-for-JSON verifier may be a little less comfortable with these (more correct pages held, e.g. 2B HTML 7, 2B Markdown 5).
* Process note: the first Markdown run read only 9 of 19 pages for 4B because my converter did not handle LaTeX tables. I fixed the converter and re-parsed the saved model answers (`--replay-from`, no new model calls); latencies are from the original run.

> **Correction (added later):** the converter at the time of 1h left the indentation markup the model writes in a label (\quad, &nbsp;) inside the label text. With the converter fixed, **4B Markdown whole page reads 467 of 477 rows exact (97.9%), 10 fully exact pages, 8 label-typo rows and no misattributed rows** (the table above says 447, 9, 13 and 15); the "20 rows shifted on P-LOOPLACE p8" was this markup, not a real shift. 2B Markdown is unchanged (463). The frozen choices of 1k are unaffected (4B bands 475 > 4B Markdown 467; 2B Markdown 463 > 2B Markdown-bands 454).

### 1i. Reading in bands (the page cut into bands of about 12 text lines, each band read on its own) vs a whole-page read

Same 19 pages, first read only (repair off), scored like 1h. Bands are cut at the white gaps between text lines (no text layer, no table detector, so it also works on scans), neighbouring bands share 2 lines,
one short call on a reduced copy of the page reads the unit and the column headings, each band is read with the repair loop's row prompt (label + one value per numeric column), and the rows are stitched with the overlap removed.
Bands run on a 300 DPI render (the band crops are the size of repair crops); the whole-page formats ran at 200 DPI, but 4B JSON at 300 DPI was no more accurate than at 200 (1f), so resolution is not the explanation.

| Model | First read | Usable pages | Rows exact (of 477) | Fully exact pages | Label typo rows | Misattributed rows | Pages with a wrong or missing number | Read per page, mean / p90 |
| :-- | :-- | :-: | :-: | :-: | :-: | :-: | :-: | :-: |
| 4B | JSON (whole page) | 19 | 470 (98.5%) | 13 | 4 | 0 | 3 | 38.9 s / 59.1 s |
| 4B | HTML (whole page) | 19 | 471 (98.7%) | 15 | 3 | 0 | 1 | 22.1 s / 33.5 s |
| 4B | **Bands** | 19 | **475 (99.6%)** | **17** | 2 | 0 | **0** | 40.5 s / 53.4 s |
| 2B | Markdown (whole page) | 19 | 463 (97.1%) | 8 | 12 | 0 | 2 | 9.9 s / 14.1 s |
| 2B | Bands | 18 | 402 (84.3%) | 1 | 3 | 9 | 15 | 24.7 s / 29.7 s |

* **4B in bands is the most accurate first read measured so far**: 475 of 477 rows, no page with a wrong or missing number, no misattributed row, 17 of 19 pages fully exact. Every page the verifier trusted was fully exact (14 pages), the 3 pages it held are correct ones
  (two split-statement halves and one half whose neighbour was flagged). Its two remaining differences are label typos (the harmless ソフトウェア spelling variant). The cost is the same as JSON: about 3.9 bands per page at 7.9 s each plus an 8.6 s metadata call.
  Bands are independent of each other, so they could run in parallel (not tested here).
* **2B in bands is worse than 2B on a whole page in Markdown** (84.3% vs 97.1%): 29 missing and 34 extra rows. The cause was not isolated; one pattern seen is a value copied into an extra third cell (AVANTIA p27, 403,124 / 497,360 / 497,360).
  The row-reading prompt (a JSON-constrained answer) suits 4B and not 2B, so the band idea works for 4B and has not been tried for 2B with the Markdown prompt.
* **A robustness fix came out of the 2B run**: Ollama aborts a generation that loops ("token repeat limit reached"), and the first version then lost the whole page (8 of 19 pages unreadable, 258 rows exact). A band that fails is now read as two halves; 7 of 84 band reads needed that and
  1 page was still unreadable. The padding of empty extra values 2B adds to rows is now trimmed. The 4B run was made before these two changes; none of its band reads failed, so the split never triggered, and the padding trim only matters for 2B.
* All number errors in the 2B run were flagged by the verifier (0 trusted pages with a wrong number), as in every other configuration.

### 1j. Qwen3-VL 8B-instruct (Q4, 8k context so that about 80% of it stays on the 8 GB GPU; 20% of the layers run on the CPU)

Same 19 pages, first read only (repair off). The 8k context was chosen from a measurement: at 8k the model takes 6.9 GB (20% CPU / 80% GPU); at 16k it takes 8.2 GB (33% CPU).

| First read | Usable pages | Rows exact (of 477) | Fully exact pages | Pages with a wrong or missing number | Label rows that differ | Read per page, mean / p90 |
| :-- | :-: | :-: | :-: | :-: | :-: | :-: |
| 4B Markdown | 19 | 447 (93.7%) | 9 | 1 | 13 typo + 15 misattributed | 18.0 s / 24.8 s |
| 4B Bands | 19 | 475 (99.6%) | 17 | 0 | 2 | 40.5 s / 53.4 s |
| **8B HTML** | **1** | 34 | - | - | - | 64.2 s / 93.1 s |
| **8B Markdown** | 19 | **475 (99.6%)** | **18** | **0** | 2 | 56.1 s / 74.0 s |
| 8B Bands (stopped after 6 pages) | 6 of 19 | 167 of 167 numbers right | - | - | - | 66-254 s, mean 106 s |

* **8B Markdown matches the best 4B result in accuracy (99.6%, no page with a wrong or missing number, 18 of 19 pages fully exact) at 56 s a page against 40 s for 4B Bands.** It does not make the label slips 4B Markdown made
  (繰延 -> 練延, 賞与 -> 貸与 do not occur); its only two label differences are 内 read as the variant glyph 內 (1年內償還予定の社債, 1年內返済予定の長期借入金).
* **8B HTML failed as a format**: with the same prompt it writes a flat list of paragraphs, one per cell, not a table, so 18 of 19 pages have no table to parse. That is a prompt-compliance problem, not an OCR verdict.
* **8B Bands was stopped**: the 6 pages read before stopping had every number right, but 5 of the 6 had one or two duplicated rows from the band overlap (the stitcher could not match the repeated rows, so the verifier would hold those pages), and the pages took 66-254 s (the 254 s page is サイプレスHD p7).
  With 4B Bands already at 475 of 477 in 40 s there was no accuracy left for 8B to win, so the remaining 13 pages (about 25 more minutes) were not run.
* **Cost of the CPU offload**: 8B Markdown reads a page in 56 s against 18 s for 4B Markdown (3.1x), and about 2.6x for bands.
* **Conclusion on this sample**: 8B is not worth using as the first reader (same accuracy as 4B Bands, 1.4x to 2.6x the time). Its value is more faithful characters in labels, so the idea to test later is an 8B read of only the label column or of flagged rows as a second opinion.
* **Process note**: 8B writes the indent as \quad or &nbsp; inside the label cell; my first conversion kept that markup in the label and scored 63 rows as misattributed. The converter now turns it into an indent, and the saved answers were re-parsed (no new model calls); the numbers above are the re-parsed ones.

### 1k. Bands read with Qwen's Markdown mode instead of the JSON row prompt (same 19 dev pages, first read only, 300 DPI)

| Model | First read | Usable pages | Rows exact (of 477) | Fully exact pages | Missing / extra rows | Misattributed rows | Pages with a trusted misattribution | Read per page, mean / p90 |
| :-- | :-- | :-: | :-: | :-: | :-: | :-: | :-: | :-: |
| 4B | Bands, JSON row prompt (1i) | 19 | **475 (99.6%)** | **17** | 0 / 0 | 0 | 0 | 40.5 s / 53.4 s |
| 4B | Bands, Markdown | 17 | 376 (78.8%) | 4 | 37 / 45 | 0 | 0 | 33.1 s / 43.0 s |
| 2B | Whole page, Markdown (1h) | 19 | **463 (97.1%)** | 8 | 2 / 1 | 0 | 0 | **9.9 s** / 14.1 s |
| 2B | Bands, JSON row prompt (1i) | 18 | 402 (84.3%) | 1 | 29 / 34 | 9 | 0 | 24.7 s / 29.7 s |
| 2B | Bands, Markdown | 19 | 454 (95.2%) | 5 | 9 / 13 | 2 | **2** | 22.4 s / 25.8 s |

* The Markdown prompt per band is **worse than the JSON row prompt for 4B** (78.8% vs 99.6%; 2 pages unreadable; 37 missing and 45 extra rows) and **not better than a whole-page Markdown read for 2B** (95.2% vs 97.1%), and for 2B it produced two pages with misattributed rows
  that the verifier trusted. The cause for 4B was not isolated (the symptoms are missing rows and extra, mostly duplicated, rows around the band overlaps).
* Result for the choice of configuration: **4B -> bands with the JSON row prompt; 2B -> whole-page Markdown.**

#### Protocol for the sealed test, written before any test page was read by a model

The test split is the 21 TDnet filings (38 gold pages: 34 clean, 2 split-statement halves, 2 ambiguous) that no model has read; their gold comes from the PDF text layer only.
Configurations are frozen from the dev results above by one rule: highest rows-exact on the 19 dev pages, fewer trusted misattributions breaking a tie. No change to the verifier, joining, repair, the band cutter or the converters will be made in response to test results; a problem found there is reported as it is and fixed afterwards on dev.
Three runs, each the full pipeline (first read -> verify -> join split pages -> retry loop):
1. **4B, bands (JSON row prompt), 300 DPI** - the frozen 4B configuration.
2. **2B, whole-page Qwen Markdown, 200 DPI** - the frozen 2B configuration.
3. **4B, whole-page solution1 JSON, 200 DPI** - the previous default, as the baseline for the improvement.
Reported for each: first-read accuracy (rows exact, pages with a wrong or missing number, misattributed rows), what the verifier trusted and flagged, whether any trusted page has a wrong number or misattributed row, accuracy and status after the retry loop, and latency per stage.

### 1l. The original benchmark documents (Yamato, Calbee, FastRetailing, Nikon, and the solution1 baseline page) and scan-like renders

Eight pages in five documents, 245 gold rows with numbers; a two-page statement is joined before scoring. First read only (repair off). The scan-like render is a half-resolution image upscaled, blurred, noised and JPEG-compressed; the skewed render is rotated one degree.
Gold: the hand-built Yamato, Calbee and baseline trees and the text-layer silver for FastRetailing and Nikon (checked equal to the PDF text layer of those pages).

**4B, clean renders (final code):**

| First read | Rows exact (of 245) | Without the Yamato T-account page (of 196) | baseline | Calbee | FastRetailing | Nikon | Yamato (T-account) | Time per document |
| :-- | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: |
| JSON, whole page | 192 (78.4%) | 192 (98.0%) | 37/37 | 51/51 | 64/67 | 40/41 | **0/49** | 70.0 s |
| Markdown, whole page | 209 (85.3%) | 188 (95.9%) | 32/37 | 51/51 | 65/67 | 40/41 | 21/49 | 34.6 s |
| **Bands** | **218 (89.0%)** | **194 (99.0%)** | **37/37** | **51/51** | **65/67** | **41/41** | 24/49 | 69.1 s |

**4B bands on degraded renders (final code):** scan-like 226 of 245 (baseline, Calbee, FastRetailing 65/67 and Nikon all as on the clean render; Yamato 32/49); skewed 213 of 245 (three documents unchanged, FastRetailing 64/67 with 9 extra rows, Yamato 20/49). Every page was read.
**2B, Markdown whole page:** clean 187, scan-like 184, skewed 188 of 245 (the Yamato page: 0, 2, 4 of 49).

* **Bands is the best 4B first read on ordinary statements, here too**: perfect on three of the four non-T-account documents (the only misses are two label differences in FastRetailing), 99.0% of rows without Yamato, and it does not get worse on a noisy or skewed render once the cutter handles them (see below).
* **T-account pages look unsolved by every first read here, but see 1o: the 4B Markdown answer is largely correct and the failure was mostly the one-sided converter.** The Yamato page is a two-sided table (assets left, liabilities right); the whole-page JSON read gets none of it, Markdown 21 of 49, bands 24 of 49 (the bands cut across both sides). The retry loop also declines these pages. This is the known open case.
* **Two pipeline gaps found by this run, both fixed before the sealed test (none was a vision error):**
  1. *Footnote markers in values.* Every configuration lost the same 6 FastRetailing rows: the page prints "※1 108,854" and "※3 △17,221", the model transcribed that correctly, and the strict number parser rejected it as "not a plain number". A footnote reference separated from the number by a space is now ignored ("※1110,588" with no space is still rejected as ambiguous). The harness gate still passes (0 violations in 28,280). None of the 38 saved dev reads of the 19 dev pages (JSON and bands) contains a footnote marker, so the dev numbers in 1a-1k are unaffected.
  2. *The band cutter on noisy or skewed pages.* The first version found only 4 to 7 text lines on degraded renders (the lines ran together), so the Nikon pages became unreadable (25 of 41 rows) in both degradations. A clean render normally takes the original path; **correction: on 3 of the 19 clean dev pages (short split-statement halves) the test that the lines ran together also fires and the line count changes by one (21 to 20, 20 to 19, 22 to 21), so this was not strictly true; the effect is measured in 1n.** When the lines run together it filters speckle, try rotating the page up to 2 degrees, and finally cut the inked span into evenly spaced pseudo-lines. Line counts on the degraded renders now match the clean ones.
* Before these two fixes the same run read: 4B JSON 186, Markdown 203, bands 212; bands on scan-like 182 and skewed 174 (one unreadable document each).

### 1m. Sealed test: 21 filings never seen while building, 38 pages (34 clean, 2 split halves, 2 ambiguous), the full pipeline (first read -> verify -> join -> retry loop)

Run exactly as written in the protocol in 1k, after the two pipeline fixes of 1l (footnote markers; the band cutter on degraded pages) and before any test result was seen. Nothing was changed afterwards. Gold = the PDF text layer. 1017 gold rows with numbers.
All three runs read every page.

| | 4B bands (frozen 4B config) | 2B whole-page Markdown (frozen 2B config) | 4B whole-page JSON (previous default) |
| :-- | :-: | :-: | :-: |
| First-read rows exact (of 1017) | **1006 (98.9%)** | 981 (96.5%) | 996 (97.9%) |
| Pages fully exact on the first read | **28** | 18 | 21 |
| Pages with a missing or extra number row on the first read | **4** | 6 | 13 |
| Verifier first-sight verdict: correct page trusted / wrong page flagged / correct page held / **wrong page trusted** | 32 / 4 / 2 / **0** | 22 / 7 / 9 / **0** | 24 / 12 / 1 / **1** |
| v1 (solution1 validator) on the same first reads: wrong page trusted | 3 | 2 | 9 |
| Retry loop on flagged pages: fixed / improved / unchanged | 1 / 1 / 4 | 3 / 0 / 13 | 10 / 1 / 2 |
| Final status: trusted (incl. repaired, joined) / left for review | 33 / 5 | 25 / 13 | 35 / 3 |
| Trusted pages with a wrong cell after the pipeline | **0** | **0** | **1** |
| VLM read per page, mean / p90 | 41.7 s / 51.6 s | 11.2 s / 15.6 s | 39.8 s / 54.0 s |
| Total per page incl. repair, mean / max | 48.4 s / 118 s | **17.0 s** / 46 s | 58.0 s / n/a |
| Wall time for 38 pages | 1842 s | **647 s** | 2207 s |

* **The dev results held on a set that was never touched**: 4B bands 98.9% (dev 99.6%), 2B Markdown 96.5% (dev 97.1%), 4B JSON 97.9% (dev 98.5%). The ranking is the same as on dev.
* **4B bands is more accurate than the old JSON read (98.9% vs 97.9%, 28 vs 21 fully exact pages, 4 vs 13 pages with a number error) and the pipeline needs far less repair** (2 pages repaired or improved instead of 11), which makes it about 17% faster per page overall (48.4 s vs 58.0 s) and 16% faster in wall time even though its first read is a little slower.
  Both end with about the same number of trusted pages (33 and 35), because the retry loop recovers most of the JSON read's errors.
* **The one trusted page with a wrong cell is in the JSON baseline** (G-TENTIAL p8, an ambiguous page): the model wrote "－ / －" on the heading その他利益剰余金, which the page prints with no value at all. It is a nil cell, so no total changes and arithmetic cannot see it. The bands and Markdown reads of the same page have no such cell (both were held by the verifier as a false alarm on that page; it is one of the two ambiguous pages whose gold the verifier also flags).
* **The old validator (v1) would have vouched for 9 wrong pages after the JSON read (3 after bands, 2 after Markdown)**; v2 vouched for 1, 0 and 0.
* **2B in Markdown trades pages for speed**: 2.8x faster than 4B bands per page and in wall time (3.7x on the model read alone), but 13 pages are left for review (9 of them are correct pages the verifier holds, because Markdown carries no indent or role) against 5 for 4B bands; the retry loop recovers only 3 of its flagged pages. It is the choice when throughput matters more than coverage; no wrong cell was trusted.
* 4B bands's 5 pages left for review: 2 correct pages the verifier holds (false alarms, one of them the ambiguous G-TENTIAL page) and 3 pages with a number error that the retry loop could not fully fix.
* Limits: 38 pages from 21 filings, mostly J-GAAP (7 IFRS pages), digital PDFs only, no T-account page, gold from the text layer. The confidence interval on 98.9% vs 97.9% is wide at this size; the difference that matters is the lower amount of repair needed and the absence of a trusted wrong cell.

### 1n. Check after the sealed test: the dev bands numbers re-run with the final code

The 4B bands result on dev (1i) was measured with an earlier `bands.py` than the sealed test used (the later one splits a failed band, trims padding, and finds lines on degraded pages; code fingerprints `bf90aec177d0` for the dev run, `f3320f76e8a7` for this re-run, `41ac42e2e3ca` for the test runs, which did not yet include `bands.py` in the hash). The same 19 dev pages, 4B, bands, 300 DPI, first read only:

| | Rows exact (of 477) | Fully exact pages | Pages with a number error | Correct pages trusted | Trusted pages with a wrong number | Read per page |
| :-- | :-: | :-: | :-: | :-: | :-: | :-: |
| Earlier code (1i) | 475 (99.6%) | 17 | 0 | 14 | 0 | 40.5 s |
| Final code | 474 (99.4%) | 16 | 0 | 12 | 0 | 40.1 s |

* Two pages differ: タキヒヨー p6 has one label difference that the earlier run did not (a model difference or the cutter; temperature 0 is not bit-for-bit repeatable here), and SHIFT p18 (a split-statement half whose line count the new cutter changes by one) has the same correct rows but is now held for review instead of being trusted after joining.
* So the effect of the later code on dev is one row (0.2 point) and one more correct page held; no number errors, no wrong number trusted. The sealed-test result (98.9%) is consistent with 99.4% to 99.6% on dev.
* The line finder's "lines ran together" test also fires on short pages (3 of the 19 clean dev pages, all split halves). A tighter test is a later fix; it was not changed after the sealed test.

### 1o. T-account pages (the Yamato two-sided balance sheet): is any model failing, or is it the converter?

Layout: four columns (account | amount | account | amount): assets on the left, liabilities and net assets on the right, rows aligned across the sides although the sides have different lengths; the totals 負債合計 and 資産合計 sit in merged cells.
The first-read results in 1l (4B JSON 0 of 49 rows, Markdown 21, bands 24) made it look as if no model could read these pages. Re-scoring the saved Markdown answers with a converter that understands a two-sided table (each 4-cell row becomes a left row and a right row; a merged total row is read as label + amount) changes the picture:

| Read of the Yamato page (49 gold rows) | Rows exact with the one-sided converter (1l) | Rows exact with a two-sided converter | Rows read |
| :-- | :-: | :-: | :-: |
| 4B Markdown, clean render | 21 | **43** (48 if the quirk below is ignored) | 48 |
| 2B Markdown, clean render | 0 | 24 | 34 |
| 2B Markdown, scan-like render | 2 | 39 | 47 |
| 2B Markdown, skewed render | 4 | 45 | 46 |

* **4B reads the page correctly; most of the earlier failure was the converter**, which glued the two sides of a row into one row with the right-hand label lost (e.g. 流動資産 [6,727,851, 4,518,204]). The model's own errors on this page are two: the page prints the label letter-spaced ("そ の 他") and the model writes "その他の" (a character added, five rows, values right; the same language-prior habit as ソフトウェア / ソフトウエア), and it dropped one right-hand row (その他 368,405).
  This matches the very first benchmark of the project (Qwen 4B 41 of 49, GLM-OCR 43 of 49, Qwen 2B 33 of 49 on this page, scored more leniently).
* **2B does struggle**: it reads 34 to 47 rows depending on the render (24, 39 and 45 exact on the same page rendered three ways), i.e. it drops rows and is not repeatable.
* **Whole-page JSON (0 of 49) and bands (24 of 49) do fail for a model reason too**: the Stage-1 JSON schema has no place for two sides, and a band cut across both sides gives the row prompt two tables at once. The raw JSON answer was not saved, so the exact failure pattern of the JSON read is not known.
* **What is missing to support these pages** (not built; about a day): a two-sided converter in the Markdown/HTML adapter (as used for the table above), a side-aware band cutter (find the vertical gutter, cut left and right separately, concatenate the sides), and crops per side in the retry loop (it currently declines two-column pages).
  The v2 verifier itself already accepts the flattened Yamato table (it is in the harness corpus and is trusted).

### 1p. The PDF text layer as a witness for the VLM's rows (`solution2/witness.py`, scored by `solution2/witness_eval.py`)

**What it is.** For a native digital PDF, every VLM row is matched to a text line of the page by its label (order-preserving, tolerant of a garbled character), and the line's numbers and label are compared with the model's. Per row: *agree*, *label_variant* (same numbers, a few characters of the label differ; the printed label is proposed), *conflict* (the numbers differ; kind = shifted / spurious / dropped / wrong / relabelled / elsewhere / duplicate) or *unknown*.
It abstains on a page whose text layer cannot be trusted (OCR-software producer, invisible OCR font, glyphs without a unicode mapping, too few numbers) and on two-sided T-account pages. `snap_labels` returns a copy with the `label_variant` labels replaced by the printed ones (numbers never touched); it is not wired into the pipeline. Six unit tests.

**How it was scored** (no model run: it re-reads the saved first-read answers of 11 answer sets, 1 to 3 times per page: 4B and 2B in JSON, HTML, Markdown, bands and bands-with-Markdown, 8B Markdown, and the three sealed-test runs). Each row with numbers is classified against the gold: correct / label_typo (numbers right, label 70% similar) / misattributed (numbers right, unrelated label: a value slid onto another row) / number_wrong; repeated rows are scored on the page only.
Splits: **A** = the five dev filings the witness was developed on; **B** = the other five dev filings (held out); **T** = the sealed test answers, scored once after the code was frozen.

| Split | Wrong rows detected | by type: label typo / misattributed / number wrong | Correct rows falsely flagged | Wrong pages flagged | Correct pages flagged |
| :-- | :-: | :-: | :-: | :-: | :-: |
| A (developed on) | 115 / 115 | 52 / 32 / 31 | 2 of 2884 (0.07%) | 58 / 58 | 0 / 60 |
| B (held-out dev) | **99 / 99** | 17 / 66 / 16 | 3 of 1921 (0.16%) | 32 / 32 | 3 / 52 |
| T (sealed test) | **54 / 54** | 39 / 11 / 4 | **0 of 2982** | 34 / 34 | 0 / 80 |
| C (5 more dev filings, read after the freeze) | **8 / 8** | 5 / 3 / 0 | **0 of 450** | 7 / 7 | 0 / 11 |
| D (the last 4 dev filings, read after the freeze) | **13 / 13** | 10 / 3 / 0 | **0 of 409** | 8 / 8 | 0 / 6 |
| E (10 filings downloaded after the freeze) | **26 / 26** | 21 / 1 / 4 | **0 of 944** | 16 / 16 | 0 / 22 |
| **All splits** | **315 / 315** | 144 / 116 / 55 | **5 of 9,590 (0.05%)** | 155 / 155 | 3 / 231 |
| of which never developed on (B + T + C + D + E) | **200 / 200** | 92 / 84 / 24 | 3 of 6,706 (0.04%) | 97 / 97 | 3 / 171 |

Injected errors on fully correct 4B-bands answers (one error per copy: a block of 5 values moved up one row, a label character swapped for a look-alike or related one, one digit changed, a value put on a heading): **detected 100% of the time in every kind and split** (A 30, B 18, T 87 of each kind).
In the first version the witness had no fallback for a row whose label could not be matched and abstained on 13 wrong rows of split A (and missed duplicated rows); the neighbour-line search and the `duplicate` verdict were added from split A only, which is why A is called developed-on.

**Fifth sample, C (run after the witness was frozen, nothing changed for it).** Five more unused dev filings chosen by a rule written before the run: the one filing whose PDF producer had not been seen before (PDFium, G-GO), and the first two unused PDFlib and the first two unused iText 7 filings by document id. 9 gold pages, read by 4B bands and by 2B Markdown (first reads: 228 of 229 and 222 of 229 rows exact).
All 8 wrong rows were detected (5 label slips, 3 misattributed rows), 0 of 450 correct rows were flagged, and the 4 kinds of injected error were detected 24 of 24 times each. On the PDFium filing the text layer was usable and all 41 rows of the 4B-bands answer agreed; the one label slip in the 2B Markdown answer was flagged as a `label_variant`.
**Sixth sample, D (the last four unused dev filings; three iText 7, one PDFlib; run after the freeze).** 7 gold pages, read by 4B bands (207 of 211 rows exact) and 2B Markdown (202 of 211). All 13 wrong rows were detected (10 label slips, 3 misattributed), 0 of 409 correct rows flagged, 12 of 12 injected errors of each of the four kinds detected. With this, every one of the 40 downloaded filings has been read by a model.

**Seventh sample, E (ten new filings, downloaded after the witness was frozen, on request).** Planned and listed first (seeded random from the 476 eligible 決算短信 of the last 31 days, 3 IFRS forced in, none from a company already used), approved by you, 4.0 MB; five iText 7, four PDFlib and one PDF with no producer metadata at all. 19 gold pages (14 clean, 1 split half, 4 ambiguous), read by 4B bands and 2B Markdown with the full pipeline (first-read witness scores below).
All 26 wrong rows were detected (21 label slips, 1 misattributed, 4 wrong numbers), 0 of 944 correct rows were flagged, 16 of 16 wrong pages were flagged and none of the 22 correct pages, and 45 of 45 injected errors of each kind were detected. The filing without producer metadata was handled like the others (usable text layer, no flags beyond the real errors).
The same fresh filings are also a second untouched run of the frozen pipelines: **4B bands** 479 of 486 first-read rows exact (98.6%), 15 of 19 pages fully exact, 14 pages trusted after the pipeline and 5 left for review (three of those five are ambiguous pages whose gold the verifier itself flags), 0 trusted pages with a wrong cell, 47.6 s per page; **2B Markdown** 466 of 486 (95.9%), 6 of 19 pages exact, 8 trusted and 11 left for review, 0 trusted with a wrong cell, 16.8 s per page. The old validator would have vouched for 2 wrong pages after the 2B read (silent errors); v2 for none.

I looked for other producers among the PDFs in the repository to widen this: none gives an ordinary balance sheet with usable gold. Two are printed web pages with no statement on them (nothing to compare; the witness abstains: "too few numbers on the page"). One is a PowerPoint slide (Nikon, 960x540, a stacked-bar chart of the balance sheet): its text layer is usable but labels and numbers are scattered around the chart, so a line holds a label after numbers; the layout test calls it two-sided and the witness abstains; no model was run on it. The Yamato annual report has T-account pages (abstains: two-sided layout, pages 1 and 11). So the abstention paths now fire on real files, but they are not scored for accuracy.

**Read this with its limits:**
* **The gold is built from the same text layer** (silver gold), so this scores the witness's *matching* of rows to lines, not the correctness of the text layer. The independent evidence is the hand-built gold of the benchmark documents, which the text layer reproduces exactly (baseline 37/37, Calbee 51/51, FastRetailing 67/67, Nikon 41/41 rows; Yamato is two-sided and declined).
* **Every PDF in the set comes from TDnet and almost all from two generators** (of the 36 filings read by a model: PDFlib 21, iText 7 12, and one each of Acrobat/Word, iText 9 and PDFium) and none is scanned. The abstention paths for OCR or broken-font text layers are covered only by unit tests (a synthetic PDF stamped with an OCR producer); they never fired on real data. Whether the near-perfect rates survive other producers, annual reports or scans is **not shown**.
* **False alarms are few and of one kind**: a correctly repeated row (a group and its only child with the same amount, e.g. 有形固定資産 or 資本金 on IFRS pages) is flagged `duplicate` (3 rows in B), and a repeated label such as その他 can mis-map (2 rows in split A, 2B bands).
* **A dropped row is the weak spot**: the "text lines no row was matched to" list names a line on 4 of 9 (A) and 3 of 9 (B) pages where a row is missing, and on 10 of 109 and 10 of 75 pages where none is, so it is informational only; arithmetic remains the way to catch a dropped row.
* The injected errors are single and clear; real errors are not always.

**What it is good for.** On a digital PDF with a normal table it recognised every label slip (繰/練, 賞/貸, ソフトウエア/ソフトウェア, 内/內), every misattributed value and every wrong or invented number that appears in the saved answers, with a false-alarm rate of 0 to 0.2% of correct rows. The next step would be to use a `conflict` as a trigger for the retry loop (re-read that row's crop) and a `label_variant` on a native PDF as a label correction, and to measure that on a handful of PDFs from other producers and with OCR layers.

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

* 92 TDnet pages from 50 filings plus 8 benchmark pages: small samples, mostly J-GAAP, all digital PDFs; no real scan has been run. Differences of one page between configurations are within noise.
* The gold for TDnet pages is the PDF text layer (kept only where the verifier agrees), so a model error that the text layer shares cannot show up; the benchmark documents' hand-built gold agrees with their text layer exactly.
* The PDFs come from two main generators (PDFlib and iText 7); the text-layer witness is not shown to hold for other producers, OCR layers or scans.
* Arithmetic identities do not see a consistent mislabel, a swapped period, or values moved between rows of one block (seen once in 1e, 2B @200 DPI); these are the "known gaps" in section 2. The text-layer witness (1p) covers them on digital PDFs.
* T-account (two-sided) pages are read by the models but not supported by the converter, the band cutter or the retry loop (1o).
* The cost of failing closed: some correct pages are held for review (verifier false alarms, e.g. a flattened sub-group, a half statement that starts at 負債及び資本).

## Reproduce

```bash
python -m pytest solution2 -q                                         # 171 tests, no model needed
python -m solution2.harness.run --verifier v2 --gate                  # section 2 (the mutation gate)
D=solution2/data_sample/tdnet                                         # the 50 filings, plan files and prepared gold pages (relative paths)
# first reads on the dev filings (needs Ollama and the model): 1h-1k
python -m solution2.tdnet eval --data-dir $D --split dev --include-split --include-ambiguous --no-repair --model qwen3-vl:4b-instruct --format bands --dpi 300 --run-name fmt_bands_qwen4b
#   --format json | html | markdown | bands | bands_md    --model qwen3-vl:2b-instruct | qwen3-vl:8b-instruct (--num-ctx 8192)
# full pipeline on the sealed test, the frozen configurations (1m): --split test, repair and joining on
python -m solution2.tdnet eval --data-dir $D --split test --include-split --include-ambiguous --model qwen3-vl:4b-instruct --format bands --dpi 300 --run-name sealed_4b_bands
python -m solution2.tdnet eval --data-dir $D --split test --include-split --include-ambiguous --model qwen3-vl:2b-instruct --format markdown --dpi 200 --run-name sealed_2b_markdown
# the original benchmark documents, clean and degraded (1l, 1o)
python -m solution2.benchmark_docs --model qwen3-vl:4b-instruct --configs json markdown bands --degrade scan skew
# the PDF text-layer witness on the saved answers of the runs above (1p); no model needed
python -m solution2.witness_eval --splits A B T C D E
```

`solution2/data_sample/tdnet` holds the 50 filings (PDF, about 24 MB), the sampling plans (`plan.json`: 40 filings, token `b174e2e274`; `plan_2.json`: the 10 of set E, token `a10fb2567c`) and the 92 prepared gold pages.
TDnet's viewer only keeps about 31 days, so these copies are the only way to reproduce the runs later. The saved model answers of the runs themselves live in `solution2/data/` (git-ignored scratch), which `witness_eval` reads.
Note: the dev/test split recorded in the manifest is the one made when each plan was drawn (a hash of the docID); set E was drawn with every filing marked "test", which is why `--split test` also contains the 10 fresh filings (use `--docs` to pick a subset).

## Appendix: what the first unseen-data pass exposed (history)

The first pass over pages that nothing had been tuned on (Nikon IFRS, Fast Retailing) found these verifier faults, all fixed since; those pages are now part of the mutation suite (`fixtures/silver/`).

| finding | cause | fix |
|---|---|---|
| Fast Retailing p2 flagged on a clean page | sub-group heading with no subtotal (`その他利益剰余金`) read as a dropped total | only suspicious when the swallowing window also holds closed section totals |
| Nikon (IFRS) p2: `非流動負債合計` off by +2 | rounding convention defaulted to truncation on thin evidence | convention needs evidence; with none, both are allowed; one outlier no longer flips it |
| Nikon p2: `資本合計` double counts | inline subtotal `親会社の所有者に帰属する持分` has no 計 label | a row equal to the sum of the rows above is a subtotal; role-only totals no longer close sections |
| Nikon: false `date_unresolved` / "3 values for 2 periods" | a 増減 (change) column has no date, and the VLM left it out of `periods` | change columns need no date, are inferred from `current - prior = change`, and add a per-row identity check; the pipeline prompt now asks for every numeric column |
| Nikon p2 flagged although every number was right | VLM echoed `資本金` as a heading above the `資本金` line | an echoed heading is ignored unless a total is named after it |
| Nikon p1 never repaired | the VLM turned the line item `有形固定資産` into a heading; "too broken" fired on one large window | blank cells of a heading beside the window can be filled by the reads; "too broken" only on near-total failure |
| loop crash on the Yamato T-account; one outcome got *worse* | overlapping crops disagreed about a row; two-column pages were not detected | deletes need unanimity, any error in a window fails closed, T-account pages are detected and declined |

