"""Gated download for TDnet: only what the plan lists, only with the plan's approval token, only within the size cap."""
import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from solution2.edinet.plan import approval_token

from .client import TdnetClient, TdnetError


def download_plan(client: TdnetClient, plan: Dict[str, Any], out_dir, token: str,
                  max_total_bytes: int = 300 * 1024 * 1024,
                  progress: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
    if token != approval_token(plan):
        raise TdnetError("the approval token does not match this plan: nothing was downloaded")
    if plan["total_bytes_known"] > max_total_bytes:
        raise TdnetError(f"the plan is {plan['total_bytes_known'] / 1e6:.0f} MB, above the "
                         f"{max_total_bytes / 1e6:.0f} MB cap: nothing was downloaded")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    ledger_path = out / "downloads.json"
    ledger: Dict[str, Any] = json.loads(ledger_path.read_text(encoding="utf-8")) if ledger_path.exists() else {}
    failures: Dict[str, str] = {}
    for i, e in enumerate(plan["entries"], start=1):
        target = out / e["docID"] / e["pdf"]
        try:
            if not target.exists():                                   # idempotent: never fetch twice
                size = client.download(e["pdf"], target)
                ledger[e["docID"]] = {"file": target.name, "size": size, "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}
            ledger.setdefault(e["docID"], {}).update({
                "filerName": e["filerName"], "secCode": e["secCode"], "title": e["title"], "date": e["date"],
                "standard": e["standard"], "split": e["split"], "pdf_url": e["pdf_url"]})
        except TdnetError as exc:
            failures[e["docID"]] = str(exc)[:160]
        ledger_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=1), encoding="utf-8")
        if progress:
            progress(f"  [{i}/{len(plan['entries'])}] {e['docID']} {e['filerName'][:22]}"
                     + (f"  FAILED: {failures[e['docID']]}" if e["docID"] in failures else ""))
    return {"downloaded": len(plan["entries"]) - len(failures), "failed": failures}
