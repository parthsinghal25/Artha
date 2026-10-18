"""Stage ledger, progress tracking and scope audit for authoring workers (W02).

Model
  * A job is one pack (one member). It has SEPARATE stage lifecycles (e.g. `style_microbatch` and `remaining_cards`), each
    queued -> working <-> question_raised -> submitted -> repair_required -> working ... -> independently_accepted.
  * A stage can start only with an APPROVAL record: an external JSON decision (kind `authoring_stage_signoff`, decision
    `approved`) bound to the exact case, stage, pack name, pack digest and pack-set version, located under the reviews folder,
    issued by a named non-assistant reviewer. An existing file is never enough. The record's sha256 is stored and re-checked on
    every start/resume; the pack's files are re-hashed against the pack digest too.
  * `independently_accepted` needs an ACCEPTANCE record (`authoring_stage_acceptance`, decision `accepted`) bound to the same
    case/stage/pack digest AND to the digest of the stage's output folder recorded at submission.
  * The stage is a required argument of every transition. A stage that `requires_accepted_stage` cannot start until that stage is
    accepted. One stage per case may be active; at most MAX_PARALLEL workers are active in the whole pack set (applies to resumes).
  * All updates run under an exclusive file lock; every change appends a hash-chained event to PROGRESS-EVENTS.jsonl, so history
    is preserved and tampering is detectable (`verify_ledger`). There is no reset or force option.

Honest limits: records are validated for form, binding and issuer, not authenticated (no signatures). The scope audit compares
file hashes before/after, so it detects WRITES outside the owned folder - it cannot show that a worker did not READ another pack.
There is no OS sandbox. Under concurrent workers a changed file inside another worker's owned folder is reported as
unattributable; it is not blamed on a specific writer."""
import fcntl
import hashlib
import json
import os
import re
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .paths import ROOT

MAX_PARALLEL_FIRST_TRIAL = 2
ACTIVE = ("working", "question_raised")
ALLOWED = {"queued": ["working"], "working": ["question_raised", "submitted"], "question_raised": ["working"],
           "submitted": ["repair_required", "independently_accepted"], "repair_required": ["working"], "independently_accepted": []}
DEFAULT_REVIEWS_ROOT = ROOT / "Data/reviews"
NOT_A_REVIEWER = re.compile(r"claude|coding[ -]assistant|synthetic[ -]data|assistant|supervisor|worker|subagent|bot\b", re.I)
SIGNOFF_KIND = "authoring_stage_signoff"
ACCEPT_KIND = "authoring_stage_acceptance"
SNAPSHOT_EXCLUDE = {".venv", "__pycache__", ".git", ".pytest_cache", "node_modules", ".staging"}
# Owned output folders. A ledger binds its folder name once, at init, from its own PACKS-INDEX.json (`outputs_dirname`). Ledgers
# created before that binding existed resolve through this fixed table of their pack-set version; it is history, never extended.
OUTPUTS_DIRNAME_RE = re.compile(r"worker-outputs(-v[1-9][0-9]*)?")
LEGACY_OUTPUTS_DIRNAME = {3: "worker-outputs-v3", 4: "worker-outputs-v4"}
SAFE_COMPONENT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")


class GateRefused(RuntimeError):
    pass


class LedgerIntegrityError(GateRefused):
    """PROGRESS.json no longer matches its event history; nothing proceeds until the difference is explained."""


class ConflictError(RuntimeError):
    """The caller's expected revision is stale."""


def _now():
    return datetime.now(timezone.utc)


