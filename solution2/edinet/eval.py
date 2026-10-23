"""
Score the full chain on prepared pages: VLM -> verify -> (join split pages) -> repair, against text-layer / XBRL ground truth,
and time every stage.

Per page, from ONE VLM reading, three verifier views on the raw output:
    v1        the original solution1 validator, page by page
    v2_alone  the v2 verifier, page by page
    v2        the pipeline's view: v2 after joining a page with the adjacent page of the same statement
each classified as   true_trust / true_flag / SILENT_ERROR (a number is wrong and it vouches) / label_gap / false_alarm
and, where the pipeline flagged the page, the retry loop:
    fixed / fixed_label / SILENT_WRONG / improved / unchanged / worse

Latency (seconds, steady state after a model warm-up whose load time is reported separately): render, VLM extract, verify
(v1 and v2 in milliseconds), join, repair (and per VLM call), export, total.

Tune on `--split dev`; report `--split test`. The report carries a fingerprint of the code that produced it.
"""
import datetime
import hashlib
import statistics
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from solution1.models import RawDocumentTree
from solution2.extract import ollama_extractor
from solution2.formats import compare_numeric_rows, format_extractor
from solution2.harness.adapters import V1Verifier
from solution2.harness.realcheck import numbers_match, wrong_cells
from solution2.pipeline import OK_STATUSES, process_pages
from solution2.repair.reader import OllamaReader
from solution2.verify import parse_stage1, verify_document

SOLUTION2 = Path(__file__).resolve().parents[1]


def code_fingerprint() -> str:
    """Hash of the verifier / repair / pipeline source, so a test-split number is tied to the code that made it."""
    h = hashlib.sha256()
    for sub in ("verify", "repair", "extract.py", "pipeline.py", "export.py"):
        path = SOLUTION2 / sub
        for p in ([path] if path.is_file() else sorted(path.rglob("*.py"))):
            if "tests" not in p.parts:
                h.update(p.read_bytes())
    return h.hexdigest()[:12]


def classify_detection(wrong: bool, flagged: bool, numbers_ok: bool) -> str:
    if not wrong:
        return "false_alarm" if flagged else "true_trust"
    if flagged:
        return "true_flag"
    return "label_gap" if numbers_ok else "SILENT_ERROR"


def classify_repair(before: int, after: int, trusted: bool, numbers_ok: bool) -> str:
    if trusted and after == 0:
        return "fixed"
    if trusted:
        return "fixed_label" if numbers_ok else "SILENT_WRONG"
    if after > before:
        return "worse"
    return "improved" if after < before else "unchanged"


def latency_summary(values: Sequence[float]) -> Dict[str, float]:
    v = sorted(values)
    if not v:
        return {"n": 0}
    return {"n": len(v), "mean": round(statistics.mean(v), 2), "median": round(statistics.median(v), 2),
            "p90": round(v[min(len(v) - 1, int(0.9 * len(v)))], 2), "max": round(v[-1], 2)}


def warm_up(model: str) -> Optional[float]:
    """Load the model once so per-page latencies are steady-state; returns the load time (cold start)."""
    t0 = time.perf_counter()
    try:
        import ollama
        ollama.generate(model=model, prompt="", keep_alive="30m")
    except Exception:
        return None
    return round(time.perf_counter() - t0, 2)


