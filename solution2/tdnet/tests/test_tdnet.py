"""TDnet source tests. No network: a fake session serves pages shaped like the live site's markup."""
import datetime
import json
import shutil
from pathlib import Path

import pytest

from solution2.tdnet import plan as planmod
from solution2.tdnet.client import PDF_NAME, USER_AGENT, TdnetClient, TdnetError, parse_list
from solution2.tdnet.download import download_plan
from solution2.tdnet.prepare import bs_candidate_pages, prepare_all

ROOT = Path(__file__).resolve().parents[3]
CALBEE = ROOT / "dataset/benchmark_suite/company_02_calbee/balance_sheet.pdf"
NIKON = ROOT / "dataset/benchmark_suite/company_04_nikon/balance_sheet.pdf"
DAY = datetime.date(2026, 10, 15)


def pdf_name(i, day="20261015"):
    return f"1401{day}{i:06d}.pdf"


def tr(i, code, name, title, zip_=False, odd=True):
    cls = "oddnew" if odd else "evennew"
    xbrl = f'<div class="xbrl-mask"><div class="xbrl-button"><a class="style002" href="0812{pdf_name(i)[4:-4]}.zip">XBRL</a></div></div>' if zip_ else " "
    return (f'<tr> <td class="{cls}-L kjTime" noWrap>17:45</td> <td class="{cls}-M kjCode" noWrap>{code}</td> '
            f'<td class="{cls}-M kjName" noWrap>{name} </td> <td class="{cls}-M kjTitle" align="left"><a href="{pdf_name(i)}" target="_blank">{title}</a></td> '
            f'<td class="{cls}-M kjXbrl" noWrap align="center">{xbrl}</td> <td class="{cls}-M kjPlace" noWrap align="left">東 </td> '
            f'<td class="{cls}-R kjHistroy" align="left"> </td> </tr>')


def page(rows, total=None):
    return f"<html><body>全 {total if total is not None else len(rows)} 件<table><tr><th>時刻</th></tr>{''.join(rows)}</table></body></html>"


class Resp:
    def __init__(self, status=200, headers=None, body=b""):
        self.status_code, self.headers, self.content, self.closed, self.body_read = status, headers or {}, body, False, False
        self._body = body

    def iter_content(self, chunk_size=1):
        self.body_read = True
        yield self._body

    def close(self):
        self.closed = True


class Session:
    def __init__(self, route):
        self.route, self.calls, self.responses = route, [], []

    def _do(self, method, url, headers=None, **kw):
        self.calls.append((method, url, dict(headers or {})))
        r = self.route(method, url.rsplit("/", 1)[1], dict(headers or {}))
        self.responses.append(r)
        return r

    def get(self, url, headers=None, **kw):
        return self._do("GET", url, headers)

    def head(self, url, headers=None, **kw):
        return self._do("HEAD", url, headers)


def client(route, **kw):
    kw.setdefault("sleep", lambda s: None)
    return TdnetClient(session=Session(route), min_interval=0, **kw)


def pdf_bytes(n=300):
    return b"%PDF-1.7 " + b"x" * n


ROWS = [tr(1, "80310", "三井物産", "（経過開示）買収の決定"),
        tr(2, "31890", "ＡＮＡＰ", "2026年8月期決算短信〔日本基準〕（連結）", zip_=True, odd=False),
        tr(3, "60850", "Ｇ－アーキテクツＳＪ", "2027年２月期第２四半期（中間期）決算短信〔日本基準〕（連結）"),
        tr(4, "32260", "Ｒ－三井不アコモ", "2026年８月期 決算短信（ＲＥＩＴ）"),
        tr(5, "99999", "テスト商事", "（訂正）「2026年８月期　決算短信〔日本基準〕（連結）」の一部訂正について"),
        tr(6, "40640", "テスト化学", "2027年3月期第2四半期（中間期）決算短信〔IFRS〕（連結）")]


