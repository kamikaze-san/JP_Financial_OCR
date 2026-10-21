# EDINET downloader + XBRL-CSV gold

Builds a held-out evaluation set from real EDINET filings: the filing PDF (what the VLM reads) and the XBRL-derived CSV
(exact yen values, independent of the PDF and of any model). Nothing is downloaded without you seeing the list first.

## Workflow

```bash
# 1. put the API key in a file OUTSIDE the repo (never on the command line, never in chat)
#    PowerShell:  Set-Content -NoNewline $HOME\.edinet_key "<your key>"
python -m solution2.edinet --key-file $HOME/.edinet_key plan --dates 2025-06-24 2025-06-25 2025-06-26 2025-06-27 --n 40 --seed 7
```
`plan` makes one `documents.json` call per date and a header-only size probe per file. It prints, for every filing:
split (dev/test), securities code, company, **docID**, document type, period end, **PDF size**, **CSV size**, plus the total
and an **approval token**. It writes `solution2/data/edinet/plan.json` and says **NOTHING HAS BEEN DOWNLOADED**.

```bash
# 2. only after you have read the list
python -m solution2.edinet --key-file $HOME/.edinet_key download --plan solution2/data/edinet/plan.json --approve <token>
# 3. no network from here on
python -m solution2.edinet prepare              # parse the CSVs, find balance-sheet pages, keep pages XBRL confirms
python -m solution2.edinet eval --split dev     # VLM -> verify -> repair vs the confirmed gold (local Ollama)
python -m solution2.edinet eval --split test    # only when the code is frozen
```

## Safety properties (each has a test in `tests/test_edinet.py`)

| property | how |
|---|---|
| nothing is fetched until you approve the exact list | `download` refuses unless `--approve` equals the token of the plan file; any change to the list changes the token |
| the key never leaks | read from the environment or `--key-file` only (not an argument); scrubbed from every error and from `repr`; never written to the plan, ledger or logs |
| sizes without downloading | `probe` reads response headers only and closes the connection; a server that sends no `Content-Length` shows `?` |
| size cap | the plan is refused above `--max-mb` (default 500) |
| polite | one request per `--interval` seconds (default 2.5), back-off on 429/5xx |
| a JSON error is never saved as a PDF | the first bytes are checked (`%PDF` / `PK`); a failed file leaves nothing behind and is reported, not fatal |
| no zip tricks | only `.csv` members are extracted, flattened to file names, with entry-count and size limits |
| idempotent | files already on disk are never fetched again; `downloads.json` records sizes and sha256 |
| the test set cannot drift | dev/test is a hash of the docID; `eval` writes the code fingerprint next to every result |

## How a page becomes ground truth

For each balance-sheet page (`資産合計`) of a downloaded PDF: build the text-layer silver table
(`harness/silver.py`), then check each of its numbers against the XBRL facts of the matching period
(newest column <-> `Current…Instant`, older <-> `Prior1YearInstant`; value x unit within one unit of a fact, which covers
both truncation and rounding). A page is kept as gold when >= 98% of its numbers are explained, i.e. **text-layer digits and
XBRL agree**. Where the verifier also flags such a page, that is a verifier false alarm on independently confirmed clean
data, and `prepare` lists them.

## What is NOT verified yet (check on the first real download)

- **The CSV layout.** The reader assumes the viewing guide's columns (`要素ID, 項目名, コンテキストID, 相対年度, 連結・個別,
  期間・時点, ユニットID, 単位, 値`) and sniffs UTF-16/UTF-8/Shift-JIS and tab/comma, because that came from secondary sources.
  Run `python -m solution2.edinet inspect solution2/data/edinet/raw/<docID>/csv/<file>.csv` and look at what it understood.
- **The API base URL** (`https://api.edinet-fsa.go.jp/api/v2`) and the meaning of `type=1` (sources disagree). Only `type=2`
  (PDF) and `type=5` (CSV) are used.
- **File sizes**: whether EDINET sends `Content-Length` for these downloads. If not, the list shows `?` and the cap cannot be checked.
- US-GAAP filers publish no balance-sheet facts in the CSV, so they are skipped (`prepare` says so).
- Only pages with a dated column header can be turned into silver; other layouts are reported as skipped, not guessed.
