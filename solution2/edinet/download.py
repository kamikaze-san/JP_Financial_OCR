"""
Gated download: only what a plan lists, only with the plan's approval token, only within the size cap.

The token is shown next to the list by `plan`; passing it back is how a human says "yes, exactly this list".
"""
import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from .client import EdinetClient, EdinetError
from .plan import approval_token

MAX_ZIP_ENTRIES = 200
MAX_UNCOMPRESSED = 500 * 1024 * 1024


def safe_extract_csvs(zip_path: Path, dest: Path) -> int:
    """Extract only .csv members, refusing paths that escape `dest` and oversized archives."""
    dest.mkdir(parents=True, exist_ok=True)
    root = dest.resolve()
    count = 0
    with zipfile.ZipFile(zip_path) as z:
        infos = z.infolist()
        if len(infos) > MAX_ZIP_ENTRIES or sum(i.file_size for i in infos) > MAX_UNCOMPRESSED:
            raise EdinetError(f"{zip_path.name}: archive is unreasonably large")
        for info in infos:
            if info.is_dir() or not info.filename.lower().endswith(".csv"):
                continue
            target = (dest / Path(info.filename).name).resolve()      # flatten: no sub-directories, no '..'
            if root not in target.parents:
                raise EdinetError(f"{zip_path.name}: unsafe member {info.filename!r}")
            with z.open(info) as src, open(target, "wb") as out:
                out.write(src.read())
            count += 1
    return count


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def download_plan(client: EdinetClient, plan: Dict[str, Any], out_dir, token: str,
                  max_total_bytes: int = 500 * 1024 * 1024,
                  progress: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
    if token != approval_token(plan):
        raise EdinetError("the approval token does not match this plan: nothing was downloaded")
    if plan["total_bytes_known"] > max_total_bytes:
        raise EdinetError(f"the plan is {plan['total_bytes_known'] / 1e6:.0f} MB, above the "
                          f"{max_total_bytes / 1e6:.0f} MB cap: nothing was downloaded")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    ledger_path = out / "downloads.json"
    ledger: Dict[str, Any] = json.loads(ledger_path.read_text(encoding="utf-8")) if ledger_path.exists() else {}
    failures = {}
    for i, e in enumerate(plan["entries"], start=1):
        doc = out / e["docID"]
        record = ledger.setdefault(e["docID"], {})
        try:
            for f in e["files"]:
                name = f["name"]
                target = doc / (f"{e['docID']}.pdf" if name == "pdf" else f"{e['docID']}_csv.zip")
                if target.exists():
                    continue                                          # idempotent: never fetch twice
                size = client.download(e["docID"], f["type"], target)
                record[name] = {"file": target.name, "size": size, "sha256": _sha256(target)}
                if name == "csv":
                    record["csv_members"] = safe_extract_csvs(target, doc / "csv")
            record.update({"filerName": e["filerName"], "secCode": e["secCode"], "periodEnd": e["periodEnd"],
                           "split": e["split"], "docTypeCode": e["docTypeCode"]})
        except EdinetError as exc:
            failures[e["docID"]] = str(exc)[:160]
        ledger_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=1), encoding="utf-8")
        if progress:
            progress(f"  [{i}/{len(plan['entries'])}] {e['docID']} {e['filerName'][:24]}"
                     + (f"  FAILED: {failures[e['docID']]}" if e["docID"] in failures else ""))
    return {"downloaded": len(plan["entries"]) - len(failures), "failed": failures}
