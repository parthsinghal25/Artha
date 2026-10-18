"""W03: the author check trusts the card, not the writer's own checklist. Each bypass the independent review reproduced is a
regression case here, plus negative fixtures for fixed values, kinds, quotes, typo occurrences and leaked controls.
Specimen prose is fixture text in disposable folders; none of it is source material."""
import copy
import json
import shutil

import pytest

import specimens as S
from sdgen.authoring_check import author_check

STAGE = "style_microbatch"


def run(tmp_path, pack, d, stage=STAGE):
    return author_check(S.write(tmp_path, d), pack, stage)


def failed(rows, prefix=None):
    return [r for r in rows if not r["ok"] and (prefix is None or r["id"].startswith(prefix))]


def ok(rows):
    return not failed(rows)


@pytest.mark.parametrize("card", list(S.ALL))
def test_each_conforming_specimen_passes_and_is_not_acceptance(tmp_path, pack11, card):
    rows = run(tmp_path, pack11, S.ALL[card]())
    assert ok(rows), failed(rows)
    assert any(r["id"] == "A12" and "attestation only" in r["detail"] for r in rows)


# ---- reproduced independent probes -----------------------------------------------------------------------
def test_probe_missing_locked_request_reference_no_longer_passes_with_empty_required_tokens(tmp_path, pack11):
    d = S.d01(blocks=[{"p": "Fixture only: DC-11.pdf, ID-11.pdf, RF-11.pdf (DC-11, ID-11, RF-11)."}], required_tokens=[])
    rows = run(tmp_path, pack11, d)
    bad = failed(rows, "A6")
    assert any("request_reference REQ-2025-11-01" in r["detail"] for r in bad), rows


def test_probe_five_repeated_typos_fail_a_one_typo_ceiling_even_with_one_declared_spelling(tmp_path, pack11):
    text = " ".join(["pleae"] * 5) + f" see {S.REQ}: {', '.join(S.FILES_D01)}, DC-11, ID-11, RF-11"
    d = S.d01(blocks=[{"p": text}])
    d["style_completion"] = S.sc("11-D01", "M2", typos=[("please", "pleae")])
    rows = run(tmp_path, pack11, d)
    assert any("5 misspelling occurrence" in r["detail"] and "ceiling 1" in r["detail"] for r in failed(rows, "A10c"))
    assert failed(rows, "A10d")  # five occurrences vs one declaration


def test_typos_counted_per_occurrence_and_reconciled_with_declarations(tmp_path, pack11):
    base = f"{S.REQ} {', '.join(S.FILES_D01)} DC-11 ID-11 RF-11"
    one = S.d01(blocks=[{"p": "pleae see " + base}])
    one["style_completion"] = S.sc("11-D01", "M2", typos=[("please", "pleae")])
    assert ok(run(tmp_path, pack11, one))  # M2 allows one; declared; present
    undeclared = S.d01(blocks=[{"p": "pleae see " + base}])
    assert failed(run(tmp_path, pack11, undeclared), "A10d")
    phantom = S.d01()
    phantom["style_completion"] = S.sc("11-D01", "M2", typos=[("please", "pleae")])
    assert failed(run(tmp_path, pack11, phantom), "A10d")  # declared but not in the text
    plural = S.d01(blocks=[{"p": "one mesages: " + base}])
    plural["style_completion"] = S.sc("11-D01", "M2", typos=[("message", "mesage")])
    assert ok(run(tmp_path, pack11, plural))  # a plural form is one occurrence of that misspelling
    twice = S.d01(blocks=[{"p": "mesage and mesages: " + base}])
    twice["style_completion"] = S.sc("11-D01", "M2", typos=[("message", "mesage")])
    assert failed(run(tmp_path, pack11, twice), "A10c")  # two occurrences, one declared spelling, ceiling one
    two_forms = S.d01(blocks=[{"p": "pleae thnaks " + base}])
    two_forms["style_completion"] = S.sc("11-D01", "M2", typos=[("please", "pleae"), ("thanks", "thnaks")])
    assert failed(run(tmp_path, pack11, two_forms), "A10c")  # two distinct spellings over a ceiling of one
    off_list = S.d01()
    off_list["style_completion"]["typos_used"] = [{"from": "please", "to": "plese"}]
    assert failed(run(tmp_path, pack11, off_list), "A2")  # not even representable: schema enum rejects it


