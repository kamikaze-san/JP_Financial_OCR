"""
The original benchmark documents (Yamato T-account page, Calbee, FastRetailing, Nikon, and the solution1 baseline page) read with each
first-read configuration, optionally on degraded renders that imitate a scan.

    python -m solution2.benchmark_docs --model qwen3-vl:4b-instruct --configs json markdown bands bands_md [--degrade scan skew]

Scoring is the row-level comparison used for the TDnet runs (structure-blind: only rows with numbers count), on the statement as a
whole: a two-page statement is joined before it is compared with its gold. The Yamato page is a two-column T-account; its gold is
the flattened list, which a row-by-row comparison does not mind the order of.
Output: solution2/data/benchmark/<model>_<config>[_<degrade>].json and a summary table on stdout.
"""
import argparse
import json
import sys
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from solution1.models import RawDocumentTree  # noqa: E402
from solution2.bands import BandConfig, band_extractor  # noqa: E402
from solution2.extract import ollama_extractor, render_page  # noqa: E402
from solution2.formats import compare_numeric_rows, format_extractor  # noqa: E402
from solution2.verify import parse_stage1, verify_document  # noqa: E402
from solution2.verify.join import join_tables  # noqa: E402

SUITE = ROOT / "dataset" / "benchmark_suite"
OUT = ROOT / "solution2" / "data" / "benchmark"
SILVER = ROOT / "solution2" / "fixtures" / "silver"


def documents() -> Dict[str, Tuple[Path, List[int], RawDocumentTree]]:
    from solution1.baseline_data import get_baseline_raw_tree
    import build_golden_anchors as bga

    def silver(name):
        return RawDocumentTree.model_validate_json((SILVER / f"{name}.json").read_text(encoding="utf-8"))

    return {
        "baseline": (ROOT / "balance_sheet_after_correction.pdf", [0], get_baseline_raw_tree()),
        "yamato": (SUITE / "company_01_yamato" / "balance_sheet.pdf", [0], bga.build_yamato_golden()),
        "calbee": (SUITE / "company_02_calbee" / "balance_sheet.pdf", [0, 1], bga.build_calbee_golden()),
        "fastretailing": (SUITE / "company_03_fastretailing" / "balance_sheet.pdf", [0, 1],
                          join_tables(silver("silver_fastretailing_p1"), silver("silver_fastretailing_p2"))),
        "nikon": (SUITE / "company_04_nikon" / "balance_sheet.pdf", [0, 1],
                  join_tables(silver("silver_nikon_p1"), silver("silver_nikon_p2"))),
    }


def degrade(path: Path, mode: str, out: Path) -> Path:
    """A scan-like copy of a rendered page: `scan` = low resolution upscaled, blur, noise, JPEG; `skew` = rotated one degree."""
    img = Image.open(path).convert("L")
    rng = np.random.default_rng(0)
    if mode == "scan":
        small = img.resize((img.width // 2, img.height // 2), Image.BILINEAR)
        img = small.resize(img.size, Image.BICUBIC).filter(ImageFilter.GaussianBlur(0.8))
        arr = np.asarray(img).astype(np.float32) + rng.normal(0, 12, (img.height, img.width))
        img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
        tmp = out.with_suffix(".jpg")
        img.save(tmp, quality=40)
        img = Image.open(tmp).convert("L")
    elif mode == "skew":
        img = img.rotate(1.0, resample=Image.BICUBIC, expand=False, fillcolor=255)
    else:
        raise ValueError(mode)
    img.convert("RGB").save(out)
    return out


def make_extractor(model: str, config: str) -> Tuple[Callable[[str], Optional[str]], int]:
    if config == "json":
        return ollama_extractor(model), 200
    if config in ("markdown", "html"):
        return format_extractor(model, config), 200
    if config == "bands":
        return band_extractor(model, BandConfig(dpi=300)), 300
    if config == "bands_md":
        return band_extractor(model, BandConfig(dpi=300, reader="markdown")), 300
    raise ValueError(config)


def run(model: str, configs: List[str], modes: List[str], clean: bool = True) -> List[dict]:
    OUT.mkdir(parents=True, exist_ok=True)
    docs = documents()
    results = []
    for config in configs:
        extractor, dpi = make_extractor(model, config)
        for mode in (["clean"] if clean else []) + modes:
            for name, (pdf, pages, gold) in docs.items():
                work = OUT / "images" / f"{model.replace(':', '_')}_{config}_{mode}" / name
                trees, secs, ok = [], 0.0, True
                for p in pages:
                    img = render_page(pdf, p, work, dpi)
                    if mode != "clean":
                        img = degrade(img, mode, work / f"{img.stem}_{mode}.png")
                    t0 = time.perf_counter()
                    text = extractor(str(img))
                    secs += time.perf_counter() - t0
                    try:
                        trees.append(parse_stage1(text)[0]) if text else None
                    except Exception:
                        pass
                    if not text:
                        ok = False
                row = {"model": model, "config": config, "mode": mode, "doc": name, "pages": len(pages), "read_ok": ok and len(trees) == len(pages), "secs": round(secs, 1)}
                if trees:
                    joined = trees[0]
                    for t in trees[1:]:
                        joined = join_tables(joined, t)
                    row.update(compare_numeric_rows(joined, gold))
                    v = verify_document(joined)
                    row["trusted"], row["coverage"] = v.trusted, v.coverage
                results.append(row)
                print(f"{model} {config:<9}{mode:<6}{name:<14}pages {len(pages)} ok={row['read_ok']!s:<5} exact {row.get('exact', '-')}/{row.get('gold_rows', '-')} "
                      f"mis {row.get('misplaced', '-')} miss {row.get('missing', '-')} extra {row.get('extra', '-')} trusted={row.get('trusted', '-')!s:<5} {row['secs']}s", flush=True)
    return results


def summarise(results: List[dict]) -> str:
    from collections import defaultdict
    groups = defaultdict(list)
    for r in results:
        groups[(r["config"], r["mode"])].append(r)
    lines = [f"{'config':<10}{'render':<7}{'docs read':>10}{'rows exact':>16}{'misplaced':>10}{'missing':>8}{'extra':>6}{'trusted':>8}{'secs/doc':>10}"]
    for (config, mode), rs in groups.items():
        g = sum(r.get("gold_rows", 0) for r in rs)
        e = sum(r.get("exact", 0) for r in rs)
        lines.append(f"{config:<10}{mode:<7}{sum(r['read_ok'] for r in rs):>6}/{len(rs):<3}{e:>8}/{g:<6}{sum(r.get('misplaced', 0) for r in rs):>9}{sum(r.get('missing', 0) for r in rs):>8}"
                     f"{sum(r.get('extra', 0) for r in rs):>6}{sum(bool(r.get('trusted')) for r in rs):>8}{sum(r['secs'] for r in rs) / len(rs):>10.1f}")
    return "\n".join(lines)


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default="qwen3-vl:4b-instruct")
    ap.add_argument("--configs", nargs="+", default=["json", "bands"], choices=["json", "html", "markdown", "bands", "bands_md"])
    ap.add_argument("--degrade", nargs="*", default=[], choices=["scan", "skew"])
    ap.add_argument("--no-clean", action="store_true", help="only the degraded renders")
    ap.add_argument("--tag", default="")
    args = ap.parse_args(argv)
    results = run(args.model, args.configs, args.degrade, not args.no_clean)
    text = summarise(results)
    print("\n" + text)
    name = f"{args.model.replace(':', '_')}{('_' + args.tag) if args.tag else ''}"
    (OUT / f"{name}.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / f"{name}.txt").write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
