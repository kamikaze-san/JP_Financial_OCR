"""EDINET tooling tests. No network and no real API key: a fake HTTP session stands in for EDINET."""
import datetime
import io
import json
import zipfile
from pathlib import Path

import pytest

from solution2.edinet import plan as planmod
from solution2.edinet.client import EdinetClient, EdinetError, load_key
from solution2.edinet.download import download_plan, safe_extract_csvs
from solution2.edinet.xbrl_csv import balance_sheet_golds, golds_from_dir, read_rows

KEY = "SECRET-KEY-abc123"


class Resp:
    def __init__(self, status=200, headers=None, body=b"", obj=None):
        self.status_code, self.headers, self._body, self._obj = status, headers or {}, body, obj
        self.closed, self.body_read = False, False

    def json(self):
        if self._obj is None:
            raise ValueError("not json")
        return self._obj

    def iter_content(self, chunk_size=1):
        self.body_read = True
        yield self._body

    def close(self):
        self.closed = True


class Session:
    """Routes requests to a function(url, params) -> Resp and records everything."""
    def __init__(self, route):
        self.route, self.calls, self.responses = route, [], []

    def get(self, url, params=None, stream=False, timeout=None, headers=None):
        self.calls.append((url, dict(params or {})))
        resp = self.route(url, params or {})
        if isinstance(resp, Exception):
            raise resp
        self.responses.append(resp)
        return resp


def client(route, **kw):
    kw.setdefault("sleep", lambda s: None)
    return EdinetClient(KEY, session=Session(route), min_interval=0, **kw)


def pdf_bytes(n=500):
    return b"%PDF-1.7 " + b"x" * n


# ----------------------------------------------------------------------------- key and errors
def test_key_comes_from_the_environment_and_is_never_shown(monkeypatch, tmp_path):
    monkeypatch.delenv("EDINET_API_KEY", raising=False)
    with pytest.raises(EdinetError):
        load_key()
    monkeypatch.setenv("EDINET_API_KEY", KEY)
    assert load_key() == KEY
    assert KEY not in repr(client(lambda u, p: Resp()))


def test_the_key_is_scrubbed_from_every_error(tmp_path):
    boom = client(lambda u, p: ConnectionError(f"failed for https://x/documents.json?Subscription-Key={KEY}"), max_retries=1)
    with pytest.raises(EdinetError) as e1:
        boom.list_documents(datetime.date(2025, 6, 25))
    assert KEY not in str(e1.value)
    leaky = client(lambda u, p: Resp(401, obj={"message": f"invalid key {KEY}"}))
    with pytest.raises(EdinetError) as e2:
        leaky.list_documents(datetime.date(2025, 6, 25))
    assert KEY not in str(e2.value) and "401" in str(e2.value)


def test_a_200_response_carrying_an_error_document_is_an_error():
    c = client(lambda u, p: Resp(200, obj={"statusCode": 401, "message": "Access denied due to invalid subscription key"}))
    with pytest.raises(EdinetError, match="401"):
        c.list_documents(datetime.date(2025, 6, 25))


def test_the_live_error_shape_with_a_capital_S_is_recognised():
    # captured from the real API (no key): HTTP 200 with this body
    c = client(lambda u, p: Resp(200, obj={"StatusCode": 401, "message": "Access denied due to invalid subscription key."}))
    with pytest.raises(EdinetError, match="401"):
        c.list_documents(datetime.date(2025, 6, 25))


def test_list_documents_sends_date_type_and_key_and_keeps_only_rows_with_ids():
    c = client(lambda u, p: Resp(200, obj={"metadata": {"status": "200"},
                                           "results": [{"docID": "S100AAAA"}, {"docTypeCode": "120"}, {"docID": "S100BBBB"}]}))
    rows = c.list_documents(datetime.date(2025, 6, 25))
    assert [r["docID"] for r in rows] == ["S100AAAA", "S100BBBB"]
    url, params = c.session.calls[0]
    assert url.endswith("/documents.json") and params["date"] == "2025-06-25" and params["type"] == 2
    assert params["Subscription-Key"] == KEY


def test_requests_are_spaced_and_5xx_is_retried():
    t = {"now": 0.0}
    sent = []
    attempts = {"n": 0}

    def route(u, p):
        attempts["n"] += 1
        sent.append(t["now"])
        return Resp(503) if attempts["n"] == 1 else Resp(200, obj={"results": []})
    c = EdinetClient(KEY, session=Session(route), min_interval=2.5, sleep=lambda s: t.update(now=t["now"] + s),
                     clock=lambda: t["now"])
    c.list_documents(datetime.date(2025, 6, 25))
    c.list_documents(datetime.date(2025, 6, 26))
    assert attempts["n"] == 3                                      # the 503 was retried
    assert all(b - a >= 2.5 for a, b in zip(sent, sent[1:]))        # never closer than min_interval