# ----------------------------------------------------------------------------- parsing and listing
def test_rows_are_parsed_from_the_live_markup_and_normalised():
    rows = parse_list(page(ROWS), DAY)
    assert len(rows) == 6
    anap = rows[1]
    assert anap["code"] == "31890" and anap["name"] == "ANAP" and anap["pdf"] == pdf_name(2)
    assert anap["xbrl"].endswith(".zip") and rows[0]["xbrl"] == ""
    assert anap["title"] == "2026年8月期決算短信〔日本基準〕(連結)" or "決算短信" in anap["title"]
    assert rows[3]["name"].startswith("R-")                                  # full-width 'Ｒ－' normalised


def test_a_day_is_listed_across_all_pages_and_an_unavailable_day_is_empty():
    def route(method, name, headers):
        if name == "I_list_001_20261015.html":
            return Resp(body=page(ROWS[:3], total=250).encode())
        if name == "I_list_002_20261015.html":
            return Resp(body=page(ROWS[3:], total=250).encode())
        if name == "I_list_003_20261015.html":
            return Resp(body=page([], total=250).encode())
        return Resp(404)
    c = client(route)
    assert len(c.list_day(DAY)) == 6
    assert [x[1].rsplit("/", 1)[1] for x in c.session.calls] == [f"I_list_00{i}_20261015.html" for i in (1, 2, 3)]
    assert client(lambda m, n, h: Resp(404)).list_day(datetime.date(2026, 9, 5)) == []


def test_requests_identify_themselves_honestly():
    c = client(lambda m, n, h: Resp(404))
    c.list_day(DAY)
    ua = c.session.calls[0][2]["User-Agent"]
    assert ua == USER_AGENT and "Mozilla" not in ua and "research" in ua


# ----------------------------------------------------------------------------- filters and sampling
def test_only_original_tanshin_of_operating_companies_are_eligible():
    rows = {r["code"]: r for r in parse_list(page(ROWS), DAY)}
    assert [planmod.eligible(rows[c]) for c in ("80310", "31890", "60850", "32260", "99999", "40640")] == \
           [False, True, True, False, False, True]
    assert planmod.eligible(rows["60850"])                                     # a Growth-market name 'Ｇ－…' is a real company
    for title in ("決算短信（インフラファンド）", "決算短信〔不動産投資信託〕", "ＥＴＦ 決算短信"):
        assert not planmod.eligible({"title": title, "name": "X"})
    assert not planmod.eligible({"title": "2027年3月期 決算短信の一部修正", "name": "X"})


def test_etfs_and_companies_already_used_in_development_are_left_out():
    rows = parse_list(page([
        tr(1, "15790", "日経レバレッジダブル", "2026年9月期決算短信"),                       # ETF: no accounting standard in the title
        tr(2, "77310", "ニコン", "2027年3月期第2四半期決算短信〔IFRS〕（連結）"),               # used while developing the verifier
        tr(3, "22290", "カルビー", "2027年3月期第2四半期決算短信〔日本基準〕（連結）"),
        tr(4, "31890", "ＡＮＡＰ", "2026年8月期決算短信〔日本基準〕（連結）")]), DAY)
    c = client(lambda m, n, h: Resp(200, body=page([]).encode()))
    c.list_day = lambda d: rows
    kept = planmod.collect(c, [DAY])
    assert [r["code"] for r in kept] == ["31890"]
    assert {r["code"] for r in planmod.collect(c, [DAY], allow_unknown=True)} == {"15790", "31890"}
    assert {r["code"] for r in planmod.collect(c, [DAY], exclude_codes=())} == {"77310", "22290", "31890"}


def test_standard_is_read_from_the_title():
    assert planmod.standard_of("2027年3月期決算短信〔IFRS〕（連結）") == "IFRS"
    assert planmod.standard_of("決算短信〔日本基準〕（連結）") == "J-GAAP"
    assert planmod.standard_of("決算短信〔米国基準〕（連結）") == "US-GAAP"
    assert planmod.standard_of("決算短信") == "unknown"
    assert planmod.standard_of("米国社債ヘッジ有 決算短信") == "unknown"         # an ETF's name mentions 米国; that is not a standard


