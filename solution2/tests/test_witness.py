from pathlib import Path

import pytest

from solution1.models import RawDocumentTree
from solution2.witness import check_page, snap_labels, text_layer_status

ROOT = Path(__file__).resolve().parents[2]
NIKON = ROOT / "dataset/benchmark_suite/company_04_nikon/balance_sheet.pdf"
YAMATO = ROOT / "dataset/benchmark_suite/company_01_yamato/balance_sheet.pdf"
SILVER = ROOT / "solution2/fixtures/silver/silver_nikon_p2.json"
needs_files = pytest.mark.skipif(not (NIKON.exists() and SILVER.exists() and YAMATO.exists()), reason="fixtures not available")


def answer():
    return RawDocumentTree.model_validate_json(SILVER.read_text(encoding="utf-8"))


def verdicts(chk):
    return {c.row_index: c for c in chk.rows}


@needs_files
def test_a_correct_answer_agrees_row_by_row():
    chk = check_page(NIKON, 1, answer())
    assert chk.usable
    assert not [c for c in chk.rows if c.verdict in ("conflict", "label_variant")]


@needs_files
def test_values_shifted_one_row_up_are_a_conflict():
    raw = answer()
    numeric = [i for i, r in enumerate(raw.rows) if any(v.strip() for v in r.values)]
    block = numeric[3:8]
    rows = list(raw.rows)
    for k, i in enumerate(block[:-1]):
        rows[i] = rows[i].model_copy(update={"values": raw.rows[block[k + 1]].values})
    rows[block[-1]] = rows[block[-1]].model_copy(update={"values": [""] * len(raw.rows[block[-1]].values)})
    chk = check_page(NIKON, 1, raw.model_copy(update={"rows": rows}))
    flagged = [c for c in chk.rows if c.verdict == "conflict"]
    assert len(flagged) >= 4 and {c.kind for c in flagged} <= {"shifted", "wrong", "dropped", "spurious"}


@needs_files
def test_a_misread_character_in_a_label_is_proposed_back_as_printed():
    raw = answer()
    i = next(k for k, r in enumerate(raw.rows) if len(r.account_raw) >= 4 and any(v.strip() for v in r.values))
    printed = raw.rows[i].account_raw
    rows = list(raw.rows)
    rows[i] = rows[i].model_copy(update={"account_raw": printed[:1] + "犬" + printed[2:]})
    bad = raw.model_copy(update={"rows": rows})
    chk = check_page(NIKON, 1, bad)
    c = verdicts(chk)[rows[i].row_index]
    assert c.verdict == "label_variant" and "犬" not in c.proposed
    fixed, changes = snap_labels(bad, chk)
    assert [ch[0] for ch in changes] == [rows[i].row_index] and fixed.rows[i].account_raw != bad.rows[i].account_raw
    assert fixed.rows[i].values == bad.rows[i].values                       # numbers are never touched


@needs_files
def test_a_changed_digit_is_a_conflict_and_a_value_on_a_heading_is_spurious():
    raw = answer()
    i = next(k for k, r in enumerate(raw.rows) if any(v.strip() for v in r.values))
    rows = list(raw.rows)
    vals = list(rows[i].values)
    j = next(k for k, v in enumerate(vals) if v.strip())
    vals[j] = vals[j][:-1] + ("1" if vals[j][-1] != "1" else "2")
    rows[i] = rows[i].model_copy(update={"values": vals})
    h = next(k for k, r in enumerate(rows) if not any(v.strip() for v in r.values) and r.account_raw.strip())
    rows[h] = rows[h].model_copy(update={"values": ["－"] * len(vals)})
    chk = check_page(NIKON, 1, raw.model_copy(update={"rows": rows}))
    by = verdicts(chk)
    assert by[rows[i].row_index].verdict == "conflict"
    assert by[rows[h].row_index].verdict == "conflict" and by[rows[h].row_index].kind == "spurious"


@needs_files
def test_a_two_sided_page_is_not_judged():
    raw = answer()
    chk = check_page(YAMATO, 0, raw)
    assert not chk.usable and all(c.verdict == "unknown" for c in chk.rows)


def test_a_text_layer_produced_by_ocr_software_is_not_trusted(tmp_path):
    import fitz
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), "label 1,000 2,000 3,000 4,000 5,000 6,000", fontname="helv")
    doc.set_metadata({"producer": "Tesseract 5.3 via ocrmypdf"})
    path = tmp_path / "scan.pdf"
    doc.save(path)
    ok, why = text_layer_status(path, 0)
    assert not ok and "OCR" in why
    doc2 = fitz.open()
    doc2.new_page().insert_text((72, 100), "label 1,000 2,000 3,000 4,000 5,000 6,000", fontname="helv")
    doc2.save(tmp_path / "native.pdf")
    assert text_layer_status(tmp_path / "native.pdf", 0) == (True, "")
