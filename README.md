# JP_Parser

Parsing Japanese financial statements (balance sheets from 決算短信 and similar filings) with a small local vision-language model
and a deterministic verification loop.

A VLM reads the page. A verifier then checks every number against the accounting identities of the table (section totals, netting,
assets = liabilities + net assets), gives every cell a verdict, and sends only the failing cells to an automated, location-only retry.
Nothing a human types is ever fed back, and the verifier fails closed: a page is "trusted" only when it balances.

```
PDF page
  -> render (PyMuPDF)
  -> first read (VLM via Ollama)    bands | Qwen Markdown | Qwen HTML | solution1 JSON
  -> verify (no model)               per-cell verdict: exact | rounding | unverified | failed | structure_ambiguous
  -> join split pages                a statement that continues on the next page is verified as one table
  -> retry loop (VLM on crops)       only the failing cells; fresh crop, 3 reads, majority; kept only if the verifier is happier
  -> export                          json + xlsx + md, every cell with its status, timing per stage
```

## Results in one table

Sealed test: 21 filings (38 pages) that no model had read while the system was built, run once with configurations frozen beforehand,
full pipeline. 8 GB GPU, Ollama, Q4 models.

| Configuration | First-read rows exact | Pages trusted / left for review | Trusted pages with a wrong cell | Time per page |
| :-- | :-: | :-: | :-: | :-: |
| qwen3-vl 4B, **bands** (page cut into bands of ~12 text lines) | **98.9%** | 33 / 5 | **0** | 48 s |
| qwen3-vl 2B, whole-page Markdown | 96.5% | 25 / 13 | 0 | **17 s** |
| qwen3-vl 4B, whole-page JSON (the earlier default) | 97.9% | 35 / 3 | 1 | 58 s |

Ten filings downloaded afterwards gave the same picture (4B bands 98.6%, 2B Markdown 95.9%, no trusted wrong cell). The old solution1 validator
would have vouched for 9 wrong pages after the JSON read; the verifier here vouched for 1, and for none after bands.
A check against the PDF's own text layer, on digital PDFs, flagged 315 of 315 wrong rows (0.05% false alarms): an evaluation tool, not yet a pipeline stage.
**Every number, with its caveats and the corrections made along the way, is in [solution2/RESULTS.md](solution2/RESULTS.md); start with its summary.**

## What is not solved

* **Two-sided (T-account) pages**: the models read them, but the converter, the band cutter and the retry loop work on one list of rows.
* **Real scans**: only digital PDFs and synthetic scan-like renders were tested.
* **Label slips that arithmetic cannot see** (a misread kanji, receivable vs payable, a value moved to a neighbouring row): the text-layer check covers digital PDFs only;
  a financial-term lexicon and a balance-sheet side check are designed but not built.
* **Variety of data**: 50 TDnet filings from two main PDF generators, mostly J-GAAP. No other document types or producers yet.
* The main CLI (`python -m solution2.pipeline`) still uses the JSON first read; bands, Markdown and HTML are chosen in the evaluation command.

## Repository map

| Path | What it is |
| :-- | :-- |
| `solution2/` | **The current system**: strict parsers, the verifier, the retry loop, first-read formats and bands, the PDF text-layer check, the test harness, the data tooling. Read `solution2/README.md`. |
| `solution2/RESULTS.md` | Every measurement (accuracy, latency), the sealed-test protocol and its result, the corrections. |
| `solution2/data_sample/` | The 50 TDnet filings (PDF, ~24 MB), the two sampling plans and the prepared gold pages, kept so runs can be reproduced after TDnet drops the filings (it keeps ~31 days). |
| `solution1/` | The original pipeline (VLM -> hard-coded validator -> feedback loop). Frozen; the baseline everything is compared with. |
| `dataset/` | The four original benchmark companies (Yamato, Calbee, FastRetailing, Nikon) as PDFs. |
| `benchmark_results/` | Output of the first benchmark rounds (model comparison at the start). |
| `legacy/` | The first-round scripts (benchmarks, probes, downloaders), kept for reference. See `legacy/README.md`. |
| `docs/prototype1.md` | The first prototype's specification (historical). |
| `references/` | The J-GAAP balance-sheet taxonomy used by solution1. |
| `balance_sheet_after_correction.pdf` | The original sample page (used by the baseline code and tests). |

## Setup and quick start

```bash
pip install -r requirements.txt
ollama pull qwen3-vl:4b-instruct                           # the models that were evaluated
ollama pull qwen3-vl:2b-instruct

python -m pytest -q                                       # 171 tests, no model needed (pytest.ini limits it to solution2)
python -m solution2.harness.run --verifier v2 --gate        # the acceptance gate: 28,280 injected corruptions, must be 0 violations

python -m solution2.pipeline statement.pdf --pages 1 2     # one PDF end to end (JSON first read) -> solution2/output/<name>/
```

Evaluate a first-read configuration on the prepared TDnet pages (needs Ollama):

```bash
python -m solution2.tdnet eval --data-dir solution2/data_sample/tdnet --split dev --include-split \
       --model qwen3-vl:4b-instruct --format bands --dpi 300 --run-name my_run
# --format json | html | markdown | bands | bands_md     --no-repair   --no-join   --docs <docIDs>   --num-ctx N
# the test split is sealed: read the protocol in solution2/RESULTS.md (section 1k) before running --split test
```

Score the PDF text-layer check on saved answers (no model): `python -m solution2.witness_eval --splits A B T C D E`.
The original benchmark documents, clean and degraded: `python -m solution2.benchmark_docs --model qwen3-vl:4b-instruct --configs json bands --degrade scan skew`.

Getting more data (nothing is downloaded until you approve the exact list; no API key needed for TDnet):

```bash
python -m solution2.tdnet plan --n 10 --seed 31 --min-ifrs 3 --exclude-code <codes already used>   # lists company, docID, URL, size; writes plan.json
python -m solution2.tdnet download --plan <plan.json> --approve <token printed by plan>
python -m solution2.tdnet prepare --docs <docIDs>                                                  # text-layer gold for the balance-sheet pages
```

(An EDINET downloader with XBRL ground truth exists in `solution2/edinet/` but has never been run: it needs an API key.)

## Principles

* **Verify, don't trust.** Equations are discovered from the table, not tied to account names; every cell gets a verdict.
* **Fail closed.** An unparsable cell, a missing total or a structure the verifier cannot explain means "review", never "ok".
* **No leaked answers.** The retry loop is never told the size of a discrepancy or what the value should be, only where to look; a patch is kept only if the verifier ends up strictly happier.
* **Ground truth without a model.** Gold pages come from the PDF text layer, used only where the verifier independently agrees.
* **Sealed evaluation.** Configurations are chosen on dev filings by a written rule; the test filings are run once afterwards.

## Data notes

TDnet 決算短信 are public disclosures; the downloader identifies itself honestly and rate-limits. Everything under `solution2/data/` (saved model answers, rendered images, logs) is git-ignored
scratch; only `solution2/data_sample/` is tracked. No credentials are stored in the repository.
