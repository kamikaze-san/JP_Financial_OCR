"""
Clean (gold) documents the harness corrupts.

All three come from existing code, untouched:
  baseline : solution1.baseline_data      (assets only, 2 columns, totals after children)
  calbee   : build_golden_anchors         (full BS, 2 columns, totals after children)
  yamato   : build_golden_anchors         (full BS, 1 column, T-account flattened, totals before children)

More gold can be added with `--extra path.json` (a RawDocumentTree serialised with
model_dump_json), e.g. documents derived from EDINET/TDnet XBRL.
"""
import sys
from pathlib import Path
from typing import Dict, Iterable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from solution1.models import RawDocumentTree  # noqa: E402


def load_corpus(extra_json_paths: Iterable[str] = ()) -> Dict[str, RawDocumentTree]:
    from solution1.baseline_data import get_baseline_raw_tree
    import build_golden_anchors as bga  # root-level script; import has no side effects beyond stdout config

    corpus: Dict[str, RawDocumentTree] = {
        "baseline": get_baseline_raw_tree(),
        "calbee": bga.build_calbee_golden(),
        "yamato": bga.build_yamato_golden(),
    }
    # silver gold built from the text layer of other digital pages (see harness/silver.py); the verifier must
    # trust each of these, so they double as clean regression documents for the mutation suite
    for path in sorted((ROOT / "solution2" / "fixtures" / "silver").glob("*.json")):
        corpus[path.stem] = RawDocumentTree.model_validate_json(path.read_text(encoding="utf-8"))
    for p in extra_json_paths:
        path = Path(p)
        corpus[path.stem] = RawDocumentTree.model_validate_json(path.read_text(encoding="utf-8"))
    return corpus