# ----------------------------------------------------------------------------- probing and downloading
def test_probe_reads_only_headers():
    c = client(lambda u, p: Resp(200, headers={"Content-Length": "4096", "Content-Type": "application/pdf"}, body=pdf_bytes()))
    assert c.probe("S100AAAA", 2).size == 4096
    r = c.session.responses[0]
    assert r.closed and not r.body_read
    assert client(lambda u, p: Resp(200, body=pdf_bytes())).probe("S100AAAA", 2).size is None


def test_suspicious_document_ids_are_refused():
    c = client(lambda u, p: Resp())
    for bad in ("../../etc/passwd", "a b", "x"):
        with pytest.raises(EdinetError):
            c.probe(bad, 2)
    assert c.session.calls == []


def test_download_checks_the_file_type_and_leaves_nothing_behind_on_error(tmp_path):
    good = client(lambda u, p: Resp(200, body=pdf_bytes()))
    assert good.download("S100AAAA", 2, tmp_path / "a.pdf") > 0 and (tmp_path / "a.pdf").read_bytes().startswith(b"%PDF")
    bad = client(lambda u, p: Resp(200, body=b'{"message":"not found"}'))
    with pytest.raises(EdinetError, match="expected a pdf"):
        bad.download("S100AAAA", 2, tmp_path / "b.pdf")
    assert not (tmp_path / "b.pdf").exists() and not list(tmp_path.glob("*.part"))
    big = client(lambda u, p: Resp(200, body=pdf_bytes(5000)))
    with pytest.raises(EdinetError, match="limit"):
        big.download("S100AAAA", 2, tmp_path / "c.pdf", max_bytes=100)
    assert not (tmp_path / "c.pdf").exists()


# ----------------------------------------------------------------------------- planning
def row(i, code=None, **kw):
    base = {"docID": f"S100{i:04d}", "edinetCode": code or f"E{i:05d}", "secCode": f"{1000 + i}0", "filerName": f"Company {i}",
            "docTypeCode": "120", "withdrawalStatus": "0", "pdfFlag": "1", "csvFlag": "1", "periodEnd": "2025-03-31"}
    return {**base, **kw}


def test_eligibility_filters():
    keep = row(1)
    assert planmod.eligible(keep, ["120"])
    assert not planmod.eligible(row(2, withdrawalStatus="1"), ["120"])
    assert not planmod.eligible(row(3, csvFlag="0"), ["120"])
    assert not planmod.eligible(row(4, pdfFlag="0"), ["120"])
    assert not planmod.eligible(row(5, secCode=None), ["120"])
    assert not planmod.eligible(row(6, docTypeCode="140"), ["120"])
    assert planmod.eligible(row(7, secCode=None), ["120"], listed_only=False)


def test_sampling_is_reproducible_and_takes_one_filing_per_company():
    rows = [row(i) for i in range(30)] + [row(100, code="E00001"), row(101, code="E00001")]
    a, b = planmod.sample(rows, 12, seed=7), planmod.sample(rows, 12, seed=7)
    assert [r["docID"] for r in a] == [r["docID"] for r in b]
    assert len({r["edinetCode"] for r in a}) == 12
    assert [r["docID"] for r in planmod.sample(rows, 12, seed=8)] != [r["docID"] for r in a]


def test_dev_test_split_is_fixed_by_the_document_id():
    ids = [f"S100{i:04d}" for i in range(400)]
    first = [planmod.split_of(i) for i in ids]
    assert first == [planmod.split_of(i) for i in ids]
    assert 140 < first.count("test") < 260 and set(first) == {"dev", "test"}


def route_for(rows, sizes=None):
    def route(url, params):
        if url.endswith("documents.json"):
            return Resp(200, obj={"results": rows})
        doc = url.rsplit("/", 1)[1]
        size = (sizes or {}).get((doc, params["type"]), 1234)
        return Resp(200, headers={"Content-Length": str(size)}, body=pdf_bytes() if params["type"] == 2 else b"PK\x03\x04")
    return route


