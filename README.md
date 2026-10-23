# JP_Parser

Parsing Japanese financial statements (balance sheets, 決算短信 / 有価証券報告書 pages) with a small local vision-language model,
and **not trusting what the model says unless the arithmetic of the table agrees with it**.

A VLM reads the page. A deterministic verifier then checks every number against the accounting identities of the table
(section totals, netting, the assets = liabilities + net assets tie), gives every cell a verdict, and sends only the
failing cells to an automated, location-only retry. Nothing a human types is ever fed back, and the verifier fails closed:
a page is "trusted" only when it balances.

```
PDF page
  -> render (PyMuPDF)
  -> first read (VLM, Ollama)       bands (default for 4B) | Qwen Markdown | Qwen HTML | solution1 JSON
  -> verify (no model)               per-cell verdicts: exact | rounding | unverified | failed | structure_ambiguous
  -> join split pages                a statement that continues on the next page is verified as one table
  -> retry loop (VLM on crops)       only the failing cells, fresh crop, 3 reads, majority; accepted only if the verifier is happier
  -> export                          json + xlsx + md, every cell with its status, timing per stage
```

## Where things stand

Measured on 57 balance-sheet pages from 31 real TDnet filings (the numbers are in [solution2/RESULTS.md](solution2/RESULTS.md)).
The most important result is the **sealed test**: 21 filings (38 pages) that no model had read while the system was built,
run once with configurations frozen beforehand.

| Configuration (full pipeline) | First-read rows exact | Trusted / left for review | Trusted pages with a wrong cell | Total per page |
| :-- | :-: | :-: | :-: | :-: |
| qwen3-vl 4B, **bands** | **98.9%** | 33 / 5 | **0** | 48 s |
| qwen3-vl 2B, whole-page Markdown | 96.5% | 25 / 13 | 0 | **17 s** |
| qwen3-vl 4B, whole-page JSON (previous default) | 97.9% | 35 / 3 | 1 | 58 s |

(8 GB GPU, Ollama, Q4 models, one request at a time. "Bands" = the page is cut into bands of about 12 text lines and each band
is read on its own.) The old solution1 validator would have vouched for 9 wrong pages after the JSON read; the v2 verifier
vouched for 1 and for none after bands.

What does not work yet, honestly:

* **Two-sided T-account pages** (assets on the left, liabilities on the right). The models can read them (4B Markdown gets 43 of 49
  rows of the Yamato page, 48 if one label-spelling quirk is ignored) but the converter and the retry loop treat a page as one list of rows, so
  these pages score low and are never repaired. See the T-account section of `solution2/RESULTS.md`.
* **Scans.** The band cutter holds up on noisy and skewed renders of digital pages, but nothing has been run on a real scan.
* **Label slips** that arithmetic cannot see (a misread kanji, receivable vs payable, a value shifted onto a neighbouring row).
  The shifted-row case was seen once and was trusted by the verifier. The planned fixes (PDF text-layer cross-check, a financial-term
  lexicon, a balance-sheet side check) are designed but not built.
* IFRS and the IFRS-specific layouts are a small part of the sample (7 sealed-test pages).
* The main CLI (`python -m solution2.pipeline`) still uses the JSON first read; bands, Markdown and HTML are selected in the evaluation command.

## Repository layout

| Path | What it is |
| :-- | :-- |
| `solution1/` | The original pipeline (VLM -> hard-coded validator -> feedback loop). **Frozen**; kept as the baseline. |
| `solution2/` | The current system: strict parsers, the hierarchy-driven verifier, the retry loop, first-read formats and bands, the test harness, and the data tooling. See `solution2/README.md`. |
| `solution2/RESULTS.md` | Every measurement (accuracy and latency), the sealed-test protocol and its result, and the corrections made along the way. **Start here.** |
| `solution2/data_sample/tdnet/` | The 40 TDnet filings (PDF), the sampling plan and the prepared gold pages, so the evaluation can be reproduced after TDnet drops the filings (its viewer keeps about 31 days). |
| `dataset/` | The original four benchmark companies (Yamato, Calbee, FastRetailing, Nikon) as PDFs. |
| `benchmark_results/` | Results of the first benchmark rounds (model comparison at the start of the project). |
| root `*.py` | The scripts of the first round (benchmarks, probes, downloaders). Kept for reference; not used by solution2 except `build_golden_anchors.py` (gold for the harness). |

## Setup

```bash
pip install -r requirements.txt
ollama pull qwen3-vl:4b-instruct          # the models that were evaluated
ollama pull qwen3-vl:2b-instruct
python -m pytest solution2 -q              # about 165 tests, no model needed
python -m solution2.harness.run --verifier v2 --gate    # the acceptance gate: 28,280 injected corruptions, must be 0 violations
```

## Run

```bash
# one PDF, end to end (JSON first read; verify -> join -> repair -> xlsx/md/json with per-cell status)
python -m solution2.pipeline statement.pdf --pages 1 2

# the evaluation on the prepared TDnet pages, choosing the first read
python -m solution2.tdnet eval --data-dir solution2/data_sample/tdnet --split dev --include-split \
       --model qwen3-vl:4b-instruct --format bands --dpi 300 --run-name my_run
#   --format json | html | markdown | bands | bands_md      --no-repair   --no-join   --num-ctx N
#   --split dev|test  (the test split is sealed: see the protocol in solution2/RESULTS.md before running it)

# the original benchmark documents, including scan-like and skewed renders
python -m solution2.benchmark_docs --model qwen3-vl:4b-instruct --configs json markdown bands --degrade scan skew
```

Getting new data (nothing is downloaded until you approve the exact list):

```bash
python -m solution2.tdnet plan --n 40 --seed 7 --min-ifrs 8     # lists company, document id, PDF URL and size; writes plan.json
python -m solution2.tdnet download --plan solution2/data/tdnet/plan.json --approve <token printed by plan>
python -m solution2.tdnet prepare                                # text-layer gold for the balance-sheet pages
```

(An EDINET downloader with XBRL ground truth exists in `solution2/edinet/` but has not been run: it needs an API key.)

## Principles the code follows

* **Verify, don't trust.** Equations are discovered from the table rather than tied to account names; every cell gets a verdict.
* **Fail closed.** A page is trusted only if the verifier balances it; an unparsable cell, a missing total or a structure it cannot explain means "review".
* **No leaked answers.** The retry loop is never told the size of a discrepancy or what the value should be; it only gets the location and a fresh crop,
  and a patch is accepted only if the verifier ends up strictly happier and nothing it already vouched for is lost.
* **Ground truth without a model.** The gold pages come from the PDF text layer and are used only where the verifier independently agrees with them.
* **Sealed evaluation.** Configurations are chosen on a dev split by a written rule; the test split is run once, after the protocol is recorded.

## Data and licensing notes

TDnet 決算短信 are public disclosures; the downloader identifies itself honestly and rate-limits. Downloaded and generated files under `solution2/data/`
are git-ignored; only the 40 source PDFs, the plan and the gold pages are tracked, in `solution2/data_sample/`. No credentials are stored in the repository.
