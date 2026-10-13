"""Content-free observations of CLI messages, before the SDK drops metadata.

These are reported usage/CLI events, NOT a measurement of the outgoing wire
request. Missing usage is unknown, never zero. No prompts or raw errors persist.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any


def _counts(value: Any) -> dict:
    if not isinstance(value, dict):
        return {}
    keys = ("input_tokens", "output_tokens", "cache_read_input_tokens",
            "cache_creation_input_tokens", "reasoning_tokens", "pre_tokens",
            "post_tokens")
    return {k: value[k] for k in keys
            if type(value.get(k)) is int and value[k] >= 0}


def context_observation(data: dict) -> dict | None:
    event: dict[str, Any] = {"event": "context_diagnostic", "schema": "v1"}
    kind = data.get("type")
    if kind == "stream_event" and isinstance(data.get("event"), dict):
        stream = data["event"]
        # Never capture content_block_delta: it contains text/thinking.
        if stream.get("type") == "message_delta":
            return {**event, "kind": "message_delta", "usage": _counts(stream.get("usage"))}
        return None
    if kind == "assistant" and isinstance(data.get("message"), dict):
        msg = data["message"]
        event["kind"] = "assistant"
        event["usage"] = _counts(msg.get("usage"))
        if isinstance(msg.get("id"), str):
            event["message_id_sha256"] = hashlib.sha256(msg["id"].encode()).hexdigest()
        # Token totals here use the Anthropic dialect (cache excluded from input).
        # Do not silently correct relay accounting or use this for billing.
        u = event["usage"]
        if all(k in u for k in ("input_tokens", "cache_read_input_tokens",
                               "cache_creation_input_tokens")):
            event["anthropic_dialect_context_input"] = sum(u[k] for k in (
                "input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
        blocks = msg.get("content", [])
        event["content_block_count"] = len(blocks) if isinstance(blocks, list) else None
        details = data.get("errorDetails")
        texts = [b.get("text", "") for b in blocks
                 if isinstance(b, dict) and b.get("type") == "text"] if isinstance(blocks, list) else []
        text = "\n".join(t for t in texts if isinstance(t, str))
        error_text = details if isinstance(details, str) else text
        if "prompt is too long" in error_text.lower():
            event["failure"] = "prompt_too_long"
            event["error_details_present"] = isinstance(details, str)
            match = re.search(r"(\d+)\s*tokens?\s*>\s*(\d+)\s*(?:maximum\s*)?tokens?", error_text, re.I)
            if match:
                event["reported_prompt_tokens"] = int(match[1])
                event["reported_limit_tokens"] = int(match[2])
        return event
    if kind == "system" and data.get("subtype") in ("compact_boundary", "microcompact_boundary"):
        event["kind"] = data["subtype"]
        metadata = data.get("compact_metadata", data.get("compactMetadata", {}))
        event["counts"] = _counts(metadata)
        if isinstance(metadata, dict) and metadata.get("trigger") in ("auto", "manual"):
            event["trigger"] = metadata["trigger"]
        return event
    if kind == "result":
        return {**event, "kind": "result", "usage": _counts(data.get("usage")),
                "is_error": data.get("is_error") is True}
    return None