def test_zero_ceiling_profiles_reject_any_misspelling(tmp_path, pack11):
    d = S.d13()
    d["blocks"][0]["p"] += " pleae confirm."
    assert failed(run(tmp_path, pack11, d), "A10c")
    d11 = S.d11()
    d11["summary"] += " The mesage was noted."
    assert failed(run(tmp_path, pack11, d11), "A10c")


def test_native_register_carries_no_deliberate_errors(tmp_path, pack11):
    d = S.d03()
    d["style_completion"]["typos_used"] = [{"from": "please", "to": "pleae"}]
    assert failed(run(tmp_path, pack11, d), "A10")


# ---- fixed values derived from the trusted card ---------------------------------------------------------------
@pytest.mark.parametrize("make,mutate,needle", [
    (S.d01, lambda d: d["blocks"][0].update(p=d["blocks"][0]["p"].replace("REQ-2025-11-01", "REQ-2025-11-02")), "request_reference"),
    (S.d01, lambda d: d["blocks"][0].update(p=d["blocks"][0]["p"].replace("RF-11.pdf", "RF-12.pdf")), "attachment filename RF-11.pdf"),
    (S.d07, lambda d: d["blocks"][0].update(p=d["blocks"][0]["p"].replace("7 January 2026", "8 January 2026")), "requested_reply_date"),
    (S.d07, lambda d: d["blocks"][0].update(p=d["blocks"][0]["p"].replace("C-e1da28538652e263887a", "C-e1da28538652e263887b")), "C-e1da28538652e263887a"),
    (S.d13, lambda d: d["blocks"][0].update(p=d["blocks"][0]["p"].replace("$100.00", "$1,000.00")), "remedy_amount_minor"),
    (S.d13, lambda d: d["blocks"][0].update(p=d["blocks"][0]["p"].replace("$100.00", "$10.00")), "remedy_amount_minor"),
    (S.d13, lambda d: d["blocks"][0].update(p=d["blocks"][0]["p"].replace("$100.00", "one hundred dollars")), "remedy_amount_minor"),
    (S.d13, lambda d: d["blocks"][0].update(p=d["blocks"][0]["p"].replace("APR-11-01", "APR-11-02")), "approval_reference"),
    (S.d13, lambda d: (d["blocks"][0].update(p=d["blocks"][0]["p"].replace("Rebecca Mason", "Rebecca Mayson")), d.update(signoff=["R. Mayson"])), "decision_maker name"),
    (S.d13, lambda d: d["blocks"][0].update(p=d["blocks"][0]["p"].replace("C-e1da28538652e263887a ", "")), "case_id"),
    (S.d20, lambda d: d["letter"]["blocks"][1].update(p="Fixture letter with no references at all."), "request_reference"),
])
def test_missing_or_wrong_fixed_values_fail_even_when_writer_declares_nothing(tmp_path, pack11, make, mutate, needle):
    d = make()
    mutate(d)
    d["required_tokens"] = []
    rows = run(tmp_path, pack11, d)
    assert any(needle in r["detail"] for r in failed(rows, "A6")), [r for r in rows if not r["ok"]]


def test_amount_dates_and_money_forms_accepted(tmp_path, pack11):
    for money in ("$100.00", "A$100.00", "AUD 100.00"):
        d = S.d13()
        d["blocks"][0]["p"] = d["blocks"][0]["p"].replace("$100.00", money)
        assert ok(run(tmp_path, pack11, d)), money
    for when in ("7 January 2026", "7 Jan", "2026-01-07", "07/01/2026", "January 7"):
        d = S.d07()
        d["blocks"][0]["p"] = d["blocks"][0]["p"].replace("7 January 2026", when)
        assert ok(run(tmp_path, pack11, d)), when


def test_native_register_values_are_checked_against_the_rendered_record_not_writer_claims(tmp_path, pack11):
    rows = run(tmp_path, pack11, S.d03())
    native = [r for r in rows if r["id"] == "A6n"]
    assert len(native) == 15 and all(r["ok"] for r in native)  # 5 locked + 5 approved values + 5 approved column headers
    assert not [r for r in rows if r["id"] == "A6"]  # no prose rules for a native record


# ---- wrong kind -------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("card,kind", [("11-D01", "portal_json"), ("11-D01", "register_export"), ("11-D02", "email"), ("11-D03", "email"),
                                      ("11-D07", "call_summary"), ("11-D11", "email"), ("11-D13", "note"), ("11-D20", "email"),
                                      ("11-D20", "letter_with_cover_email_x")])
