# TDnet source (no API key)

Public 決算短信 PDFs from TDnet's disclosure viewer, with text-layer **silver gold** as ground truth. Same gate as the EDINET
source: nothing is downloaded until you have seen the list and approved it.

```bash
python -m solution2.tdnet plan --from 2026-09-21 --to 2026-10-20 --n 40 --seed 7 --min-ifrs 8   # LIST ONLY
python -m solution2.tdnet download --plan solution2/data/tdnet/plan.json --approve <token>     # exactly that list
python -m solution2.tdnet prepare [--sample 5 --split dev --ifrs 2]   # no network; incremental, so the rest can be done later: find balance-sheet pages, build silver gold, gold vs ambiguous
python -m solution2.tdnet eval --split dev             # VLM -> verify -> repair vs the gold (local Ollama)
python -m solution2.tdnet eval --split test            # only when the code is frozen
```

`plan` prints company, docID, accounting standard, date, **PDF size and URL** for every filing, the total size, and an
approval token; it writes `solution2/data/tdnet/plan.json`. A PDF's URL is always `https://www.release.tdnet.info/inbs/<docID>.pdf`.

## What the plan filters (checked against the live site)

| filtered out | how |
|---|---|
| anything that is not a 決算短信 | title |
| amendments | 訂正 / 修正 in the title |
| REITs, infrastructure funds, ETFs | REIT / インフラ / 投資法人 / ファンド / ETF in title or name, names starting `R-`; and any title naming no accounting standard (`〔日本基準〕`, `〔IFRS〕`, `〔米国基準〕`), which is what ETFs and ETNs look like |
| companies already used while developing the verifier | codes 2229, 7731, 7886, 9983 (`--exclude-code` adds more) |

One filing per company, seeded. `--min-ifrs K` puts at least K IFRS filings in the sample (the window is ~95% Japanese GAAP).
The dev/test split is a hash of the docID (same function as EDINET), so it cannot drift.

## Ground truth: gold vs ambiguous

For each balance-sheet page (a page with 資産合計 / 負債合計 / 資本合計 that, or whose predecessor, is titled
貸借対照表 / 財政状態計算書) `prepare` reads the table from the PDF text layer (`harness/silver.py`), then asks the verifier:

- **gold**: silver built and the verifier trusts it, i.e. text-layer digits and accounting identities agree. Used for scoring.
- **gold_split**: the verifier does not trust the page alone but does trust it **joined with the adjacent page**: the
  statement continues across the page break (e.g. p26 ends before 固定資産合計, p27 carries it). Not an error in the page; a
  limit of verifying page by page. Scored only with `eval --include-split`.
- **ambiguous**: silver built but the verifier flags it, and no neighbour explains it: either a verifier false alarm or a text-layer error. **Listed, not
  scored** unless `--include-ambiguous`. This split has a selection bias (a verifier that decides which pages count as clean can
  hide its own false alarms), so the ambiguous list must be read by a person, not ignored.
- skipped, with the reason, when a page cannot be read cleanly (no dated column header, a layout the extractor will not guess).

## Politeness and honesty

Descriptive `User-Agent` (the server refuses curl's default agent on HEAD but answers an honest one), one request per
1.5 s, back-off on 429/5xx. Sizes come from HEAD requests, with a one-byte range request as fallback; no body is read.
The viewer keeps only about the last 31 days. PDFs and prepared pages live in `solution2/data/` (git-ignored).
