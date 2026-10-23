# solution2

The current system for reading Japanese balance sheets with a VLM and not trusting it unless the table balances.
`solution1/` is untouched (it is the baseline). Measurements: [RESULTS.md](RESULTS.md). Overview and quick start: the repository `README.md`.

## Layout

```
solution2/
  pipeline.py        entry point: render -> first read -> verify -> join split pages -> retry loop -> export
  extract.py         page rendering and the solution1 JSON first read (solution1's prompt, imported unchanged)
  bands.py           first read in bands: the page is cut at the white gaps between text lines, each band read on its own, rows stitched
  formats.py         first read as Qwen HTML / Markdown (LaTeX) tables, converted to the Stage-1 tree; structure-blind row scoring
  witness.py         the PDF text layer as a witness for the model's rows (digital PDFs); witness_eval.py scores it on saved answers
  benchmark_docs.py  the original benchmark documents, clean and degraded (scan-like, skewed) renders
  export.py          per-cell-status exporters (json / xlsx / md)
  verify/            strict parsers, the hierarchy-driven verifier (per-cell verdicts), page joining
  repair/            the automated, location-only retry loop (layout from the text layer, readers, consensus)
  harness/           mutation suite and gate, real-output scoring, silver gold from the text layer, hand-built gold (see harness/README.md)
  tdnet/             keyless TDnet source: plan -> approve -> download -> prepare -> eval (see tdnet/README.md)
  edinet/            EDINET downloader + XBRL-CSV gold + the shared evaluation runner (see edinet/README.md); never run with a key
  fixtures/          real Qwen3-VL output and silver gold pages used by tests and the harness
  data_sample/       the 50 TDnet filings (PDF), the sampling plans and the prepared gold pages (tracked)
  data/, output/     git-ignored scratch: saved model answers, images, logs, pipeline output
  tests/             pipeline, bands, formats and witness tests (verify, repair, tdnet, edinet and harness tests sit next to their code)
```

## Commands

```bash
python -m solution2.pipeline statement.pdf [--pages 1 2] [--model qwen3-vl:4b-instruct] [--dpi 200] [--no-repair] [--no-join]
python -m solution2.verify stage1.json -v                  # verify a Stage-1 table; exit 1 if not trusted
python -m solution2.repair stage1.json --pdf page.pdf      # verify, then repair what is not trusted
python -m solution2.harness.run --verifier v2 --gate       # the acceptance gate (mutation suite)
python -m solution2.tdnet eval --format bands ...          # the same chain with another first read: json | html | markdown | bands | bands_md
python -m solution2.witness_eval --splits A B T C D E      # PDF text-layer check on the saved answers (no model)
python -m pytest solution2 -q
```

```python
from solution2.verify import parse_stage1, verify_document
raw, notes = parse_stage1(open("page.json", encoding="utf-8").read())
v = verify_document(raw)
v.trusted, v.coverage            # document verdict
v.cell_status[(row_index, col)]  # exact | rounding | unverified | failed | structure_ambiguous
v.errors, v.equations            # what failed, and every equation with its per-column diff
```

## The pipeline

Per page it writes to `solution2/output/<pdf name>/`: `page<N>_vlm_raw.json` (what the model returned), `page<N>_stage1.json` (the table the verifier saw, after repair),
and `page<N>.json / .xlsx / .md`: every cell with its status, repaired cells with their original reading, the equations checked, findings, and the windows left for review, with timing per stage.
A page that is not trusted is still exported (with a banner), never dropped.

Statuses: `trusted` | `trusted_joined` (verified together with the adjacent page of the same statement) | `repaired` | `needs_review` | `no_table` | `extraction_failed`.
Exit code 0 only if every page with a table is trusted, joined or repaired. A test checks that running the pipeline leaves every file under `solution1/` unchanged.

## How the verifier works (`verify/`)

1. **Strict parsing.** A cell is a number, a nil dash, blank, or `bad` (letters, a glued-on footnote, decimals, misplaced commas). A footnote reference separated by a space (`※1 110,588`) is ignored.
   Units and dates resolve or raise an error finding (万円/億円, 平成/昭和 supported); nothing defaults to ×1000 or to a date. Simplified-Chinese glyphs are mapped back and reported.
2. **Equations are discovered, not named.** The table is reduced bottom-up with a stack: a value-less header opens a section, a total row (label ends in 計) closes the innermost open section whose rows sum to it,
   a 純額 row after a contra run is gross + contra, a valued parent followed by deeper rows is their sum (the T-account flattened layout), and a 負債純資産合計 row plus the assets = liabilities + net assets tie close the statement.
   A bad cell fails only the equations it touches.