def test_incompatible_kind_is_refused_from_the_trusted_card(tmp_path, pack11, card, kind):
    d = S.ALL[card]()
    d["kind"] = kind
    rows = run(tmp_path, pack11, d)
    assert failed(rows, "A2") or failed(rows, "A3")


def test_kind_with_the_right_name_but_for_a_different_card_is_refused(tmp_path, pack11):
    d = S.d20(card="11-D13")
    d["style_completion"]["card"] = "11-D13"
    assert failed(run(tmp_path, pack11, d), "A3") or failed(run(tmp_path, pack11, d), "A2")


# ---- quotes ----------------------------------------------------------------------------------------------------
def _prior(pack):
    return {o["card"]: o for o in json.loads((pack / "context/ACCEPTED-PRIOR-ORIGINALS.json").read_text())["originals"]}


def _passage(pack, card="11-D05"):
    """A character-exact passage: one physical line of the accepted text."""
    text = _prior(pack)[card]["text"]
    return next(ln for ln in text.split("\n") if len(ln.strip()) > 40 and ln == ln.strip())


def test_exact_quote_of_accepted_earlier_text_passes_and_altered_quote_fails(tmp_path, pack11):
    q = _passage(pack11)
    d = S.d07()
    d["blocks"].insert(1, {"quote": q, "from_card": "11-D05"})
    assert ok(run(tmp_path, pack11, d)), failed(run(tmp_path, pack11, d))
    d["blocks"][1]["quote"] = q[:20] + "X" + q[21:]
    assert failed(run(tmp_path, pack11, d), "A5")
    d["blocks"][1]["quote"] = q + " and one more thing"
    assert failed(run(tmp_path, pack11, d), "A5")


def test_quote_must_come_from_an_available_earlier_card(tmp_path, pack11):
    q = _passage(pack11, "11-D10")
    d = S.d07()  # 11-D07 (5 Jan) predates 11-D10 (8 Jan): quoting it would be later knowledge
    d["blocks"].insert(1, {"quote": q, "from_card": "11-D10"})
    assert failed(run(tmp_path, pack11, d), "A5")
    d["blocks"][1] = {"quote": "anything", "from_card": "11-D15"}
    assert failed(run(tmp_path, pack11, d), "A5")
    d["blocks"][1] = {"quote": "anything", "from_card": "11-D99"}
    assert failed(run(tmp_path, pack11, d), "A5")


def test_quoted_text_is_exempt_from_style_counters_and_leak_scans(tmp_path, pack11, pack_set_copy):
    pack = next(pack_set_copy.glob("11-C-*"))
    ctx = json.loads((pack / "context/ACCEPTED-PRIOR-ORIGINALS.json").read_text())
    quoted = "pleae send the thnaks mesage and the style profile M2 text exactly."
    next(o for o in ctx["originals"] if o["card"] == "11-D05")["text"] += "\n" + quoted
    (pack / "context/ACCEPTED-PRIOR-ORIGINALS.json").write_text(json.dumps(ctx))
    d = S.d07()
    d["blocks"].insert(1, {"quote": quoted, "from_card": "11-D05"})
    rows = run(tmp_path, pack, d)
    assert ok(rows), failed(rows)  # I2 allows zero typos and no control words, yet the copied original is untouched
    d["blocks"][1]["quote"] = quoted.replace("pleae", "please")
    assert failed(run(tmp_path, pack, d), "A5")  # "correcting" the original is a fidelity failure


def test_forwarding_is_not_part_of_the_worker_contract(tmp_path, pack11):
    d = S.d07(forward_of="11-D05")
    rows = run(tmp_path, pack11, d)
    assert any("unexpected field(s) ['forward_of']" in r["detail"] for r in failed(rows, "A2"))


# ---- leaked control material ------------------------------------------------------------------------------------
@pytest.mark.parametrize("leak", ["written in style profile M2", "applied profile I2", "roughness level", "per the fact card",
                                  "see source_assertions", "protected values were checked", "writer instructions say", "this is a naturalistic sample",
                                  "micro-batch card", "typo ceiling", "synthetic complaint", "the stage gate", "private dialogue", "holdout set"])
