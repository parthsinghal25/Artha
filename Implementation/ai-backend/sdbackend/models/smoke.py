"""Operational model smoke test on explicitly synthetic, in-process inputs.

Not customer evidence, not benchmark proof. Checks the provider/runtime actually supports: text, image input,
PDF (image-only, i.e. scanned) input, schema-constrained JSON output, and a host-validated function-call round
trip. Records latency, token usage and (for local Gemma) memory.
"""
from __future__ import annotations

import io
import json
import platform
import time

from jsonschema import Draft202012Validator

from ..canonical import random_id, utc_now_iso
from ..config import BACKEND_ROOT
from ..runtime import make_runtime
from .base import GenRequest, Message, ModelError, Part, ToolResult, ToolSpec
from .gateway import ModelGateway, provider_status

SMOKE_PROMPT_VERSION = "smoke-1.0.0"
SYNTHETIC_CODE = "SMOKE-7Q4"
SYS = ("You are running an automated, explicitly synthetic operational smoke test. Nothing here is real "
       "customer data. Answer exactly and briefly.")


def _synthetic_image() -> bytes:
    from PIL import Image, ImageDraw, ImageFont
    im = Image.new("RGB", (640, 240), "white")
    d = ImageDraw.Draw(im)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 56)
    except OSError:
        font = ImageFont.load_default()
    d.rectangle([20, 20, 140, 140], fill=(200, 30, 30))
    d.text((170, 60), SYNTHETIC_CODE, fill="black", font=font)
    d.text((20, 180), "SYNTHETIC TEST IMAGE", fill="gray", font=ImageFont.load_default())
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def _synthetic_scanned_pdf() -> bytes:
    from PIL import Image, ImageDraw, ImageFont
    im = Image.new("RGB", (1240, 1754), "white")
    d = ImageDraw.Draw(im)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 44)
    except OSError:
        font = ImageFont.load_default()
    for i, line in enumerate(["SYNTHETIC TEST LETTER", "Reference: TEST-PDF-2291", "Amount stated: AUD 125.40",
                              "This page is generated for a smoke test only."]):
        d.text((100, 150 + i * 90), line, fill="black", font=font)
    buf = io.BytesIO()
    im.save(buf, format="PDF", resolution=150)
    return buf.getvalue()


STRUCT_SCHEMA = {"type": "object", "additionalProperties": False,
                 "properties": {"code": {"type": "string"}, "square_colour": {"type": "string", "enum": ["red", "green", "blue", "other"]},
                                "unknowns": {"type": "array", "items": {"type": "string"}}},
                 "required": ["code", "square_colour", "unknowns"]}
TOOL = ToolSpec("lookup_synthetic_record", "Look up one synthetic test record by its identifier.",
                {"type": "object", "properties": {"record_id": {"type": "string", "description": "identifier such as R-1"}},
                 "required": ["record_id"], "additionalProperties": False})
TOOL_ARGS = Draft202012Validator(TOOL.parameters)
FINAL_SCHEMA = {"type": "object", "additionalProperties": False,
                "properties": {"record_id": {"type": "string"}, "status": {"type": "string"}},
                "required": ["record_id", "status"]}


