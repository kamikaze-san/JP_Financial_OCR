"""
Planning for the TDnet source: which 決算短信 to fetch, shown (company, doc id, PDF URL, size) before anything is downloaded.

Filters: only 決算短信; no amendments (訂正 / 修正 in the title); no REITs / infrastructure funds / ETFs. One filing per
company, seeded, optionally with a minimum number of IFRS filings (the window is mostly Japanese GAAP, so without a quota
the sample would barely test IFRS layouts). The dev/test split is a hash of the docID, exactly as in the EDINET source.
"""
import datetime
import random
import re
from typing import Any, Callable, Dict, List, Optional, Sequence

from solution2.edinet.plan import approval_token, split_of

from .client import TdnetClient

AMENDMENT_WORDS = ("訂正", "修正")
FUND_WORDS = ("REIT", "ＲＥＩＴ", "インフラ", "不動産投資", "投資法人", "投資証券", "ETF", "ＥＴＦ", "ファンド")
FUND_NAME = re.compile(r"^R-")                       # after NFKC, REIT names are written 'Ｒ－…' -> 'R-…'


def standard_of(title: str) -> str:
    t = title.upper()
    if "IFRS" in t or "国際会計基準" in title:
        return "IFRS"
    if "米国基準" in title or "米国会計基準" in title or "US GAAP" in t or "USGAAP" in t:     # the formal tag, not the bare word 米国
        return "US-GAAP"
    return "J-GAAP" if "日本基準" in title else "unknown"


def eligible(row: Dict[str, Any]) -> bool:
    title, name = row["title"], row["name"]
    return ("決算短信" in title
            and not any(w in title for w in AMENDMENT_WORDS)
            and not any(w in title or w in name for w in FUND_WORDS)
            and not FUND_NAME.match(name))


# Companies whose statements were used while developing the verifier (Baseline = Fast Retailing-like page, Calbee 2229,
# Yamato 7886, Nikon 7731, Fast Retailing 9983): excluded so the sample is genuinely held out.
USED_CODES = frozenset({"2229", "7886", "7731", "9983"})


def collect(client: TdnetClient, days: Sequence[datetime.date], allow_unknown: bool = False,
            exclude_codes: Sequence[str] = tuple(USED_CODES),
            progress: Optional[Callable[[str], None]] = None) -> List[Dict[str, Any]]:
    """Eligible filings. `unknown` accounting standard means the title names none (ETFs and ETNs publish 決算短信 too but
    never say 日本基準 / IFRS), so those are excluded unless asked for."""
    rows: Dict[str, Dict[str, Any]] = {}
    for d in days:
        day_rows = client.list_day(d)
        keep = [r for r in day_rows if eligible(r) and (allow_unknown or standard_of(r["title"]) != "unknown")
                and r["code"][:4] not in exclude_codes]
        for r in keep:
            rows.setdefault(r["pdf"][:-4], r)
        if progress:
            progress(f"  {d} {d:%a}: {len(day_rows):>4} disclosures, {len(keep):>3} eligible 決算短信")
    return list(rows.values())


def sample(rows: Sequence[Dict[str, Any]], n: int, seed: int = 0, min_ifrs: int = 0) -> List[Dict[str, Any]]:
    """One filing per company; IFRS filings first up to `min_ifrs`, then a seeded fill."""
    by_company: Dict[str, Dict[str, Any]] = {}
    for r in sorted(rows, key=lambda r: r["pdf"]):
        by_company.setdefault(r["code"], r)
    pool = sorted(by_company.values(), key=lambda r: r["code"])
    random.Random(seed).shuffle(pool)
    ifrs = [r for r in pool if standard_of(r["title"]) == "IFRS"][:min_ifrs]
    chosen = list(ifrs)
    chosen += [r for r in pool if r not in chosen][: max(0, n - len(chosen))]
    return chosen[:n]


