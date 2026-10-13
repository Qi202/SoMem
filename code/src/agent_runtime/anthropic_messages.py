"""Non-streaming Anthropic Messages adapter for compatible gateways.

Some third-party Anthropic-compatible endpoints implement Messages and tool
use correctly but do not complete Claude Code's richer streaming request.  The
adapter keeps SoMem's prompts and tool schemas unchanged while using the
plain Messages protocol.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from anthropic import Anthropic

from .claude_code import AgentExecutionError, AgentResult


@dataclass(frozen=True)
class AnthropicMessagesConfig:
    base_url: str
    api_key: str
    model: str
    timeout_s: float = 300.0
    max_output_tokens: int = 16_384

    def __post_init__(self) -> None:
        if not self.base_url.strip():
            raise ValueError("base_url is required")
        if not self.api_key:
            raise ValueError("api_key is required")
        if not self.model.strip():
            raise ValueError("model is required")
        if self.timeout_s <= 0:
            raise ValueError("timeout_s must be positive")
        if self.max_output_tokens < 1:
            raise ValueError("max_output_tokens must be positive")


def _usage_value(usage: Any, name: str) -> int:
    return int(getattr(usage, name, 0) or 0)


def _assistant_blocks(response: Any) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for block in response.content:
        if block.type == "text":
            blocks.append({"type": "text", "text": block.text})
        elif block.type == "tool_use":
            blocks.append({
                "type": "tool_use",
                "id": block.id,
                "name": block.name,
                "input": block.input,
            })
    return blocks


class AnthropicMessagesAgent:
    """Run the existing Writer protocol over plain Anthropic Messages."""

    def __init__(
        self,
        config: AnthropicMessagesConfig,
        *,
        client: Any | None = None,
    ) -> None:
        self.config = config
        self._client = client or Anthropic(
            base_url=config.base_url,
            api_key=config.api_key,
            timeout=config.timeout_s,
        )

    @staticmethod
    def _tool_definitions(tools: list[Any]) -> list[dict[str, Any]]:
        return [{
            "name": definition.name,
            "description": definition.description,
            "input_schema": definition.input_schema,
        } for definition in tools]

    @staticmethod
    def _run_tool(definition: Any, arguments: dict[str, Any]) -> dict[str, Any]:
        return asyncio.run(definition.handler(arguments))

    def run(
        self,
        *,
        prompt: str,
        system_prompt: str,
        cwd: str | Path,
        tools: list[Any] | None = None,
        max_turns: int = 20,
        max_budget_usd: float | None = None,
        output_schema: dict[str, Any] | None = None,
        progress_fn: Any | None = None,
        builtin_tools_enabled: bool = True,
    ) -> AgentResult:
        del builtin_tools_enabled
        if max_turns < 1:
            raise ValueError("max_turns must be positive")
        if max_budget_usd is not None:
            raise ValueError(
                "AnthropicMessagesAgent does not support max_budget_usd"
            )
        if not Path(cwd).resolve().is_dir():
            raise ValueError(f"agent working directory does not exist: {cwd}")

        definitions = list(tools or [])
        definitions_by_name = {item.name: item for item in definitions}
        api_tools = self._tool_definitions(definitions)
        structured_tool_name = "submit_structured_output"
        if output_schema is not None:
            if definitions:
                raise ValueError(
                    "structured output and workspace tools cannot be combined"
                )
            api_tools = [{
                "name": structured_tool_name,
                "description": "Return the requested structured result.",
                "input_schema": output_schema,
            }]

        messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
        texts: list[str] = []
        structured_output: dict[str, Any] | None = None
        input_tokens = output_tokens = 0
        cache_creation_tokens = cache_read_tokens = 0
        started = time.monotonic()
        stop_reason: str | None = None
        turns = 0

        try:
            for turns in range(1, max_turns + 1):
                kwargs: dict[str, Any] = {
                    "model": self.config.model,
                    "max_tokens": self.config.max_output_tokens,
                    "system": system_prompt,
                    "messages": messages,
                    "thinking": {"type": "disabled"},
                }
                if api_tools:
                    kwargs["tools"] = api_tools
                    kwargs["tool_choice"] = {
                        "type": "auto",
                        # Gemini's Anthropic compatibility layer can lose the
                        # call/result pairing when several tools are emitted
                        # in one assistant message. Serial tool use preserves
                        # the same schemas and trajectory semantics.
                        "disable_parallel_tool_use": True,
                    }
                if output_schema is not None:
                    kwargs["tool_choice"] = {
                        "type": "tool",
                        "name": structured_tool_name,
                        "disable_parallel_tool_use": True,
                    }
                response = self._client.messages.create(**kwargs)
                usage = response.usage
                input_tokens += _usage_value(usage, "input_tokens")
                output_tokens += _usage_value(usage, "output_tokens")
                cache_creation_tokens += _usage_value(
                    usage, "cache_creation_input_tokens"
                )
                cache_read_tokens += _usage_value(
                    usage, "cache_read_input_tokens"
                )
                stop_reason = response.stop_reason
                assistant = _assistant_blocks(response)
                messages.append({"role": "assistant", "content": assistant})
                texts.extend(
                    block["text"] for block in assistant
                    if block["type"] == "text"
                )
                if progress_fn is not None:
                    progress_fn({
                        "event": "assistant_message",
                        "assistant_messages": turns,
                        "content_blocks": len(assistant),
                    })

                tool_results: list[dict[str, Any]] = []
                for block in assistant:
                    if block["type"] != "tool_use":
                        continue
                    if block["name"] == structured_tool_name:
                        structured_output = block["input"]
                        continue
                    definition = definitions_by_name.get(block["name"])
                    if definition is None:
                        result = {
                            "content": [{
                                "type": "text",
                                "text": json.dumps({
                                    "error": f"unknown tool: {block['name']}"
                                }),
                            }],
                            "is_error": True,
                        }
                    else:
                        result = self._run_tool(definition, block["input"])
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block["id"],
                        # Not required by Anthropic proper, but accepted as an
                        # extension by compatibility gateways and removes any
                        # ambiguity when Gemini emits duplicate/unstable call
                        # ids for parallel functions.
                        "name": block["name"],
                        "content": result.get("content", []),
                        "is_error": bool(result.get("is_error", False)),
                    })

                if output_schema is not None:
                    if structured_output is None:
                        raise AgentExecutionError(
                            "model did not submit the structured output tool"
                        )
                    break
                if not tool_results:
                    break
                messages.append({"role": "user", "content": tool_results})
            else:
                raise AgentExecutionError(
                    f"agent exceeded max_turns={max_turns}"
                )
        except Exception as exc:
            if isinstance(exc, AgentExecutionError):
                raise
            message = str(exc).replace(self.config.api_key, "[redacted]")
            raise AgentExecutionError(message) from exc

        duration_ms = int((time.monotonic() - started) * 1000)
        if progress_fn is not None:
            progress_fn({
                "event": "result",
                "assistant_messages": turns,
                "num_turns": turns,
                "is_error": False,
            })
        return AgentResult(
            text="\n".join(texts).strip(),
            structured_output=structured_output,
            num_turns=turns,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_creation_input_tokens=cache_creation_tokens,
            cache_read_input_tokens=cache_read_tokens,
            anthropic_equivalent_cost_usd=None,
            duration_ms=duration_ms,
            duration_api_ms=duration_ms,
            stop_reason=stop_reason,
            session_id=str(uuid.uuid4()),
            reasoning_tokens=0,
        )
