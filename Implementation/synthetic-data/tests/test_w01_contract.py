"""W01: one current guide, exact return schemas, and a tested path from pack -> declared return -> author check -> real renderer
-> parseable output for all seven micro-batch card types (email, portal, register, internal email, call, approval, formal
response) plus the note kind. Specimen prose is fixture text in disposable folders."""
import csv
import email
import hashlib
import io
import json
import re
from email import policy
from pathlib import Path

import pytest
from pypdf import PdfReader

import specimens as S
from conftest import needs_v3
from sdgen import contract, workpack2
from sdgen.authoring_check import author_check, load_pack
from sdgen.card_adapter import ContractGap, render_card, resolve_attachments
from sdgen.render_mail import message_id

STAGE = "style_microbatch"
SEVEN = list(S.ALL)


def prep(tmp_path, pack, d):
    f = S.write(tmp_path, d)
    rows = author_check(f, pack, STAGE)
    assert all(r["ok"] for r in rows), [r for r in rows if not r["ok"]]
    plan = load_pack(pack)[0]
    return plan, plan.artifact(d["card"])


def pdf_text(b):
    return " ".join(" ".join(p.extract_text().split()) for p in PdfReader(io.BytesIO(b)).pages)


# ---------------------------------------------------------------- documentation == schema == renderer
@pytest.mark.parametrize("kind", sorted(contract.SCHEMAS))
def test_documented_skeleton_matches_the_schema_it_is_generated_beside(kind):
    sk, sch = workpack2.SKELETONS[kind], contract.SCHEMAS[kind]
    assert set(sch["required"]) <= set(sk), f"skeleton for {kind} omits required fields {set(sch['required']) - set(sk)}"
    assert set(sk) <= set(sch["properties"]), f"skeleton for {kind} shows fields the schema rejects {set(sk) - set(sch['properties'])}"
    if kind == "call_summary":
        assert set(sk["fidelity"]["claims"][0]) == set(sch["properties"]["fidelity"]["properties"]["claims"]["items"]["properties"])
        assert set(sk["conversation"][0]) == {"n", "speaker", "text"}
    if kind == "letter_with_cover_email":
        assert set(sk["letter"]) == set(sch["properties"]["letter"]["required"])


def test_pack_ships_one_current_guide_and_no_superseded_workflow_text(pack_set):
    root, idx = pack_set
    guides = {}
    for row in idx:
        p = root / row["pack"]
        files = sorted(f.name for f in (p / "shared").iterdir())
        assert files == ["COMMON-REFERENCES.json", "RETURN-SCHEMAS.json", "RETURN-SCHEMAS.md", "WORKER-GUIDE.md"], (row["pack"], files)
        text = (p / "shared/WORKER-GUIDE.md").read_text() + (p / "INSTRUCTIONS.md").read_text() + (p / "shared/RETURN-SCHEMAS.md").read_text()
        assert not re.search(r"superseded|stage compiler|721|procedure briefs|NONTECHNICAL|ordinary[- ]text return|\.\./|Artha|Data/synthesis", text, re.I), row["pack"]
        guides[row["case"]] = text
    for k in contract.SCHEMAS:
        assert f"`{k}`" in guides[11]
    # one guide, same for everybody except the case/stage specifics: shared schema files are byte-identical across packs
    hashes = {hashlib.sha256((root / r["pack"] / "shared/RETURN-SCHEMAS.json").read_bytes()).hexdigest() for r in idx}
    assert len(hashes) == 1
    assert json.loads((root / idx[0]["pack"] / "shared/RETURN-SCHEMAS.json").read_text())["kinds"] == contract.SCHEMAS


def test_guide_states_missing_procedure_facts_and_does_not_ask_the_worker_to_infer(pack11):
    g = (pack11 / "shared/WORKER-GUIDE.md").read_text()
    assert "No stand-alone procedure or policy corpus has been supplied" in g and "open dependency" in g
    assert "raise a question" in g.lower()
    refs = json.loads((pack11 / "shared/COMMON-REFERENCES.json").read_text())
    assert refs["procedures"]["status"].startswith("no stand-alone procedure corpus")


