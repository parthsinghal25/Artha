#!/usr/bin/env python3
"""Zero-login read journey against a serving boundary: backend, gateway, emulated Hosting, or the hosted URL.

    python3 scripts/cloud_journey_check.py --base URL [--compare-base URL2] [--out FILE] [--view-date 2026-09-30]
                                           [--cookie-jar FILE]   # rerun with the same jar after a restart

There is no login code and no sign-in. The first request takes the server's automatic read-only demo session. The
CSRF value is read from the readable `sdb_csrf` cookie and echoed as `X-CSRF-Token`, as the UI does; every cookie
the server set is offered, and an edge such as Firebase forwards only `__session`.

Steps:
1. overview: publication, coverage and cutoff;
2. the issue list, then the issue with the most included cases;
3. the first included case: `case.memory.get` and `case.get`;
4. the claim supporting that membership: `source.get`, which must give a valid citation;
5. `location.resolve`;
6. `asset.get` inline, then redeeming the opaque token: the bytes must match the SHA-256 the API declared;
7. the burden section (cash and hours) at month and observation-year grain.

Every response is normalised: per-request identifiers (context/request IDs, tokens, cursors, timestamps) are
removed and the rest is hashed. With `--compare-base`, the same journey runs against a second server and every
step's digest must match, for example the restored cloud copy against the live local pilot. Standard library
only; reads only, so no model is ever invoked.
"""
from __future__ import annotations

import argparse
import hashlib
import http.cookiejar
import json
import sys
import urllib.error
import urllib.request

VOLATILE = {"context_id", "request_id", "resolved_at", "expires_at", "retrieval_token", "action_id", "next_cursor",
            "correlation_id", "stream_id", "stream_reference", "context", "stale"}


def norm(x):
    if isinstance(x, dict):
        return {k: norm(v) for k, v in sorted(x.items()) if k not in VOLATILE}
    if isinstance(x, list):
        return [norm(v) for v in x]
    return x


