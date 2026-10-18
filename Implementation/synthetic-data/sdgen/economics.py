"""Measured effort/cost record. Only facts we can measure are filled; unknown stays null. No per-record price is
inferred from any token counter."""
import json
from pathlib import Path

from .effort import read_log
from .paths import TOOL_ROOT


def _words(d):
    n = sum(len(b.get("p", "").split()) + sum(len(x.split()) for x in b.get("ul", []) + b.get("ol", [])) for b in d.get("blocks", []))
    n += len(d.get("summary", "").split())
    if "letter" in d:
        n += sum(len(b.get("p", "").split()) + sum(len(x.split()) for x in b.get("ul", [])) for b in d["letter"]["blocks"])
    return n


def write_economics(pkg_dir, extra=None):
    pkg = Path(pkg_dir)
    extra = extra or {}
    man = json.loads((pkg / "intake-bundle/delivery-manifest.json").read_text())
    br = json.loads((pkg / "restricted/private-controls/build-record.json").read_text())
    log_path = pkg / "restricted/review/repair-log.json"
    repair = json.loads(log_path.read_text()) if log_path.exists() else {"defects": []}
    originals = man["originals"]
    words = {}
    for card, info in br["authored_used"].items():
        words[card] = _words(json.loads((TOOL_ROOT / info["path"]).read_text()))
    changed = [o for o in originals if o["lineage"]["relation"] == "revision" and "carried_forward" not in o["lineage"]]
    edited = [c for c, i in br["authored_used"].items() if i["revision"] > 1]
    res = {
        "batch": man["batch"]["batch_id"], "package_version": man["batch"]["package_version"],
        "measured": {
            "originals": len(originals),
            "emails": sum(o["kind"] == "email" for o in originals), "call_summaries": sum(o["kind"] == "call_summary" for o in originals),
            "letters": sum(o["kind"] == "letter" for o in originals),
            "reusable_attachment_originals": sum(o["kind"] == "attachment_original" for o in originals),
            "attachment_occurrences_in_messages": len(man["attachment_occurrences"]),
            "advertised_unheld": len(man["advertised_unheld"]),
            "bundle_bytes_total": sum(o["size_bytes"] for o in originals),
            "render_seconds": br["timings"],
        },
        "repair_scope": {
            "directly_edited_prose_cards": edited,
            "directly_edited_prose_words_in_this_revision": {c: words[c] for c in edited},
            "quote_dependent_originals_changed_without_own_edit": [o["staging_card_ref"] for o in changed if o["staging_card_ref"] not in edited],
            "changed_original_files": [o["filename"] for o in changed],
            "carried_forward_byte_identical": [c["original_id"] for c in br.get("carried_forward", [])],
            "counts": {"cards_in_batch": len(man["coverage"]["cards_in_batch"]), "directly_edited_cards": len(edited), "original_files": len(originals),
                       "changed_original_files": len(changed), "unchanged_original_files": len(originals) - len(changed)},
            "reviewed_denominators": f"{len(edited)} of {len(man['coverage']['cards_in_batch'])} cards directly edited; {len(changed)} of {len(originals)} original files changed bytes; {len(originals) - len(changed)} of {len(originals)} unchanged; these are different denominators and not estimates for the full pilot",
        },
        "tooling_work_is_separate": {
            "note": "R03, R04, timezone, scan-scope and fixture work is tooling effort, not prose-defect count",
            "items": ["R03 staging/atomic publish/review-record protection", "R04 exact-input provenance + layered verification + carry-forward",
                      "AEDT removal with Australia/Melbourne rules", "scan-scope correction", "new fidelity/regression fixtures"],
        },
        "author_side_defects": repair["defects"],
        "model_usage": {"prose_author": "Claude (Anthropic) acting as the coding assistant in the Claude Code desktop session",
                        "api_input_tokens": None, "api_output_tokens": None, "dollar_cost": None,
                        "note": "Per-call API usage and dollars are not exposed to the tool, so they are unknown. No per-record price is inferred from any session token counter."},
        "effort": {"logged_entries": read_log(),
                   "author_wall_clock": extra.get("author_wall_clock"),
                   "active_minutes": None, "reviewer_minutes": None,
                   "note": "wall-clock bounds are not active time; active author/review minutes stay null until logged with `sdgen effort`"},
        "caveat": "Author-side and not independent review. The independent-review repair rate for v2 is unmeasured.",
    }
    (pkg / "restricted/review/ECONOMICS-AND-REPAIR.json").write_text(json.dumps(res, indent=2) + "\n")
    return res