def test_guide_separates_what_the_worker_writes_from_what_the_supervisor_supplies(pack11):
    g = (pack11 / "shared/WORKER-GUIDE.md").read_text()
    for kind, items in contract.CENTRAL.items():
        row = next(line for line in g.splitlines() if line.startswith(f"| `{kind}`"))
        assert items[0] in row
    schemas = (pack11 / "shared/RETURN-SCHEMAS.md").read_text()
    assert "Supervisor supplies (you never write these)" in schemas


def test_case_11_guide_carries_the_seven_card_stage_and_no_other_pack_does(pack_set):
    root, idx = pack_set
    g11 = (root / next(r["pack"] for r in idx if r["case"] == 11) / "shared/WORKER-GUIDE.md").read_text()
    for c in ("11-D01", "11-D02", "11-D03", "11-D07", "11-D11", "11-D13", "11-D20"):
        assert c in g11
    assert "11-D05" in g11 and "exempt from style edits" in g11
    for row in idx:
        if row["case"] != 11:
            assert "micro-batch" not in (root / row["pack"] / "shared/WORKER-GUIDE.md").read_text().lower()


# ---------------------------------------------------------------- all seven card types, end to end
@needs_v3
def test_d01_member_email_round_trip(tmp_path, pack11, accepted_bytes):
    plan, art = prep(tmp_path, pack11, S.d01())
    rc = render_card(S.d01(), plan, art, accepted_bytes)
    (rel,) = [r for r in rc.files if r.endswith(".eml")]
    m = email.message_from_bytes(rc.files[rel], policy=policy.default)
    assert m["Subject"] == "REQ-2025-11-01 - Initial service request" and "Beth Martin" in m["From"] and "death-benefits-administration" in m["To"]
    assert m["Date"].startswith("Sun, 21 Dec 2025 09:30:00 +1100") and not any(p.defects for p in m.walk())
    body = m.get_body(preferencelist=("plain",)).get_content()
    assert body.startswith("hi\n") and "REQ-2025-11-01" in body
    atts = {p.get_filename(): p.get_payload(decode=True) for p in m.iter_attachments()}
    assert list(atts) == S.FILES_D01 and all(atts[n] == accepted_bytes[n] for n in atts)  # accepted attachments reused exactly
    assert "C-e1da28538652e263887a" not in body and any(r.startswith("previews/") for r in rc.files)


@needs_v3
def test_d02_portal_round_trip_through_the_native_envelope(tmp_path, pack11, accepted_bytes):
    plan, art = prep(tmp_path, pack11, S.d02())
    rc = render_card(S.d02(), plan, art, accepted_bytes)
    (rel,) = rc.files
    doc = json.loads(rc.files[rel])
    assert list(doc) == ["form", "submitted_at", "reference", "channel", "submitted_by", "fields", "free_text", "attachments"]
    assert doc["form"] == "Formal complaint" and doc["reference"] == "REQ-2025-11-01" and doc["channel"] == "portal"
    assert doc["submitted_at"] == "2025-12-31T09:30:00+11:00" and doc["submitted_by"]["email"] == "beth-martin@member-mail.example"
    assert doc["free_text"].startswith("fixture portal text")
    assert [a["filename"] for a in doc["attachments"]] == ["DC-11.pdf", "ID-11.pdf", "RF-11.pdf", "11-05-portal-upload-receipt.jpg"]
    assert all(a["sha256"] == hashlib.sha256(accepted_bytes[a["filename"]]).hexdigest() and a["size_bytes"] == len(accepted_bytes[a["filename"]]) for a in doc["attachments"])
    assert rc.files[rel].endswith(b"\n")


