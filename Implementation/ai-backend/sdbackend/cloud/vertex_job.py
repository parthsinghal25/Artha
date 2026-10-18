"""Single-case Cloud Run Job. No serving, discovery, publication or live-store access.

The input packet is a hash-pinned export of validated held extraction, not a full runtime database.
The existing worker's memory phase supplies RunSpec, leases, generation, critique and fenced sealing.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

CASE_ID = "170ac13c-0c12-46e7-ae6e-0630b8372d06"
MEMBER = "7704d1fd-6aa5-4c79-b795-a96bb11f5f95"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def validate_input(root, manifest):
    from ..canonical import source_content_sha256
    root = Path(root).resolve()
    if manifest.get("case_id") != CASE_ID:
        raise ValueError("qualification case mismatch")
    for name, digest in manifest["files"].items():
        p = (root / name).resolve()
        if not p.is_relative_to(root) or not p.is_file():
            raise ValueError("unsafe or missing input path")
        if sha(p.read_bytes()) != digest:
            raise ValueError("input hash mismatch")
    inp = json.loads((root / "case-inputs.json").read_text())
    if inp.get("case_id") != CASE_ID or inp.get("members") != [MEMBER]:
        raise ValueError("case/member mismatch")
    allowed = set(manifest.get("allowed_evidence_case_ids") or [CASE_ID])
    for src in inp["narrative"]:
        d = src["data"]
        if CASE_ID not in d["case_ids"] or not set(d["case_ids"]) <= allowed:
            raise ValueError("foreign evidence scope")
        if source_content_sha256(d) != d["content_sha256"]:
            raise ValueError("source text hash mismatch")
    return inp


class PrivateObjects:
    def __init__(self, project, input_uri, output_uri):
        from google.cloud import storage
        self.client = storage.Client(project=project)
        self.input_uri, self.output_uri = input_uri, output_uri

    def blob(self, uri):
        from urllib.parse import urlsplit
        u = urlsplit(uri)
        if u.scheme != "gs" or not u.netloc:
            raise ValueError("private gs URI required")
        return self.client.bucket(u.netloc).blob(u.path.lstrip("/"))

    def read(self, name):
        return self.blob(self.input_uri.rstrip("/") + "/" + name).download_as_bytes(timeout=30)

    def write(self, name, body):
        data = json.dumps(body, ensure_ascii=False, sort_keys=True, default=str).encode()
        b = self.blob(self.output_uri.rstrip("/") + "/" + name)
        b.upload_from_string(data, content_type="application/json", if_generation_match=0, timeout=30)
        return {"uri": "gs://" + b.bucket.name + "/" + b.name, "sha256": sha(data), "generation": b.generation}


def execute(root, manifest, objects):
    from ..config import load_settings
    from ..runtime import make_runtime
    from ..intel import memory, worker
    inp_dict = validate_input(root, manifest)
    inp_dict["knowledge_end"] = datetime.fromisoformat(inp_dict["knowledge_end"])
    inp = memory.CaseInputs(**inp_dict)
    os.environ["SDB_POLICY_PATH"] = str(root / "policy.json")
    settings = replace(load_settings(), data_dir=root / "runtime", model_provider="vertex",
                       model_call_timeout_s=65.0)
    if settings.data_dir.exists():
        raise ValueError("scratch runtime must be empty; execution is single-use")
    rt = make_runtime(settings)
    worker.ensure_schema(rt)
    for s in inp.narrative:
        rt.db.put_object("source", s["data"]["source_id"], s, links={"case": s["data"]["case_ids"]})
    ctx = SimpleNamespace(knowledge_end=inp.knowledge_end,
                          rv=SimpleNamespace(release_id=manifest["source_release_id"],
                                             control={"source_set_version": manifest["source_set_version"]}),
                          inputs=lambda cid: inp if cid == CASE_ID else (_ for _ in ()).throw(ValueError("case scope")))
    gw, prov, reason = worker._provider(rt, "vertex")
    gw.provider.budget.receipt = lambda r: objects.write(f"attempt-{r['attempts']:02d}-reserved.json", r)
    gw.provider.receipt = lambda r: objects.write(f"attempt-{r['attempts']:02d}-{r['state']}.json", r)
    budget = worker.Budget(12, 780)
    gw.call_guard = budget.reserve
    row = {"case_id": CASE_ID, "action": "new", "input_digest": inp.input_digest(),
           "head_memory_id": None, "estimated_calls": len(memory.packets(inp, inp.narrative)) + 2}
    result = {"qualification": "one case, cached extraction, no publication", "case_id": CASE_ID,
              "input_digest": inp.input_digest(), "provider": gw.fingerprint(), "state": "failed",
              "job": {k: os.environ.get(k) for k in ("CLOUD_RUN_JOB", "CLOUD_RUN_EXECUTION", "CLOUD_RUN_TASK_INDEX")}}
    try:
        rep = worker.memory_phase(rt, ctx, {"cases": [row]}, gw=gw, prov=prov, reason=reason,
                                  budget=budget, log=lambda m: print(m, flush=True), limit_cases=1)
        mem = worker.head(rt, CASE_ID)
        result["memory_phase"] = rep
        if mem is None or rep["processed"] != 1:
            raise ValueError("fresh case memory not produced")
        sources = {s["data"]["source_id"]: s["data"] for s in inp.narrative}
        for claim in mem["claims"]:
            from ..citations import check_citation
            src = sources[claim["source_id"]]
            if check_citation(src, claim["ref"]) or claim["content_sha256"] != src["content_sha256"] or claim["quote"] not in src["text"]:
                raise ValueError("claim grounding failure")
        result.update(state="complete", memory_id=mem["memory_id"], claims=len(mem["claims"]),
                      factors=len(mem["factors"]), coverage=mem["coverage"], grounding="passed")
        result["memory_receipt"] = objects.write("case-memory.json", mem)
        # Optional bounded collective check against ONLY the frozen definitions exported with this case.
        issues = manifest.get("issue_subset", [])[:3]
        if issues and budget.requests < 12:
            from ..intel import discovery
            usage = []
            try:
                assessed, pending = discovery.assess(gw, rt, None, {CASE_ID: mem},
                    [(CASE_ID, i) for i in issues], task_id="qualification-subset", usage=usage)
                result["issue_subset"] = {"scope": "one case against frozen definitions; no discovery",
                                          "assessed": len(assessed), "unassessed": len(pending), "usage": usage}
                result["issue_subset_receipt"] = objects.write("issue-subset.json", {"assessments": assessed, "unassessed": pending})
            except Exception as exc:
                result["issue_subset"] = {"state": "partial", "error_class": type(exc).__name__}
    except Exception as exc:
        result.update(state="partial" if budget.requests else "failed", error_class=type(exc).__name__)
    finally:
        result["actual_attempts"] = budget.requests
        result["usage"] = [dict(r) for r in rt.db.connect().execute("SELECT provider,model,role,status,input_tokens,output_tokens,thinking_tokens,latency_ms FROM usage_ledger")]
        result["budget"] = json.loads((settings.data_dir / "vertex-budget.json").read_text()) if (settings.data_dir / "vertex-budget.json").exists() else None
        receipt = objects.write("result.json", result)
        print(json.dumps({"state": result["state"], "receipt": receipt}), flush=True)
    return result


def main():
    for key in ("GOOGLE_API_KEY", "GEMINI_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS"):
        if os.environ.get(key):
            raise ValueError("job requires attached identity; credential files/keys forbidden")
    os.environ["SDB_NO_DOTENV"] = "1"
    objects = PrivateObjects(os.environ["SDB_VERTEX_PROJECT"], os.environ["QUAL_INPUT_URI"], os.environ["QUAL_OUTPUT_URI"])
    raw = objects.read("manifest.json")
    if sha(raw) != os.environ["QUAL_MANIFEST_SHA256"]:
        raise ValueError("manifest hash mismatch")
    manifest = json.loads(raw)
    root = Path("/tmp/vertex-qualification")
    root.mkdir(exist_ok=False)
    for name in manifest["files"]:
        p = (root / name).resolve()
        if not p.is_relative_to(root):
            raise ValueError("unsafe path")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(objects.read(name))
    objects.write("execution-start.json", {"manifest_sha256": sha(raw), "case_id": CASE_ID,
                  "execution": os.environ.get("CLOUD_RUN_EXECUTION"), "expected_scope": "single ordinary-development case"})
    result = execute(root, manifest, objects)
    return 0 if result["state"] == "complete" else 2


if __name__ == "__main__":
    sys.exit(main())
