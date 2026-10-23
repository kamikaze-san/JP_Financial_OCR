# solution2 - deterministic verification v2

`solution1/` is untouched. This package replaces its `AccountingValidator` with a verifier that
(a) never silently defaults, (b) discovers its equations from the table instead of hardcoding account
names, and (c) gives every cell a verdict. It is judged by the test harness in `harness/`.
Measured accuracy and latency of everything built so far: [RESULTS.md](RESULTS.md).

```
solution2/
  pipeline.py  entry point: render -> VLM -> verify -> repair -> export (solution1's pipeline is untouched)
  extract.py   Stage-1 VLM call (solution1's prompt, imported unchanged) and page rendering
  bands.py     first read in bands: the page cut at the white gaps between text lines, each band read on its own, rows stitched
  formats.py   first read as Qwen HTML / Markdown tables (+ LaTeX tabular) converted to the Stage-1 tree; structure-blind row scoring
  benchmark_docs.py  the original benchmark documents, clean and degraded (scan-like, skewed) renders
  export.py    per-cell-status exporters (json / xlsx / md)
  verify/      parsing.py   strict cell / unit / date / label parsers (no silent defaults)
               reducer.py   hierarchy-driven verifier -> DocumentVerdict (per-cell verdicts)
               loader.py    tolerant Stage-1 JSON loader (reports every repair)
  repair/      the automated, location-only retry loop (layout, readers, consensus, loop)
  tdnet/       keyless TDnet 決算短信 source (plan -> approve -> download), text-layer silver gold (see tdnet/README.md)
  edinet/      EDINET downloader (list first, approve, then fetch) + XBRL-CSV gold + held-out evaluation (see edinet/README.md)
  harness/     mutation suite, real-output scoring, repair scoring, gate (see harness/README.md)
  fixtures/    real Qwen3-VL output collected for the gold pages
  data_sample/ the 40 TDnet filings (PDF), the plan and the prepared gold pages (tracked; solution2/data/ is the git-ignored scratch area)
  tests/       pipeline, bands and formats tests (the verifier, repair and TDnet/EDINET tests sit next to their packages)
```

## Use

```bash
python -m solution2.pipeline statement.pdf                 # the whole thing: PDF -> VLM (JSON read) -> verify -> join -> repair -> xlsx/md/json
python -m solution2.tdnet eval --format bands --dpi 300 ...  # the same chain with another first read: json | html | markdown | bands | bands_md
python -m solution2.verify path/to/stage1.json -v          # exit 1 if not trusted
python -m solution2.harness.run --verifier v2 --gate        # acceptance gate
python -m solution2.repair stage1.json --pdf page.pdf        # verify, then repair what is not trusted
python -m solution2.harness.realcheck --strict              # real VLM output vs ground truth
python -m solution2.harness.repair_eval --reader ollama     # the retry loop vs ground truth
python -m pytest solution2 -q
```

```python
from solution2.verify import parse_stage1, verify_document
raw, notes = parse_stage1(open("page.json", encoding="utf-8").read())
v = verify_document(raw)
v.trusted, v.coverage          # document verdict
v.cell_status[(row_index, col)]  # exact | rounding | unverified | failed | structure_ambiguous
v.errors, v.equations          # what failed, and every equation with its per-column diff
```

## The pipeline (`pipeline.py`)

```bash
python -m solution2.pipeline balance_sheet_after_correction.pdf [--pages 1 2] [--model qwen3-vl:4b-instruct] [--dpi 200] [--no-repair]
```

Per page it writes to `solution2/output/<pdf name>/`: `page<N>_vlm_raw.json` (what the VLM returned, byte for byte),
`page<N>_stage1.json` (the table the verifier saw, after repair), and `page<N>.json/.xlsx/.md`: every cell with its
verification status, repaired cells with their original reading, the equations checked, findings, repairs, and the
windows left for review. A page that is not trusted is still exported (with a red banner), never dropped.

Page statuses: `trusted` | `repaired` | `needs_review` | `no_table` (page has no numbers: skipped, not a failure) |
`extraction_failed`. Exit code 0 only if every page with a table is trusted or repaired.