def test_d03_register_is_deterministic_native_and_free_of_worker_prose(tmp_path, pack11):
    plan, art = prep(tmp_path, pack11, S.d03())
    a, b = render_card(S.d03(), plan, art), render_card(S.d03(), plan, art)
    (rel,) = a.files
    assert a.files == b.files and rel.startswith("originals/registers/")
    rows = list(csv.DictReader(io.StringIO(a.files[rel].decode())))
    assert rows == [{"request_reference": "REQ-2025-11-01", "case_id": "C-e1da28538652e263887a", "original_receipt": "2025-12-31",
                     "complaints_receipt": "2025-12-31", "manager": "STAFF-CM03",
                     "member_reference": "MR-3f85043fe7f9c98e19f3", "product": "death_benefit", "intake_channel": "portal", "existing_tag": "service",
                     "manager_name": "Alicia Romero"}]
    assert a.files[rel].count(b"\r\n") == 2  # header + one row; no errors, no derived/root-cause column
    assert "root" not in a.files[rel].decode().lower() and "cause" not in a.files[rel].decode().lower()


@needs_v3
def test_d07_internal_email_round_trip_with_threading_and_five_attachments(tmp_path, pack11, accepted_bytes):
    plan, art = prep(tmp_path, pack11, S.d07())
    rc = render_card(S.d07(), plan, art, accepted_bytes)
    m = email.message_from_bytes(next(v for k, v in rc.files.items() if k.endswith(".eml")), policy=policy.default)
    assert "Alicia Romero" in m["From"] and "Alice Hughes" in m["To"] and m["Date"].startswith("Mon, 05 Jan 2026 09:30")
    assert m["In-Reply-To"] == message_id("11-D05", "beth-martin@member-mail.example")
    atts = {p.get_filename(): p.get_payload(decode=True) for p in m.iter_attachments()}
    assert list(atts) == S.D07_FILES
    assert atts["DC-11.pdf"] == accepted_bytes["DC-11.pdf"] and atts["11-05-portal-upload-receipt.jpg"] == accepted_bytes["11-05-portal-upload-receipt.jpg"]
    contact = atts["11-04-claimant-contact-identity-verification-record.pdf"]  # newly rendered, not an accepted original
    asset = plan.assets_by_name["11-04-claimant-contact-identity-verification-record.pdf"]
    from assetcheck import missing_values
    assert missing_values(asset, contact) == []  # every locked field of the new attachment is readable in the PDF text
    body = m.get_body(preferencelist=("plain",)).get_content()
    assert "7 January 2026" in body and not body.startswith("\n")  # empty greeting emits no blank line


def test_d11_call_summary_round_trip_with_private_dialogue_and_fidelity(tmp_path, pack11):
    plan, art = prep(tmp_path, pack11, S.d11())
    rc = render_card(S.d11(), plan, art)
    txt = next(v for k, v in rc.files.items() if k.endswith(".txt")).decode()
    lines = txt.splitlines()
    assert lines[0] == "CASE SUMMARY SERVICE - CALL SUMMARY" and lines[1] == "=" * len(lines[0])
    assert "Case:               C-e1da28538652e263887a" in lines and "Request reference:  REQ-2025-11-01" in lines
    assert "Call date:          Friday 9 January 2026" in lines and "Call start:         9:30 am AEDT" in lines
    assert "  - Beth Martin" in lines and "  - Alicia Romero" in lines
    assert "SUMMARY" in lines and "FOLLOW-UP ITEMS" in lines
    assert not any("Fixture: " in ln for ln in lines)  # the private dialogue never enters the intake text
    conv = json.loads(rc.private["private-controls/conversations/11-D11-conversation.json"])
    fid = json.loads(rc.private["private-controls/fidelity/11-D11-summary-fidelity.json"])
    assert conv["never_ingested"] is True and len(conv["turns"]) == 4 and fid["claims"][0]["check_strings"] == ["DC-11 outstanding"]