def test_control_vocabulary_is_rejected_in_any_document_text(tmp_path, pack11, leak):
    d = S.d01()
    d["blocks"].append({"p": leak})
    assert failed(run(tmp_path, pack11, d), "A11")


def test_control_vocabulary_in_private_dialogue_and_letter_and_signoff_is_rejected(tmp_path, pack11):
    d = S.d11()
    d["conversation"][1]["text"] += " (the fact card says so)"
    assert failed(run(tmp_path, pack11, d), "A11")
    d = S.d20()
    d["letter"]["blocks"].append({"p": "per the writer instructions"})
    assert failed(run(tmp_path, pack11, d), "A11")
    d = S.d13()
    d["signoff"] = ["Rebecca Mason, profile M2"]
    assert failed(run(tmp_path, pack11, d), "A11")


def test_ordinary_use_of_profile_and_stage_is_not_rejected(tmp_path, pack11):
    d = S.d01()
    d["blocks"].append({"p": "I also updated my profile on the portal and I am at the stage of waiting; the M2 form is not mentioned."})
    assert ok(run(tmp_path, pack11, d)), failed(run(tmp_path, pack11, d))
    d = S.d01()
    d["blocks"].append({"p": "Please note my I1 and F2 reference codes."})
    assert ok(run(tmp_path, pack11, d))


# ---- forbidden: future knowledge, foreign identifiers -----------------------------------------------------------
@pytest.mark.parametrize("extra,needle", [
    ("see DOC-11-NDA", "DOC-11-NDA"),
    ("approval APR-11-01 is expected", "APR-11-01"),
    ("also 11-10-new-dc-11-assessment.pdf", "11-10-new-dc-11-assessment.pdf"),
    ("UP-11-20251221", "UP-11-20251221"),
    ("complaint C-e1da28538652e263887a", "complaint id"),
    ("complaint C-219eaa57d9f9498c4846", "case id other"),
    ("request REQ-2025-05-01", "request reference other"),
])
def test_future_knowledge_and_foreign_identifiers_are_forbidden_for_the_earliest_card(tmp_path, pack11, extra, needle):
    d = S.d01()
    d["blocks"].append({"p": extra})
    assert any(needle in r["detail"] for r in failed(run(tmp_path, pack11, d), "A7")), extra


def test_the_card_that_owns_a_token_may_use_it(tmp_path, pack11):
    assert ok(run(tmp_path, pack11, S.d13()))  # APR-11-01 and the attached DOC are legitimate for 11-D13


# ---- writer declarations can only add checks --------------------------------------------------------------------
def test_empty_declarations_do_not_weaken_the_derived_checks(tmp_path, pack11):
    d = S.d13(required_tokens=[], must_not_match=[])
    d["blocks"][0]["p"] = "decision made."
    assert len(failed(run(tmp_path, pack11, d), "A6")) >= 5


def test_writer_declared_additional_checks_are_applied_and_bad_regex_is_a_clear_failure(tmp_path, pack11):
    d = S.d01(required_tokens=["a phrase that is not there"], must_not_match=["fixture"])
    rows = run(tmp_path, pack11, d)
    assert failed(rows, "A8a") and failed(rows, "A8b")
    rows = run(tmp_path, pack11, S.d01(must_not_match=["("]))
    assert any("not a valid regular expression" in r["detail"] for r in failed(rows, "A8b"))


def test_protected_values_flag_is_only_an_attestation(tmp_path, pack11):
    d = S.d13()
    d["blocks"][0]["p"] = "decision made."
    assert d["style_completion"]["protected_values_checked"] is True
    rows = run(tmp_path, pack11, d)
    assert failed(rows, "A6") and any(r["id"] == "A12" and r["ok"] for r in rows)
    d["style_completion"]["protected_values_checked"] = False
    assert failed(run(tmp_path, pack11, d), "A12")


# ---- call-specific consistency ----------------------------------------------------------------------------------
def test_call_checks_speakers_turns_fidelity_strings_and_summary(tmp_path, pack11):
    d = S.d11()
    d["conversation"][0]["speaker"] = "Oliver Grant"
    assert failed(run(tmp_path, pack11, d), "A9b")
    d = S.d11()
    d["conversation"][2]["n"] = 7
    assert failed(run(tmp_path, pack11, d), "A9a")
    d = S.d11()
    d["fidelity"]["claims"][0]["check_strings"] = ["words never spoken"]
    assert failed(run(tmp_path, pack11, d), "A9c")
    d = S.d11()
    d["fidelity"]["claims"][0]["turns"] = [99]
    assert any("missing turn 99" in r["detail"] for r in failed(run(tmp_path, pack11, d), "A9c"))
    d = S.d11(summary="Short.")
    assert failed(run(tmp_path, pack11, d), "A9d")


