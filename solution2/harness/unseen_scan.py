"""
Find balance-sheet pages in digital PDFs, build silver gold from their text layer, and check the verifier on them.

    python -m solution2.harness.unseen_scan [pdf ...]        # default: every PDF under dataset/

A page is a candidate if it contains 資産合計. For each candidate:
    silver      the text layer could be turned into a clean table (or the reason it could not)
    v2          the verifier's verdict on that silver table (independent of any VLM)
A silver table the verifier trusts is two independent paths agreeing (text-layer digits + accounting identities),
which makes it usable as ground truth for the VLM -> verify -> repair evaluation. A silver table the verifier
flags is either a verifier weakness or a table the text layer cannot read: both are worth a look.
"""
import argparse
import sys
import unicodedata
from pathlib import Path
from typing import List, Tuple

from solution2.verify import verify_document

from .corpus import ROOT
from .silver import silver_gold

# pages used while developing the verifier: a clean result here proves little, so they are labelled
DEV = {"balance_sheet_after_correction": "baseline", "calbee_financial_2026": "Calbee (dev)", "yamato_fs66": "Yamato (dev)",
       "company_01_yamato": "Yamato (dev)", "company_02_calbee": "Calbee (dev)", "company_03_fastretailing": "Fast Retailing (dev)"}


# totals that appear on a balance-sheet page: 資産合計 (assets) and, for the liabilities/equity page, 負債合計 or 資本合計
# (IFRS statements have no 資産合計 on their second page: it totals up as 負債及び資本合計)
BS_MARKERS = ("資産合計", "負債合計", "資本合計")


def candidate_pages(pdf: Path) -> List[int]:
    import fitz
    with fitz.open(pdf) as doc:
        texts = [unicodedata.normalize("NFKC", p.get_text().replace(" ", "").replace("\n", "")) for p in doc]
    return [i for i, t in enumerate(texts) if any(m in t for m in BS_MARKERS)]


def scan(pdfs: List[Path]) -> List[Tuple[str, int, str, str]]:
    rows = []
    for pdf in pdfs:
        for i in candidate_pages(pdf):
            tag = DEV.get(pdf.stem) or DEV.get(pdf.parent.name) or "held-out"
            try:
                gold = silver_gold(pdf, i)
            except Exception as exc:
                rows.append((pdf.stem, i + 1, tag, f"silver failed: {str(exc)[:70]}"))
                continue
            v = verify_document(gold)
            rows.append((pdf.stem, i + 1, tag, f"{len(gold.rows)} rows x {len(gold.metadata.periods)} cols -> "
                         + ("TRUSTED" if v.trusted else f"FLAGGED ({', '.join(sorted({f.kind for f in v.errors}))}; "
                                                         f"coverage {v.coverage:.0%})" if v.coverage is not None else "FLAGGED")))
    return rows


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("pdfs", nargs="*")
    args = ap.parse_args(argv)
    pdfs = [Path(p) for p in args.pdfs] or sorted((ROOT / "dataset").rglob("*.pdf"))
    results = scan(pdfs)
    print(f"{'pdf':<34}{'page':>5}  {'set':<22}result")
    for name, page, tag, outcome in results:
        print(f"{name:<34}{page:>5}  {tag:<22}{outcome}")
    held = [r for r in results if r[2] == "held-out"]
    ok = [r for r in held if "TRUSTED" in r[3]]
    print(f"\nheld-out candidates: {len(held)}   silver built and verifier trusts it: {len(ok)}   "
          f"verifier flags: {sum(1 for r in held if 'FLAGGED' in r[3])}   silver could not be built: {sum(1 for r in held if 'silver failed' in r[3])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