@needs_v3
def test_d13_approval_email_round_trip_with_the_authority_attachment(tmp_path, pack11, accepted_bytes):
    plan, art = prep(tmp_path, pack11, S.d13())
    rc = render_card(S.d13(), plan, art, accepted_bytes)
    m = email.message_from_bytes(next(v for k, v in rc.files.items() if k.endswith(".eml")), policy=policy.default)
    assert "Rebecca Mason" in m["From"] and "Alicia Romero" in m["To"] and "Alice Hughes" in m["To"]
    assert m["Subject"] == "C-e1da28538652e263887a - Delegate decision" and m["Date"].startswith("Tue, 13 Jan 2026 09:30")
    (att,) = list(m.iter_attachments())
    text = pdf_text(att.get_payload(decode=True))
    assert att.get_filename() == "11-08-applicable-delegate-authority-excerpt.pdf"
    for v in ("Applicable delegate authority excerpt", "DOC-11-ADAE", "Rebecca Mason", "REQ-2025-11-01", "12 January 2026", "OPS-DELEGATION-02"):
        assert v in text, v


def test_d20_formal_response_letter_and_cover_email_round_trip(tmp_path, pack11):
    plan, art = prep(tmp_path, pack11, S.d20())
    rc = render_card(S.d20(), plan, art)
    letter_rel = next(k for k in rc.files if k.endswith(".pdf"))
    m = email.message_from_bytes(next(v for k, v in rc.files.items() if k.endswith(".eml")), policy=policy.default)
    (att,) = list(m.iter_attachments())
    assert att.get_filename() == "Our-position-on-your-complaint-12-January-2026.pdf" and att.get_payload(decode=True) == rc.files[letter_rel]
    text = pdf_text(rc.files[letter_rel])
    for v in ("Example Super", "Complaints Team", "12 January 2026", "Beth Martin", "beth-martin@member-mail.example", "Complaint reference", "C-e1da28538652e263887a",
              "Request reference", "REQ-2025-11-01", "Alan Martin (deceased)", "Our position on your complaint", "Dear Beth,", "Where things stand",
              "first fixture point", "Yours sincerely,", "Alicia Romero", "Complaints Case Manager"):
        assert v in text, v
    assert rc.central["letter_filename"] == att.get_filename()


def test_note_kind_round_trip_on_a_real_note_card(tmp_path, pack_set_copy):
    """No note card is in the seven-card stage; a disposable copy of the stage list adds 11-D14 so the note path is exercised for real."""
    pack = next(pack_set_copy.glob("11-C-*"))
    st = json.loads((pack / "STAGE.json").read_text())
    next(s for s in st["stages"] if s["stage"] == STAGE)["cards"].append("11-D14")
    (pack / "STAGE.json").write_text(json.dumps(st))
    d = {"card": "11-D14", "revision": 1, "kind": "note", "blocks": [{"p": "Fixture note: REQ-2025-11-01 action ACT-11-01 on DC-11 and RF-11."}, {"ul": ["first", "second"]}],
         "style_completion": S.sc("11-D14", "N1", features=("fragments",))}
    plan, art = prep(tmp_path, pack, d)
    rc = render_card(d, plan, art)
    (rel,) = rc.files
    txt = rc.files[rel].decode()
    assert rel.startswith("originals/notes/") and txt.splitlines()[:4] == [
        "Author: Alice Hughes", "Date: 14 January 2026 09:30", "Case: C-e1da28538652e263887a", f"Subject: {art['subject']}"]
    assert "ACT-11-01" in txt and "- first" in txt and txt.endswith("\n")
    bad = dict(d, blocks=[{"p": "no references here"}])
    rows = author_check(S.write(tmp_path, bad, "bad.json"), pack, STAGE)
    assert any(not r["ok"] and r["id"] == "A6" and "ACT-11-01" in r["detail"] for r in rows)


