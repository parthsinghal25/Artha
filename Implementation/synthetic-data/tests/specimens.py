"""Disposable specimen returns for interface tests. FIXTURE TEXT ONLY: these are not source prose, are never published and
exist so the declared return shapes can be pushed through the check and the real renderers."""
import json
from pathlib import Path

REQ, CASE = "REQ-2025-11-01", "C-e1da28538652e263887a"
FILES_D01 = ["DC-11.pdf", "ID-11.pdf", "RF-11.pdf"]


def sc(card, profile, typos=(), features=("fixture feature",)):
    return {"card": card, "applied_profile": profile, "features_used": list(features),
            "typos_used": [{"from": a, "to": b} for a, b in typos], "protected_values_checked": True, "concerns": []}


def d01(**kw):
    d = {"card": "11-D01", "revision": 1, "kind": "email", "greeting": "hi",
         "blocks": [{"p": f"Fixture text for request {REQ}: attached are {', '.join(FILES_D01)}, being DC-11, ID-11 and RF-11."}],
         "signoff": ["Beth"], "style_completion": sc("11-D01", "M2")}
    d.update(kw)
    return d


def d02(**kw):
    d = {"card": "11-D02", "revision": 1, "kind": "portal_json",
         "free_text": f"fixture portal text about {REQ} and DC-11 being asked for again",
         "style_completion": sc("11-D02", "M3")}
    d.update(kw)
    return d


def d03(**kw):
    d = {"card": "11-D03", "revision": 1, "kind": "register_export", "style_completion": sc("11-D03", "S0", features=())}
    d.update(kw)
    return d


D07_FILES = ["DC-11.pdf", "ID-11.pdf", "RF-11.pdf", "11-04-claimant-contact-identity-verification-record.pdf", "11-05-portal-upload-receipt.jpg"]


def d07(**kw):
    d = {"card": "11-D07", "revision": 1, "kind": "email", "greeting": "",
         "blocks": [{"p": f"fixture: {CASE} / {REQ}. pls send the record for DC-11 and say what RF-11 still lacks, by 7 January 2026."},
                    {"ul": D07_FILES}],
         "signoff": ["Alicia"], "in_reply_to_card": "11-D05", "style_completion": sc("11-D07", "I2")}
    d.update(kw)
    return d


def d11(**kw):
    conv = [{"n": 1, "speaker": "Beth Martin", "text": f"Fixture: it's about {REQ}, I sent DC-11, ID-11 and RF-11 already."},
            {"n": 2, "speaker": "Alicia Romero", "text": "Fixture: I can see the upload receipt listing them, but the checklist still shows DC-11 outstanding."},
            {"n": 3, "speaker": "Beth Martin", "text": "Fixture: so which item is missing exactly?"},
            {"n": 4, "speaker": "Alicia Romero", "text": "Fixture: I will check and come back to you, no date yet."}]
    d = {"card": "11-D11", "revision": 1, "kind": "call_summary", "conversation": conv,
         "summary": (f"Fixture summary for {REQ}. Beth Martin said DC-11, ID-11 and RF-11 were sent already and asked which item is missing. "
                     "Alicia Romero said the upload receipt lists them and the checklist shows DC-11 outstanding, and that she would check and report back without a date."),
         "follow_up_items": ["Alicia Romero to check and report back; no date given."],
         "fidelity": {"claims": [{"claim": "checklist shows DC-11 outstanding", "turns": [2], "check_strings": ["DC-11 outstanding"]},
                                 {"claim": "no date given", "turns": [4], "check_strings": ["no date yet"]}]},
         "style_completion": sc("11-D11", "V2")}
    d.update(kw)
    return d


def d13(**kw):
    d = {"card": "11-D13", "revision": 1, "kind": "email", "greeting": "",
         "blocks": [{"p": f"Fixture decision {CASE} {REQ}: approval APR-11-01 by Rebecca Mason, conditional on verification of DC-11 separate from RF-11. "
                          "A remedy of $100.00 is approved; no payment is made by this approval. See 11-08-applicable-delegate-authority-excerpt.pdf."}],
         "signoff": ["Rebecca Mason"], "style_completion": sc("11-D13", "A1")}
    d.update(kw)
    return d


def d20(**kw):
    d = {"card": "11-D20", "revision": 1, "kind": "letter_with_cover_email", "greeting": "Dear Beth,",
         "blocks": [{"p": "Fixture cover note: our response is attached."}], "signoff": ["Kind regards,", "Alicia Romero"],
         "letter": {"title": "Our position on your complaint", "salutation": "Dear Beth,",
                    "blocks": [{"h": "Where things stand"},
                               {"p": f"Fixture letter for {CASE}, request {REQ}. DC-11 is separate from the RF-11 witness question."},
                               {"ul": ["first fixture point", "second fixture point"]}],
                    "signoff": ["Yours sincerely,", "", "Alicia Romero", "Complaints Case Manager"]},
         "style_completion": sc("11-D20", "F1")}
    d.update(kw)
    return d


ALL = {"11-D01": d01, "11-D02": d02, "11-D03": d03, "11-D07": d07, "11-D11": d11, "11-D13": d13, "11-D20": d20}


def write(tmp, d, name=None):
    f = Path(tmp) / (name or f"{d['card']}.json")
    f.write_text(json.dumps(d))
    return f