def contiguous_groups(entries: Sequence[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """Entries of one filing whose pages are consecutive (a statement that may continue across the break)."""
    groups: List[List[Dict[str, Any]]] = []
    for e in sorted(entries, key=lambda e: (e["pdf"], e["page_index"])):
        if groups and groups[-1][-1]["pdf"] == e["pdf"] and groups[-1][-1]["page_index"] + 1 == e["page_index"]:
            groups[-1].append(e)
        else:
            groups.append([e])
    return groups


def _timed_ms(fn) -> float:
    t0 = time.perf_counter()
    fn()
    return round((time.perf_counter() - t0) * 1000, 2)


def evaluate_group(group: List[Dict[str, Any]], out_dir: Path, model: str, do_repair: bool = True,
                   join: bool = True, dpi: int = 200, fmt: str = "json") -> List[Dict[str, Any]]:
    folder = out_dir / f"{group[0]['docID'] if 'docID' in group[0] else group[0]['id']}_p{group[0]['page_index'] + 1}"
    extractor = ollama_extractor(model) if fmt == "json" else format_extractor(model, fmt)
    outcomes = process_pages(group[0]["pdf"], [e["page_index"] for e in group], folder, extractor,
                             lambda page: OllamaReader(model), dpi=dpi, do_repair=do_repair, join=join)
    rows = []
    for entry, o in zip(group, outcomes):
        gold = RawDocumentTree.model_validate_json(Path(entry["silver"]).read_text(encoding="utf-8"))
        row: Dict[str, Any] = {"id": entry["id"], "company": entry.get("filerName", ""), "standard": entry["standard"],
                               "unit": entry["unit_multiplier"], "status": o.status, "joined_with": o.joined_with,
                               "calls": o.vlm_calls, "timing": o.timing, "page_status": entry.get("status", "")}
        if o.status in ("extraction_failed", "no_table"):
            rows.append(row)
            continue
        n = entry["page_index"] + 1
        raw, _ = parse_stage1((folder / f"page{n}_vlm_raw.json").read_text(encoding="utf-8"))
        final, _ = parse_stage1((folder / f"page{n}_stage1.json").read_text(encoding="utf-8"))
        before, _ = wrong_cells(raw, gold)
        after, _ = wrong_cells(final, gold)
        ok_before, ok_after = numbers_match(raw, gold), numbers_match(final, gold)
        v1, v2 = V1Verifier(), None
        row["v1_verify_ms"] = _timed_ms(lambda: v1.verify(raw))
        row["v2_verify_ms"] = _timed_ms(lambda: verify_document(raw))
        v2 = verify_document(raw)
        flagged_pipeline = o.status in ("needs_review", "repaired") or o.vlm_calls > 0   # not trusted at first sight (repair ran)
        row.update({
            "wrong_before": len(before), "wrong_after": len(after), "rowcmp": compare_numeric_rows(raw, gold),
            "v1": classify_detection(bool(before), v1.verify(raw).flagged, ok_before),
            "v2_alone": classify_detection(bool(before), not v2.trusted, ok_before),
            "v2": classify_detection(bool(before), flagged_pipeline, ok_before),
        })
        if flagged_pipeline:
            row["repair"] = classify_repair(len(before), len(after), o.trusted, ok_after)
        rows.append(row)
    return rows


def run_eval(manifest: List[Dict[str, Any]], split: str, model: str, out_dir: Path, only: str = "",
             do_repair: bool = True, join: bool = True, dpi: int = 200, fmt: str = "json", progress=print) -> Dict[str, Any]:
    chosen = [e for e in manifest if (split == "all" or e["split"] == split) and only in e["id"]]
    load_s = warm_up(model)
    progress(f"model {model} loaded in {load_s}s (cold start); scoring {len(chosen)} pages")
    rows: List[Dict[str, Any]] = []
    wall0 = time.perf_counter()
    for group in contiguous_groups(chosen):
        for r in evaluate_group(group, out_dir, model, do_repair, join, dpi, fmt):
            rows.append(r)
            t = r["timing"]
            progress(f"[{len(rows)}/{len(chosen)}] {r['id']:<24} {r['company'][:14]:<14} {r['standard']:<7} {r['status']:<15} "
                     f"v1={r.get('v1', '-'):<12} v2alone={r.get('v2_alone', '-'):<12} v2={r.get('v2', '-'):<12} "
                     f"repair={r.get('repair', '-'):<10} total={t.get('total_s', 0):.1f}s extract={t.get('extract_s', 0):.1f}s")
    wall = time.perf_counter() - wall0
    return {"split": split, "model": model, "dpi": dpi, "format": fmt, "code": code_fingerprint(),
            "when": datetime.datetime.now().isoformat(timespec="seconds"), "wall_s": round(wall, 1), "model_load_s": load_s,
            **aggregate(rows)}


def aggregate(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Counters and latency summaries from per-page rows (also used to re-score a saved report)."""
    scored = [r for r in rows if "v2" in r]
    timing = {k: latency_summary([r["timing"][k] for r in rows if k in r["timing"]])
              for k in ("render_s", "extract_s", "verify_s", "join_s", "repair_s", "export_s", "total_s")}
    repaired = [r for r in rows if r["timing"].get("repair_s") and r["calls"]]
    return {
        "pages": len(rows), "rows": rows,
        "v1": dict(Counter(r["v1"] for r in scored)), "v2_alone": dict(Counter(r["v2_alone"] for r in scored)),
        "v2": dict(Counter(r["v2"] for r in scored)), "repair": dict(Counter(r["repair"] for r in scored if "repair" in r)),
        "status": dict(Counter(r["status"] for r in rows)), "by_standard": {
            s: dict(Counter(r["v2"] for r in scored if r["standard"] == s)) for s in sorted({r["standard"] for r in rows})},
        "latency": timing,
        "repair_s_per_vlm_call": latency_summary([r["timing"]["repair_s"] / r["calls"] for r in repaired]),
        "verify_ms": {"v1": latency_summary([r["v1_verify_ms"] for r in scored]), "v2": latency_summary([r["v2_verify_ms"] for r in scored])},
        "not_scored": [r["id"] for r in rows if "v2" not in r],
    }


def _lat(d: Dict[str, float]) -> str:
    return "n/a" if not d.get("n") else f"mean {d['mean']}s  median {d['median']}s  p90 {d['p90']}s  max {d['max']}s  (n={d['n']})"


def format_report(rep: Dict[str, Any]) -> str:
    L = [f"split={rep['split']}  model={rep['model']}  dpi={rep.get('dpi', 200)}  code={rep['code']}  {rep['when']}",
         f"{rep['pages']} pages, wall {rep['wall_s']}s, model cold-start load {rep['model_load_s']}s", "",
         f"pipeline status: {rep['status']}", "",
         "verifier on the raw VLM output:",
         f"  v1 (page by page)       : {rep['v1']}", f"  v2 (page by page)       : {rep['v2_alone']}",
         f"  v2 + joined split pages : {rep['v2']}   <- what the pipeline does",
         f"retry loop (pages the pipeline flagged): {rep['repair']}",
         f"v2 by accounting standard: {rep['by_standard']}", "", "latency per page (steady state):"]
    for k, label in (("extract_s", "VLM extract (Stage 1)"), ("render_s", "render"), ("join_s", "join check"),
                     ("repair_s", "repair (pages that needed it)"), ("export_s", "export"), ("total_s", "TOTAL per page")):
        L.append(f"  {label:<30} {_lat(rep['latency'][k])}")
    L.append(f"  {'repair per VLM crop read':<30} {_lat(rep['repair_s_per_vlm_call'])}")
    v = rep["verify_ms"]
    L.append(f"  verification time: v1 mean {v['v1'].get('mean', 'n/a')} ms, v2 mean {v['v2'].get('mean', 'n/a')} ms (CPU, per page)")
    if rep["not_scored"]:
        L.append(f"not scored (extraction failed / no table): {rep['not_scored']}")
    bad = [r for r in rep["rows"] if r.get("v2") == "SILENT_ERROR" or r.get("repair") in ("SILENT_WRONG", "worse")]
    L += ["", "MUST BE ZERO (wrong number vouched for, or repair made it worse): "
          + (f"{len(bad)} page(s): " + ", ".join(r["id"] for r in bad) if bad else "none")]
    return "\n".join(L)