def test_building_a_plan_lists_and_probes_but_never_reads_a_body():
    rows = [row(i) for i in range(10)]
    c = client(route_for(rows, {("S1000003", 2): 2_000_000}))
    plan = planmod.build_plan(c, [datetime.date(2025, 6, 25)], n=5, seed=1)
    assert len(plan["entries"]) == 5 and plan["approval_token"] and plan["total_bytes_known"] > 0
    assert not any(r.body_read for r in c.session.responses)                 # nothing was downloaded
    text = planmod.format_plan(plan)
    assert "docID" in text and plan["approval_token"] in text and "Company" in text


def test_the_approval_token_identifies_exactly_this_list():
    rows = [row(i) for i in range(10)]
    p1 = planmod.build_plan(client(route_for(rows)), [datetime.date(2025, 6, 25)], n=5, seed=1)
    p2 = planmod.build_plan(client(route_for(rows)), [datetime.date(2025, 6, 25)], n=5, seed=1)
    p3 = planmod.build_plan(client(route_for(rows)), [datetime.date(2025, 6, 25)], n=5, seed=2)
    assert p1["approval_token"] == p2["approval_token"] != p3["approval_token"]
    p1["entries"].pop()
    assert planmod.approval_token(p1) != p2["approval_token"]


# ----------------------------------------------------------------------------- gated download
def make_zip(members):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in members.items():
            z.writestr(name, data)
    return buf.getvalue()


def test_nothing_is_downloaded_without_the_matching_token_or_over_the_cap(tmp_path):
    rows = [row(i) for i in range(4)]
    c = client(route_for(rows))
    plan = planmod.build_plan(c, [datetime.date(2025, 6, 25)], n=3)
    before = len(c.session.calls)
    with pytest.raises(EdinetError, match="token"):
        download_plan(c, plan, tmp_path, "wrong-token")
    with pytest.raises(EdinetError, match="cap"):
        download_plan(c, plan, tmp_path, plan["approval_token"], max_total_bytes=10)
    assert len(c.session.calls) == before and not list(tmp_path.iterdir())


def test_download_fetches_the_approved_files_extracts_csv_and_is_idempotent(tmp_path):
    zipped = make_zip({"data.csv": b"a,b\n1,2\n", "readme.txt": b"skip", "../../escape.csv": b"x,y\n"})

    def route(url, params):
        if url.endswith("documents.json"):
            return Resp(200, obj={"results": [row(1)]})
        return Resp(200, headers={"Content-Length": "10"}, body=pdf_bytes() if params["type"] == 2 else zipped)
    c = client(route)
    plan = planmod.build_plan(c, [datetime.date(2025, 6, 25)], n=1)
    result = download_plan(c, plan, tmp_path, plan["approval_token"])
    assert result == {"downloaded": 1, "failed": {}}
    doc = tmp_path / "S1000001"
    assert (doc / "S1000001.pdf").exists() and (doc / "S1000001_csv.zip").exists()
    assert sorted(p.name for p in (doc / "csv").iterdir()) == ["data.csv", "escape.csv"]   # members flattened, txt skipped
    assert not (tmp_path.parent / "escape.csv").exists() and not (tmp_path / "escape.csv").exists()
    ledger = json.loads((tmp_path / "downloads.json").read_text(encoding="utf-8"))
    assert ledger["S1000001"]["pdf"]["sha256"] and ledger["S1000001"]["split"] in ("dev", "test")
    n = len(c.session.calls)
    download_plan(c, plan, tmp_path, plan["approval_token"])
    assert len(c.session.calls) == n                                         # second run fetched nothing


def test_a_failed_file_is_reported_not_fatal(tmp_path):
    def route(url, params):
        if url.endswith("documents.json"):
            return Resp(200, obj={"results": [row(1), row(2)]})
        if "S1000001" in url and params["type"] == 2:
            return Resp(200, body=b'{"message":"gone"}', headers={"Content-Length": "5"})
        return Resp(200, headers={"Content-Length": "9"}, body=pdf_bytes() if params["type"] == 2 else make_zip({"a.csv": b"x,y\n"}))
    c = client(route)
    plan = planmod.build_plan(c, [datetime.date(2025, 6, 25)], n=2)
    result = download_plan(c, plan, tmp_path, plan["approval_token"])
    assert result["downloaded"] == 1 and "S1000001" in result["failed"]


def test_zip_bombs_are_refused(tmp_path):
    z = tmp_path / "x.zip"
    z.write_bytes(make_zip({f"f{i}.csv": b"1" for i in range(300)}))
    with pytest.raises(EdinetError):
        safe_extract_csvs(z, tmp_path / "out")


# ----------------------------------------------------------------------------- XBRL CSV
HEADER = ["要素ID", "項目名", "コンテキストID", "相対年度", "連結・個別", "期間・時点", "ユニットID", "単位", "値"]


