"""Opt-in compatibility for empty unsigned relay thinking blocks.

Only empty metadata blocks are removed from CLI -> SDK messages. Nonempty
thinking and malformed blocks remain fail-closed; no signature is invented.
The CLI's own session and provider request history are not modified.
"""

from typing import Any

from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport


def remove_empty_unsigned_thinking(data: dict[str, Any]) -> tuple[dict[str, Any], int]:
    if data.get("type") != "assistant" or not isinstance(data.get("message"), dict):
        return data, 0
    content = data["message"].get("content")
    if not isinstance(content, list):
        return data, 0
    kept = [block for block in content if not (
        isinstance(block, dict) and block.get("type") == "thinking"
        and block.get("thinking") == "" and "signature" not in block
        and set(block).issubset({"type", "thinking"})
    )]
    count = len(content) - len(kept)
    if not count:
        return data, 0
    return {**data, "message": {**data["message"], "content": kept}}, count


class EmptyThinkingCompatTransport(SubprocessCLITransport):
    def __init__(self, *, prompt, options, on_compat=None, on_context=None, filter_empty=True):
        super().__init__(prompt=prompt, options=options)
        self.on_compat = on_compat
        self.on_context = on_context
        self.filter_empty = filter_empty

    async def read_messages(self):
        stream = super().read_messages()
        try:
            async for data in stream:
                if self.on_context is not None:
                    from .context_diagnostics import context_observation
                    event = context_observation(data)
                    if event is not None:
                        self.on_context(event)
                normalized, count = (remove_empty_unsigned_thinking(data)
                                     if self.filter_empty else (data, 0))
                if count and self.on_compat is not None:
                    self.on_compat({
                        "event": "empty_unsigned_thinking_removed",
                        "blocks": count, "adapter": "empty-thinking-v1",
                    })
                yield normalized
        finally:
            close = getattr(stream, "aclose", None)
            if close is not None:
                await close()