`solution1/` is not modified: the pipeline imports only solution1's Stage-1 prompt and data models, and a test
checks that running it leaves every file under `solution1/` byte-for-byte unchanged. Sample outputs from real runs
(`qwen3-vl:4b-instruct`, Ollama) are in `solution2/output/`:

| run | result |
|---|---|
| baseline PDF @ 200 DPI | trusted, 0 repair calls, 59 s |
| baseline PDF @ 100 DPI | VLM misread a printed total (35,869 -> 35,866); verifier flagged it; loop re-read the crop and restored exactly that cell: `repaired` |
| Calbee 2-page PDF @ 200 DPI | both pages trusted, 100% coverage (v1 false-alarms on page 1) |

## Evaluating on unseen data

`harness/unseen_scan.py` finds balance-sheet pages in digital PDFs (`資産合計`), builds **silver gold** for each from the PDF
text layer (`harness/silver.py`: digits straight from the text layer, indentation from label x-positions, no VLM involved),
and checks the verifier on it. A silver table the verifier also trusts is two independent paths agreeing (text-layer
digits and accounting identities); calibration: on the two pages with hand-built gold, silver matches it with 0 differences.
Silver gold gives ground truth for any digital PDF without labelling, so the VLM -> verify -> repair chain can be scored on
as many pages as can be downloaded.

First unseen-data pass (local pages that nothing had been tuned on), and what it exposed:

| finding | cause | fix |
|---|---|---|
| Fast Retailing p2 flagged on a clean page | sub-group heading with no subtotal (`その他利益剰余金`) read as a dropped total | only suspicious when the swallowing window also holds closed section totals |
| Nikon (IFRS) p2: `非流動負債合計` off by +2 | rounding convention defaulted to truncation on thin evidence | convention needs evidence; with none, both are allowed; one outlier no longer flips it |
| Nikon p2: `資本合計` double counts | inline subtotal `親会社の所有者に帰属する持分` has no 計 label | a row equal to the sum of the rows above is a subtotal; role-only totals no longer close sections |
| Nikon: false `date_unresolved` / "3 values for 2 periods" | a 増減 (change) column has no date, and the VLM left it out of `periods` | change columns need no date, are inferred from `current - prior = change`, and add a per-row identity check; the pipeline prompt now asks for every numeric column |
| Nikon p2 flagged although every number was right | VLM echoed `資本金` as a heading above the `資本金` line | an echoed heading is ignored unless a total is named after it |
| Nikon p1 never repaired | the VLM turned the line item `有形固定資産` into a heading; "too broken" fired on one large window | blank cells of a heading beside the window can be filled by the reads; "too broken" only on near-total failure |
| loop crash on the Yamato T-account; one outcome got *worse* | overlapping crops disagreed about a row; two-column pages were not detected | deletes need unanimity, any error in a window fails closed, T-account pages are detected and declined |

After those fixes Nikon p1/p2 (and Fast Retailing p1/p2) are **dev data**, not held-out: they are in the mutation suite
(`fixtures/silver/`, 7 documents, 28,280 corruptions, gate passes) and no longer prove generalisation. A clean held-out set
needs new pages: `solution2/edinet/` builds one from EDINET (PDF + XBRL-CSV gold); see its README.

Real VLM end to end on the Nikon IFRS statement (`qwen3-vl:4b-instruct`): page 1 `repaired` (three cells restored from
crop reads), page 2 `trusted`, every number matching the text-layer gold, 100% coverage.

**Where to get more data** (nothing here has been downloaded):
- **EDINET** (金融庁, official, free API key): annual / quarterly filings for every listed company in J-GAAP, IFRS and US GAAP.
  `documents.json?date=` lists a day's filings; `documents/{docID}?type=` returns the filing as PDF (2) or XBRL-derived data
  (CSV, 5), giving exact line-item values as independent ground truth. Check the API spec for the `type=1` meaning (sources disagree).
