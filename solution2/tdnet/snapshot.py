"""
Copy the downloaded filings, the plan and the prepared gold pages into a tracked folder (solution2/data_sample/tdnet),
with the manifest's paths made relative, so the evaluation can be reproduced from a fresh clone after TDnet has
dropped the filings (its viewer keeps about 31 days).

    python -m solution2.tdnet snapshot            # solution2/data/tdnet -> solution2/data_sample/tdnet
    python -m solution2.tdnet eval --data-dir solution2/data_sample/tdnet --split dev --include-split
"""
import json
import shutil
from pathlib import Path
from typing import Any, Dict, List


def resolve_manifest(manifest: List[Dict[str, Any]], data_dir: Path) -> List[Dict[str, Any]]:
    """Entries with `pdf` and `silver` as absolute paths (they may be stored relative to `data_dir`)."""
    out = []
    for e in manifest:
        e = dict(e)
        for key in ("pdf", "silver"):
            if not Path(e[key]).is_absolute():
                e[key] = str(data_dir / e[key])
        out.append(e)
    return out


def snapshot(src: Path, dst: Path) -> Dict[str, int]:
    src, dst = src.resolve(), dst.resolve()
    (dst / "raw").mkdir(parents=True, exist_ok=True)
    (dst / "prepared").mkdir(parents=True, exist_ok=True)
    pdfs = 0
    for pdf in sorted((src / "raw").glob("*/*.pdf")):
        target = dst / "raw" / pdf.parent.name / pdf.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(pdf, target)
        pdfs += 1
    for name in ("downloads.json",):
        if (src / "raw" / name).exists():
            shutil.copy2(src / "raw" / name, dst / "raw" / name)
    if (src / "plan.json").exists():
        shutil.copy2(src / "plan.json", dst / "plan.json")
    manifest = json.loads((src / "prepared" / "manifest.json").read_text(encoding="utf-8"))
    for e in manifest:
        shutil.copy2(e["silver"], dst / "prepared" / Path(e["silver"]).name)
        e["silver"] = f"prepared/{Path(e['silver']).name}"
        e["pdf"] = Path(e["pdf"]).resolve().relative_to(src).as_posix()
    (dst / "prepared" / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"pdfs": pdfs, "pages": len(manifest)}