def rows_for(n_jgaap, n_ifrs):
    out = []
    for i in range(n_jgaap):
        out.append({"date": "2026-10-15", "time": "", "code": f"{1000 + i}0", "name": f"J{i}", "pdf": pdf_name(i), "xbrl": "",
                    "title": "2027年3月期第2四半期決算短信〔日本基準〕（連結）"})
    for i in range(n_ifrs):
        out.append({"date": "2026-10-15", "time": "", "code": f"{5000 + i}0", "name": f"I{i}", "pdf": pdf_name(500 + i), "xbrl": "",
                    "title": "2027年3月期第2四半期決算短信〔IFRS〕（連結）"})
    return out


def test_sampling_takes_one_filing_per_company_reproducibly_with_an_ifrs_quota():
    rows = rows_for(60, 10) + [dict(rows_for(1, 0)[0], pdf=pdf_name(900))]      # a second filing by company 10000
    a = planmod.sample(rows, 20, seed=7, min_ifrs=5)
    assert [r["pdf"] for r in a] == [r["pdf"] for r in planmod.sample(rows, 20, seed=7, min_ifrs=5)]
    assert len({r["code"] for r in a}) == 20
    assert sum(planmod.standard_of(r["title"]) == "IFRS" for r in a) >= 5
    assert [r["pdf"] for r in planmod.sample(rows, 20, seed=8, min_ifrs=5)] != [r["pdf"] for r in a]
    assert sum(planmod.standard_of(r["title"]) == "IFRS" for r in planmod.sample(rows_for(30, 2), 10, 1, min_ifrs=5)) == 2


def test_business_days_skip_weekends():
    assert [d.day for d in planmod.business_days(datetime.date(2026, 10, 9), datetime.date(2026, 10, 14))] == [9, 12, 13, 14]


# ----------------------------------------------------------------------------- sizes, downloads, gating
def test_probe_prefers_head_and_never_reads_a_body():
    c = client(lambda m, n, h: Resp(200, {"Content-Length": "518019"}, pdf_bytes()))
    p = c.probe(pdf_name(1))
    assert (p.size, p.how) == (518019, "head") and not any(r.body_read for r in c.session.responses)


def test_probe_falls_back_to_a_one_byte_range_request_when_head_is_refused():
    def route(method, name, headers):
        if method == "HEAD":
            return Resp(403)
        assert headers.get("Range") == "bytes=0-0"
        return Resp(206, {"Content-Range": "bytes 0-0/518019"}, b"%")
    c = client(route)
    assert (c.probe(pdf_name(1)).size, c.probe(pdf_name(1)).how) == (518019, "range")
    assert client(lambda m, n, h: Resp(403) if m == "HEAD" else Resp(200)).probe(pdf_name(1)).size is None


def test_unexpected_pdf_names_are_refused_before_any_request():
    c = client(lambda m, n, h: Resp(200))
    for bad in ("../../x.pdf", "140120261015.pdf", "abc.pdf", "140120261015551440.zip"):
        with pytest.raises(TdnetError):
            c.probe(bad)
    assert c.session.calls == [] and PDF_NAME.match(pdf_name(1))


def test_download_checks_for_a_pdf_and_leaves_nothing_behind(tmp_path):
    assert client(lambda m, n, h: Resp(200, body=pdf_bytes())).download(pdf_name(1), tmp_path / "a.pdf") > 0
    with pytest.raises(TdnetError, match="not a PDF"):
        client(lambda m, n, h: Resp(200, body=b"<html>blocked</html>")).download(pdf_name(1), tmp_path / "b.pdf")
    with pytest.raises(TdnetError, match="limit"):
        client(lambda m, n, h: Resp(200, body=pdf_bytes(5000))).download(pdf_name(1), tmp_path / "c.pdf", max_bytes=100)
    assert not (tmp_path / "b.pdf").exists() and not (tmp_path / "c.pdf").exists() and not list(tmp_path.glob("*.part"))


def site(rows=ROWS):
    def route(method, name, headers):
        if name.startswith("I_list_001_20261015"):
            return Resp(body=page(rows).encode())
        if name.startswith("I_list_"):
            return Resp(404)
        return Resp(200, {"Content-Length": "4321"}, pdf_bytes())
    return route