def build_entry(row: Dict[str, Any], client: TdnetClient, test_fraction: float = 0.5) -> Dict[str, Any]:
    doc_id = row["pdf"][:-4]
    return {"docID": doc_id, "secCode": row["code"], "filerName": row["name"], "title": row["title"], "date": row["date"],
            "time": row["time"], "standard": standard_of(row["title"]), "pdf": row["pdf"], "pdf_url": client.url(row["pdf"]),
            "xbrl": row["xbrl"], "split": split_of(doc_id, test_fraction),
            "files": [{"type": "pdf", "name": "pdf", "size": None}]}


def probe(client: TdnetClient, entries: List[Dict[str, Any]], progress: Optional[Callable[[str], None]] = None) -> None:
    for i, e in enumerate(entries, start=1):
        try:
            p = client.probe(e["pdf"])
            e["files"][0]["size"], e["files"][0]["how"] = p.size, p.how
        except Exception as exc:
            e["files"][0]["size"], e["files"][0]["error"] = None, str(exc)[:120]
        if progress and i % 10 == 0:
            progress(f"  probed {i}/{len(entries)}")


def business_days(start: datetime.date, end: datetime.date) -> List[datetime.date]:
    return [start + datetime.timedelta(days=i) for i in range((end - start).days + 1)
            if (start + datetime.timedelta(days=i)).weekday() < 5]


def build_plan(client: TdnetClient, days: Sequence[datetime.date], n: int, seed: int = 0, min_ifrs: int = 0,
               test_fraction: float = 0.5, allow_unknown: bool = False,
               exclude_codes: Sequence[str] = tuple(USED_CODES),
               progress: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
    rows = collect(client, days, allow_unknown, exclude_codes, progress)
    entries = [build_entry(r, client, test_fraction) for r in sample(rows, n, seed, min_ifrs)]
    probe(client, entries, progress)
    plan = {
        "source": "tdnet", "created": datetime.datetime.now().isoformat(timespec="seconds"),
        "filters": {"from": days[0].isoformat(), "to": days[-1].isoformat(), "n": n, "seed": seed, "min_ifrs": min_ifrs,
                    "test_fraction": test_fraction, "allow_unknown": allow_unknown,
                    "excluded_codes": sorted(exclude_codes), "eligible_found": len(rows),
                    "eligible_ifrs": sum(1 for r in rows if standard_of(r["title"]) == "IFRS")},
        "total_bytes_known": sum(f["size"] or 0 for e in entries for f in e["files"]),
        "unknown_sizes": sum(1 for e in entries for f in e["files"] if f["size"] is None),
        "entries": entries,
    }
    plan["approval_token"] = approval_token(plan)
    return plan


def _kb(size: Optional[int]) -> str:
    return "?" if size is None else f"{size / 1e6:.2f} MB"


def format_plan(plan: Dict[str, Any]) -> str:
    lines = [f"{'#':>3} {'split':<5} {'code':<6} {'company':<22} {'docID':<20} {'std':<7} {'date':<10} {'size':>8}  PDF URL"]
    for i, e in enumerate(plan["entries"], start=1):
        lines.append(f"{i:>3} {e['split']:<5} {e['secCode'][:4]:<6} {e['filerName'][:20]:<22} {e['docID']:<20} "
                     f"{e['standard']:<7} {e['date']:<10} {_kb(e['files'][0]['size']):>8}  {e['pdf_url']}")
    f = plan["filters"]
    ents = plan["entries"]
    lines += ["", f"{len(ents)} filings ({sum(e['split'] == 'test' for e in ents)} test / {sum(e['split'] == 'dev' for e in ents)} dev; "
                  f"{sum(e['standard'] == 'IFRS' for e in ents)} IFRS), one per company, chosen from {f['eligible_found']} eligible 決算短信 "
                  f"({f['eligible_ifrs']} IFRS) between {f['from']} and {f['to']}; seed {f['seed']}",
              f"filtered out: amendments (訂正/修正), REITs/funds, titles naming no accounting standard (ETFs), "
              f"and companies already used in development ({', '.join(f['excluded_codes'])})",
              f"known download size: {_kb(plan['total_bytes_known'])}   files with unknown size: {plan['unknown_sizes']}",
              f"approval token: {plan['approval_token']}"]
    return "\n".join(lines)
