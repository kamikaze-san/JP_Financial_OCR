"""Order-preserving alignment of two label sequences (document rows <-> page lines <-> re-read rows)."""
from difflib import SequenceMatcher
from typing import Dict, List


def align_labels(a: List[str], b: List[str], min_ratio: float = 0.6) -> Dict[int, int]:
    """Map index in `a` -> index in `b`. Exact matches first (so repeated labels such as 'その他' line up by
    position), then in-order fuzzy pairing inside mismatching blocks so a slightly garbled label still aligns."""
    sm = SequenceMatcher(None, a, b, autojunk=False)
    mapping: Dict[int, int] = {}
    for tag, i0, i1, j0, j1 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i1 - i0):
                mapping[i0 + k] = j0 + k
        elif tag == "replace":
            j = j0
            for i in range(i0, i1):
                best = None
                for jj in range(j, j1):
                    ratio = SequenceMatcher(None, a[i], b[jj]).ratio()
                    if ratio >= min_ratio and (best is None or ratio > best[0]):
                        best = (ratio, jj)
                if best:
                    mapping[i] = best[1]
                    j = best[1] + 1
    return mapping