# ---- stage and card scope ---------------------------------------------------------------------------------------
def test_stage_is_mandatory_and_must_list_the_card(tmp_path, pack11):
    f = S.write(tmp_path, S.d01())
    with pytest.raises(TypeError):
        author_check(f, pack11)  # noqa: no stage
    assert failed(author_check(f, pack11, "remaining_cards"), "A1a")  # a stage that is not launchable
    assert failed(author_check(f, pack11, "no_such_stage"), "A1a")


def test_rehearsal_accepted_cards_and_other_cards_cannot_be_authored(tmp_path, pack11):
    for card in ("11-D05", "11-D06", "11-D10", "11-D15", "11-D17"):
        rows = author_check(S.write(tmp_path, S.d01(card=card)), pack11, STAGE)
        assert failed(rows, "A1c")
    assert failed(author_check(S.write(tmp_path, S.d01(card="11-D14")), pack11, STAGE), "A1c")  # a remaining-stage card
    assert failed(author_check(S.write(tmp_path, S.d01(card="12-D01")), pack11, STAGE), "A1b")  # another member's card


def test_unreadable_returns_are_clear_failures(tmp_path, pack11):
    f = tmp_path / "bad.json"
    f.write_text("{not json")
    assert failed(author_check(f, pack11, STAGE), "A0")
    f.write_text("[1,2]")
    assert failed(author_check(f, pack11, STAGE), "A0")


# ---- approved clarifications -------------------------------------------------------------------------------------
def _with_clarifications(pack_set_copy, entries):
    pack = next(pack_set_copy.glob("11-C-*"))
    p = pack / "plan/APPROVED-CLARIFICATIONS.json"
    doc = json.loads(p.read_text())
    doc["clarifications"] = entries
    p.write_text(json.dumps(doc))
    return pack


def test_only_approved_clarifications_apply_and_can_require_forbid_and_waive(tmp_path, pack_set_copy):
    ok_entry = {"id": "CL-1", "card": "11-D01", "status": "approved", "approved_by": "Principal Data Engineer", "approved_on": "2026-10-16",
                "required_tokens": ["a fixed phrase"], "forbidden_tokens": ["never this"]}
    pending = {"id": "CL-2", "card": "11-D01", "status": "pending", "approved_by": "", "approved_on": "", "required_tokens": ["ignored phrase"]}
    pack = _with_clarifications(pack_set_copy, [ok_entry, pending])
    rows = run(tmp_path, pack, S.d01())
    assert any("clarification CL-1" in r["detail"] for r in failed(rows, "A6"))
    assert not any("ignored phrase" in r["detail"] for r in rows)
    d = S.d01()
    d["blocks"][0]["p"] += " a fixed phrase"
    assert ok(run(tmp_path, pack, d))
    d["blocks"][0]["p"] += " never this"
    assert any("CL-1" in r["detail"] for r in failed(run(tmp_path, pack, d), "A7"))


def test_clarification_waiver_removes_only_the_named_derived_requirement(tmp_path, pack_set_copy):
    pack = _with_clarifications(pack_set_copy, [{"id": "CL-3", "card": "11-D11", "status": "approved", "approved_by": "Principal Data Engineer",
                                                  "approved_on": "2026-10-16", "waive_required": ["reference ID-11"]}])
    d = S.d11()
    d["summary"] = d["summary"].replace("ID-11 ", "")
    assert ok(run(tmp_path, pack, d)), failed(run(tmp_path, pack, d))
    assert failed(run(tmp_path, pack, S.d11(summary=d["summary"].replace("DC-11", "the death certificate"))), "A6")


def test_clarification_can_add_native_register_columns(tmp_path, pack_set_copy):
    pack = _with_clarifications(pack_set_copy, [{"id": "CL-4", "card": "11-D03", "status": "approved", "approved_by": "Principal Data Engineer",
                                                  "approved_on": "2026-10-16", "native_columns": {"intake_channel": "portal"}}])
    rows = run(tmp_path, pack, S.d03())
    assert any("column intake_channel" in r["detail"] and r["ok"] for r in rows)