def test_building_a_plan_lists_and_probes_but_downloads_nothing():
    c = client(site())
    plan = planmod.build_plan(c, [DAY], n=5, seed=1)
    assert {e["standard"] for e in plan["entries"]} == {"J-GAAP", "IFRS"}                  # amendment / REIT / non-tanshin filtered
    assert len(plan["entries"]) == 3 and all(e["files"][0]["size"] == 4321 for e in plan["entries"])
    assert not any(r.body_read for r in c.session.responses)
    text = planmod.format_plan(plan)
    assert all(e["docID"] in text and e["pdf_url"] in text for e in plan["entries"]) and plan["approval_token"] in text
    assert "ANAP" in text and "R-" not in text and "三井物産" not in text


def test_nothing_is_downloaded_without_the_matching_token_or_over_the_cap(tmp_path):
    c = client(site())
    plan = planmod.build_plan(c, [DAY], n=5)
    before = len(c.session.calls)
    with pytest.raises(TdnetError, match="token"):
        download_plan(c, plan, tmp_path, "nope")
    with pytest.raises(TdnetError, match="cap"):
        download_plan(c, plan, tmp_path, plan["approval_token"], max_total_bytes=10)
    assert len(c.session.calls) == before and not list(tmp_path.iterdir())


def test_approved_download_is_idempotent_records_a_ledger_and_reports_failures(tmp_path):
    def route(method, name, headers):
        if name.startswith("I_list_"):
            return site()(method, name, headers)
        if method == "GET" and name == ROWS[1].split('href="')[1].split('"')[0]:        # one file is refused
            return Resp(403)
        return Resp(200, {"Content-Length": "999"}, pdf_bytes())
    c = client(route)
    plan = planmod.build_plan(c, [DAY], n=5)
    result = download_plan(c, plan, tmp_path, plan["approval_token"])
    assert result["downloaded"] == 2 and len(result["failed"]) == 1
    ledger = json.loads((tmp_path / "downloads.json").read_text(encoding="utf-8"))
    ok = [d for d in ledger.values() if "sha256" in d]
    assert len(ok) == 2 and all(d["split"] in ("dev", "test") and d["pdf_url"].startswith("https://") for d in ok)
    n = len(c.session.calls)
    download_plan(c, plan, tmp_path, plan["approval_token"])
    gets = [x for x in c.session.calls[n:] if x[0] == "GET"]
    assert len(gets) == 1                                                  # only the failed file was tried again


# ----------------------------------------------------------------------------- prepare: real PDFs, text-layer silver gold
def fake_download(tmp_path, doc_id, pdf, standard, **meta):
    folder = tmp_path / "raw" / doc_id
    folder.mkdir(parents=True)
    shutil.copy(pdf, folder / f"{doc_id}.pdf")
    ledger_path = tmp_path / "raw" / "downloads.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8")) if ledger_path.exists() else {}
    ledger[doc_id] = {"file": f"{doc_id}.pdf", "filerName": meta.get("name", "T"), "secCode": "99990", "title": "t", "standard": standard, "split": "dev"}
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")


@pytest.mark.skipif(not (CALBEE.exists() and NIKON.exists()), reason="sample PDFs not available")
def test_balance_sheet_pages_become_gold_with_text_layer_ground_truth(tmp_path):
    fake_download(tmp_path, "140120261015000001", CALBEE, "J-GAAP", name="カルビー")
    fake_download(tmp_path, "140120261015000002", NIKON, "IFRS", name="ニコン")
    assert bs_candidate_pages(CALBEE) == [0, 1] and bs_candidate_pages(NIKON) == [0, 1]       # p2 has no title: its predecessor does
    res = prepare_all(tmp_path / "raw", tmp_path / "prepared", progress=lambda s: None)
    m = {x["id"]: x for x in res["manifest"]}
    assert len(m) == 4 and all(x["status"] == "gold" for x in m.values())                     # incl. IFRS + the 増減 change column
    nikon = m["140120261015000002_p1"]
    assert nikon["standard"] == "IFRS" and nikon["unit_multiplier"] == 1_000_000 and nikon["columns"] == 3
    assert Path(nikon["silver"]).exists() and (tmp_path / "prepared" / "manifest.json").exists()
    # the rows carry everything the shared evaluation runner reads
    assert {"id", "silver", "pdf", "page_index", "standard", "unit_multiplier", "split", "filerName"} <= set(nikon)