def digest(x) -> str:
    return hashlib.sha256(json.dumps(norm(x), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class Client:
    def __init__(self, base: str, origin: str | None, jar_path: str | None = None):
        self.base, self.origin = base.rstrip("/"), origin
        self.jar_path = jar_path
        self.jar = http.cookiejar.MozillaCookieJar(jar_path) if jar_path else http.cookiejar.CookieJar()
        if jar_path:
            try:
                self.jar.load(ignore_discard=True, ignore_expires=False)
            except FileNotFoundError:
                pass
        self.http = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def cookie(self, name):
        return next((c.value for c in self.jar if c.name == name), None)

    def request(self, method, path, body=None):
        headers = {"Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if self.origin:
            headers["Origin"] = self.origin
        if self.cookie("sdb_csrf"):
            headers["X-CSRF-Token"] = self.cookie("sdb_csrf")
        req = urllib.request.Request(self.base + path, method=method, headers=headers,
                                     data=None if body is None else json.dumps(body).encode())
        try:
            with self.http.open(req, timeout=60) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def op(self, body):
        status, _, data = self.request("POST", "/v1/operations", body)
        return status, json.loads(data or b"{}")


def journey(base: str, origin: str | None, view_date: str | None, jar_path: str | None = None) -> dict:
    c = Client(base, origin, jar_path)
    steps, out = [], {"base": base}
    before = {x.name: x.value for x in c.jar}

    def rec(name, status, resp, **extra):
        steps.append({"step": name, "http": status, "status": resp.get("status"),
                      "error": (resp.get("error") or {}).get("code"), "digest": digest(resp.get("payload")), **extra})
        if status != 200 or resp.get("status") != "ok":
            raise SystemExit(json.dumps({"failed_step": name, "http": status, "response": resp}, indent=1)[:4000])
        return resp

    status, headers, data = c.request("GET", "/v1/session")
    try:
        who = json.loads(data or b"{}")
    except ValueError:                 # behind the UI gateway only /v1/operations and asset redemption are routed;
        who = None                     # the first operation then establishes the automatic session
    out["session"] = {"session_endpoint": "routed" if who is not None else "not_routed (gateway)",
                      "http": status, "principal_id": (who or {}).get("principal_id"), "sign_in_required": status == 401}
    if who is not None and (status != 200 or who.get("principal_id") != "leader.demo"):
        raise SystemExit(f"automatic demo session not established: {status} {who}")
    sel = {"temporal_policy": "demo-held-day-v1", **({"view_date": view_date} if view_date else {})}

    def intel(ctx, cursor=None, **kw):
        return c.op({"operation": "intelligence.get", "context_id": ctx, "selection": {**sel, **kw},
                     "cursor": cursor, "page_size": 50})
    s, ov = intel(None, section="overview")
    rec("overview", s, ov)
    out["session"]["cookies_after_first_operation"] = sorted(x.name for x in c.jar)
    after = {x.name: x.value for x in c.jar}
    sess = "__session" if "__session" in after else "sdb_session"
    out["session"]["session_cookie_reused"] = bool(before.get(sess)) and before.get(sess) == after.get(sess)
    ctx = ov["context"]["context_id"]
    pub = ov["publication"] if "publication" in ov else ov["payload"].get("publication")
    out["publication"] = {k: pub.get(k) for k in ("publication_id", "analysis_set_version", "view_date",
                                                  "knowledge_end_exclusive", "coverage", "model_assessed",
                                                  "human_review")} if pub else None
    # every page of the issue collection (a larger publication spans several): rank over the full set only
    s, issues = intel(ctx, section="issues")
    rec("issues", s, issues)
    items, pages, seen_cursors = list(issues["payload"]["items"]), 1, set()
    cur = issues["payload"].get("next_cursor")
    while cur:
        if cur in seen_cursors or pages > 50:
            raise SystemExit("issue pagination did not terminate (cursor loop)")
        seen_cursors.add(cur)
        s, more = intel(ctx, cursor=cur, section="issues")
        rec(f"issues_page_{pages + 1}", s, more)
        items += more["payload"]["items"]
        cur, pages = more["payload"].get("next_cursor"), pages + 1
    ids = [i["issue_id"] for i in items]
    total = issues["payload"].get("total_items")
    if len(ids) != len(set(ids)) or (total is not None and len(ids) != total):
        raise SystemExit(f"issue pages inconsistent: {len(ids)} items, {len(set(ids))} unique, total_items {total}")
    out["issues_paged"] = {"pages": pages, "items": len(ids), "unique": len(set(ids)), "total_items": total}
    top = max(items, key=lambda i: ((i.get("membership") or {}).get("included") or {}).get("value", 0))
    iid = top["issue_id"]
    s, det = intel(ctx, section="issue", issue_id=iid, grain="month")
    rec("issue_month", s, det)
    contributors = ((top.get("membership") or {}).get("included") or {}).get("contributors") or []
    out["issue"] = {"issue_id": iid, "title": top.get("title") or top.get("label"),
                    "included_cases": len(contributors)}
    cid = contributors[0]
    s, mem = c.op({"operation": "case.memory.get", "context_id": ctx, "case_id": cid, "memory_revision_id": None,
                   "cursor": None, "page_size": 50})
    rec("case_memory", s, mem)
    s, case = c.op({"operation": "case.get", "context_id": ctx, "case_id": cid, "cursor": None, "page_size": 20})
    rec("case_get", s, case)
    claims = {i["claim_id"]: i for i in mem["payload"]["items"]}
    ms = {m["issue_id"]: m for m in mem["payload"]["detail"]["memberships"]}
    sup = [x for x in ms.get(iid, {}).get("support_claim_ids", []) if x in claims] or sorted(claims)
    cl = claims[sup[0]]
    s, src = c.op({"operation": "source.get", "context_id": ctx, "source_id": cl["source_id"],
                   "content_sha256": cl["content_sha256"], "citation": cl["ref"]})
    rec("source_get", s, src, citation_state=src.get("payload", {}).get("citation_state"))
    ex = (src["payload"].get("extractions") or [None])[0]
    s, loc = c.op({"operation": "location.resolve", "context_id": ctx, "citation": cl["ref"],
                   "asset_version_id": ex["permitted_asset_version_id"], "extraction_id": ex["extraction_id"]})
    rec("location_resolve", s, loc)
    s, act = c.op({"operation": "asset.get", "context_id": ctx, "asset_version_id": ex["permitted_asset_version_id"],
                   "action": "inline"})
    rec("asset_get", s, act)
    tok = act["payload"]
    s, h, data = c.request("GET", f"/v1/assets/{tok['retrieval_token']}")
    got = hashlib.sha256(data).hexdigest()
    asset_ok = s == 200 and got == tok["bytes_sha256"] == h.get("X-Content-SHA256", got)
    steps.append({"step": "asset_redeem", "http": s, "status": "ok" if asset_ok else "mismatch",
                  "digest": got, "media_type": tok["media_type"], "bytes": len(data)})
    if not asset_ok:
        raise SystemExit(f"original bytes do not match the declared SHA-256 ({s})")
    out["evidence"] = {"case_id": cid, "claim_id": cl["claim_id"], "source_id": cl["source_id"],
                       "citation_state": src["payload"].get("citation_state"),
                       "asset_version_id": tok["asset_version_id"], "media_type": tok["media_type"],
                       "bytes_sha256": got, "bytes": len(data)}
    burden = {}
    for g in ("month", "observation_year"):
        s, b = intel(ctx, section="burden", grain=g)
        rec(f"burden_{g}", s, b)
        burden[g] = b["payload"]
    out["burden"] = burden_summary(burden)
    out["steps"] = steps
    if jar_path:
        c.jar.save(ignore_discard=True)
    return out


def burden_summary(burden: dict) -> dict:
    """Pick the cash/hours series out of the burden payload without assuming more than its documented keys."""
    out = {}
    for g, p in burden.items():
        flat = {}

        def walk(x, path):
            if isinstance(x, dict):
                if "value" in x and isinstance(x.get("value"), (int, float)) and any(
                        w in path for w in ("paid", "hours", "seconds", "work", "cash", "expense", "amount")):
                    flat[path] = {"value": x["value"], "unit": x.get("unit"),
                                  "contributors": len(x.get("contributors") or [])}
                for k, v in x.items():
                    walk(v, f"{path}.{k}" if path else k)
            elif isinstance(x, list):
                for i, v in enumerate(x[-3:]):           # the latest buckets are enough for a summary
                    walk(v, f"{path}[-{len(x[-3:]) - i}]")
        walk(p, "")
        out[g] = dict(list(sorted(flat.items()))[:24])
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--origin")
    ap.add_argument("--compare-base")
    ap.add_argument("--view-date")
    ap.add_argument("--out")
    ap.add_argument("--cookie-jar", help="persist cookies between runs (session continuity across restarts)")
    a = ap.parse_args()
    result = {"primary": journey(a.base, a.origin, a.view_date, a.cookie_jar)}
    if a.compare_base:
        other = journey(a.compare_base, None, a.view_date)
        pairs = list(zip(result["primary"]["steps"], other["steps"]))
        diff = [x["step"] for x, y in pairs if x["digest"] != y["digest"]]
        result["comparison"] = {"base": a.compare_base, "steps_compared": len(pairs), "differing_steps": diff,
                                "same_publication": result["primary"]["publication"] == other["publication"],
                                "same_burden": result["primary"]["burden"] == other["burden"],
                                "identical": not diff and len(pairs) == len(other["steps"])}
    text = json.dumps(result, indent=1, sort_keys=True)
    if a.out:
        open(a.out, "w").write(text + "\n")
    print(text if not a.out else json.dumps({"written": a.out, "identical": result.get("comparison", {}).get("identical"),
                                             "steps": [s["step"] for s in result["primary"]["steps"]]}))
    return 0 if result.get("comparison", {}).get("identical", True) else 1


if __name__ == "__main__":
    sys.exit(main())