- **Company IR sites / TDnet**: 決算短信 (the format Calbee and Nikon came from). TDnet's public page keeps about 31 days; older
  ones are on company IR pages. TDnet bulk distribution is a paid JPX service.
- **EDINET-Bench / jfinqa** (Hugging Face): built from EDINET filings, but for LLM tasks (fraud, forecasting, table QA), not page
  images with cell labels, so not a drop-in evaluation set.

## How it works

1. **Strict parsing.** A cell is a number, a nil dash, blank, or `bad` (letters, `※1` glued on, `4.048`,
   misplaced commas, ...). Units and dates either resolve or raise an error finding: 万円/億円, 平成/昭和 are
   supported and nothing defaults to ×1000 or `2025-03-31`. Simplified-Chinese glyphs (流动资产) are mapped back
   and reported, so a drifting model cannot silently skip a check.
2. **Equations are discovered, not named.** The table is reduced bottom-up with a stack: a value-less header
   opens a section, a total row (label ends in 計) closes the innermost open section whose rows actually sum to
   it, a 純額 row after a contra run is gross + contra, a valued parent followed by deeper rows is their sum
   (the T-account layout), and a 負債純資産合計 row plus the A = L + NA tie close the statement.
   Evaluated components collapse into their total, so a bad cell fails only the equations it touches.
3. **The label drives meaning, not the VLM's role/indent tags**, which real output gets wrong (subtotals tagged
   `netted_item`, subtotals indented like items). Indentation is used only for the T-account layout.
4. **Rounding is inferred, not assumed.** Section sums are checked as truncation (`-(n-1) <= sum - total <= 0`)
   or round-to-nearest (`|diff| <= ceil(n/2)`) from the document's own diffs; net rows tolerate their three
   independently rounded figures; yen statements get zero slack. Balanced-only-through-slack cells are
   `rounding`, never `exact`.
5. **Per-cell verdicts and a trust rule.** `trusted` requires: nothing failed, nothing unparseable/unresolved,
   and >= 95% of numeric cells covered by a balanced equation. A check that cannot run leaves cells
   `unverified`; a section whose total row is missing marks its rows `structure_ambiguous`.

## What it catches, and what it cannot

Caught: wrong digits/dropped/swapped digits, comma/decimal slips, sign drops (incl. accumulated depreciation, which
v1 absorbed), dash <-> number, skipped/duplicated rows (including a skipped 純額 row), unparseable cells, missing
total rows, degenerate output, and coherent "plug" corruptions whenever both sides of the balance sheet are present.

**Cannot be caught by arithmetic** (the harness tracks these as `gap` classes): swapped adjacent rows, swapped
columns/period headers, a wrong unit that is still a valid unit, 債権/債務-style label flips, and plugs on an
assets-only page. These need a second signal (PDF text layer, dual read, label/section-type check).

## Limits

- The total-then-children layout depends on indentation and a parent's value matching its children.
- Sections are matched through value-less header rows. If a VLM drops its headers, each total falls back to
  the nearest run of rows directly above it that balances (weaker: it can no longer see a row dropped at the
  start of a section).
- A total is recognised by the 計 suffix, or by the VLM's subtotal role plus a balancing window; a total with
  neither is an ordinary row and its section stays unverified.
- Tuned against 3 gold documents; extend `harness` with more gold (EDINET/TDnet XBRL) before trusting it widely.
- Multi-page statements are verified page by page; cross-page ties are not implemented.

## The retry loop (`repair/`)

When the verifier does not trust a page, the loop tries to fix it without a human and without telling the VLM
what the answer should be:

```
verify -> failing windows -> crop the page around each window -> 3 fresh independent reads
       -> majority vote -> patches limited to the window -> keep a patch only if the verifier is strictly
          happier and nothing it vouched for is lost -> verify again (max 2 rounds)
```