@pytest.mark.skipif(not CALBEE.exists(), reason="sample PDF not available")
def test_a_page_the_verifier_flags_is_listed_as_ambiguous_not_used_as_gold(tmp_path, monkeypatch):
    import solution2.tdnet.prepare as prep
    from solution2.verify import verify_document as real
    fake_download(tmp_path, "140120261015000003", CALBEE, "J-GAAP")
    calls = {"n": 0}

    def flaky(doc):
        calls["n"] += 1
        v = real(doc)
        if calls["n"] >= 2:                                   # page 2 is flagged alone AND when joined with page 1
            v.trusted = False
        return v
    monkeypatch.setattr(prep, "verify_document", flaky)
    res = prepare_all(tmp_path / "raw", tmp_path / "prepared", progress=lambda s: None)
    assert [x["status"] for x in res["manifest"]] == ["gold", "ambiguous"]


def test_a_pdf_without_a_readable_balance_sheet_is_reported_not_guessed(tmp_path):
    import fitz
    pdf = tmp_path / "blank.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.save(pdf)
    fake_download(tmp_path, "140120261015000004", pdf, "J-GAAP")
    res = prepare_all(tmp_path / "raw", tmp_path / "prepared", progress=lambda s: None)
    assert res["manifest"] == [] and res["no_balance_sheet"] == ["140120261015000004"]


# ----------------------------------------------------------------------------- CLI
def test_plan_command_lists_and_says_nothing_was_downloaded(monkeypatch, tmp_path, capsys):
    from solution2.tdnet import __main__ as cli
    monkeypatch.setattr(cli, "_client", lambda args: client(site()))
    code = cli.main(["plan", "--from", "2026-10-15", "--to", "2026-10-15", "--n", "3", "--min-ifrs", "1", "--out", str(tmp_path / "plan.json")])
    out = capsys.readouterr().out
    assert code == 0 and "NOTHING HAS BEEN DOWNLOADED" in out and "approval token" in out and "https://www.release.tdnet.info" in out
    plan = json.loads((tmp_path / "plan.json").read_text(encoding="utf-8"))
    assert plan["approval_token"] in out and sum(e["standard"] == "IFRS" for e in plan["entries"]) >= 1


def test_the_old_date_warning(monkeypatch, tmp_path, capsys):
    from solution2.tdnet import __main__ as cli
    monkeypatch.setattr(cli, "_client", lambda args: client(lambda m, n, h: Resp(404)))
    assert cli.main(["plan", "--from", "2020-01-06", "--to", "2020-01-07", "--out", str(tmp_path / "p.json")]) == 1
    err = capsys.readouterr().err
    assert "more than 31 days" in err and "no plan written" in err and not (tmp_path / "p.json").exists()


@pytest.mark.skipif(not (CALBEE.exists() and NIKON.exists()), reason="sample PDFs not available")
def test_prepare_can_run_on_a_sample_and_a_later_run_keeps_earlier_work(tmp_path):
    from solution2.tdnet.prepare import sample_docs
    fake_download(tmp_path, "140120261015000011", CALBEE, "J-GAAP")
    fake_download(tmp_path, "140120261015000012", NIKON, "IFRS")
    raw = tmp_path / "raw"
    ledger = json.loads((raw / "downloads.json").read_text(encoding="utf-8"))
    ledger["140120261015000012"]["split"] = "test"
    (raw / "downloads.json").write_text(json.dumps(ledger), encoding="utf-8")
    assert sample_docs(raw, 5, "dev") == ["140120261015000011"]                 # the test split is not touched by default
    assert sample_docs(raw, 1, "all", ifrs=1) == ["140120261015000012"]         # an IFRS quota is honoured
    first = prepare_all(raw, tmp_path / "prepared", ["140120261015000011"], progress=lambda s: None)
    assert {m["docID"] for m in first["manifest"]} == {"140120261015000011"}
    assert not (tmp_path / "prepared" / "140120261015000012_p1.json").exists()   # the other filing was not parsed
    second = prepare_all(raw, tmp_path / "prepared", ["140120261015000012"], progress=lambda s: None)
    assert {m["docID"] for m in second["manifest"]} == {"140120261015000011", "140120261015000012"}
    assert {m["docID"] for m in second["new"]} == {"140120261015000012"}


