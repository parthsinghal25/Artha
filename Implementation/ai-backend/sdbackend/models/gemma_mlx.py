"""Local Google Gemma 4 adapter on Apple-silicon MLX (mlx-vlm), lazily loaded.

* Native Gemma 4 tool declarations/calls via the model's own chat template; calls are parsed from
  `<|tool_call>call:NAME{...}<tool_call|>` and dispatched by the host (never auto-executed).
* Structured finalisation: tools disabled, JSON-only instruction, then strict parse + server schema validation
  (no constrained decoding in this runtime; the caller performs at most one bounded repair turn).
* PDF parts are rendered to page images locally (pypdfium2) because the runtime takes images, not PDFs.
* Greedy decoding (temperature 0) for repeatability; still not a determinism guarantee across versions.
"""
from __future__ import annotations

import io
import json
import re
import threading
import time
from pathlib import Path

from .base import GenRequest, GenResult, Message, OutputInvalid, ProviderUnavailable, ToolCall, Usage, extract_json

PROVIDER = "gemma-local-mlx"
_LOCK = threading.Lock()
_LOADED: dict[str, tuple] = {}
TOOL_CALL_RE = re.compile(r"<\|tool_call>call:([A-Za-z0-9_.-]+)\{(.*?)\}<tool_call\|>", re.S)
THOUGHT_RE = re.compile(r"<\|channel>.*?<channel\|>", re.S)
SPECIAL_RE = re.compile(r"<\|?[a-z_]+\|?>|<turn\|>|<eos>|<bos>")
Q = '<|"|>'


def parse_gemma_args(body: str) -> dict:
    """Parse Gemma's argument syntax (`key:<|"|>str<|"|>,n:3,l:[...],o:{...}`) into a dict."""
    pos = 0

    def ws():
        nonlocal pos
        while pos < len(body) and body[pos] in " \n\t\r":
            pos += 1

    def value():
        nonlocal pos
        ws()
        if body.startswith(Q, pos):
            end = body.find(Q, pos + len(Q))
            if end == -1:
                raise OutputInvalid("unterminated string in tool call")
            s = body[pos + len(Q):end]
            pos = end + len(Q)
            return s
        if body[pos] == "[":
            pos += 1
            items = []
            ws()
            if body[pos] == "]":
                pos += 1
                return items
            while True:
                items.append(value())
                ws()
                if body[pos] == ",":
                    pos += 1
                    continue
                if body[pos] == "]":
                    pos += 1
                    return items
                raise OutputInvalid("bad list in tool call")
        if body[pos] == "{":
            pos += 1
            obj = members("}")
            pos += 1
            return obj
        m = re.compile(r"-?\d+(?:\.\d+)?|true|false|null").match(body, pos)
        if not m:
            raise OutputInvalid(f"unparseable tool-call value at {pos}")
        pos = m.end()
        return json.loads(m.group(0))

    def members(close: str | None) -> dict:
        nonlocal pos
        obj = {}
        ws()
        while pos < len(body) and (close is None or body[pos] != close):
            m = re.compile(r"[A-Za-z_][A-Za-z0-9_]*").match(body, pos)
            if not m:
                raise OutputInvalid(f"bad key at {pos}")
            key = m.group(0)
            pos = m.end()
            ws()
            if body[pos] != ":":
                raise OutputInvalid("missing ':' in tool call")
            pos += 1
            obj[key] = value()
            ws()
            if pos < len(body) and body[pos] == ",":
                pos += 1
                ws()
        return obj

    result = members(None)
    if pos != len(body):
        raise OutputInvalid("trailing content in tool call arguments")
    return result


def _pdf_to_images(data: bytes, max_pages: int = 4, scale: float = 1.4):
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(data)
    images = []
    for i in range(min(len(pdf), max_pages)):
        images.append(pdf[i].render(scale=scale).to_pil().convert("RGB"))
    return images