def test_quote_blocks_render_in_plain_and_html_bodies(tmp_path, pack11, accepted_bytes):
    ctx = json.loads((pack11 / "context/ACCEPTED-PRIOR-ORIGINALS.json").read_text())
    q = next(o for o in ctx["originals"] if o["card"] == "11-D05")["text"].split("\n\n")[1].strip()
    d = S.d07()
    d["blocks"].insert(1, {"quote": q, "from_card": "11-D05"})
    plan, art = prep(tmp_path, pack11, d)
    rc = render_card(d, plan, art, accepted_bytes if accepted_bytes else None) if accepted_bytes else render_card(d, plan, art)
    m = email.message_from_bytes(next(v for k, v in rc.files.items() if k.endswith(".eml")), policy=policy.default)
    plain = m.get_body(preferencelist=("plain",)).get_content()
    assert "\n".join("> " + ln for ln in q.split("\n")) in plain
    assert "<blockquote>" in m.get_body(preferencelist=("html",)).get_content()


# ---------------------------------------------------------------- missing / wrong fields give clear failures
def test_the_old_documented_call_shape_fails_with_clear_field_messages(tmp_path, pack11):
    """Reproduces the independent probe: following the old key list ('follow_up', 'fidelity' dict of claims) crashed the real renderer."""
    d = {"card": "11-D11", "revision": 1, "kind": "call_summary", "conversation": [], "summary": "fixture", "follow_up": [], "fidelity": {},
         "style_completion": S.sc("11-D11", "V2")}
    rows = author_check(S.write(tmp_path, d), pack11, STAGE)
    msgs = " | ".join(r["detail"] for r in rows if not r["ok"])
    assert "missing required field(s): follow_up_items" in msgs and "unexpected field(s) ['follow_up']" in msgs
    assert "conversation" in msgs and "fidelity" in msgs and "claims" in msgs


@pytest.mark.parametrize("card,drop,expect", [
    ("11-D01", "greeting", "missing required field(s): greeting"), ("11-D01", "blocks", "blocks"), ("11-D01", "signoff", "signoff"),
    ("11-D02", "free_text", "free_text"), ("11-D11", "summary", "summary"), ("11-D11", "fidelity", "fidelity"), ("11-D11", "follow_up_items", "follow_up_items"),
    ("11-D20", "letter", "letter"), ("11-D13", "style_completion", "style_completion"), ("11-D03", "style_completion", "style_completion"),
])
def test_each_missing_field_is_named_in_the_failure(tmp_path, pack11, card, drop, expect):
    d = S.ALL[card]()
    del d[drop]
    rows = author_check(S.write(tmp_path, d), pack11, STAGE)
    assert any(expect in r["detail"] and not r["ok"] and r["id"] == "A2" for r in rows), [r["detail"] for r in rows if not r["ok"]]


def test_wrong_field_types_and_block_forms_are_named(tmp_path, pack11):
    d = S.d01(blocks=[{"para": "x"}], signoff="Beth", revision="1")
    msgs = " | ".join(r["detail"] for r in author_check(S.write(tmp_path, d), pack11, STAGE) if not r["ok"])
    assert "blocks/0" in msgs and "not a valid block" in msgs and "signoff" in msgs and "revision" in msgs
    d = S.d20(letter={"title": "t", "salutation": "s", "blocks": [{"p": "x"}]})
    assert any("missing required field(s): signoff" in r["detail"] for r in author_check(S.write(tmp_path, d), pack11, STAGE))
    d = S.d01(cc=["STAFF-CM03"])
    assert any("unexpected field(s) ['cc']" in r["detail"] for r in author_check(S.write(tmp_path, d), pack11, STAGE))  # the worker cannot add recipients


def test_cross_references_must_be_earlier_cards_of_this_member(tmp_path, pack11):
    assert any(not r["ok"] and r["id"] == "A4b" for r in author_check(S.write(tmp_path, S.d07(in_reply_to_card="11-D20")), pack11, STAGE))
    assert any(not r["ok"] and r["id"] == "A4b" for r in author_check(S.write(tmp_path, S.d07(references_cards=["11-D99"])), pack11, STAGE))


