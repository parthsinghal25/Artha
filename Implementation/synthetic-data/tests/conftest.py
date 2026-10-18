import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdgen.build import build  # noqa: E402
from sdgen.paths import AUTHORED, RELEASE_ROOT  # noqa: E402

REAL_V1 = RELEASE_ROOT / "case-11-rehearsal-v1"
RULING = AUTHORED / "case-11/supplemental-rulings/SR-1-case11-rehearsal-v2.md"
needs_v1 = pytest.mark.skipif(not REAL_V1.exists(), reason="delivered v1 package not present")


@pytest.fixture(scope="session")
def pkg(tmp_path_factory):
    """A fresh build of v1 from authored v1 inputs in a disposable root."""
    root = tmp_path_factory.mktemp("rel")
    return build("case11-rehearsal", 1, 1, root).dir


@pytest.fixture()
def pkg_copy(pkg, tmp_path):
    dst = tmp_path / "copy"
    shutil.copytree(pkg, dst)
    return dst


@pytest.fixture(scope="session")
def v1_root(tmp_path_factory):
    """Disposable root holding a COPY of the real delivered v1 (with its external review records). The real v1 is never written."""
    root = tmp_path_factory.mktemp("v1root")
    shutil.copytree(REAL_V1, root / "case-11-rehearsal-v1")
    return root


@pytest.fixture(scope="session")
def v2(v1_root):
    from sdgen.release import release
    batch, res, ok, published = release("case11-rehearsal", 2, 2, v1_root, rulings=[RULING])
    assert published, res["summary"]
    return batch.dir


@pytest.fixture()
def v2_copy(v2, tmp_path):
    root = tmp_path / "r"
    root.mkdir()
    shutil.copytree(v2, root / v2.name)
    shutil.copytree(v2.parent / "case-11-rehearsal-v1", root / "case-11-rehearsal-v1")
    return root / v2.name


REAL_V2 = RELEASE_ROOT / "case-11-rehearsal-v2"
RULING2 = AUTHORED / "case-11/supplemental-rulings/SR-2-case11-rehearsal-v3.md"
needs_v2 = pytest.mark.skipif(not REAL_V2.exists(), reason="delivered v2 package not present")


@pytest.fixture(scope="session")
def v2_root(tmp_path_factory):
    """Disposable root holding a COPY of the real, externally reviewed v2. The real v2 is never written."""
    root = tmp_path_factory.mktemp("v2root")
    shutil.copytree(REAL_V2, root / "case-11-rehearsal-v2")
    return root


@pytest.fixture(scope="session")
def v3(v2_root):
    from sdgen.release import release
    batch, res, ok, published = release("case11-rehearsal", 3, 3, v2_root, rulings=[RULING2])
    assert published, res["summary"]
    return batch.dir


@pytest.fixture()
def v3_copy(v3, tmp_path):
    root = tmp_path / "r"
    root.mkdir()
    shutil.copytree(v3, root / v3.name)
    shutil.copytree(v3.parent / "case-11-rehearsal-v2", root / "case-11-rehearsal-v2")
    return root / v3.name


# ---------------------------------------------------------------- worker-readiness fixtures (all disposable)
import json  # noqa: E402

REAL_V3 = RELEASE_ROOT / "case-11-rehearsal-v3"
needs_v3 = pytest.mark.skipif(not REAL_V3.exists(), reason="delivered v3 package not present")


@pytest.fixture(scope="session")
def pack_set(tmp_path_factory):
    """Pack set 2 built into a disposable directory (the real Data/synthesis pack sets are never written)."""
    from sdgen import workpack2
    base = tmp_path_factory.mktemp("ps")
    root = base / "worker-packs-v3"
    idx = workpack2.build_all(root)
    return root, idx


@pytest.fixture()
def pack_set_copy(pack_set, tmp_path):
    """A private copy of the pack set (and its output folders) for tests that mutate packs or the ledger."""
    root, idx = pack_set
    dst = tmp_path / "worker-packs-v3"
    shutil.copytree(root, dst)
    shutil.copytree(root.parent / "worker-outputs-v3", tmp_path / "worker-outputs-v3")
    return dst


@pytest.fixture()
def pack11(pack_set):
    root, idx = pack_set
    return root / next(p["pack"] for p in idx if p["case"] == 11)


@pytest.fixture()
def reviews(tmp_path):
    d = tmp_path / "reviews"
    d.mkdir()
    return d


@pytest.fixture()
def accepted_bytes():
    d = REAL_V3 / "intake-bundle/originals/attachments"
    if not d.exists():
        pytest.skip("delivered v3 package not present")
    return {f.name: f.read_bytes() for f in d.iterdir()}


def write_decision(reviews_dir, root, case, stage, kind="authoring_stage_signoff", decision="approved", name=None, fields=None, **over):
    """A well-formed external decision record bound to the pack currently on disk. `over` overrides/removes fields."""
    index = json.loads((Path(root) / "PACKS-INDEX.json").read_text())
    row = next(p for p in index["packs"] if p["case"] == case)
    rec = {"kind": kind, "schema_version": 1, "decision": decision, "reviewer": "Principal Data Engineer (fixture)",
           "reviewer_role": "Principal Data Engineer", "decided_at": "2026-10-01T00:00:00Z", "case": case, "stage": stage,
           "pack": row["pack"], "pack_digest": row["pack_digest"], "pack_set_version": index["pack_set_version"]}
    rec.update(over)
    rec.update(fields or {})
    rec = {k: v for k, v in rec.items() if v is not ...}
    f = Path(reviews_dir) / (name or f"{kind}-{case}-{stage}.json")
    f.write_text(json.dumps(rec))
    return f
