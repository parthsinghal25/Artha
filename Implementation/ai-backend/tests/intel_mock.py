"""Deterministic MOCK provider for intel mechanics tests. It proves queues, grounding, reuse, publication, time and
API mechanics only — its "diagnoses" are fixed strings, never evidence of real discovery quality."""
from __future__ import annotations

import json
import re

from sdbackend.models.base import GenResult, Usage


class MockProvider:
    name = "mock"
    model = "mock-deterministic-1"

    def __init__(self):
        self.calls = []
        self.propose_more = False        # when set, discovery also proposes a second issue next time
        self.weak = False                # when set, the assessor defers every pair to original-evidence retrieval

    def generate(self, req):
        self.calls.append(req.role)
        text = req.messages[-1].parts[0].text
        if req.role in ("case_reader", "case_read_repair"):
            data = {"claims": []}
            for m in re.finditer(r"\[(S\d+)\] date [^\n]*\n(.*?)(?=\n\[S\d+\] date |\n\nReturn one JSON|\Z)", text, re.S):
                handle, body = m.group(1), m.group(2)
                lines = [ln.strip() for ln in body.splitlines() if len(ln.strip()) > 25 and ":" not in ln[:12]]
                for ln in lines[:2]:
                    kind = ("approval" if "approv" in ln.lower() else "closure" if "clos" in ln.lower() else
                            "execution" if "posted" in ln.lower() else "request")
                    data["claims"].append({"handle": handle, "quote": ln[:120], "kind": kind, "actor": "member",
                                           "item": "item", "modality": "recorded", "date": None, "note": "mock"})
        elif req.role == "case_factor_analyst":
            ids = re.findall(r"^C(\d+) ", text, re.M)
            data = {"factors": [{"key": "N1", "label": "mock handling delay", "description": "mock",
                                 "level": "symptom", "prominence": "primary", "status": "active",
                                 "began": f"C{ids[0]}" if ids else None, "ceased": None,
                                 "support": [f"C{ids[0]}"] if ids else [], "contrary": [], "alternatives": None}]}
            if "PRIOR FACTORS" in text and "F1:" in text:
                data["factors"][0]["key"] = "F1"
        elif req.role == "case_synthesiser":
            ids = re.findall(r"^C(\d+) ", text, re.M)
            data = {"narrative": [{"text": "mock narrative", "claims": [f"C{ids[0]}"] if ids else []}],
                    "handoffs": [], "commitments": [], "member_effort": [], "responses": [],
                    "service": {"state": "confirmed_complete", "claims": [f"C{ids[0]}"] if ids else [],
                                "reason": "mock claims completion (host must downgrade unless recorded execution)"},
                    "contradictions": [], "unresolved_questions": ["mock open question"]}
        elif req.role == "collective_discovery":
            keys = re.findall(r"^(K\d+) ·", text, re.M)
            existing = re.findall(r"^(I\d+): ", text, re.M)
            data = {"existing": [{"key": k, "action": "keep"} for k in existing],
                    "new": [] if existing else [{"label": "mock recurring delay", "definition": "mock definition",
                                                 "include_if": "mock delay evidence", "exclude_if": "none",
                                                 "level": "symptom", "dimension": "handling",
                                                 "examples": keys[:2], "lookalikes": [], "alternatives": "mock"}]}
            if existing and self.propose_more:
                self.propose_more = False
                data["new"] = [{"label": "mock repeated document request", "definition": "mock definition 2",
                                "include_if": "mock repeated request", "exclude_if": "none", "level": "mechanism",
                                "dimension": "handling", "examples": keys[-2:], "lookalikes": [],
                                "alternatives": "mock"}]
        elif req.role == "membership_assessor" and self.weak:
            pairs = re.findall(r"\((K\d+),(I\d+)\)", text)
            data = {"assessments": [{"case": k, "issue": i, "state": "uncertain", "support": [], "contrary": [],
                                     "reason": "mock: memory insufficient", "needs_evidence": True} for k, i in pairs]}
        elif req.role == "membership_retrieval" and self.weak:
            pairs = re.findall(r"\((K\d+),(I\d+)\)", text)
            out = []
            for n, (k, i) in enumerate(pairs):
                handles = re.findall(rf"^  ({k}\.P\d+): ", text, re.M)
                if n % 2 == 0:       # retrieval-only inclusion, first handle as support, last as contrary context
                    out.append({"case": k, "issue": i, "state": "included", "support": handles[:1],
                                "contrary": handles[-1:] if len(handles) > 1 else [], "reason": "mock retrieval"})
                else:                # conflicting: both sides from passages
                    out.append({"case": k, "issue": i, "state": "conflicting", "support": handles[-1:],
                                "contrary": handles[:1], "reason": "mock conflicting retrieval"})
            data = {"assessments": out}
        elif req.role in ("membership_assessor", "membership_retrieval"):
            pairs = re.findall(r"\((K\d+),(I\d+)\)", text)
            out = []
            for k, i in pairs:
                c = re.search(rf"^  ({k}\.C\d+) ", text, re.M)
                included = c is not None and int(k[1:]) % 2 == 1
                out.append({"case": k, "issue": i, "state": "included" if included else "excluded",
                            "factor": "F1", "support": [c.group(1)] if included else [], "contrary": [],
                            "reason": "mock", "needs_evidence": False})
            data = {"assessments": out}
        else:
            data = {}
        return GenResult(provider="mock", model=self.model, text=json.dumps(data), data=data,
                         usage=Usage(input_tokens=len(text) // 4, output_tokens=50, total_tokens=len(text) // 4 + 50,
                                     source="mock"), latency_ms=1)


def install(monkeypatch):
    """Route provider selection to the mock (tests only)."""
    import sdbackend.models.gateway as gw
    import sdbackend.intel.worker as worker
    prov = MockProvider()
    monkeypatch.setattr(gw, "make_provider", lambda name, settings: prov)
    monkeypatch.setattr(worker, "choose_provider", lambda settings, pref, db=None, **k: ("mock", "test mock"))
    orig = gw.ModelGateway.fingerprint

    def fp(self):
        if self.provider_name == "mock":
            return {"provider": "mock", "model": prov.model, "sha256": "0" * 64}
        return orig(self)
    monkeypatch.setattr(gw.ModelGateway, "fingerprint", fp)
    return prov
