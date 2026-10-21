"""
Collect real Stage-1 output from local Ollama VLMs for the gold pages, as fixtures for `realcheck`.

    python -m solution2.harness.collect_real --models qwen3-vl:2b-instruct qwen3-vl:4b-instruct

Uses the project's own generic STAGE1_SYSTEM_PROMPT (no document-specific examples), 200 DPI pages,
temperature 0. Existing fixtures are kept, so re-running only fills what is missing.
"""
import argparse
import sys
import time
from pathlib import Path

from .corpus import ROOT

# name -> (source PDF, 0-based page index, existing 200 DPI render)
PAGES = {
    "baseline_p1": (ROOT / "balance_sheet_after_correction.pdf", 0,
                    ROOT / "solution1/rendered_images_200dpi/balance_sheet_after_correction_page_1.png"),
    "calbee_p1": (ROOT / "dataset/benchmark_suite/company_02_calbee/balance_sheet.pdf", 0,
                  ROOT / "dataset/benchmark_suite/company_02_calbee/page_1.png"),
    "calbee_p2": (ROOT / "dataset/benchmark_suite/company_02_calbee/balance_sheet.pdf", 1,
                  ROOT / "dataset/benchmark_suite/company_02_calbee/page_2.png"),
    "yamato_p1": (ROOT / "dataset/benchmark_suite/company_01_yamato/balance_sheet.pdf", 0,
                  ROOT / "dataset/benchmark_suite/company_01_yamato/page_1.png"),
}
FIXTURES = ROOT / "solution2" / "fixtures" / "real_vlm"
IMAGES = ROOT / "solution2" / "fixtures" / "_rendered"


def page_image(name: str, dpi: int) -> Path:
    pdf, index, existing = PAGES[name]
    if dpi == 200:
        return existing
    import fitz  # PyMuPDF
    IMAGES.mkdir(parents=True, exist_ok=True)
    out = IMAGES / f"{name}@{dpi}dpi.png"
    if not out.exists():
        with fitz.open(pdf) as doc:
            doc[index].get_pixmap(dpi=dpi).save(out)
    return out


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    import ollama
    from solution1.vlm_interface import STAGE1_SYSTEM_PROMPT

    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", default=["qwen3-vl:2b-instruct"])
    ap.add_argument("--pages", nargs="*", default=list(PAGES))
    ap.add_argument("--dpi", type=int, default=200, help="render resolution; lower values induce natural errors")
    args = ap.parse_args(argv)
    FIXTURES.mkdir(parents=True, exist_ok=True)
    for model in args.models:
        for name in args.pages:
            tag = name if args.dpi == 200 else f"{name}@{args.dpi}"
            out = FIXTURES / f"{tag}__{model.replace(':', '_').replace('/', '_')}.json"
            if out.exists():
                print(f"[skip] {out.name}")
                continue
            t0 = time.time()
            try:
                resp = ollama.chat(model=model, format="json",
                                   messages=[{"role": "user", "content": STAGE1_SYSTEM_PROMPT,
                                              "images": [str(page_image(name, args.dpi))]}],
                                   options={"num_ctx": 16384, "num_predict": 8192, "temperature": 0.0})
                text = resp["message"]["content"]
            except Exception as exc:
                print(f"[fail] {model} {name}: {exc}")
                continue
            out.write_text(text, encoding="utf-8")
            print(f"[ok]   {out.name}  {len(text):,} chars  {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