def test_a_statement_split_across_pages_is_recognised_not_called_ambiguous():
    from solution2.harness.silver import silver_gold
    from solution2.tdnet.prepare import join_tables, split_partners
    from solution2.verify import verify_document
    if not NIKON.exists():
        pytest.skip("sample PDF not available")
    whole = silver_gold(NIKON, 1)                                        # one clean statement
    first = whole.model_copy(update={"rows": whole.rows[:12]})            # cut it mid-section, as a page break does
    second = whole.model_copy(update={"rows": whole.rows[12:]})
    assert verify_document(whole).trusted
    assert not verify_document(first).trusted and not verify_document(second).trusted
    assert verify_document(join_tables(first, second)).trusted
    assert split_partners({3: first, 4: second}, {3: False, 4: False}) == {3: [4], 4: [3]}
    assert split_partners({3: first, 7: second}, {3: False, 7: False}) == {}              # non-adjacent pages do not pair
    other = second.model_copy(update={"metadata": second.metadata.model_copy(update={"unit_raw": "（単位：円）"})})
    assert split_partners({3: first, 4: other}, {3: False, 4: False}) == {}               # different columns/unit do not pair
    assert split_partners({3: whole}, {3: True}) == {}                                    # a trusted page needs no partner


def test_a_single_child_sub_group_and_a_plus_one_rounding_difference_are_not_errors():
    from solution1.models import RawDocumentTree, RawMetadata, RawPeriod, RawRow
    from solution2.verify import verify_document

    def doc(rows):
        return RawDocumentTree(metadata=RawMetadata(title="t", unit_raw="（単位：千円）",
                               periods=[RawPeriod(col_index=1, raw_label="c", raw_date="2026年3月31日")]),
                               rows=[RawRow(row_index=i + 1, indent_level=ind, account_raw=lab, role=role, values=v)
                                     for i, (ind, lab, role, v) in enumerate(rows)])
    base = [(0, "純資産の部", "section_header", []), (1, "株主資本", "group_header", []),
            (2, "資本金", "item", ["500,000"]), (2, "利益剰余金", "group_header", []),
            (3, "利益準備金", "item", ["20,000"]), (3, "その他利益剰余金", "item", ["1,069,292"]),
            (4, "繰越利益剰余金", "item", ["1,069,292"]),
            (3, "利益剰余金合計", "subtotal", ["1,089,292"]), (2, "株主資本合計", "subtotal", ["1,589,292"]),
            (1, "純資産合計", "grand_total", ["1,589,292"])]
    assert verify_document(doc(base)).trusted
    wrong = [list(r) for r in base]
    wrong[6][3] = ["1,069,000"]                                              # a real disagreement is still caught
    assert not verify_document(doc([tuple(r) for r in wrong])).trusted


def test_snapshot_manifest_is_relative_and_resolves(tmp_path):
    import json
    from solution2.tdnet.snapshot import resolve_manifest, snapshot
    src = tmp_path / "src"
    (src / "raw" / "d1").mkdir(parents=True)
    (src / "prepared").mkdir()
    (src / "raw" / "d1" / "d1.pdf").write_bytes(b"%PDF-1.4")
    (src / "prepared" / "d1_p1.json").write_text("{}", encoding="utf-8")
    entry = {"id": "d1_p1", "pdf": str(src / "raw" / "d1" / "d1.pdf"), "silver": str(src / "prepared" / "d1_p1.json")}
    (src / "prepared" / "manifest.json").write_text(json.dumps([entry]), encoding="utf-8")
    dst = tmp_path / "dst"
    assert snapshot(src, dst) == {"pdfs": 1, "pages": 1}
    stored = json.loads((dst / "prepared" / "manifest.json").read_text(encoding="utf-8"))[0]
    assert stored["pdf"] == "raw/d1/d1.pdf" and stored["silver"] == "prepared/d1_p1.json"
    resolved = resolve_manifest([stored], dst)[0]
    assert Path(resolved["pdf"]).exists() and Path(resolved["silver"]).exists()
