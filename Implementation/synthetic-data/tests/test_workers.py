"""Attachment renderer determinism across all forty plans. Pack, stage-ledger and author-check tests live in test_w01_contract.py,
test_w02_stage.py and test_w03_checks.py; asset CONTENT checks in test_asset_content.py."""
import collections
import glob
import io
import json
from pathlib import Path

import openpyxl
import pytest

from sdgen.assets import AuthoringRequired, render_asset
from sdgen.paths import PLAN_DIR


def test_every_held_non_email_asset_in_all_forty_plans_renders_deterministically():
    kinds, authoring = collections.Counter(), 0
    for f in sorted(glob.glob(str(PLAN_DIR / "assistant-packs/*.json"))):
        d = json.load(open(f))
        names = {a["id"]: a["name"] for a in d["actors"]}
        for a in d["assets"]:
            if a.get("delivery_state") != "held":
                continue
            try:
                b1, _ = render_asset(a, names[a["owner"]])
            except AuthoringRequired:
                authoring += 1
                continue
            b2, _ = render_asset(a, names[a["owner"]])
            assert b1 == b2 and b1, a["filename"]
            if a["media_type"].endswith("sheet"):
                ws = openpyxl.load_workbook(io.BytesIO(b1)).active
                assert ws.max_row == 1 + max(1, len(a["document_spec"]["rows"]))
            kinds[a["media_type"]] += 1
    assert sum(kinds.values()) == 217 + 64 + 62 + 7 + 1 and authoring == 15