class GemmaProvider:
    name = PROVIDER

    def __init__(self, model_path: Path, model_id: str):
        self.model_path = Path(model_path)
        self.model = model_id
        if not (self.model_path / "config.json").is_file():
            raise ProviderUnavailable(f"Gemma weights not present at {self.model_path}")

    def _load(self):
        key = str(self.model_path)
        if key not in _LOADED:
            from mlx_vlm import load
            t = time.monotonic()
            model, processor = load(key)
            _LOADED[key] = (model, processor, time.monotonic() - t)
        return _LOADED[key]

    def warm(self) -> float:
        with _LOCK:
            return self._load()[2]

    def _messages(self, req: GenRequest):
        from PIL import Image
        msgs = [{"role": "system", "content": req.system}]
        images = []
        for m in req.messages:
            if m.native is not None:
                msgs.append(m.native)
                continue
            if m.role == "tool":
                for r in m.tool_results:
                    msgs.append({"role": "tool", "tool_call_id": r.call_id, "content": json.dumps(r.response, ensure_ascii=False)})
                continue
            content = []
            for p in m.parts:
                if p.kind == "text":
                    content.append({"type": "text", "text": p.text})
                elif p.kind == "image":
                    images.append(Image.open(io.BytesIO(p.data)).convert("RGB"))
                    content.append({"type": "image"})
                elif p.kind == "pdf":
                    for im in _pdf_to_images(p.data):
                        images.append(im)
                        content.append({"type": "image"})
            msgs.append({"role": "assistant" if m.role == "model" else "user", "content": content})
        return msgs, images

    def generate(self, req: GenRequest) -> GenResult:
        from mlx_vlm import generate
        import mlx.core as mx
        system = req.system
        if req.response_schema is not None and not req.tools:
            system += ("\n\nRespond with a single JSON object only, no prose and no code fences, conforming to this "
                       "JSON Schema:\n" + json.dumps(req.response_schema, ensure_ascii=False))
        req = GenRequest(**{**req.__dict__, "system": system})
        with _LOCK:
            model, processor, _ = self._load()
            tok = getattr(processor, "tokenizer", processor)
            msgs, images = self._messages(req)
            tools = [{"type": "function", "function": {"name": t.name, "description": t.description,
                                                       "parameters": t.parameters}} for t in req.tools] or None
            prompt = tok.apply_chat_template(msgs, tools=tools, add_generation_prompt=True, tokenize=False)
            start = time.monotonic()
            mx.reset_peak_memory()
            out = generate(model, processor, prompt, image=images or None, max_tokens=req.max_output_tokens,
                           temperature=0.0, verbose=False, skip_special_tokens=False)
            latency = int((time.monotonic() - start) * 1000)
            peak = mx.get_peak_memory()
        raw = getattr(out, "text", out)
        raw = THOUGHT_RE.sub("", raw)
        calls = []
        try:
            for i, m in enumerate(TOOL_CALL_RE.finditer(raw)):
                calls.append(ToolCall(call_id=f"gemma-call-{i}", name=m.group(1), args=parse_gemma_args(m.group(2))))
        except OutputInvalid:
            raise
        except Exception as exc:  # noqa: BLE001 - malformed local tool syntax is a bounded output failure
            raise OutputInvalid(f"malformed tool-call syntax: {type(exc).__name__}") from exc
        if "<|tool_call>" in raw and not calls:
            raise OutputInvalid("unterminated or malformed tool-call block")
        text = SPECIAL_RE.sub("", TOOL_CALL_RE.sub("", raw)).strip()
        if "<|image|>" in raw or "<image|>" in raw:
            raise OutputInvalid("model emitted an image special token in text output")
        data = extract_json(text) if (req.response_schema is not None and not calls) else None
        usage = Usage(input_tokens=getattr(out, "prompt_tokens", None), output_tokens=getattr(out, "generation_tokens", None),
                      total_tokens=(getattr(out, "prompt_tokens", 0) or 0) + (getattr(out, "generation_tokens", 0) or 0),
                      source="local_runtime")
        native = {"role": "assistant", "content": text,
                  **({"tool_calls": [{"id": c.call_id, "type": "function",
                                      "function": {"name": c.name, "arguments": c.args}} for c in calls]} if calls else {})}
        res = GenResult(provider=PROVIDER, model=self.model, text=text, data=data, tool_calls=calls, usage=usage,
                        latency_ms=latency, finish_reason=getattr(out, "finish_reason", None),
                        model_version=f"{self.model}@local", native_message=Message(role="model", native=native))
        res.peak_memory_bytes = int(peak)            # type: ignore[attr-defined]
        res.generation_tps = getattr(out, "generation_tps", None)   # type: ignore[attr-defined]
        res.prompt_tps = getattr(out, "prompt_tps", None)           # type: ignore[attr-defined]
        return res