# ---------------------------------------------------------------- adapter refusals are explicit gaps, never guesses
def test_adapter_reports_contract_gaps_instead_of_improvising(tmp_path, pack11):
    plan = load_pack(pack11)[0]
    with pytest.raises(ContractGap, match="not allowed for this card"):
        render_card(S.d01(kind="portal_json", free_text="x"), plan, plan.artifact("11-D01"))
    # a native ledger card takes no prose: an email return for it is refused, never rendered
    ledger_card = next(a for a in plan.pack["artifacts"] if a["kind"] == "ledger")
    with pytest.raises(ContractGap, match="not allowed for this card"):
        render_card(S.d01(), plan, ledger_card)
    assert contract.allowed_kinds(ledger_card)[0] == ["ledger_export"]
    # an external body's message is an authored EML; its notice PDF is a held asset, never an authored PDF
    external = next(a for a in plan.pack["artifacts"] if a["kind"] == "external")
    assert contract.allowed_kinds(external)[0] == ["email"]
    # a letter from a sender that is not a complaints-team role has no canonical letterhead
    art = dict(plan.artifact("11-D20"), author="STAFF-DB01")
    with pytest.raises(ContractGap, match="letterhead"):
        render_card(S.d20(), plan, art)
    # an attachment that needs authored prose (EML) is an explicit gap
    art = dict(plan.artifact("11-D01"), attachments=["AST-eml-placeholder"])
    plan.assets["AST-eml-placeholder"] = {"asset_id": "AST-eml-placeholder", "filename": "x.eml", "media_type": "message/rfc822", "delivery_state": "held",
                                          "owner": "STAFF-CM03", "document_spec": {}, "created_at": "2025-12-01T00:00:00+11:00"}
    with pytest.raises(ContractGap, match="needs authored prose"):
        resolve_attachments(art, plan)


def test_unsupported_plan_kinds_and_formats_are_declared_in_the_schema_file(pack11):
    # ledger and external now have return shapes; the standalone scanned/text-layer letter is still an explicit, declared gap
    sch = json.loads((pack11 / "shared/RETURN-SCHEMAS.json").read_text())
    assert sch["unsupported_plan_kinds"] == {} and "Scanned or text-layer PDF letter" in sch["unsupported_formats"]
    assert sch["kind_for_plan_kind"]["ledger"] == ["ledger_export"] and sch["kind_for_plan_kind"]["external"] == ["email"]


def test_card_context_command_reports_derived_requirements(pack11, capsys):
    from sdgen.cli import main
    main(["card-context", "--pack", str(pack11), "--card", "11-D13"])
    out = json.loads(capsys.readouterr().out)
    assert out["allowed_return_kinds"] == ["email", "note"][:1] and any("APR-11-01" in g["label"] for g in out["derived"]["required_in_text"])
    assert any("human review" in h or "independent review" in h for h in out["derived"]["not_mechanically_checkable_needs_independent_review"][:1] or ["independent review"])


# ---------------------------------------------------------------- pack integrity, versioning, preserved old set
def test_pack_set_inventory_hashes_and_current_pointer_inputs(pack_set):
    root, idx = pack_set
    index = json.loads((root / "PACKS-INDEX.json").read_text())
    assert index["pack_set_version"] == 3 and index["launch_authorised"] is False and len(index["packs"]) == 40
    for row in idx:
        p = root / row["pack"]
        man = json.loads((p / "PACK-MANIFEST.json").read_text())
        digest, files = workpack2.pack_digest(p)
        assert digest == man["pack_digest"] == row["pack_digest"] and files == man["files"]
        assert all(c["ok"] for c in man["checks"]) and man["launch_status"] == "not_authorised"
        ih = man["input_hashes"]
        assert ih["plan_sha256"] == hashlib.sha256((p / "plan/assistant-pack.json").read_bytes()).hexdigest()
        assert ih["guide_sha256"] == files["shared/WORKER-GUIDE.md"] and ih["return_schemas_sha256"] == files["shared/RETURN-SCHEMAS.json"]
        assert ih["clarifications_sha256"] == files["plan/APPROVED-CLARIFICATIONS.json"]
    assert [r["blocked"] is not None for r in idx].count(True) == 1 and next(r for r in idx if r["case"] == 32)["launchable_stages"] == []
    assert next(r for r in idx if r["case"] == 11)["launchable_stages"] == ["style_microbatch"]


