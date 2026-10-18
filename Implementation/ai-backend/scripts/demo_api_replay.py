#!/usr/bin/env python3
"""Reproducible API replay against the running DEMO server (scripts/demo-control.sh server start).

Walks: intelligence overview → issues → one issue's cells → its included contributors → case.memory.get (claims)
→ source.get (citation validity) → location.resolve (original location) → case.get, and saves every request and
response under reports/intel/<corpus>/api-examples/. It never triggers model inference (reads only) and the
login code is read from the demo store's local file and never printed or saved.

  .venv-intel/bin/python scripts/demo_api_replay.py [--view-date 2026-09-30] [--base http://127.0.0.1:8091]"""
import argparse
import http.cookiejar
import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAME = os.environ.get("SDB_DEMO_NAME", "p4-pilot-v1")
STORE = ROOT / "var" / "demo" / NAME
OUT = ROOT / "reports" / "intel" / NAME / "api-examples"     # overridden by --out (earlier evidence is kept)
PRINCIPAL = "leader.demo"


class Client:
    def __init__(self, base, origin):
        self.base, self.origin = base, origin
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.csrf = None
        self.n = 0

    def _post(self, path, body):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "Origin": self.origin,
                                              **({"X-CSRF-Token": self.csrf} if self.csrf else {})})
        try:
            with self.opener.open(req, timeout=60) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            return e.code, json.load(e)

    def signin(self):
        codes = json.loads((STORE / "login-codes.json").read_text())
        status, body = self._post("/v1/session", {"principal_id": PRINCIPAL, "login_code": codes[PRINCIPAL]})
        if status != 200:
            raise SystemExit(f"sign-in failed ({status})")
        self.csrf = body["csrf_token"]

    def op(self, label, body):
        self.n += 1
        status, resp = self._post("/v1/operations", body)
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / f"{self.n:02d}-{label}.json").write_text(json.dumps({"request": body, "http_status": status,
                                                                     "response": resp}, indent=1, ensure_ascii=False))
        st = resp.get("status")
        print(f"{self.n:02d} {label:<28} {status} {st} {(resp.get('error') or {}).get('code', '')}")
        return resp


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=f"http://127.0.0.1:{os.environ.get('SDB_PORT', '8091')}")
    ap.add_argument("--origin", default=None, help="Origin header (default: same origin as --base)")
    ap.add_argument("--view-date", default=None)
    ap.add_argument("--out", default=None, help="output directory name under reports/intel/<corpus>/")
    a = ap.parse_args()
    global OUT
    if a.out:
        OUT = ROOT / "reports" / "intel" / NAME / a.out
        if OUT.exists():
            raise SystemExit(f"{OUT} exists; choose a new --out so earlier evidence is never overwritten")
    c = Client(a.base, a.origin or a.base)
    c.signin()
    sel = {"temporal_policy": "demo-held-day-v1", **({"view_date": a.view_date} if a.view_date else {})}

    def intel(label, ctx_id, **kw):
        return c.op(label, {"operation": "intelligence.get", "context_id": ctx_id, "selection": {**sel, **kw},
                            "cursor": None, "page_size": 50})

    ov = intel("intelligence-overview", None, section="overview")
    if ov.get("status") != "ok":
        raise SystemExit("no publication available")
    ctx = ov["context"]["context_id"]
    intel("intelligence-publications", ctx, section="publications")
    issues = intel("intelligence-issues", ctx, section="issues")
    intel("intelligence-alerts", ctx, section="alerts")
    intel("intelligence-priorities", ctx, section="priorities")
    for g in ("week", "month", "quarter", "observation_year"):
        intel(f"intelligence-burden-{g}", ctx, section="burden", grain=g)
    intel("intelligence-arrivals-month", ctx, section="arrivals", grain="month")
    intel("intelligence-experience", ctx, section="experience")
    intel("intelligence-coverage", ctx, section="coverage")
    intel("intelligence-afca", ctx, section="afca")
    intel("intelligence-continuity", ctx, section="continuity")
    items = issues["payload"]["items"]
    if not items:
        print("no issues published at this view")
        return 0
    top = max(items, key=lambda i: (i.get("membership", {}).get("included", {}) or {}).get("value", 0))
    iid = top["issue_id"]
    detail = intel("intelligence-issue", ctx, section="issue", issue_id=iid)
    for g in ("month", "quarter"):
        intel(f"intelligence-issue-series-{g}", ctx, section="issue", issue_id=iid, grain=g)
    contributors = (top.get("membership", {}).get("included") or {}).get("contributors", [])
    if not contributors:
        return 0
    cid = contributors[0]
    mem = c.op("case-memory-get", {"operation": "case.memory.get", "context_id": ctx, "case_id": cid,
                                   "memory_revision_id": None, "cursor": None, "page_size": 50})
    c.op("case-get", {"operation": "case.get", "context_id": ctx, "case_id": cid, "cursor": None, "page_size": 20})
    if mem.get("status") != "ok":
        return 0
    claims = {i["claim_id"]: i for i in mem["payload"]["items"]}
    ms = {m["issue_id"]: m for m in mem["payload"]["detail"]["memberships"]}
    sup = [x for x in ms.get(iid, {}).get("support_claim_ids", []) if x in claims] or list(claims)
    if not sup:
        return 0
    cl = claims[sup[0]]                     # the claim supporting this case's membership of the issue
    src = c.op("source-get", {"operation": "source.get", "context_id": ctx, "source_id": cl["source_id"],
                              "content_sha256": cl["content_sha256"], "citation": cl["ref"]})
    if src.get("status") == "ok":
        ex = (src["payload"].get("extractions") or [None])[0]
        if ex:
            c.op("location-resolve", {"operation": "location.resolve", "context_id": ctx, "citation": cl["ref"],
                                      "asset_version_id": ex["permitted_asset_version_id"],
                                      "extraction_id": ex["extraction_id"]})
    (OUT / "INDEX.json").write_text(json.dumps({"base": a.base, "principal": PRINCIPAL, "view_date": a.view_date,
                                                "issue_id": iid, "case_id": cid, "claim_id": cl.get("claim_id"),
                                                "files": sorted(p.name for p in OUT.glob("[0-9]*.json"))}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
