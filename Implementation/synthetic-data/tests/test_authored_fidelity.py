"""Acceptance 1: authored histories stay distinct; supplied originals and attachments keep exact bytes; source text is
preserved with no template replacement; rebuilds are byte-reproducible."""
import hashlib
import json

import pytest

from authored_helpers import EXAMPLE, Input
from sdgen.authored.export import build
from sdgen.authored.render import eml_parts, pdf_text
from sdgen.authored.verify import read_tree


def sha(b):
    return hashlib.sha256(b).hexdigest()


@pytest.fixture(scope="module")
def cand(tmp_path_factory):
    out = tmp_path_factory.mktemp("cands")
    res = build(EXAMPLE, out)
    assert res["ok"], res["findings"]
    return out / "fx-five-members-p1"


def bundle(cand):
    files = read_tree(cand / "runtime/intake-bundle")
    m = json.loads(files["delivery-manifest.json"])
    return files, {e["original_id"]: e for e in m["originals"]}, m


def authored(sid):
    rows = [json.loads(x) for x in (EXAMPLE / "runtime/sources.jsonl").read_text().splitlines() if x.strip()]
    return next(r for r in rows if r["source_id"] == sid)


def test_two_similar_histories_remain_distinct(cand):
    files, by, _ = bundle(cand)
    nair = eml_parts(files[by["FX-SRC-0104"]["file"]])[1]
    alvarez = eml_parts(files[by["FX-SRC-0203"]["file"]])[1]
    assert nair == authored("FX-SRC-0104")["content"]["body_text"]
    assert alvarez == authored("FX-SRC-0203")["content"]["body_text"]
    assert "dishonour fee" in nair and "dishonour" not in alvarez
    # the lookalike's own explanation survives: payments asked for a BSB; the first member's change was accepted
    assert "BSB is missing" in eml_parts(files[by["FX-SRC-0202"]["file"]])[1]
    assert "have been accepted" in eml_parts(files[by["FX-SRC-0103"]["file"]])[1]


def test_text_is_exact_without_template_additions(cand):
    files, by, _ = bundle(cand)
    for sid in ("FX-SRC-0105", "FX-SRC-0106", "FX-SRC-0302", "FX-SRC-0503"):
        assert files[by[sid]["file"]].decode("utf-8") == authored(sid)["content"]["text"]
    for sid in ("FX-SRC-0101", "FX-SRC-0201", "FX-SRC-0306", "FX-SRC-0402"):
        assert eml_parts(files[by[sid]["file"]])[1] == authored(sid)["content"]["body_text"]
    pdf = " ".join(pdf_text(files[by["FX-SRC-0403"]["file"]]).split())
    assert "One case fee applies to this matter. It covers both internal complaint references listed." in pdf


def test_supplied_originals_and_attachments_keep_exact_bytes(cand):
    files, by, m = bundle(cand)
    eml_in = (EXAMPLE / "runtime/files/2026-04-28_1347_mei-lin-zhou_fee.eml").read_bytes()
    png_in = (EXAMPLE / "runtime/files/statement-fee-screenshot.png").read_bytes()
    assert files[by["FX-SRC-0501"]["file"]] == eml_in
    assert files[by["FX-SRC-0502"]["file"]] == png_in
    assert sha(png_in) in {p["sha256"] for p in eml_parts(eml_in)[2]}
    inv = sha(files[by["FX-SRC-0403"]["file"]])
    carriers = [o["parent_original_id"] for o in m["attachment_occurrences"] if o["references_original"] == "FX-SRC-0403"]
    assert sorted(carriers) == ["FX-SRC-0404", "FX-SRC-0405"]
    for sid in carriers:
        assert inv in {p["sha256"] for p in eml_parts(files[by[sid]["file"]])[2]}


def test_rebuild_is_byte_identical(tmp_path, cand):
    res = build(EXAMPLE, tmp_path, name="again")
    assert res["ok"]
    a, b = read_tree(cand / "runtime"), read_tree(tmp_path / "again/runtime")
    assert a == b


def test_one_member_repair_changes_only_that_members_originals(tmp_path, cand):
    inp = Input(tmp_path)
    inp.source("FX-SRC-0306", lambda r: r["content"].update(body_text=r["content"]["body_text"].replace("Lucy,", "Hi Lucy,")))
    inp.batch(lambda b: b.update(package_version=2))
    res = build(inp.root, tmp_path / "out")
    assert res["ok"], res["findings"]
    from sdgen.authored.verify import diff_candidates
    d = diff_candidates(cand, tmp_path / "out/fx-five-members-p2")
    assert d["bytes_changed"] == ["FX-SRC-0306"] and not d["added"] and not d["removed"]
    assert len(d["unchanged"]) == 28
