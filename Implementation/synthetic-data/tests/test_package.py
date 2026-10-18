import hashlib
import json
import re
from pathlib import Path

from email import policy
import email

from sdgen.build import build
from sdgen.checks import eml_parts, resolve_date, run_checks, DATE_RE
from datetime import datetime


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def failed(res, cid=None):
    return [i for i in res["results"] if i["status"] == "fail" and (cid is None or i["id"] == cid)]


def test_v1_authored_text_fails_only_the_known_r02_finding(pkg):
    """v1 narrates the case reference (a header field) as if spoken; the new F8 check must flag exactly that and nothing else."""
    res = run_checks(pkg, deep=True)
    assert {i["id"] for i in failed(res)} == {"F8"}, failed(res)
    assert res["summary"]["by_layer"]["integrity"]["fail"] == 0
    assert res["summary"]["total"] > 250


def test_build_is_byte_deterministic(pkg, tmp_path):
    b2 = build("case11-rehearsal", 1, 1, tmp_path)
    for rel in b2.files:
        assert sha(pkg / "intake-bundle" / rel) == hashlib.sha256(b2.files[rel]).hexdigest(), rel


def test_embedded_attachment_bytes_equal_standalone(pkg):
    b = pkg / "intake-bundle"
    for eml in (b / "originals/emails").glob("*.eml"):
        _, _, atts = eml_parts(eml.read_bytes())
        for name, _, payload in atts:
            stand = list(b.rglob(name))
            assert stand, name
            assert hashlib.sha256(payload).hexdigest() == sha(stand[0])


def test_unheld_historic_assessment_has_no_bytes(pkg):
    b = pkg / "intake-bundle"
    assert not list(b.rglob("11-09*"))
    man = json.loads((b / "delivery-manifest.json").read_text())
    u = man["advertised_unheld"][0]
    assert u["file"] is None and u["sha256"] is None
    for eml in (b / "originals/emails").glob("*.eml"):
        assert "11-09" not in " ".join(n for n, _, _ in eml_parts(eml.read_bytes())[2])


def test_forward_quotes_original_with_its_date_and_bundle(pkg):
    f = next((pkg / "intake-bundle/originals/emails").glob("2026-01-08*.eml"))
    msg, body, atts = eml_parts(f.read_bytes())
    assert "---------- Forwarded message ---------" in body
    assert "Date: Fri, 2 Jan 2026 at 08:45" in body
    assert "Beth Martin <beth-martin@member-mail.example>" in body
    assert len(atts) == 5
    assert "historical validity record is not in this bundle" in body


def test_checklist_csv_leaves_absent_values_blank(pkg):
    t = (pkg / "intake-bundle/originals/attachments/11-07-receiving-document-checklist.csv").read_text()
    rows = t.strip().splitlines()
    assert rows[2].endswith(",") and rows[3].endswith(",")
    assert rows[1].endswith("not retained")


def test_pdf_metadata_carries_scenario_date_not_year_2000(pkg):
    d = (pkg / "intake-bundle/originals/attachments/DC-11.pdf").read_bytes()
    assert b"D:20251221090000+11'00'" in d and b"D:20000101" not in d


def test_call_summary_is_summary_only_and_in_range(pkg):
    t = next((pkg / "intake-bundle/originals/call-summaries").glob("*.txt")).read_text()
    summ = t.split("\nSUMMARY\n")[1].split("\n\nFOLLOW-UP")[0]
    assert 60 <= len(summ.split()) <= 180
    assert not re.search(r"^[A-Z][a-z]+( [A-Z][a-z]+)?:\s", summ, re.M)


def test_final_letter_does_not_claim_payment(pkg):
    f = next((pkg / "intake-bundle/originals/emails").glob("2026-03-13*.eml"))
    _, _, atts = eml_parts(f.read_bytes())
    from sdgen.render_pdf import render_document  # noqa: F401
    from sdgen.checks import pdf_text
    letter = pdf_text(next(p for n, _, p in atts if n.startswith("Final-response")))
    assert "does not confirm that the payment has been made" in letter
    assert "has been paid" not in letter and "payment was made" not in letter


