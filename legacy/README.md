# legacy

The scripts of the first rounds of the project (before `solution2/`): model benchmarks, probes, downloaders and one-off tests. Kept for reference only; nothing in `solution1/` or `solution2/` imports them.
Their results are in `benchmark_results/`. Run them from the repository root as modules, for example `python -m legacy.run_benchmark` (they use paths relative to the root).

| Group | Scripts |
| :-- | :-- |
| Benchmarks | `run_benchmark.py` (the first benchmark: several small VLMs, pass 1 and a pass 2 with a Python feedback loop), `run_full_benchmark_matrix.py` (GLM-OCR vs 2B vs 4B), `benchmark_vlms.py`, `benchmark_comparator.py` (HTML table parsing and comparison), `run_pure_autonomous_benchmark.py`, `check_pass2_accuracy.py` |
| Single experiments | `test_200dpi.py`, `test_2b_200dpi.py`, `test_rerun_initial_4b.py`, `test_qwen_feedback.py`, `test_autonomous_feedback.py`, `test_calbee_glm.py`, `test_calbee_p2.py`, `test_yamato_glm.py`, `test_glm_ocr.py`, `test_glm_json.py`, `test_qwen2b_calbee.py`, `validate_glm_ocr.py` |
| Data collection | `download_samples.py`, `download_targets.py`, `download_nikon.py`, `fetch_yamato.py`, `parse_links.py`, `prepare_dataset.py`, `probe_candidates.py`, `probe_companies.py`, `probe_new_models.py` |

Known broken: `benchmark_dpi_and_instruct.py` has a syntax error on its import line 20 (`from ... import ... if ... else None`), so it cannot be imported. It is kept as it was.
(`build_golden_anchors.py`, the hand-built gold for Calbee and Yamato, is used by the harness and now lives in `solution2/harness/golden_anchors.py`.)
