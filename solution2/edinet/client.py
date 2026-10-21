"""
Minimal EDINET API v2 client.

    GET {base}/documents.json?date=YYYY-MM-DD&type=2         one day's filings, as `results` rows
    GET {base}/documents/{docID}?type=N                      N: 1 XBRL zip, 2 PDF, 5 CSV zip (per FSA material)

The API key is sent as the `Subscription-Key` query parameter. It is only ever read from the environment (or a
key file), is never written anywhere, and is scrubbed from every error message and log line this module produces.

Politeness: at most one request every `min_interval` seconds, and retries with back-off on 429/5xx. Nothing in
this module downloads a file unless `download()` is called; `probe()` reads only the response headers.
"""
import datetime
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

BASE_URL = "https://api.edinet-fsa.go.jp/api/v2"
KEY_ENV = "EDINET_API_KEY"
DOC_ID = re.compile(r"^[A-Za-z0-9_-]{6,20}$")
TYPE_NAMES = {1: "xbrl", 2: "pdf", 3: "attach", 4: "english", 5: "csv"}


class EdinetError(Exception):
    pass


def load_key(key_file: Optional[str] = None) -> str:
    """The key comes from the environment or a file, never from a command-line argument (shell history)."""
    key = os.environ.get(KEY_ENV, "").strip()
    if not key and key_file:
        key = Path(key_file).read_text(encoding="utf-8").strip()
    if not key:
        raise EdinetError(f"no API key: set the {KEY_ENV} environment variable (or pass --key-file)")
    return key


@dataclass
class Probe:
    status: int
    size: Optional[int]            # Content-Length, None if the server did not say
    content_type: str


class EdinetClient:
    def __init__(self, api_key: str, base_url: str = BASE_URL, session: Any = None, min_interval: float = 2.5,
                 max_retries: int = 3, timeout: float = 60.0, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic):
        if not api_key:
            raise EdinetError("an API key is required")
        self._key = api_key
        self.base_url = base_url.rstrip("/")
        if session is None:
            import requests
            session = requests.Session()
        self.session = session
        self.min_interval, self.max_retries, self.timeout = min_interval, max_retries, timeout
        self._sleep, self._clock = sleep, clock
        self._last = None
        self.requests_made = 0

    # ---- safety
    def _scrub(self, text: str) -> str:
        return str(text).replace(self._key, "***")

    def __repr__(self) -> str:                       # never show the key
        return f"EdinetClient(base_url={self.base_url!r}, key=***)"

    # ---- transport
    def _wait(self) -> None:
        if self._last is not None:
            gap = self.min_interval - (self._clock() - self._last)
            if gap > 0:
                self._sleep(gap)
        self._last = self._clock()

    def _get(self, path: str, params: Dict[str, Any], stream: bool = False):
        url = f"{self.base_url}/{path.lstrip('/')}"
        query = dict(params, **{"Subscription-Key": self._key})
        last_exc: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            self._wait()
            self.requests_made += 1
            try:
                resp = self.session.get(url, params=query, stream=stream, timeout=self.timeout,
                                        headers={"Accept": "*/*"})
            except Exception as exc:                 # connection errors: retry, then fail with the key scrubbed
                last_exc = EdinetError(self._scrub(f"{type(exc).__name__}: {exc}"))
                self._sleep(2 ** attempt)
                continue
            if resp.status_code in (429, 500, 502, 503, 504):
                last_exc = EdinetError(f"HTTP {resp.status_code} from {path}")
                self._sleep(2 ** attempt)
                continue
            if resp.status_code != 200:
                raise EdinetError(f"HTTP {resp.status_code} from {path}: {self._scrub(self._body_message(resp))}")
            return resp
        raise last_exc or EdinetError(f"request to {path} failed")

    @staticmethod
    def _body_message(resp) -> str:
        try:
            body = resp.json()
        except Exception:
            return ""
        if isinstance(body, dict):
            lowered = {str(k).lower(): v for k, v in body.items()}
            meta = lowered.get("metadata") if isinstance(lowered.get("metadata"), dict) else {}
            return str(lowered.get("message") or meta.get("message") or "")
        return ""

    def _check_json_error(self, resp) -> Dict[str, Any]:
        """EDINET can answer 200 with an error document, so look at the body, not just the status."""
        body = resp.json()
        lowered = {str(k).lower(): v for k, v in body.items()}         # the live API answers {"StatusCode": 401, ...}
        meta = lowered.get("metadata") if isinstance(lowered.get("metadata"), dict) else {}
        status = str(lowered.get("statuscode") or meta.get("status") or "200")
        if status != "200":
            raise EdinetError(f"EDINET error {status}: {self._scrub(self._body_message(resp))}")
        return body

    # ---- API
    def list_documents(self, date: datetime.date) -> List[Dict[str, Any]]:
        body = self._check_json_error(self._get("documents.json", {"date": date.isoformat(), "type": 2}))
        return [r for r in body.get("results", []) if r.get("docID")]

    def probe(self, doc_id: str, doc_type: int) -> Probe:
        """File size from the response headers only: the body is never read and the connection is closed."""
        if not DOC_ID.match(doc_id):
            raise EdinetError(f"suspicious document id {doc_id!r}")
        resp = self._get(f"documents/{doc_id}", {"type": doc_type}, stream=True)
        try:
            length = resp.headers.get("Content-Length")
            return Probe(resp.status_code, int(length) if length and length.isdigit() else None,
                         resp.headers.get("Content-Type", ""))
        finally:
            resp.close()

    def download(self, doc_id: str, doc_type: int, dest: Path, max_bytes: int = 200 * 1024 * 1024) -> int:
        """Stream a document to `dest`; checks that it really is a PDF/ZIP rather than a JSON error body."""
        if not DOC_ID.match(doc_id):
            raise EdinetError(f"suspicious document id {doc_id!r}")
        resp = self._get(f"documents/{doc_id}", {"type": doc_type}, stream=True)
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        written, head = 0, b""
        try:
            with open(tmp, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1 << 16):
                    if not chunk:
                        continue
                    if len(head) < 8:
                        head = (head + chunk)[:8]
                    written += len(chunk)
                    if written > max_bytes:
                        raise EdinetError(f"{doc_id}: larger than the {max_bytes:,}-byte limit")
                    f.write(chunk)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
        finally:
            resp.close()
        expected = b"%PDF" if doc_type == 2 else b"PK"
        if not head.startswith(expected):
            message = ""
            try:
                message = self._scrub(tmp.read_text(encoding="utf-8", errors="replace")[:200])
            except Exception:
                pass
            tmp.unlink(missing_ok=True)
            raise EdinetError(f"{doc_id}: expected a {TYPE_NAMES.get(doc_type, doc_type)} file, got: {message!r}")
        tmp.replace(dest)
        return written