# ---- mutation tests: the checks must catch planted defects ----

def test_detects_tampered_original(pkg_copy):
    p = pkg_copy / "intake-bundle/originals/attachments/DC-11.pdf"
    p.write_bytes(p.read_bytes() + b"x")
    assert failed(run_checks(pkg_copy, deep=False), "A1")


def test_detects_unlisted_file_in_bundle(pkg_copy):
    (pkg_copy / "intake-bundle/stray.json").write_text("{}")
    assert failed(run_checks(pkg_copy, deep=False), "A2") or failed(run_checks(pkg_copy, deep=False), "A3")


def test_detects_future_identifier_leak(pkg_copy):
    eml = next((pkg_copy / "intake-bundle/originals/emails").glob("2026-01-02*.eml"))
    raw = eml.read_bytes().replace(b"Hi Alicia,", b"Hi Alicia, approval APR-11-01 refers,", 1)
    eml.write_bytes(raw)
    assert failed(run_checks(pkg_copy, deep=False), "D3")


def test_detects_later_date_mention(pkg_copy):
    eml = next((pkg_copy / "intake-bundle/originals/emails").glob("2026-01-02*.eml"))
    raw = eml.read_bytes().replace(b"Hi Alicia,", b"Hi Alicia, as of 20 January 2026,", 1)
    eml.write_bytes(raw)
    assert failed(run_checks(pkg_copy, deep=False), "D4")


def test_detects_planning_vocabulary_in_preview(pkg_copy):
    p = next((pkg_copy / "intake-bundle/previews").glob("*.html"))
    p.write_text(p.read_text().replace("</body>", "<p>source_assertions writer</p></body>"))
    assert failed(run_checks(pkg_copy, deep=False), "E1")


def test_detects_restricted_manifest_key(pkg_copy):
    m = pkg_copy / "intake-bundle/delivery-manifest.json"
    d = json.loads(m.read_text())
    d["originals"][0]["incident_family"] = "x"
    m.write_text(json.dumps(d))
    assert failed(run_checks(pkg_copy, deep=False), "E3")


def test_detects_private_conversation_leak_into_summary(pkg_copy):
    conv = json.loads((pkg_copy / "restricted/private-controls/conversations/11-D06-conversation.json").read_text())
    line = next(t["text"] for t in conv["turns"] if len(t["text"]) > 80)
    s = next((pkg_copy / "intake-bundle/originals/call-summaries").glob("*.txt"))
    s.write_text(s.read_text().replace("FOLLOW-UP", line + "\n\nFOLLOW-UP", 1))
    assert failed(run_checks(pkg_copy, deep=False), "E4")


def test_detects_fabricated_unheld_file(pkg_copy):
    (pkg_copy / "intake-bundle/originals/attachments/11-09-historic-dc-11-validity-assessment.pdf").write_bytes(b"%PDF-1.4")
    assert failed(run_checks(pkg_copy, deep=False), "B5") or failed(run_checks(pkg_copy, deep=False), "A2")


def test_detects_broken_summary_fidelity_claim(pkg_copy):
    f = pkg_copy / "restricted/private-controls/fidelity/11-D06-summary-fidelity.json"
    d = json.loads(f.read_text())
    d["claims"][0]["check_strings"] = ["a sentence nobody said"]
    f.write_text(json.dumps(d))
    assert failed(run_checks(pkg_copy, deep=False), "F4")


def test_date_resolution_without_year_picks_nearest():
    ev = datetime.fromisoformat("2026-01-02T08:45:00+11:00")
    m = DATE_RE.search("papers on 21 December and update for 7 January")
    assert resolve_date(m, ev).isoformat() == "2025-12-21"
    m = DATE_RE.search("update for 7 January")
    assert resolve_date(m, ev).isoformat() == "2026-01-07"