| property | how it is enforced (each has a test in `repair/tests/test_loop.py`) |
|---|---|
| **No deficit/answer leaked** | the reader prompt is a function of the column count only: no labels, no numbers, no equation, no deficit |
| **Fresh** | every read is a single new chat turn with one image; the first answer is never in context |
| **Where to zoom** | the PDF text layer gives each row's line, so a window becomes a crop (used for layout only, never for values); if too few rows can be found, no crop is made |
| **Majority of reads** | 3 renders (300/400/250 DPI, different padding); a cell needs >= 2 agreeing reads, compared as parsed numbers |
| **Only failing-window cells** | a cell can change only if the verifier did not vouch for it and it is in the failing window; rows are inserted/deleted only inside or next to the window; totals and headings are never deleted |
| **Verifier-confirmed** | a patch is kept only if failed cells strictly drop, no new equation fails, coverage does not fall and no previously vouched cell is lost |
| **Fail-closed** | unresolved windows end as `needs_review`; a two-column (T-account) page is declined; a page where > 90% of cells fail is not worth reads; any error inside one window leaves the document unchanged |

Patches the loop can make: change a cell, blank a heading's stray numbers (and re-tag the row as a heading),
insert a row the VLM skipped, delete a duplicated line item.

**Results on real VLM output** (`harness/reports/repair_eval_ollama_4b.txt`): every output the verifier did not trust
(17: the real fixtures from the scorecard plus the two benchmark failures) was sent through the loop with
`qwen3-vl:4b-instruct` reading the crops, and the result compared with ground truth:

| fixed (all cells right) | fixed, but a label is off | improved | sent back untouched | **wrong number trusted** | **made worse** |
|---:|---:|---:|---:|---:|---:|
| 9 | 1 | 1 | 6 | **0** | **0** |

78 crop reads, 699 s in total. Repaired: the missed 4,553 and the fabricated 44,556 from the earlier benchmark, a 3-unit
error in a printed total, headings the 2B model had stuffed with a missing row's numbers, a line item the 4B model turned
into a heading, shifted-value cascades, and two 150-DPI baseline pages. The 6 sent back are the Yamato T-account (detected as
two-column and declined) and pages where the crops cannot be aligned. The exact-digit reader (text layer, no VLM) gives the
identical per-output result, so the outcome comes from the loop's logic and the verifier, not from lucky reads.

**Limits**
- Needs the source page as a digital PDF (row positions come from the text layer). Scans fail closed
  (`no_region`); a scan layout locator is future work.
- Two-column T-account pages are detected and declined (not cropped by column).
- A patch can only be as good as the verifier's ability to confirm it: an error inside rounding slack (a few
  units on a large section) cannot be confirmed or rejected.
- The one `fixed_label` case shows the remaining verifier gap: a correct number under a wrong label
  (here a section name used as a line-item label) is invisible to arithmetic.

## Results

**Synthetic corruption harness** (13,943 mutations over 3 gold documents; `harness/reports/v1.md`, `v2.md`):

| | v1 | v2 |
|---|---|---|
| gate | FAIL (18 violations) | PASS (0) |
| clean documents false-alarmed | Calbee | none |
| must-catch corruptions caught: Baseline / Calbee / Yamato | 99.4% / 39.1% / 0.0% | 100% / 100% / 100% |
| zero-row / blank / unparseable output vouched for | 12 of 12 | 0 |
| a footnote or space on a total label silently skips its check | 30 of 78 | 0 |

**Real VLM output** (`solution2/fixtures/real_vlm/`: Qwen3-VL 2B/4B-instruct, 4 pages, 200/150/100 DPI = 24 outputs,
`harness/reports/realcheck.txt`), scored against ground truth:

| | true_trust | true_flag | **SILENT_ERROR** | false_alarm | unparseable |
|---|---:|---:|---:|---:|---:|
| v1 | 4 | 3 | **13** | 3 | 1 |
| v2 | 7 | 16 | **0** | 0 | 1 |

(`unparseable` is a model that returned truncated JSON; not a verdict.) Notable: a single 3-unit error in a
printed total (35,866 vs 35,869) is caught by v2's one-sided truncation rule and missed by v1's symmetric tolerance.
The earlier hand-run failures reproduce with the same diagnoses: the missed 4,553 shows as a -4,556 deficit on
有形固定資産合計, and the fabricated 44,556 as +44,555 / +40,000.
