"""Gemini Developer API adapter (google-genai SDK, server-side API key, Free tier only).

* Refuses to run unless the operator attests the key belongs to a verified Free-tier project with billing disabled
  (SDB_GEMINI_FREE_TIER_ATTESTED=1). A code-level budget cannot make a paid project free.
* Manual function dispatch: automatic function calling is disabled; complete model content (including opaque
  thought signatures) is preserved for the next turn within the same run.
* Uses Standard requests only (no Batch/Flex/priced features).
"""
from __future__ import annotations

import os
import re
import time

from .base import (GenRequest, GenResult, Message, OutputInvalid, ProviderUnavailable, QuotaExhausted, ToolCall,
                   TransientError, Usage, extract_json)

PROVIDER = "gemini-developer-api"


def _retry_after(exc) -> float | None:
    text = str(exc)
    m = re.search(r"retry(?:Delay| in)[\"': ]+(\d+(?:\.\d+)?)s", text, flags=re.I)
    return float(m.group(1)) if m else None


class GeminiProvider:
    name = PROVIDER

    def __init__(self, model: str, *, free_tier_attested: bool, thinking_level: str = "low"):
        key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not key:
            raise ProviderUnavailable("GEMINI_API_KEY is not configured")
        if not free_tier_attested:
            raise ProviderUnavailable("Free-tier status of the key's project is not attested "
                                      "(set SDB_GEMINI_FREE_TIER_ATTESTED=1 only after verifying in AI Studio)")
        from google import genai
        self._genai = genai
        self.client = genai.Client(api_key=key)
        self.model = model
        self.thinking_level = thinking_level

    # -- conversion --------------------------------------------------------
    def _generation_overrides(self) -> dict:
        return {}

    def _contents(self, messages: list[Message]):
        from google.genai import types
        out = []
        for m in messages:
            if m.native is not None:
                out.append(m.native)
                continue
            if m.role == "tool":
                # function responses carry the originating call id (correlation verified in google-genai 2.30.1)
                out.append(types.Content(role="user", parts=[
                    types.Part(function_response=types.FunctionResponse(
                        id=None if r.call_id.startswith("call-") else r.call_id, name=r.name, response=r.response))
                    for r in m.tool_results]))
                continue
            parts = []
            for p in m.parts:
                if p.kind == "text":
                    parts.append(types.Part.from_text(text=p.text))
                else:
                    parts.append(types.Part.from_bytes(data=p.data, mime_type=p.mime))
            out.append(types.Content(role="model" if m.role == "model" else "user", parts=parts))
        return out

    def generate(self, req: GenRequest) -> GenResult:
        from google.genai import errors, types
        cfg = dict(system_instruction=req.system, candidate_count=1, max_output_tokens=req.max_output_tokens,
                   temperature=req.temperature,
                   thinking_config=types.ThinkingConfig(thinking_level=self.thinking_level, include_thoughts=False))
        cfg.update(self._generation_overrides())
        if req.tools:
            cfg["tools"] = [types.Tool(function_declarations=[
                types.FunctionDeclaration(name=t.name, description=t.description, parameters_json_schema=t.parameters)
                for t in req.tools])]
            cfg["automatic_function_calling"] = types.AutomaticFunctionCallingConfig(disable=True)
        if req.response_schema is not None:
            cfg["response_mime_type"] = "application/json"
            cfg["response_json_schema"] = req.response_schema
        start = time.monotonic()
        try:
            resp = self.client.models.generate_content(model=self.model, contents=self._contents(req.messages),
                                                       config=types.GenerateContentConfig(**cfg))
        except errors.APIError as exc:
            code = getattr(exc, "code", None)
            text = str(exc)
            if code == 429:
                scope = "day" if re.search(r"per.?day|PerDay|RPD", text, flags=re.I) else \
                        "minute" if re.search(r"per.?minute|PerMinute|RPM|TPM", text, flags=re.I) else "unknown"
                if scope == "day":
                    raise QuotaExhausted(text[:300], scope="day", retry_after=_retry_after(exc)) from exc
                raise TransientError(text[:300], retry_after=_retry_after(exc)) from exc
            if code in (500, 502, 503, 504):
                raise TransientError(text[:300], retry_after=_retry_after(exc)) from exc
            if code in (401, 403):
                raise ProviderUnavailable(f"Gemini access denied ({code})") from exc
            if code == 404:
                raise ProviderUnavailable(f"model {self.model} not available to this key") from exc
            raise OutputInvalid(f"Gemini request rejected ({code}): {text[:200]}") from exc
        except (TimeoutError, ConnectionError) as exc:
            raise TransientError(f"transport: {type(exc).__name__}") from exc
        except Exception as exc:  # noqa: BLE001 - httpx transport classes used by the SDK
            import httpx
            if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
                raise TransientError(f"transport: {type(exc).__name__}") from exc
            raise
        latency = int((time.monotonic() - start) * 1000)
        um = resp.usage_metadata
        usage = Usage(input_tokens=getattr(um, "prompt_token_count", None),
                      cached_tokens=getattr(um, "cached_content_token_count", None),
                      thinking_tokens=getattr(um, "thoughts_token_count", None),
                      output_tokens=getattr(um, "candidates_token_count", None),
                      total_tokens=getattr(um, "total_token_count", None), source="provider" if um else "unknown")
        cand = resp.candidates[0] if resp.candidates else None
        if cand is None or cand.content is None:
            raise OutputInvalid(f"no candidate content (finish={getattr(cand, 'finish_reason', None)})")
        calls, texts = [], []
        for i, part in enumerate(cand.content.parts or []):
            if part.function_call is not None:
                fc = part.function_call
                calls.append(ToolCall(call_id=fc.id or f"call-{i}", name=fc.name, args=dict(fc.args or {})))
            elif part.text and not part.thought:
                texts.append(part.text)
        text = "".join(texts)
        data = None
        if req.response_schema is not None and not calls:
            data = extract_json(text)
        return GenResult(provider=self.name, model=self.model, text=text, data=data, tool_calls=calls, usage=usage,
                         latency_ms=latency, finish_reason=str(cand.finish_reason),
                         model_version=getattr(resp, "model_version", None),
                         native_message=Message(role="model", native=cand.content))