def _stamp(dt=None):
    return (dt or _now()).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha_file(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def _canon(o):
    return json.dumps(o, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _parse(ts):
    dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _dir_inside(path, base):
    """Containment of a path's parent directory (the leaf itself, possibly a symlink, is not resolved)."""
    return _inside(os.path.dirname(path), base)


def _inside(path, base):
    """Path-boundary-aware containment after resolving symlinks: `owned-other` is NOT inside `owned`."""
    p, b = os.path.realpath(path), os.path.realpath(base)
    try:
        return os.path.commonpath([p, b]) == b
    except ValueError:
        return False


# --------------------------------------------------------------------------- storage
def _paths(root):
    root = Path(root)
    return root / "PROGRESS.json", root / "PROGRESS-EVENTS.jsonl", root / ".progress.lock"


@contextmanager
def _locked(root):
    _, _, lock = _paths(root)
    with open(lock, "a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _load(root):
    return json.loads(_paths(root)[0].read_text())


def state_payload_hash(state):
    """sha256 of the canonical state without head_hash (head_hash is derived from the event that carries this value)."""
    return hashlib.sha256(_canon({k: v for k, v in state.items() if k != "head_hash"}).encode()).hexdigest()


def _event(root, state, action, case, stage, detail):
    _, ev, _ = _paths(root)
    seq = state["revision"]
    rec = {"seq": seq, "at": _stamp(), "action": action, "case": case, "stage": stage, "detail": detail, "prev": state["head_hash"],
           "state_sha256": state_payload_hash(state)}
    rec["hash"] = hashlib.sha256(_canon(rec).encode()).hexdigest()
    with open(ev, "a") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    state["head_hash"] = rec["hash"]


def _commit(root, state, action, case, stage, detail):
    state["revision"] += 1
    _event(root, state, action, case, stage, detail)
    prog, _, _ = _paths(root)
    fd, tmp = tempfile.mkstemp(dir=str(Path(root)), prefix=".progress.", suffix=".tmp")
    with os.fdopen(fd, "w") as fh:
        fh.write(json.dumps(state, indent=1, ensure_ascii=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, prog)


def pack_digest_on_disk(root, pack):
    from .workpack2 import pack_digest
    return pack_digest(Path(root) / pack)[0]


def init(root):
    """Create the ledger once from PACKS-INDEX.json. Refuses if a ledger exists; there is no reset."""
    root = Path(root)
    prog, ev, lock = _paths(root)
    if prog.exists() or ev.exists():
        raise FileExistsError("progress ledger exists; it is never reset or re-created")
    index = json.loads((root / "PACKS-INDEX.json").read_text())
    jobs = []
    for p in index["packs"]:
        stages = json.loads((root / p["pack"] / "STAGE.json").read_text())["stages"]
        jobs.append({"case": p["case"], "pack": p["pack"], "pack_digest": p["pack_digest"], "blocked": p.get("blocked"),
                     "stages": {s["stage"]: {"state": "queued" if s.get("launchable") else "not_launchable", "launchable": bool(s.get("launchable")),
                                             "requires_accepted_stage": s.get("requires_accepted_stage"), "approval": None, "approval_history": [],
                                             "worker": None, "output_digest": None, "submitted_at": None, "submissions": [],
                                             "acceptance": None, "acceptance_history": []}
                                for s in stages}})
    binding = _outputs_binding_for_init(root)
    binding["packs_index_sha256"] = _sha_file(root / "PACKS-INDEX.json")
    state = {"kind": "worker_progress", "ledger_format": 2, "pack_set_version": index["pack_set_version"], "revision": 0, "head_hash": "0" * 64,
             "max_concurrent_workers": MAX_PARALLEL_FIRST_TRIAL, "outputs_binding": binding, "jobs": jobs}
    with _locked(root):
        _commit(root, state, "init", None, None, {"jobs": len(jobs), "outputs_dirname": binding["outputs_dirname"]})
    return state


def integrity_problems(root, state=None):
    """Why PROGRESS.json cannot be trusted, as a list ([] when intact). Checks the event chain, that every event carries the
    state payload hash, and that the CURRENT payload (approvals, digests, states, caps, revision) equals the last event's.
    Local detection of edits and truncation; an attacker who can rewrite both files consistently is out of scope (no authentication)."""
    prog, ev, _ = _paths(root)
    try:
        state = state if state is not None else json.loads(prog.read_text())
        lines = ev.read_text().splitlines()
    except (OSError, ValueError) as e:
        return [f"ledger unreadable: {e}"]
    if state.get("ledger_format") != 2:
        return ["legacy ledger without state binding (ledger_format != 2); it is read-only history and must be migrated explicitly"]
    problems, prev, last = [], "0" * 64, None
    for i, line in enumerate(lines, 1):
        try:
            rec = json.loads(line)
            h = rec.pop("hash")
        except (ValueError, KeyError):
            return [f"event {i} is not a valid record"]
        if rec.get("prev") != prev or hashlib.sha256(_canon(rec).encode()).hexdigest() != h or rec.get("seq") != i or "state_sha256" not in rec:
            return [f"event chain broken at event {i}"]
        prev, last = h, rec
    if last is None:
        return ["no events"]
    if prev != state.get("head_hash"):
        problems.append("head_hash does not match the last event")
    if len(lines) != state.get("revision"):
        problems.append(f"revision {state.get('revision')} does not match {len(lines)} events")
    if state_payload_hash(state) != last["state_sha256"]:
        problems.append("PROGRESS.json state payload differs from the state recorded by the last event (edited approvals, digests, states or caps?)")
    return problems


def verify_ledger(root):
    return not integrity_problems(root)


def _load_verified(root):
    """Load the state for a decision, refusing if it does not match its history."""
    state = _load(root)
    problems = integrity_problems(root, state)
    if problems:
        raise LedgerIntegrityError("ledger integrity check failed: " + "; ".join(problems))
    return state


def _job(state, case):
    job = next((j for j in state["jobs"] if j["case"] == case), None)
    if job is None:
        raise GateRefused(f"case {case} is not in this pack set")
    return job


def _stage(job, stage):
    if stage is None:
        raise GateRefused("a stage is required")
    st = job["stages"].get(stage)
    if st is None:
        raise GateRefused(f"case {job['case']} has no stage {stage!r} (stages: {sorted(job['stages'])})")
    return st


# --------------------------------------------------------------------------- external decision records
def validate_decision(path, kind, decision, case, stage, job, pack_set_version, reviews_root=None, now=None, output_digest=None):
    """Validate an external decision record. Returns (record, sha256). Raises GateRefused with the specific reason."""
    reviews_root = Path(reviews_root) if reviews_root else DEFAULT_REVIEWS_ROOT
    p = Path(path)
    if not p.is_file():
        raise GateRefused(f"decision record not found: {p}")
    if not _inside(p, reviews_root):
        raise GateRefused(f"decision record {p} is not under the reviews folder {reviews_root}; decisions are issued outside the work area")
    try:
        rec = json.loads(p.read_text())
    except ValueError as e:
        raise GateRefused(f"decision record is not valid JSON: {e}") from None
    if not isinstance(rec, dict):
        raise GateRefused("decision record must be a JSON object")
    if rec.get("kind") != kind:
        raise GateRefused(f"record kind is {rec.get('kind')!r}, expected {kind!r} (a different kind of review is not an authoring decision)")
    if rec.get("schema_version") != 1:
        raise GateRefused("record schema_version must be 1")
    if rec.get("decision") != decision:
        raise GateRefused(f"record decision is {rec.get('decision')!r}, expected {decision!r}")
    who, role = str(rec.get("reviewer", "")).strip(), str(rec.get("reviewer_role", "")).strip()
    if not who or not role:
        raise GateRefused("record must name a reviewer and reviewer_role")
    if NOT_A_REVIEWER.search(who) or NOT_A_REVIEWER.search(role):
        raise GateRefused(f"reviewer {who!r} / {role!r} is not an independent reviewer; the supervisor and workers cannot approve or accept")
    try:
        at = _parse(rec.get("decided_at", ""))
    except ValueError:
        raise GateRefused("record decided_at is missing or not an ISO date-time") from None
    if at > (now or _now()):
        raise GateRefused("record decided_at is in the future")
    for k, want in (("case", case), ("stage", stage), ("pack", job["pack"]), ("pack_digest", job["pack_digest"]), ("pack_set_version", pack_set_version)):
        if rec.get(k) != want:
            raise GateRefused(f"record is bound to {k}={rec.get(k)!r}, but this decision is for {k}={want!r}")
    if output_digest is not None and rec.get("output_digest") != output_digest:
        raise GateRefused("acceptance record is bound to a different output digest than the one recorded at submission")
    return rec, _sha_file(p)


# --------------------------------------------------------------------------- operations
def authorise(case, stage, decision_path, root, reviews_root=None, now=None):
    """Attach a validated approval to (case, stage). Does not start anything."""
    root = Path(root)
    with _locked(root):
        state = _load_verified(root)
        job = _job(state, case)
        st = _stage(job, stage)
        if job.get("blocked"):
            raise GateRefused(f"case {case} is blocked: {job['blocked']}")
        if not st["launchable"]:
            raise GateRefused(f"case {case} stage {stage!r} is not launchable in this pack set")
        if st["state"] != "queued":
            raise GateRefused(f"case {case} stage {stage!r} is {st['state']}; approvals attach only to a queued stage")
        if st["approval"]:
            raise GateRefused(f"case {case} stage {stage!r} already has an approval; it is withdrawn explicitly, never overwritten")
        live = pack_digest_on_disk(root, job["pack"])
        if live != job["pack_digest"]:
            raise GateRefused("pack files on disk no longer match the pack digest recorded in the index")
        rec, digest = validate_decision(decision_path, SIGNOFF_KIND, "approved", case, stage, job, state["pack_set_version"], reviews_root, now)
        st["approval"] = {"path": str(Path(decision_path).resolve()), "sha256": digest, "reviewer": rec["reviewer"], "reviewer_role": rec["reviewer_role"],
                          "decided_at": rec["decided_at"], "recorded_at": _stamp(), "pack_digest": job["pack_digest"]}
        _commit(root, state, "authorise", case, stage, {"approval_sha256": digest, "reviewer": rec["reviewer"]})
        return st["approval"]


def withdraw_approval(case, stage, reason, root):
    """Withdraw an approval from a stage that has not started. The approval moves to history; nothing is deleted."""
    if not reason or not reason.strip():
        raise GateRefused("a reason is required")
    root = Path(root)
    with _locked(root):
        state = _load_verified(root)
        st = _stage(_job(state, case), stage)
        if st["state"] != "queued" or not st["approval"]:
            raise GateRefused("only an unused approval on a queued stage can be withdrawn")
        st["approval_history"].append({**st["approval"], "withdrawn_at": _stamp(), "reason": reason})
        st["approval"] = None
        _commit(root, state, "withdraw_approval", case, stage, {"reason": reason})


def _index_outputs_dirname(root):
    """(pack_set_version, outputs_dirname or None) declared by the pack set's own PACKS-INDEX.json."""
    index = json.loads((Path(root) / "PACKS-INDEX.json").read_text())
    return index.get("pack_set_version"), index.get("outputs_dirname")


def _outputs_binding_for_init(root):
    psv, name = _index_outputs_dirname(root)
    if name is None:
        name = LEGACY_OUTPUTS_DIRNAME.get(psv)
        source = "legacy pack-set table (index predates the binding)"
    else:
        source = "PACKS-INDEX.json outputs_dirname"
    if not name or not OUTPUTS_DIRNAME_RE.fullmatch(name):
        raise GateRefused(f"pack set {psv} declares no valid owned-output folder binding ({name!r}); refusing to create a ledger")
    return {"outputs_dirname": name, "source": source}


def outputs_dirname(root, state):
    """The owned-output folder name this ledger is bound to. New ledgers carry it in their hash-chained state (set once at init from
    PACKS-INDEX.json); ledgers created before the binding existed resolve through the fixed legacy table for their pack-set version.
    Either way the pack set's own index must not declare a different folder."""
    bound = (state.get("outputs_binding") or {}).get("outputs_dirname")
    name = bound or LEGACY_OUTPUTS_DIRNAME.get(state.get("pack_set_version"))
    if not name or not OUTPUTS_DIRNAME_RE.fullmatch(name):
        raise GateRefused(f"ledger for pack set {state.get('pack_set_version')} has no valid owned-output folder binding ({name!r})")
    psv, declared = _index_outputs_dirname(root)
    if psv != state.get("pack_set_version"):
        raise GateRefused(f"PACKS-INDEX.json is pack set {psv}, but the ledger is pack set {state.get('pack_set_version')}")
    if declared is not None and declared != name:
        raise GateRefused(f"binding mismatch: the ledger owns {name!r} but PACKS-INDEX.json declares {declared!r}")
    return name


def owned_folder(root, job, stage, state=None):
    """The one owned output folder of (job, stage), resolved from the ledger's pack-set binding (never a module-wide constant).
    Refuses an unsafe pack/stage name or a folder that resolves outside the bound outputs directory (e.g. through a symlink)."""
    root = Path(root)
    state = state if state is not None else _load(root)
    name = outputs_dirname(root, state)
    pack = job["pack"]
    if not SAFE_COMPONENT_RE.fullmatch(pack) or stage not in job["stages"] or not SAFE_COMPONENT_RE.fullmatch(stage):
        raise GateRefused(f"unsafe or unknown pack/stage for an owned folder: {pack!r}/{stage!r}")
    base = root.parent / name
    folder = base / pack / stage
    if base.exists() and not _inside(base, root.parent):
        raise GateRefused(f"outputs folder {base} resolves outside the pack-set parent {root.parent}")
    if folder.exists() and not _inside(folder, base):
        raise GateRefused(f"owned folder {folder} resolves outside its bound outputs folder {base}")
    return folder


def owned_folder_for(case, stage, root):
    """Supervisor helper: the owned folder of (case, stage) from a verified ledger."""
    state = _load_verified(root)
    job = _job(state, case)
    _stage(job, stage)
    return owned_folder(root, job, stage, state)


def tree_digest(folder):
    """Digest of EVERY file (README included), every directory and the absence of symlinks in an owned folder; None if the folder is missing.
    Changed, added, deleted or emptied output therefore changes the digest."""
    folder = Path(folder)
    if not folder.is_dir():
        return None
    files, dirs = {}, []
    for dirpath, dirnames, filenames in os.walk(folder, followlinks=False):
        for d in sorted(dirnames):
            full = os.path.join(dirpath, d)
            if os.path.islink(full):
                files[os.path.relpath(full, folder)] = "symlink->" + os.readlink(full)
            else:
                dirs.append(os.path.relpath(full, folder))
        for f in sorted(filenames):
            full = os.path.join(dirpath, f)
            rel = os.path.relpath(full, folder)
            files[rel] = "symlink->" + os.readlink(full) if os.path.islink(full) else hashlib.sha256(Path(full).read_bytes()).hexdigest()
    return hashlib.sha256(_canon({"files": files, "dirs": sorted(dirs)}).encode()).hexdigest()


def _has_symlink(folder):
    return any(os.path.islink(os.path.join(d, n)) for d, ds, fs in os.walk(folder, followlinks=False) for n in ds + fs)


def check_checklist_copy(folder, case, cards):
    """The worker's completed RETURN-CHECKLIST.json COPY in its owned folder (the pack's template is immutable)."""
    f = Path(folder) / "RETURN-CHECKLIST.json"
    if not f.is_file():
        raise GateRefused(f"nothing to submit: the completed RETURN-CHECKLIST.json copy is missing from the owned folder {folder}")
    try:
        doc = json.loads(f.read_text())
        listed = {c["card"] for c in doc["cards"]}
    except (ValueError, KeyError, TypeError):
        raise GateRefused("the RETURN-CHECKLIST.json copy is not a valid checklist (needs case and cards[].card)") from None
    if doc.get("case") != case or listed != set(cards):
        raise GateRefused(f"the checklist copy must be for case {case} and list exactly the stage's cards {sorted(cards)}; it lists {sorted(listed)}")


def set_state(case, stage, new, root, note="", worker=None, acceptance_record=None, reviews_root=None, expected_revision=None, now=None):
    """Move (case, stage) to `new`. `stage` is always required. Raises GateRefused with the reason; nothing changes on refusal."""
    root = Path(root)
    with _locked(root):
        state = _load_verified(root)
        if expected_revision is not None and state["revision"] != expected_revision:
            raise ConflictError(f"ledger revision is {state['revision']}, expected {expected_revision}")
        job = _job(state, case)
        st = _stage(job, stage)
        old = st["state"]
        if new not in ALLOWED.get(old, []):
            raise GateRefused(f"case {case} stage {stage}: {old} -> {new} is not a permitted transition")
        detail = {"from": old, "to": new, "note": note}
        if new == "working":
            detail["owned_folder"] = str(_check_may_work(root, state, job, st, stage, reviews_root, now))
            if worker:
                st["worker"] = worker
        elif new == "submitted":
            folder = owned_folder(root, job, stage, state)
            detail["owned_folder"] = str(folder)
            if not folder.is_dir() or not any(f.is_file() and f.name not in ("README.txt", "RETURN-CHECKLIST.json") for f in folder.rglob("*")):
                raise GateRefused(f"nothing to submit: owned folder {folder} holds no returned card files")
            if _has_symlink(folder):
                raise GateRefused("the owned folder contains a symlink; submissions are plain files only")
            stage_cards = next(s for s in json.loads((Path(root) / job["pack"] / "STAGE.json").read_text())["stages"] if s["stage"] == stage)["cards"]
            check_checklist_copy(folder, case, stage_cards)
            st["output_digest"], st["submitted_at"] = tree_digest(folder), _stamp()
            st["submissions"].append({"output_digest": st["output_digest"], "at": st["submitted_at"]})
            detail["output_digest"] = st["output_digest"]
        elif new == "repair_required":
            if not note.strip():
                raise GateRefused("repair_required needs a note naming the repair")
        elif new == "independently_accepted":
            if not acceptance_record:
                raise GateRefused("acceptance must cite an external acceptance record; the supervisor cannot self-accept")
            live = pack_digest_on_disk(root, job["pack"])
            if live != job["pack_digest"]:
                raise GateRefused("pack files changed after the pack digest was recorded; acceptance refused")
            current = tree_digest(owned_folder(root, job, stage, state))
            if current != st["output_digest"]:
                # the files accepted-on-paper are not the files on disk: stale. Route to repair; history and the external record are kept.
                st["state"] = "repair_required"
                note2 = "output folder differs from the submitted digest at acceptance time (changed, added, deleted or missing files); resubmission and a fresh acceptance record are required"
                _commit(root, state, "stale_submission_detected", case, stage, {"from": old, "submitted_digest": st["output_digest"],
                                                                               "current_digest": current, "note": note2, "attempted_record": str(acceptance_record)})
                raise GateRefused(f"acceptance refused: {note2}")
            rec, digest = validate_decision(acceptance_record, ACCEPT_KIND, "accepted", case, stage, job, state["pack_set_version"], reviews_root, now,
                                            output_digest=current)
            if _parse(rec["decided_at"]) < _parse(st["submitted_at"]):
                raise GateRefused("acceptance predates the submission it accepts")
            st["acceptance"] = {"path": str(Path(acceptance_record).resolve()), "sha256": digest, "reviewer": rec["reviewer"], "decided_at": rec["decided_at"],
                                "output_digest": current}
            st["acceptance_history"].append(st["acceptance"])
            detail["acceptance_sha256"] = digest
        st["state"] = new
        _commit(root, state, "set_state", case, stage, detail)
        return json.loads(json.dumps(st))


def _check_may_work(root, state, job, st, stage, reviews_root, now):
    if job.get("blocked"):
        raise GateRefused(f"case {job['case']} is blocked: {job['blocked']}")
    if not st["launchable"]:
        raise GateRefused(f"stage {stage!r} is not launchable in this pack set")
    ap = st["approval"]
    if not ap:
        raise GateRefused(f"case {job['case']} stage {stage!r} has no approval; refusing to start or resume")
    live = pack_digest_on_disk(root, job["pack"])
    if live != job["pack_digest"]:
        raise GateRefused("pack files changed after the pack digest was recorded; refusing to start or resume")
    rec, digest = validate_decision(ap["path"], SIGNOFF_KIND, "approved", job["case"], stage, job, state["pack_set_version"], reviews_root, now)
    if digest != ap["sha256"]:
        raise GateRefused("the approval record changed after it was recorded; refusing to start or resume")
    req = st.get("requires_accepted_stage")
    if req and job["stages"][req]["state"] != "independently_accepted":
        raise GateRefused(f"stage {stage!r} needs stage {req!r} independently accepted first (it is {job['stages'][req]['state']})")
    for other, ost in job["stages"].items():
        if other != stage and ost["state"] in ACTIVE:
            raise GateRefused(f"case {job['case']} already has stage {other!r} active")
    cap = min(state["max_concurrent_workers"], MAX_PARALLEL_FIRST_TRIAL)
    active = sum(s["state"] in ACTIVE for j in state["jobs"] for k, s in j["stages"].items() if not (j is job and k == stage))
    if active >= cap:
        raise GateRefused(f"already {active} active workers (limit {cap}); cannot start or resume another")
    return owned_folder(root, job, stage, state)  # the bound folder must resolve safely before anyone starts or resumes


def status(root):
    state = _load(root)
    return {"revision": state["revision"], "ledger_intact": verify_ledger(root), "integrity_problems": integrity_problems(root, state), "max_concurrent_workers": state["max_concurrent_workers"],
            "active": [(j["case"], k) for j in state["jobs"] for k, s in j["stages"].items() if s["state"] in ACTIVE],
            "approved_stages": [(j["case"], k) for j in state["jobs"] for k, s in j["stages"].items() if s["approval"]],
            "authorisation_present": any(s["approval"] for j in state["jobs"] for s in j["stages"].values())}


# --------------------------------------------------------------------------- scope audit (writes only)
def snapshot_tree(paths, exclude=SNAPSHOT_EXCLUDE):
    """{path: sha256 | 'symlink->target'} for every file (symlinks recorded, not followed). Hashes detect WRITES only."""
    out = {}
    for base in map(Path, paths):
        if base.is_file() or base.is_symlink():
            walk = [(str(base.parent), [], [base.name])]
        else:
            walk = os.walk(base, followlinks=False)
        for dirpath, dirs, files in walk:
            dirs[:] = sorted(d for d in dirs if d not in exclude)
            for name in sorted(files + [d for d in dirs if os.path.islink(os.path.join(dirpath, d))]):
                f = os.path.join(dirpath, name)
                if os.path.islink(f):
                    out[f] = "symlink->" + os.readlink(f)
                elif os.path.isfile(f):
                    out[f] = hashlib.sha256(Path(f).read_bytes()).hexdigest()
    return out


def scope_audit(before, after, owned, concurrent_owned=()):
    """Classify every changed/added/removed path.
    violations: changed outside `owned` and outside every other authorised concurrent owned folder.
    unattributable: changed inside ANOTHER active worker's owned folder - could be that worker's authorised write; the global diff cannot say who wrote it.
    symlink_escapes: a changed symlink under `owned` whose target resolves outside `owned`.
    This audits WRITES. It is not evidence that no worker READ outside its pack."""
    changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    violations, unattrib, escapes = [], [], []
    for k in changed:
        if _dir_inside(k, owned):
            if str(after.get(k, "")).startswith("symlink->") and not _inside(k, owned):
                escapes.append(k)  # the link itself sits in the owned folder but points outside it
            continue
        if any(_dir_inside(k, o) for o in concurrent_owned):
            unattrib.append(k)
        else:
            violations.append(k)
    return {"audits": "writes_only_not_reads", "violations": violations, "unattributable_to_a_writer": unattrib, "symlink_escapes": escapes,
            "clean": not (violations or escapes), "changed_total": len(changed),
            "note": "File hashes show writes. They do not show that another pack was not read, and no OS sandbox is in force."}