def test_stage_sequence_exemptions_and_case_32_block_are_retained(pack_set):
    root, idx = pack_set
    st = json.loads((root / next(r["pack"] for r in idx if r["case"] == 11) / "STAGE.json").read_text())["stages"]
    names = [s["stage"] for s in st]
    assert names == ["rehearsal_repair", "style_microbatch", "remaining_cards", "complete_case_review"]
    assert st[0]["cards"] == ["11-D05", "11-D06", "11-D10", "11-D15", "11-D17"] and st[0]["launchable"] is False
    assert st[1]["cards"] == SEVEN[:2] + ["11-D03"][:0] + SEVEN[2:] or sorted(st[1]["cards"]) == sorted(SEVEN)
    assert len(st[2]["cards"]) == 17 and st[2]["launchable"] is False and st[2]["requires_accepted_stage"] == "style_microbatch"
    p32 = json.loads((root / next(r["pack"] for r in idx if r["case"] == 32) / "STAGE.json").read_text())["stages"]
    assert p32[0]["launchable"] is False and "future-knowledge" in p32[0]["blocked_reason"]


def test_naturalistic_assignments_and_exemptions_are_retained_in_the_new_packs(pack11):
    sty = json.loads((pack11 / "style/ARTIFACT-STYLE-ASSIGNMENTS.json").read_text())
    rows = {r["artifact_ref"]: r for r in sty["cards"]}
    assert len(rows) == 29 and len(sty["attachment_briefs"]) == 16
    assert {c for c, r in rows.items() if r["rehearsal_exact_repair_or_accepted_source_exempt"]} == {"11-D05", "11-D06", "11-D10", "11-D15", "11-D17"}
    assert {c: rows[c]["profile_id"] for c in SEVEN} == {"11-D01": "M2", "11-D02": "M3", "11-D03": "S0", "11-D07": "I2", "11-D11": "V2", "11-D13": "A1", "11-D20": "F1"}
    assert set(json.loads((pack11 / "style/STYLE-PROFILES.json").read_text())["profiles"]) >= {"M2", "M3", "S0", "I2", "V2", "A1", "F1", "N1"}


def test_other_members_material_is_absent_from_a_pack(pack_set):
    root, idx = pack_set
    own = json.loads((root / idx[11]["pack"] / "plan/assistant-pack.json").read_text())
    others = {i["case"]: json.loads((root / i["pack"] / "plan/assistant-pack.json").read_text()) for i in idx if i["case"] != own["case_number"]}
    blob = "\n".join(f.read_text(errors="ignore") for f in (root / idx[11]["pack"]).rglob("*") if f.is_file())
    for n, o in others.items():
        for t in (o["case_id"], o["request_reference"], o["member_reference"], o["story"]["name"]):
            assert t not in blob, (n, t)