3. **The label drives meaning, not the model's role and indent tags**, which real output gets wrong.
4. **Rounding is inferred**, not assumed: truncation or round-to-nearest from the document's own diffs; cells that balance only through slack are `rounding`, never `exact`.
5. **Per-cell verdicts and a trust rule.** `trusted` requires nothing failed, nothing unparseable, and at least 95% of numeric cells covered by a balanced equation. A section whose total row is missing marks its rows `structure_ambiguous`.
6. **Split statements.** A statement that continues on the next page cannot balance on either page; `verify/join.py` joins two pages with the same unit and period columns and gives each page its share of the joint verdict.

**What arithmetic catches:** wrong, dropped, swapped digits, comma and decimal slips, sign drops, dash vs number, skipped or duplicated rows, unparseable cells, missing totals, degenerate output, coherent "plug" corruptions when both sides of the balance sheet are present.
**What it cannot catch** (the harness tracks these as `gap` classes): swapped adjacent rows, swapped columns or period headers, a wrong but valid unit, 債権/債務-style label flips, and values moved between rows of one block. For digital PDFs `witness.py` covers most of these.

## The retry loop (`repair/`)

```
verify -> failing windows -> crop the page around each window -> 3 fresh independent reads
       -> majority vote -> patches limited to the window -> keep a patch only if the verifier is strictly
          happier and nothing it vouched for is lost -> verify again (max 2 rounds)
```

| property | how it is enforced (tests in `repair/tests/test_loop.py`) |
|---|---|
| No answer leaked | the reader prompt depends only on the column count: no labels, numbers, equation or deficit |
| Fresh | every read is a new chat turn with one image; the first answer is never in context |
| Where to zoom | the PDF text layer gives each row's line, so a window becomes a crop (layout only, never values); if too few rows are found, no crop |
| Majority of reads | 3 renders (300/400/250 DPI, different padding); a cell needs at least 2 agreeing reads, compared as parsed numbers |
| Only failing-window cells | a cell can change only if the verifier did not vouch for it and it is in the failing window |
| Verifier-confirmed | kept only if failed cells strictly drop, no new equation fails, coverage does not fall, no vouched cell is lost |
| Fail closed | unresolved windows end as `needs_review`; two-column (T-account) pages are declined; a page where over 90% of cells fail is not worth reads |

Limits: it needs the page as a digital PDF (row positions come from the text layer); scans fail closed. An error inside rounding slack cannot be confirmed or rejected.

## First reads and the text-layer witness

* **Bands** (`bands.py`): no text layer or table detector needed. The page image is scanned for text lines (noise and skew handled), grouped into bands of about 12 lines with 2 shared, one short call reads the unit and column headings,
  each band is read with the repair loop's row prompt, and repeated overlap rows are dropped. A band the model fails on is read in halves.
* **Formats** (`formats.py`): Qwen's own HTML and Markdown (LaTeX tabular) table output, converted to the same Stage-1 tree. Two-sided tables are not converted yet.
* **Witness** (`witness.py`): matches each model row to its text line in a native digital PDF and compares numbers and label characters; it abstains on OCR-looking text layers, glyphs without a unicode mapping,
  pages with too few numbers, and two-sided pages. Nothing in it changes a table except the optional `snap_labels`, which is not wired into the pipeline.

Which one to use, and why, is in RESULTS.md (summary at the top).

## Data and evaluation tooling

* `tdnet/`: public 決算短信 PDFs (no key). `plan` lists company, docID, URL and size and writes an approval token; `download` fetches exactly that list; `prepare` builds **silver gold** from the text layer;
  `eval` runs a first read + the pipeline against the gold; `snapshot` copies filings and gold into `data_sample/`.
* Gold categories: **gold** (text layer and verifier agree), **gold_split** (trusted only joined with the next page), **ambiguous** (the verifier flags even the gold: listed, scored only on request).
* `edinet/`: EDINET downloader with XBRL-CSV ground truth and the evaluation runner both sources share. Not exercised with a real key.
* `harness/`: the mutation suite (28,280 corruptions over 7 documents) that gates the verifier; `realcheck` and `repair_eval` score it on real model output.

## Limits

* Sections are matched through value-less headers; if the model drops its headers, each total falls back to the nearest balancing run of rows above it (weaker).
* A total is recognised by the 計 suffix or by a subtotal role plus a balancing window; a total with neither is an ordinary row.
* Two-sided pages are not supported end to end; scans have not been run; the sample is small and from two main PDF generators (see RESULTS.md, Limits).
