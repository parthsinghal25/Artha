"""Explicit standard Vertex ADC route; bounded single-worker qualification profile.

SDK retries are disabled: each gateway transport retry passes this durable reservation.
Text-only qualification uses a deliberately loose UTF-8 byte upper bound, not a chars/3 estimate.
Media conversion is inherited, but media generation needs a separately qualified token counter.
"""
from __future__ import annotations

import json
from dataclasses import replace
from importlib.metadata import version
from pathlib import Path
from time import monotonic

from .base import ProviderUnavailable
from .gemini import GeminiProvider


class QualificationBudget:
    def __init__(self, path: Path, *, attempts=12, input_tokens=160000, receipt=None):
        self.path = Path(path)
        self.max_attempts = min(12, attempts)
        self.max_input = min(160000, input_tokens)
        self.receipt = receipt
        self.started = monotonic()

    def reserve(self, req):
        if monotonic() - self.started > 780:
            raise ProviderUnavailable("qualification time budget exhausted")
        if any(p.kind != "text" for m in req.messages for p in m.parts):
            raise ProviderUnavailable("qualification budget supports cached text only; media counter unqualified")
        context = [req.system, req.response_schema, [vars(t) for t in req.tools],
                   [{"parts": [vars(p) for p in m.parts], "native": str(m.native) if m.native else None,
                     "tool_results": [vars(r) for r in m.tool_results],
                     "tool_calls": [vars(c) for c in m.tool_calls]} for m in req.messages]]
        # One token per UTF-8 byte plus 2048 framing tokens is a conservative bound for this text path.
        bound = len(json.dumps(context, ensure_ascii=False, default=str).encode("utf-8")) + 2048
        state = json.loads(self.path.read_text()) if self.path.exists() else {"attempts": 0, "input_upper_bound": 0}
        if state["attempts"] >= self.max_attempts or state["input_upper_bound"] + bound > self.max_input:
            raise ProviderUnavailable("qualification attempt/input budget exhausted")
        state.update(attempts=state["attempts"] + 1, input_upper_bound=state["input_upper_bound"] + bound)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state))
        tmp.replace(self.path)
        receipt = {**state, "state": "reserved", "input_bound_this_attempt": bound,
                   "max_output_tokens": min(req.max_output_tokens, 4096), "role": req.role}
        if self.receipt:
            self.receipt(receipt)  # must durably succeed before dispatch; duplicate receipt refuses a restart
        return receipt


class VertexProvider(GeminiProvider):
    name = "vertex-ai"

    def __init__(self, project, location, model, *, budget_path: Path):
        if not project or not location or not model:
            raise ProviderUnavailable("explicit Vertex project/location/model required")
        import google.auth
        from google import genai
        from google.genai import types
        credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        self._genai = genai
        self.client = genai.Client(vertexai=True, project=project, location=location, credentials=credentials,
                                  http_options=types.HttpOptions(api_version="v1", timeout=60000,
                                      retry_options=types.HttpRetryOptions(attempts=1)))
        self.project, self.location, self.model = project, location, model
        self.thinking_level = "low"
        self.budget = QualificationBudget(budget_path)
        self.receipt = None

    def identity(self):
        return {"provider": self.name, "route": "standard-vertex-adc", "project": self.project,
                "location": self.location, "model": self.model, "sdk": {"google-genai": version("google-genai")},
                "thinking_level": self.thinking_level, "profile": "one-case-12-attempts-v1"}

    def _generation_overrides(self):
        from google.genai import types
        return {"automatic_continuation": False,
                "automatic_function_calling": types.AutomaticFunctionCallingConfig(disable=True)}

    def generate(self, req):
        req = replace(req, max_output_tokens=min(req.max_output_tokens, 4096))
        reserved = self.budget.reserve(req)
        try:
            result = super().generate(req)
        except BaseException as exc:
            if self.receipt:
                self.receipt({**reserved, "state": "failed", "error_class": type(exc).__name__})
            raise
        if self.receipt:
            self.receipt({**reserved, "state": "complete", "model_version": result.model_version,
                          "usage": vars(result.usage), "latency_ms": result.latency_ms})
        return result