def run_smoke(provider: str, write_report: bool = True) -> dict:
    rt = make_runtime()
    run_id = random_id("SMOKE", 6)
    report = {"kind": "model-operational-smoke", "synthetic_inputs_only": True, "run_id": run_id,
              "started_at": utc_now_iso(), "provider_requested": provider, "host": {
                  "machine": platform.machine(), "system": platform.system(), "release": platform.release()},
              "provider_status": provider_status(rt.settings), "checks": [], "notes": []}
    try:
        gw = ModelGateway(rt, provider, run_id)
    except ModelError as exc:
        report["result"] = "provider_unavailable"
        report["reason"] = str(exc)
        return _finish(report, provider, write_report)
    report["provider"], report["model"] = gw.provider.name, gw.model
    if provider == "gemma":
        t = time.monotonic()
        report["model_load_seconds"] = round(gw.provider.warm(), 2)
        report["notes"].append(f"weights warm in {round(time.monotonic() - t, 2)} s")

    def check(name, fn):
        t = time.monotonic()
        entry = {"check": name}
        try:
            ok, detail, res = fn()
            entry.update(passed=bool(ok), detail=detail)
            if res is not None:
                entry.update(latency_ms=res.latency_ms, usage=res.usage.__dict__,
                             peak_memory_gb=round(getattr(res, "peak_memory_bytes", 0) / 1e9, 2) or None,
                             generation_tps=getattr(res, "generation_tps", None), prompt_tps=getattr(res, "prompt_tps", None))
        except ModelError as exc:
            entry.update(passed=False, detail=f"{type(exc).__name__}: {str(exc)[:300]}")
        entry["wall_seconds"] = round(time.monotonic() - t, 2)
        report["checks"].append(entry)

    def text_check():
        r = gw.call(GenRequest("smoke", SYS, [Message("user", [Part.t("Reply with exactly the word: ACKNOWLEDGED")])],
                               max_output_tokens=64, prompt_version=SMOKE_PROMPT_VERSION), task_id="text")
        return "ACKNOWLEDGED" in r.text.upper(), r.text[:80], r

    img = _synthetic_image()

    def image_check():
        r = gw.call(GenRequest("smoke", SYS, [Message("user", [
            Part("image", data=img, mime="image/png"),
            Part.t("What code is printed in large black letters? Reply with the code only.")])],
            max_output_tokens=64, prompt_version=SMOKE_PROMPT_VERSION), task_id="image")
        return SYNTHETIC_CODE in r.text.replace(" ", "").upper(), r.text[:80], r

    pdf = _synthetic_scanned_pdf()

    def pdf_check():
        r = gw.call(GenRequest("smoke", SYS, [Message("user", [
            Part("pdf", data=pdf, mime="application/pdf"),
            Part.t("This is a scanned PDF. State the Reference value only.")])],
            max_output_tokens=64, prompt_version=SMOKE_PROMPT_VERSION), task_id="pdf")
        return "TEST-PDF-2291" in r.text.upper().replace(" ", ""), r.text[:80], r

    def struct_check():
        r = gw.call(GenRequest("smoke", SYS, [Message("user", [
            Part("image", data=img, mime="image/png"),
            Part.t("Return the printed code, the colour of the square, and any facts you cannot determine.")])],
            response_schema=STRUCT_SCHEMA, max_output_tokens=256, prompt_version=SMOKE_PROMPT_VERSION), task_id="struct")
        errs = list(Draft202012Validator(STRUCT_SCHEMA).iter_errors(r.data)) if r.data is not None else ["no data"]
        ok = not errs and SYNTHETIC_CODE in str(r.data.get("code", "")).replace(" ", "").upper() and r.data["square_colour"] == "red"
        return ok, json.dumps(r.data)[:200] if r.data is not None else r.text[:200], r

    def tool_check():
        history = [Message("user", [Part.t("Use the lookup tool to find the status of synthetic record R-17, then "
                                           "answer with the record id and its status.")])]
        r1 = gw.call(GenRequest("smoke", SYS, history, tools=[TOOL], max_output_tokens=256,
                                prompt_version=SMOKE_PROMPT_VERSION), task_id="tool-1")
        if not r1.tool_calls:
            return False, "no function call returned: " + r1.text[:120], r1
        call = r1.tool_calls[0]
        if call.name != TOOL.name or list(TOOL_ARGS.iter_errors(call.args)):
            return False, f"host rejected call {call.name} {call.args}", r1
        record = {"R-17": {"record_id": "R-17", "status": "SYNTHETIC-CLOSED"}}.get(call.args["record_id"],
                                                                              {"error": "not found"})
        history += [r1.native_message, Message("tool", tool_results=[ToolResult(call.call_id, call.name, record)])]
        r2 = gw.call(GenRequest("smoke", SYS, history, response_schema=FINAL_SCHEMA, max_output_tokens=256,
                                prompt_version=SMOKE_PROMPT_VERSION), task_id="tool-2")
        ok = isinstance(r2.data, dict) and r2.data.get("record_id") == "R-17" and r2.data.get("status") == "SYNTHETIC-CLOSED"
        return ok, f"call={call.args} final={json.dumps(r2.data)[:120]}", r2

    for name, fn in (("text", text_check), ("image_input", image_check), ("scanned_pdf_input", pdf_check),
                     ("structured_json_output", struct_check), ("host_validated_tool_round_trip", tool_check)):
        check(name, fn)
    report["result"] = "pass" if all(c["passed"] for c in report["checks"]) else "partial"
    return _finish(report, provider, write_report)


def _finish(report: dict, provider: str, write: bool) -> dict:
    report["finished_at"] = utc_now_iso()
    if write:
        out = BACKEND_ROOT / "reports" / "model-smoke"
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{provider}-{report['run_id']}.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    return report