def fact(element, ctx, value, scope="連結", unit="JPY", ptype="時点", label="項目"):
    return [element, label, ctx, "当期末", scope, ptype, unit, "円", str(value)]


def write_csv(path, rows, encoding="utf-16", sep="\t"):
    lines = [sep.join(HEADER)] + [sep.join(r) for r in rows]
    path.write_bytes("\r\n".join(lines).encode(encoding))


def test_utf16_tab_csv_gives_consolidated_and_individual_golds(tmp_path):
    rows = [fact("jppfs_cor:Assets", "CurrentYearInstant", 7_534_110_000), fact("jppfs_cor:Assets", "Prior1YearInstant", 5_135_249_000),
            fact("jppfs_cor:Assets", "CurrentYearInstant_NonConsolidatedMember", 6_000_000_000, "個別"),
            fact("jppfs_cor:RetainedEarnings", "CurrentYearInstant_RetainedEarningsMember", 5, "連結"),     # dimensional: excluded
            fact("jppfs_cor:Assets", "CurrentYearDuration", 1, ptype="期間"),                               # duration: excluded
            fact("jppfs_cor:NumberOfShares", "CurrentYearInstant", 99, unit="shares"),                      # not yen: excluded
            fact("jpdei_cor:Foo", "CurrentYearInstant", 3)]                                                  # not financial
    write_csv(tmp_path / "x.csv", rows)
    golds = {g.scope: g for g in balance_sheet_golds(read_rows(tmp_path / "x.csv"))}
    assert set(golds) == {"consolidated", "individual"} and golds["consolidated"].standard == "J-GAAP"
    cons = golds["consolidated"]
    assert cons.contexts_by_recency() == ["CurrentYearInstant", "Prior1YearInstant"]
    assert cons.values("CurrentYearInstant") == [7_534_110_000] and cons.values("Prior1YearInstant") == [5_135_249_000]
    assert golds["individual"].values("CurrentYearInstant_NonConsolidatedMember") == [6_000_000_000]


def test_ifrs_is_detected_and_other_encodings_parse(tmp_path):
    ifrs = [fact("jpigp_cor:AssetsIFRS", "CurrentYearInstant", 1_075_007_000_000)]
    for i, (enc, sep) in enumerate([("utf-16", "\t"), ("utf-8-sig", ","), ("cp932", "\t")]):
        write_csv(tmp_path / f"f{i}.csv", ifrs, enc, sep)
    for i in range(3):
        (g,) = balance_sheet_golds(read_rows(tmp_path / f"f{i}.csv"))
        assert g.standard == "IFRS" and g.values("CurrentYearInstant") == [1_075_007_000_000]
    assert golds_from_dir(tmp_path)[0].standard == "IFRS"


def test_unexpected_headers_fall_back_to_column_positions(tmp_path):
    p = tmp_path / "nohdr.csv"
    p.write_bytes("\r\n".join("\t".join(r) for r in [fact("jppfs_cor:Assets", "CurrentYearInstant", 42_000_000)]).encode("utf-16"))
    (g,) = balance_sheet_golds(read_rows(p))
    assert g.values("CurrentYearInstant") == [42_000_000]


def test_a_file_with_only_notes_yields_no_gold(tmp_path):
    write_csv(tmp_path / "n.csv", [fact("jpcrp_cor:Something", "CurrentYearInstant", 5)])
    assert golds_from_dir(tmp_path) == []


# ----------------------------------------------------------------------------- matching a page to the facts
def test_page_numbers_are_checked_against_the_facts_with_unit_tolerance():
    from solution2.edinet.match import explain
    from solution2.edinet.xbrl_csv import BsGold, Fact
    from solution2.harness.corpus import load_corpus
    page = load_corpus()["baseline"]                                       # 千円; col 0 = 2025-03-31, col 1 = 2026-03-31
    from solution2.verify.parsing import parse_cell
    def facts_for(col, shift=0):
        return [Fact("e", "", "x", parse_cell(r.values[col]).value * 1000 + 500 + shift)       # truncated yen amounts
                for r in page.rows if len(r.values) > col and parse_cell(r.values[col]).value]
    gold = BsGold("consolidated", "J-GAAP", {"CurrentYearInstant": facts_for(1), "Prior1YearInstant": facts_for(0)})
    n, ok, _ = explain(page, gold)
    assert n > 60 and ok == n                                              # newest column <-> Current, older <-> Prior1
    swapped = BsGold("consolidated", "J-GAAP", {"CurrentYearInstant": facts_for(0), "Prior1YearInstant": facts_for(1)})
    n2, ok2, _ = explain(page, swapped)
    assert ok2 < n2 * 0.2                                                  # wrong period mapping is not explained
    gold.by_context["CurrentYearInstant"][0] = Fact("e", "", "x", 123_000_000_000)           # one number disagrees
    n3, ok3, misses = explain(page, gold)
    assert ok3 == n3 - 1 and len(misses) == 1