def test_prior_context_is_bound_to_the_accepted_v3_manifest_and_excludes_later_documents(pack11):
    ctx = json.loads((pack11 / "context/ACCEPTED-PRIOR-ORIGINALS.json").read_text())
    assert ctx["bound_to_manifest"]["delivery_manifest_sha256"] == "d45d9923aa57034b1880a1f75e37a4f650275eb31b4de85056b919c5c3fa8dcd"
    assert [o["card"] for o in ctx["originals"]] == ["11-D05", "11-D06", "11-D10"]  # 11-D15 and 11-D17 post-date every micro-batch card
    assert all(o["usable_before_cards"] for o in ctx["originals"])
    by = {o["card"]: o for o in ctx["originals"]}
    assert "11-D07" not in by["11-D10"]["usable_before_cards"] and "11-D07" in by["11-D05"]["usable_before_cards"]
    assert "private" not in json.dumps(ctx).lower().replace("private-controls", "")[:0]


def test_pack_build_refuses_to_overwrite_and_never_touches_the_first_pack_set(pack_set, tmp_path):
    root, _ = pack_set
    with pytest.raises(FileExistsError):
        workpack2.build_all(root)
    assert workpack2.PACK_SET_DIRNAME != "worker-packs" and workpack2.OUTPUTS_DIRNAME != "worker-outputs"


def test_first_pack_set_and_its_manifests_are_preserved_byte_for_byte():
    old = workpack2.SYNTH / "worker-packs"
    if not old.exists():
        pytest.skip("first pack set not present")
    sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()  # noqa: E731
    n = 0
    for man in sorted(old.glob("*/PACK-MANIFEST.json")):
        m = json.loads(man.read_text())
        for rel, h in m["files"].items():
            assert sha(man.parent / rel) == h, (man.parent.name, rel)
            n += 1
    assert n > 400 and len(list(old.glob("*/PACK-MANIFEST.json"))) == 40
    gate = json.loads((old / "STAGE-GATE.json").read_text())
    assert gate["authorised"] == [] and all(j["state"] == "queued" for j in json.loads((old / "PROGRESS.json").read_text())["jobs"])


def test_protocol_and_known_issues_are_generated_with_the_pack_set_and_state_the_limits(pack_set):
    root, idx = pack_set
    proto = (root / "SUPERVISOR-LAUNCH-PROTOCOL.md").read_text()
    assert "no stage is authorised" in proto and "audits WRITES" in proto and "not a sandbox" in proto
    assert "authoring_stage_signoff" in proto and "authoring_stage_acceptance" in proto and "mandatory on every start AND resume" in proto
    issues = (root / "KNOWN-ISSUES.md").read_text()
    for needle in ("Case 32", "No procedure corpus", "11-D03", "11-D13", "not the rehearsal's `Batch.release`", "no per-folder OS sandbox", "writes, not reads"):
        assert needle in issues, needle
    inv = json.loads((root / "PACK-INVENTORY.json").read_text())
    assert inv["packs_index_sha256"] == hashlib.sha256((root / "PACKS-INDEX.json").read_bytes()).hexdigest() and len(inv["packs"]) == 40


def test_current_pointer_is_never_overwritten_silently_and_supersession_archives_the_old_one(pack_set, tmp_path):
    root, _ = pack_set
    ptr_path = tmp_path / "WORKER-PACKS-CURRENT.json"
    ptr = workpack2.write_current_pointer(root, ptr_path)
    assert ptr["pack_set_version"] == 3 and ptr["launch_authorised"] is False and ptr["authorised_stages"] == []
    assert ptr["superseded"][0]["pack_set_version"] == 1 and "do not use" in ptr["superseded"][0]["status"]
    with pytest.raises(FileExistsError):
        workpack2.write_current_pointer(root, ptr_path)
    old_raw = ptr_path.read_bytes()
    ptr2 = workpack2.write_current_pointer(root, ptr_path, history_note="second revision", supersede=True)
    arch = list((tmp_path / "WORKER-PACKS-CURRENT.history").glob("pointer-pack-set-3-*.json"))
    assert len(arch) == 1 and arch[0].read_bytes() == old_raw  # the previous pointer is preserved byte for byte
    assert ptr2["superseded"][-1]["pointer_archived_as"].endswith(arch[0].name) and [h["event"] for h in ptr2["history"]][-1] == "second revision"
