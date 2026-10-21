# solution2 results: accuracy and latency

Model for every VLM number below: `qwen3-vl:4b-instruct` via Ollama, 200 DPI pages, 8 GB GPU, one request at a time.
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

* 11 pages from 5 filings, dev split only; no test-split number exists yet. The test split must stay unseen until the verifier is frozen.
* The text-layer gold covers digital PDFs only. Scans would need the XBRL gold (EDINET path is built but has never been run with a key).
* Arithmetic identities do not see a consistent mislabel or a swapped period; these show up as "known gaps" in section 2.
* One P-LOOPLACE-style false alarm per ~11 pages is the current cost of failing closed.

## Reproduce

```bash
python -m pytest solution2 -q                                                            # 148 tests
python -m solution2.harness.run --verifier v2 --gate                                     # section 2
python -m solution2.tdnet eval --data-dir solution2/data_sample/tdnet --split dev --include-split   # section 1 (needs Ollama + qwen3-vl:4b-instruct)
python -m solution2.tdnet eval --data-dir solution2/data_sample/tdnet --split dev --include-split --no-join   # same run, page by page
```

`solution2/data_sample/tdnet` holds all 40 downloaded filings (about 20 MB), the plan (token `b174e2e274`), and the 11 prepared gold pages
(relative paths). TDnet's viewer only keeps about 31 days, so these copies are the only way to reproduce the run later.
