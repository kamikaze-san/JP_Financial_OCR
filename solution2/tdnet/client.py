"""
Client for TDnet's public disclosure viewer (no API key).

    GET https://www.release.tdnet.info/inbs/I_list_{page:03d}_{YYYYMMDD}.html     one page (100 rows) of a day's disclosures
    GET https://www.release.tdnet.info/inbs/{name}.pdf                           the disclosure PDF

The viewer only keeps roughly the last 31 days. Row markup (checked against the live site): one <tr> per disclosure with
cells kjTime, kjCode, kjName, kjTitle (an <a href="….pdf">), kjXbrl (an <a href="….zip"> when XBRL exists), kjPlace.

Politeness and honesty: a descriptive User-Agent (the server answers HEAD 403 to curl's default agent but 200 to an honest
one), at most one request per `min_interval` seconds, back-off on 429/5xx. `probe()` reads headers only. Nothing is downloaded
unless `download()` is called, and the plan/approve gate sits in front of that.
"""
import datetime
import html as htmllib
import math
import re
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

BASE_URL = "https://www.release.tdnet.info/inbs"
USER_AGENT = "solution2-eval/1.0 (research; Japanese balance-sheet parsing benchmark)"
PDF_NAME = re.compile(r"^\d{18}\.pdf$")
ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)


class TdnetError(Exception):
    pass


@dataclass
class Probe:
    size: Optional[int]
    how: str                      # head | range | none


def _cell(row: str, cls: str) -> str:
    m = re.search(rf'<td[^>]*class="[^"]*\b{cls}\b[^"]*"[^>]*>(.*?)</td>', row, re.S)
    return m.group(1) if m else ""


def parse_list(html: str, day: datetime.date) -> List[Dict[str, Any]]:
    """Rows of one list page."""
    out = []
    for row in ROW.findall(html):
        if "kjCode" not in row:
            continue
        link = re.search(r'<a[^>]*href="([^"]+\.pdf)"[^>]*>(.*?)</a>', _cell(row, "kjTitle"), re.S)
        if not link:
            continue
        zip_ = re.search(r'href="([^"]+\.zip)"', _cell(row, "kjXbrl"))
        norm = lambda s: unicodedata.normalize("NFKC", htmllib.unescape(re.sub(r"<[^>]+>", "", s))).strip()
        out.append({"date": day.isoformat(), "time": norm(_cell(row, "kjTime")), "code": norm(_cell(row, "kjCode")),
                    "name": norm(_cell(row, "kjName")), "title": norm(link.group(2)), "pdf": link.group(1),
                    "xbrl": zip_.group(1) if zip_ else ""})
    return out


class TdnetClient:
    def __init__(self, base_url: str = BASE_URL, session: Any = None, min_interval: float = 1.5, max_retries: int = 3,
                 timeout: float = 60.0, user_agent: str = USER_AGENT, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic):
        if session is None:
            import requests
            session = requests.Session()
        self.base_url, self.session = base_url.rstrip("/"), session
        self.min_interval, self.max_retries, self.timeout = min_interval, max_retries, timeout
        self.headers = {"User-Agent": user_agent}
        self._sleep, self._clock, self._last = sleep, clock, None
        self.requests_made = 0

    def url(self, name: str) -> str:
        return f"{self.base_url}/{name}"

    def _wait(self) -> None:
        if self._last is not None:
            gap = self.min_interval - (self._clock() - self._last)
            if gap > 0:
                self._sleep(gap)
        self._last = self._clock()

    def _request(self, method: str, name: str, extra_headers: Optional[Dict[str, str]] = None, stream: bool = False):
        last: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            self._wait()
            self.requests_made += 1
            try:
                resp = getattr(self.session, method)(self.url(name), headers={**self.headers, **(extra_headers or {})},
                                                     timeout=self.timeout, **({"stream": stream} if method == "get" else {}))
            except Exception as exc:
                last = TdnetError(f"{type(exc).__name__}: {exc}")
                self._sleep(2 ** attempt)
                continue
            if resp.status_code in (429, 500, 502, 503, 504):
                last = TdnetError(f"HTTP {resp.status_code} for {name}")
                self._sleep(2 ** attempt)
                continue
            return resp
        raise last or TdnetError(f"request for {name} failed")

    # ---- listings
    def list_day(self, day: datetime.date) -> List[Dict[str, Any]]:
        """Every disclosure of one day (all pages). An unavailable day (weekend, holiday, older than the window) is []."""
        rows: List[Dict[str, Any]] = []
        page, pages = 1, 1
        while page <= pages:
            resp = self._request("get", f"I_list_{page:03d}_{day:%Y%m%d}.html")
            if resp.status_code == 404:
                break
            if resp.status_code != 200:
                raise TdnetError(f"HTTP {resp.status_code} for the {day} list (page {page})")
            html = resp.content.decode("utf-8", errors="replace")
            rows += parse_list(html, day)
            total = re.search(r"全\s*(\d+)\s*件", html)
            if page == 1 and total:
                pages = math.ceil(int(total.group(1)) / 100)
            page += 1
        return rows

    # ---- PDFs
    def probe(self, pdf_name: str) -> Probe:
        """File size from headers only (HEAD; a one-byte range request if HEAD is refused)."""
        if not PDF_NAME.match(pdf_name):
            raise TdnetError(f"unexpected PDF name {pdf_name!r}")
        resp = self._request("head", pdf_name)
        if resp.status_code == 200 and str(resp.headers.get("Content-Length", "")).isdigit():
            return Probe(int(resp.headers["Content-Length"]), "head")
        resp = self._request("get", pdf_name, {"Range": "bytes=0-0"}, stream=True)
        try:
            m = re.search(r"/(\d+)$", str(resp.headers.get("Content-Range", "")))
            if resp.status_code == 206 and m:
                return Probe(int(m.group(1)), "range")
            return Probe(None, "none")
        finally:
            resp.close()

    def download(self, pdf_name: str, dest: Path, max_bytes: int = 100 * 1024 * 1024) -> int:
        if not PDF_NAME.match(pdf_name):
            raise TdnetError(f"unexpected PDF name {pdf_name!r}")
        resp = self._request("get", pdf_name, stream=True)
        if resp.status_code != 200:
            raise TdnetError(f"HTTP {resp.status_code} for {pdf_name}")
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".part")
        written, head = 0, b""
        try:
            with open(tmp, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1 << 16):
                    if not chunk:
                        continue
                    head = (head + chunk)[:8] if len(head) < 8 else head
                    written += len(chunk)
                    if written > max_bytes:
                        raise TdnetError(f"{pdf_name}: larger than the {max_bytes:,}-byte limit")
                    f.write(chunk)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
        finally:
            resp.close()
        if not head.startswith(b"%PDF"):
            tmp.unlink(missing_ok=True)
            raise TdnetError(f"{pdf_name}: not a PDF")
        tmp.replace(dest)
        return written
