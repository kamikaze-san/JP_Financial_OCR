# Verification test harness v2

Measures a deterministic verifier by corrupting known-good Stage-1 (VLM output) documents the way
VLMs actually fail, and checking what the verifier does. `solution1/` is not modified; it is imported
as the baseline (`--verifier v1`).

```bash
python -m solution2.harness.run --selftest                 # prove the harness itself is sound
python -m solution2.harness.run --verifier v1              # baseline report -> reports/v1.md / v1.json
python -m solution2.harness.run --verifier pkg.mod:Class --gate   # your new verifier; exit 1 on violations
python -m pytest solution2/harness/tests -q
```

Useful flags: `--docs baseline,calbee`, `--classes digit_sub,row_omit`, `--stride 5` (faster),
`--material-delta 100`, `--extra gold1.json gold2.json` (more clean `RawDocumentTree` files, e.g. from XBRL).

## Plugging in a verifier

Implement `Verifier.verify(raw: RawDocumentTree) -> Verdict` (see `contract.py`). Only `flagged` is
required. Richer fields make the scoring sharper:

| field | use |
|---|---|
| `flagged` | verifier does not vouch for the document (v1: `not is_valid`) |
| `signature` | what it found wrong; a mutation only counts as caught if this differs from the clean document's |
| `evaluated_equations` / `coverage` | lets the harness see a verifier quietly checking less (fail-open) |
| `cell_status` | per-cell `exact / rounding / unverified / failed / structure_ambiguous / conflict`; corrupted cells are then scored directly |

## What each mutation class expects (`kind`)

| kind | meaning | gated |
|---|---|:---:|
| `detect` | corruption >= material delta; arithmetic must catch it (silent or `rounding`-labelled = bad) | yes |
| `no_degrade` | surface noise (footnote on a total, Chinese glyphs, dropped total row) must not silently shrink what is checked | yes |
| `flag` | degenerate output (zero rows, nothing parseable) must never be vouched for | yes |
| `invariant` | equivalent spelling (full-width digits, ▲/−/-, 令和, nil variants) and VLM indent/role noise; verdict must not change | yes |
| `detect_small` | same classes below the material delta (rounding zone) | no |
| `gap` | arithmetic cannot see it (row/column swaps, unit, 債権/債務 flips, coherent plug); needs a second signal | no |

The gate also fails on any **false alarm on a clean document** and on any verifier crash.
`gap` rows are the roadmap: they should rise as second signals (text layer, dual read, label/section
checks, A = L + NA tie) are added.

## Outcomes

`caught` flagged and findings changed | `not_vouched` unverified/failed at cell level | `weak` labelled
`rounding` | `silent` vouched despite corruption | `silent_degraded` checks less, still vouches |
`surfaced` coverage loss reported at cell level | `benign` noise absorbed | `crash`.

## Verifiers and real-output tools

| command | what it does |
|---|---|
| `--verifier v1` | the existing solution1 validator (baseline) |
| `--verifier v2` | `solution2.verify` (hierarchy-driven reducer, per-cell verdicts) |
| `python -m solution2.harness.collect_real --models qwen3-vl:4b-instruct [--dpi 150]` | collect real Stage-1 output from local Ollama VLMs into `solution2/fixtures/real_vlm/` |
| `python -m solution2.harness.repair_eval --reader text\|ollama [--strict]` | run the retry loop on every untrusted real output and compare with ground truth: `fixed / fixed_label / SILENT_WRONG / improved / unchanged / worse` (the last two bad ones must be 0) |
| `python -m solution2.harness.unseen_scan [pdf ...]` | find balance-sheet pages in digital PDFs, build silver gold from the text layer (`silver.py`), check the verifier on each |
| `python -m solution2.harness.realcheck [--strict]` | score verifiers against ground truth on that real output: `true_trust / true_flag / SILENT_ERROR / false_alarm` |

Synthetic mutations only test what we thought of. `realcheck` tests what the VLM actually does, and it
found two bugs the mutations missed (VLM indentation, and VLMs tagging subtotals as `netted_item`);
both are now mutation classes (`structure_noise`) so they stay fixed.

## Changes made to the harness while building the v2 verifier (and why)

Each is a correction of the *measurement*, not a relaxation for the verifier; v1 is re-scored with the same rules.

- `U+2212`/hyphen-minus as a negative sign moved from `value_unparseable` (corruption) to `invariant_forms`:
  an unambiguous minus in another glyph is the same value. v1 turns it into 0, so v1 now (correctly) fails it.
- The oracle no longer judges equivalence through v1's parser (which mis-reads `−174` as 0).
- "Quietly checks less" is measured by **cell coverage** when a verifier reports it. Equation counts fall whenever a
  row is legitimately removed even though every remaining cell is still verified. v1 reports no coverage, so it
  is still judged by equation count.
- `numeric_cells` includes zero-valued cells for digit substitution (a printed `0` misread as `8`).
- `heading_echo` added after real Qwen3-VL-4B output repeated a line item as a heading above itself.
- `structure_noise` added after real Qwen output showed indentation/role noise the idealised gold never has.

## Self-test (what keeps the harness honest)

No mutation may be a no-op; `oracle` (knows the truth) must score 100%; `always_pass` must catch nothing and
accept every equivalent spelling; `always_flag` must false-alarm on every clean document. The self-test
already caught three no-op "equivalent spelling" mutations while this was being written.

## Known limits

- The clean corpus is 7 documents: baseline, Calbee and Yamato (hand-built gold in `golden_anchors.py`) and four silver pages from the text layer (`fixtures/silver/`). Add more gold via `--extra`.
- `plug_subtotals` needs a children-then-total layout, so it does not apply to the Yamato T-account.
- Detection on a document that already false-alarms (v1 on Calbee) is an upper bound.
- Mutations are applied to Stage-1 output; pixel-level failures (blur, skew) are out of scope here.
- The "visually confusable digit pairs" set is an assumption, used only for a tag.
