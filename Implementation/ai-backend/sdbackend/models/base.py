"""Provider-neutral model request/response types.

Provider-native conversation state (Gemini content parts with thought signatures; Gemma chat-template messages)
is carried opaquely in `Message.native` and is never translated across providers. A run is sealed to one
provider/model; fallback starts a new run and analysis version.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any


class ModelError(Exception):
    """Computation failure (never evidence of insufficiency)."""


class TransientError(ModelError):
    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class QuotaExhausted(ModelError):
    def __init__(self, message: str, scope: str = "unknown", retry_after: float | None = None):
        super().__init__(message)
        self.scope = scope            # minute | day | unknown
        self.retry_after = retry_after


class ProviderUnavailable(ModelError):
    pass


class OutputInvalid(ModelError):
    pass


@dataclass
class Part:
    kind: str                     # text | image | pdf
    text: str | None = None
    data: bytes | None = None
    mime: str | None = None

    @staticmethod
    def t(text: str) -> "Part":
        return Part("text", text=text)


@dataclass
class ToolCall:
    call_id: str
    name: str
    args: dict


@dataclass
class ToolResult:
    call_id: str
    name: str
    response: dict


@dataclass
class Message:
    role: str                                   # user | model | tool
    parts: list[Part] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    native: Any = None                          # provider-specific opaque content (never cross-provider)


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict                            # JSON Schema subset (object)


@dataclass
class GenRequest:
    role: str                                   # stage role: smoke | source_interpreter | case_investigator ...
    system: str
    messages: list[Message]
    tools: list[ToolSpec] = field(default_factory=list)
    response_schema: dict | None = None         # model-facing DTO schema (simplified); server validates fully
    max_output_tokens: int = 4096
    temperature: float = 0.0
    prompt_version: str = "unversioned"


@dataclass
class Usage:
    input_tokens: int | None = None
    cached_tokens: int | None = None
    thinking_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    source: str = "provider"                    # provider | estimated | unknown


@dataclass
class GenResult:
    provider: str
    model: str
    text: str
    data: Any = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    latency_ms: int = 0
    finish_reason: str | None = None
    model_version: str | None = None
    native_message: Message | None = None       # append to history for the next turn (same provider only)


def extract_json(text: str) -> Any:
    """Parse the first complete JSON value in `text` (tolerates code fences). Raises OutputInvalid."""
    stripped = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", stripped, flags=re.S)
    if fence:
        stripped = fence.group(1)
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    for i, ch in enumerate(stripped):
        if ch in "{[":
            try:
                value, _ = decoder.raw_decode(stripped[i:])
                return value
            except json.JSONDecodeError:
                continue
    raise OutputInvalid("no JSON value in model output")
