"""
Planning: decide WHICH filings to fetch, and show them (company, doc id, files, sizes) before anything is downloaded.

    collect()   one documents.json call per requested date (metadata only)
    sample()    one filing per company, deterministic for a given seed
    probe()     file sizes from response headers (no body is read)
    build_plan  -> plan dict; `approval_token(plan)` identifies exactly this list

The dev/test split is fixed by a hash of the docID, so it cannot drift while code is being tuned: tune on `dev`,
report on `test`.
"""
import datetime
import hashlib
import json
import random
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

from .client import EdinetClient, EdinetError

DOC_TYPES = {"120": "annual securities report (有価証券報告書)", "160": "semi-annual report (半期報告書)",
             "140": "quarterly report (四半期報告書)"}
FILE_TYPES = ((2, "pdf"), (5, "csv"))


def split_of(doc_id: str, test_fraction: float = 0.5) -> str:
    """Deterministic: the same docID is always dev or test."""
    h = int(hashlib.sha256(doc_id.encode()).hexdigest(), 16) % 1000
    return "test" if h < test_fraction * 1000 else "dev"


def eligible(row: Dict[str, Any], doc_types: Sequence[str], listed_only: bool = True) -> bool:
    return (row.get("docTypeCode") in doc_types
            and str(row.get("withdrawalStatus", "0")) == "0"
            and str(row.get("pdfFlag")) == "1" and str(row.get("csvFlag")) == "1"
            and (not listed_only or bool(row.get("secCode"))))


def collect(client: EdinetClient, dates: Iterable[datetime.date], doc_types: Sequence[str] = ("120",),
            listed_only: bool = True, progress: Optional[Callable[[str], None]] = None) -> List[Dict[str, Any]]:
    rows: Dict[str, Dict[str, Any]] = {}
    for d in dates:
        day = client.list_documents(d)
        keep = [r for r in day if eligible(r, doc_types, listed_only)]
        for r in keep:
            rows.setdefault(r["docID"], r)
        if progress:
            progress(f"  {d}: {len(day)} filings listed, {len(keep)} eligible (PDF + CSV available)")
    return list(rows.values())


def sample(rows: Sequence[Dict[str, Any]], n: int, seed: int = 0) -> List[Dict[str, Any]]:
    """At most one filing per company, chosen by a seeded shuffle (so the sample is reproducible)."""
    by_company: Dict[str, List[Dict[str, Any]]] = {}
    for r in sorted(rows, key=lambda r: r["docID"]):
        by_company.setdefault(r.get("edinetCode") or r["docID"], []).append(r)
    companies = sorted(by_company)
    random.Random(seed).shuffle(companies)
    return [by_company[c][0] for c in companies[:n]]


def build_entry(row: Dict[str, Any], test_fraction: float = 0.5) -> Dict[str, Any]:
    return {
        "docID": row["docID"], "edinetCode": row.get("edinetCode", ""), "secCode": row.get("secCode", ""),
        "filerName": row.get("filerName", ""), "docTypeCode": row.get("docTypeCode", ""),
        "docDescription": row.get("docDescription", ""), "periodEnd": row.get("periodEnd", ""),
        "submitDateTime": row.get("submitDateTime", ""), "split": split_of(row["docID"], test_fraction),
        "files": [{"type": t, "name": name, "size": None} for t, name in FILE_TYPES],
    }


def probe(client: EdinetClient, entries: List[Dict[str, Any]],
          progress: Optional[Callable[[str], None]] = None) -> None:
    """Fill in file sizes from headers. A server that does not send Content-Length leaves the size unknown."""
    for i, e in enumerate(entries, start=1):
        for f in e["files"]:
            try:
                f["size"] = client.probe(e["docID"], f["type"]).size
            except EdinetError as exc:
                f["size"], f["error"] = None, str(exc)[:120]
        if progress and i % 10 == 0:
            progress(f"  probed {i}/{len(entries)}")


def approval_token(plan: Dict[str, Any]) -> str:
    """Identifies exactly which files the plan would fetch; any change to the list changes the token."""
    canonical = json.dumps([(e["docID"], [f["type"] for f in e["files"]]) for e in plan["entries"]], sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()[:10]


def build_plan(client: EdinetClient, dates: Sequence[datetime.date], n: int, seed: int = 0,
               doc_types: Sequence[str] = ("120",), test_fraction: float = 0.5,
               progress: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
    rows = collect(client, dates, doc_types, progress=progress)
    chosen = sample(rows, n, seed)
    entries = [build_entry(r, test_fraction) for r in chosen]
    probe(client, entries, progress)
    known = sum(f["size"] or 0 for e in entries for f in e["files"])
    plan = {
        "created": datetime.datetime.now().isoformat(timespec="seconds"),
        "filters": {"dates": [d.isoformat() for d in dates], "doc_types": list(doc_types), "n": n, "seed": seed,
                    "test_fraction": test_fraction, "eligible_found": len(rows)},
        "total_bytes_known": known,
        "unknown_sizes": sum(1 for e in entries for f in e["files"] if f["size"] is None),
        "entries": entries,
    }
    plan["approval_token"] = approval_token(plan)
    return plan


def _mb(size: Optional[int]) -> str:
    return "?" if size is None else f"{size / 1e6:.1f} MB"


def format_plan(plan: Dict[str, Any]) -> str:
    lines = [f"{'#':>3} {'split':<5} {'code':<6} {'company':<30} {'docID':<10} {'type':<5} {'period end':<11} "
             f"{'PDF':>9} {'CSV':>9}"]
    for i, e in enumerate(plan["entries"], start=1):
        size = {f["name"]: f["size"] for f in e["files"]}
        lines.append(f"{i:>3} {e['split']:<5} {e['secCode'][:4]:<6} {e['filerName'][:28]:<30} {e['docID']:<10} "
                     f"{e['docTypeCode']:<5} {e['periodEnd']:<11} {_mb(size.get('pdf')):>9} {_mb(size.get('csv')):>9}")
    f = plan["filters"]
    lines.append("")
    lines.append(f"{len(plan['entries'])} filings ({sum(1 for e in plan['entries'] if e['split'] == 'test')} test / "
                 f"{sum(1 for e in plan['entries'] if e['split'] == 'dev')} dev), chosen from {f['eligible_found']} eligible "
                 f"filings on {len(f['dates'])} date(s); seed {f['seed']}")
    lines.append(f"known download size: {_mb(plan['total_bytes_known'])}   files with unknown size: {plan['unknown_sizes']}")
    lines.append(f"approval token: {plan['approval_token']}")
    return "\n".join(lines)