# ----------------------------------------------------------------------------- scoring helpers and CLI
def test_scoring_classes():
    from solution2.edinet.eval import classify_detection, classify_repair, code_fingerprint
    assert classify_detection(False, False, True) == "true_trust" and classify_detection(False, True, True) == "false_alarm"
    assert classify_detection(True, True, False) == "true_flag" and classify_detection(True, False, False) == "SILENT_ERROR"
    assert classify_detection(True, False, True) == "label_gap"
    assert classify_repair(3, 0, True, True) == "fixed" and classify_repair(3, 2, True, False) == "SILENT_WRONG"
    assert classify_repair(3, 5, False, False) == "worse" and classify_repair(3, 1, False, False) == "improved"
    assert code_fingerprint() == code_fingerprint() and len(code_fingerprint()) == 12


def test_plan_command_prints_the_list_and_says_nothing_was_downloaded(monkeypatch, tmp_path, capsys):
    from solution2.edinet import __main__ as cli
    rows = [row(i) for i in range(6)]
    monkeypatch.setattr(cli, "_client", lambda args: client(route_for(rows)))
    code = cli.main(["plan", "--dates", "2025-06-25", "--n", "3", "--out", str(tmp_path / "plan.json")])
    out = capsys.readouterr().out
    assert code == 0 and "NOTHING HAS BEEN DOWNLOADED" in out and "approval token" in out
    plan = json.loads((tmp_path / "plan.json").read_text(encoding="utf-8"))
    assert KEY not in json.dumps(plan) and plan["approval_token"] in out


# ----------------------------------------------------------------------------- prepare, end to end (real PDF, synthetic CSV)
CALBEE = Path(__file__).resolve().parents[3] / "dataset/benchmark_suite/company_02_calbee/balance_sheet.pdf"


@pytest.mark.skipif(not CALBEE.exists(), reason="Calbee PDF not available")
def test_prepare_matches_pdf_pages_to_xbrl_facts_and_flags_a_wrong_fact(tmp_path):
    from solution2.edinet.match import PageMatch, match_pdf, prepare_entry
    from solution2.harness.silver import silver_gold
    from solution2.verify.parsing import parse_cell
    facts = []
    for page in (0, 1):                                                   # 百万円 statement: newest column = interim, older = prior year-end
        s = silver_gold(CALBEE, page)
        for r in s.rows:
            for col, ctx in ((1, "InterimInstant"), (0, "Prior1YearInstant")):
                v = parse_cell(r.values[col]).value if col < len(r.values) else None
                if v:
                    facts.append(fact("jppfs_cor:X", ctx, v * 1_000_000 + 300_000))
    write_csv(tmp_path / "main.csv", facts)
    (gold,) = golds_from_dir(tmp_path)
    matches = [m for m in match_pdf(CALBEE, [gold]) if m.n_cells]
    assert {m.page for m in matches} == {0, 1}
    assert all(m.explained == 1.0 and m.usable and m.v2_trusted and m.scope == "consolidated" for m in matches)
    manifest = prepare_entry(CALBEE, [gold], tmp_path / "prepared", {"docID": "S1000001", "filerName": "Calbee", "split": "dev"})
    assert [m["id"] for m in manifest] == ["S1000001_p1", "S1000001_p2"]
    assert Path(manifest[0]["silver"]).exists() and manifest[0]["unit_multiplier"] == 1_000_000
    # a filing whose XBRL disagrees with the page (a wrong fact) is NOT usable as gold
    gold.by_context["InterimInstant"][0].value += 50_000_000_000
    gold.by_context["Prior1YearInstant"] = gold.by_context["Prior1YearInstant"][:5]
    assert not any(m.usable for m in match_pdf(CALBEE, [gold]) if m.n_cells)


def test_the_cli_without_a_key_fails_cleanly_and_touches_nothing(monkeypatch, capsys):
    from solution2.edinet import __main__ as cli
    monkeypatch.delenv("EDINET_API_KEY", raising=False)
    assert cli.main(["plan", "--dates", "2025-06-25"]) == 2
    assert "EDINET_API_KEY" in capsys.readouterr().err
