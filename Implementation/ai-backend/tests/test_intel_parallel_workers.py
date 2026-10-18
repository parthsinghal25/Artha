"""Parallel case-memory workers share one RunSpec: a worker whose plan predates another worker's sealed memory must
not re-investigate that case with the just-sealed memory as its prior (18 Oct agy bulk regression)."""
from test_intel_mechanics import _make_demo


def test_stale_plan_does_not_reinvestigate_cases_sealed_by_this_run(tmp_path, monkeypatch):
    from sdbackend.intel import worker
    demo = _make_demo(tmp_path, monkeypatch)
    rt, corpus, prov = demo["rt"], demo["corpus"], demo["prov"]
    ctx = worker.Context(rt, corpus, "2026-09-30")
    stale = worker.plan(ctx)                       # worker B's plan, computed before worker A seals anything
    todo = [c for c in stale["cases"] if c["action"] in worker.MODEL_ACTIONS]
    assert todo
    gw, prov_meta, reason = worker._provider(rt, "mock")
    first = worker.memory_phase(rt, ctx, stale, gw=gw, prov=prov_meta, reason=reason,
                                budget=worker.Budget(None, None), log=lambda *a: None)     # worker A
    assert first["processed"] == len(todo) and not first["failed"]
    heads_after_a = {c: h["memory_id"] for c, h in worker.heads(rt).items()}
    calls = len(prov.calls)
    again = worker.memory_phase(rt, ctx, stale, gw=gw, prov=prov_meta, reason=reason,
                                budget=worker.Budget(None, None), log=lambda *a: None)     # worker B, stale plan
    assert again["processed"] == 0 and again["reused"] == len(todo) and not again["failed"]
    assert len(prov.calls) == calls                                                       # no model work
    assert {c: h["memory_id"] for c, h in worker.heads(rt).items()} == heads_after_a


def test_shards_partition_cases_disjointly():
    from sdbackend.intel.worker import in_shard
    ids = [f"C-{i:04d}" for i in range(400)]
    owners = [[s for s in range(6) if in_shard(c, (s, 6))] for c in ids]
    assert all(len(o) == 1 for o in owners) and in_shard("x", None)
    assert min(sum(o == [s] for o in owners) for s in range(6)) > 30


def test_assessment_shards_are_disjoint_and_publish_only_makes_no_calls(tmp_path, monkeypatch):
    from sdbackend.intel import worker
    demo = _make_demo(tmp_path, monkeypatch)
    rt, corpus, prov = demo["rt"], demo["corpus"], demo["prov"]
    mem = worker.run_step(rt, corpus, "2026-09-30", provider="mock", log=lambda *a: None, collective=False,
                          publish=False)
    assert mem["state"] == "complete"
    worker.run_step(rt, corpus, "2026-09-30", provider="mock", log=lambda *a: None, collective_only=True,
                    publish=False, limit_cases=None)          # discovery + full assessment, unsharded
    from sdbackend.intel.discovery import registry_head, active_issues
    n_issues = len(active_issues(registry_head(rt)))
    # wipe nothing: shards over an already-assessed store must find no work and make no calls
    calls = len(prov.calls)
    for i in range(3):
        rep = worker.run_step(rt, corpus, "2026-09-30", provider="mock", log=lambda *a: None, collective_only=True,
                              publish=False, assess_shard=(i, 3))
        assert rep["state"] == "complete" and rep["collective"]["assessment"]["to_assess"] == 0
    assert len(prov.calls) == calls and n_issues > 0
    pub = worker.run_step(rt, corpus, "2026-09-30", provider="mock", log=lambda *a: None, publish_only=True)
    assert pub["state"] == "complete" and pub["publication"]["state"] == "published" and len(prov.calls) == calls
