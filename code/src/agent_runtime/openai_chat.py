"""OpenAI Chat Completions adapter for provider-compatible agent models."""

from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openai import OpenAI, RateLimitError

from .claude_code import AgentExecutionError, AgentResult


_REQUEST_START_LOCK = threading.Lock()
_LAST_REQUEST_STARTED = 0.0


@dataclass(frozen=True)
class OpenAIChatConfig:
    base_url: str
    api_key: str
    model: str
    reasoning_effort: str | None = "none"
    google_thinking_level: str | None = None
    openrouter_flex: bool = False
    openrouter_reasoning_level: str | None = None
    enable_thinking: bool | None = None
    thinking_budget: int | None = None
    timeout_s: float = 300.0
    max_output_tokens: int = 16_384
    stream_response: bool = False
    force_final_at_max_turns: bool = False
    allowed_response_models: tuple[str, ...] = ()
    preserve_reasoning_content: bool = False
    record_response_diagnostics: bool = False
    rate_limit_backoff_s: float = 0.0
    max_rate_limit_retries: int = 0
    min_request_interval_s: float = 0.0

    def __post_init__(self) -> None:
        if not self.base_url.strip():
            raise ValueError("base_url is required")
        if not self.api_key:
            raise ValueError("api_key is required")
        if not self.model.strip():
            raise ValueError("model is required")
        if self.google_thinking_level is not None:
            if self.google_thinking_level not in {"minimal", "low", "medium", "high"}:
                raise ValueError("unsupported Google thinking level")
            if self.reasoning_effort is not None:
                raise ValueError("Google thinking level conflicts with reasoning_effort")
        if self.openrouter_flex:
            if self.openrouter_reasoning_level not in {"minimal", "low", "medium", "high"}:
                raise ValueError("OpenRouter Flex requires an explicit reasoning level")
            if self.reasoning_effort is not None or self.google_thinking_level is not None:
                raise ValueError("OpenRouter reasoning controls conflict with other controls")


def _tool_result_text(result: dict[str, Any]) -> str:
    text = "\n".join(
        str(part.get("text", "")) for part in result.get("content", [])
        if part.get("type") == "text"
    )
    return text or json.dumps(result, ensure_ascii=False)


class OpenAIChatAgent:
    """Run SoMem prompts while preserving every emitted tool call."""

    def __init__(self, config: OpenAIChatConfig, *, client: Any | None = None) -> None:
        self.config = config
        base_url = config.base_url.rstrip("/")
        if not base_url.endswith("/v1"):
            base_url += "/v1"
        self._client = client or OpenAI(
            base_url=base_url, api_key=config.api_key, timeout=config.timeout_s
        )

    @staticmethod
    def _definitions(tools: list[Any]) -> list[dict[str, Any]]:
        return [{
            "type": "function",
            "function": {
                "name": item.name,
                "description": item.description,
                "parameters": item.input_schema,
            },
        } for item in tools]

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
            raise ValueError("OpenAIChatAgent does not support max_budget_usd")
        if not Path(cwd).resolve().is_dir():
            raise ValueError(f"agent working directory does not exist: {cwd}")

        definitions = list(tools or [])
        by_name = {item.name: item for item in definitions}
        api_tools = self._definitions(definitions)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]
        texts: list[str] = []
        structured_output: dict[str, Any] | None = None
        input_tokens = output_tokens = cache_read_tokens = reasoning_tokens = 0
        started = time.monotonic()
        stop_reason: str | None = None
        turns = 0
        response_models: list[str] = []
        response_request_ids: list[str] = []
        response_diagnostics: list[dict[str, Any]] = []

        try:
            for turns in range(1, max_turns + 1):
                force_final = self.config.force_final_at_max_turns and turns == max_turns
                if force_final:
                    messages.append({
                        "role": "user",
                        "content": (
                            "Stop retrieving now. Based only on information already "
                            "collected in this conversation, answer the original "
                            "question. Do not call tools or request more information. "
                            "Return only <answer>...</answer>."
                        ),
                    })
                kwargs: dict[str, Any] = {
                    "model": self.config.model,
                    "messages": messages,
                    "max_completion_tokens": self.config.max_output_tokens,
                }
                if self.config.reasoning_effort is not None:
                    kwargs["reasoning_effort"] = self.config.reasoning_effort
                extra_body = {}
                if self.config.enable_thinking is not None:
                    extra_body["enable_thinking"] = self.config.enable_thinking
                if self.config.thinking_budget is not None:
                    extra_body["thinking_budget"] = self.config.thinking_budget
                if self.config.google_thinking_level is not None:
                    extra_body["extra_body"] = {
                        "google": {"thinking_config": {
                            "thinking_level": self.config.google_thinking_level,
                        }}
                    }
                if self.config.openrouter_flex:
                    extra_body["service_tier"] = "flex"
                    extra_body["provider"] = {
                        "only": ["google-ai-studio/flex", "google-vertex/flex"],
                        "allow_fallbacks": False,
                        "require_parameters": True,
                    }
                    extra_body["reasoning"] = {
                        "effort": self.config.openrouter_reasoning_level,
                    }
                if extra_body:
                    kwargs["extra_body"] = extra_body
                if api_tools and not force_final:
                    kwargs["tools"] = api_tools
                    if not self.config.openrouter_flex:
                        kwargs["parallel_tool_calls"] = False
                if output_schema is not None:
                    kwargs["response_format"] = {
                        "type": "json_schema",
                        "json_schema": {
                            "name": "structured_output",
                            "strict": True,
                            "schema": output_schema,
                        },
                    }
                for retry in range(self.config.max_rate_limit_retries + 1):
                    try:
                        if self.config.min_request_interval_s > 0:
                            global _LAST_REQUEST_STARTED
                            with _REQUEST_START_LOCK:
                                wait_s = self.config.min_request_interval_s - (time.monotonic() - _LAST_REQUEST_STARTED)
                                if wait_s > 0:
                                    time.sleep(wait_s)
                                _LAST_REQUEST_STARTED = time.monotonic()
                        if self.config.stream_response:
                            with self._client.chat.completions.stream(**kwargs) as stream:
                                response = stream.get_final_completion()
                        else:
                            response = self._client.chat.completions.create(**kwargs)
                        break
                    except RateLimitError:
                        if retry >= self.config.max_rate_limit_retries:
                            raise
                        time.sleep(self.config.rate_limit_backoff_s)
                response_model = str(getattr(response, "model", "") or "")
                request_id = str(getattr(response, "_request_id", "") or "")
                response_models.append(response_model)
                response_request_ids.append(request_id)
                response_extra = getattr(response, "model_extra", None) or {}
                service_tier = getattr(response, "service_tier", None) or response_extra.get("service_tier")
                if self.config.openrouter_flex and service_tier != "flex":
                    raise AgentExecutionError(
                        f"OpenRouter service tier mismatch: {service_tier!r} "
                        f"request_id={request_id!r}"
                    )
                if (self.config.allowed_response_models
                        and response_model not in self.config.allowed_response_models):
                    raise AgentExecutionError(
                        "response model mismatch: "
                        f"requested={self.config.model!r} received={response_model!r} "
                        f"request_id={request_id!r}"
                    )
                choice = response.choices[0]
                message = choice.message
                stop_reason = choice.finish_reason
                reasoning_content = getattr(message, "reasoning_content", None)
                if reasoning_content is None:
                    reasoning_content = (getattr(message, "model_extra", None) or {}).get(
                        "reasoning_content"
                    )
                if self.config.record_response_diagnostics:
                    usage_payload = (
                        response.usage.model_dump(exclude_none=True)
                        if response.usage is not None else {}
                    )
                    gemini_usage = (
                        (usage_payload.get("billing_usage") or {})
                        .get("gemini_usage_metadata") or {}
                    )
                    response_diagnostics.append({
                        "turn": turns,
                        "model": response_model,
                        "request_id": request_id,
                        "service_tier": service_tier,
                        "provider": response_extra.get("provider"),
                        "finish_reason": stop_reason,
                        "content": message.content,
                        "reasoning_content_present": bool(reasoning_content),
                        "tool_call_count": len(message.tool_calls or []),
                        "usage_keys": sorted(usage_payload),
                        "billed_cost_usd": usage_payload.get("cost"),
                        "cached_tokens": (usage_payload.get("prompt_tokens_details") or {}).get("cached_tokens"),
                        "reasoning_tokens": (usage_payload.get("completion_tokens_details") or {}).get("reasoning_tokens"),
                        "gemini_thoughts_token_count": gemini_usage.get(
                            "thoughtsTokenCount"
                        ),
                        "gemini_total_token_count": gemini_usage.get(
                            "totalTokenCount"
                        ),
                    })
                usage = response.usage
                if usage is not None:
                    input_tokens += int(usage.prompt_tokens or 0)
                    output_tokens += int(usage.completion_tokens or 0)
                    cache_read_tokens += int(
                        getattr(usage.prompt_tokens_details, "cached_tokens", 0) or 0
                    )
                    reasoning_tokens += int(
                        getattr(usage.completion_tokens_details, "reasoning_tokens", 0)
                        or 0
                    )
                if message.content:
                    texts.append(message.content)
                tool_calls = list(message.tool_calls or [])
                assistant_message: dict[str, Any] = {
                    "role": "assistant", "content": message.content,
                }
                if self.config.preserve_reasoning_content and reasoning_content:
                    assistant_message["reasoning_content"] = reasoning_content
                if self.config.openrouter_flex:
                    reasoning_details = (getattr(message, "model_extra", None) or {}).get(
                        "reasoning_details"
                    )
                    if reasoning_details:
                        assistant_message["reasoning_details"] = reasoning_details
                if tool_calls:
                    assistant_message["tool_calls"] = [
                        call.model_dump(exclude_none=True) for call in tool_calls
                    ]
                messages.append(assistant_message)
                if progress_fn is not None:
                    progress_fn({
                        "event": "assistant_message",
                        "assistant_messages": turns,
                        "content_blocks": len(tool_calls) + int(bool(message.content)),
                    })

                if output_schema is not None:
                    structured_output = json.loads(message.content or "")
                    break
                if not tool_calls:
                    break
                for call in tool_calls:
                    arguments = json.loads(call.function.arguments or "{}")
                    definition = by_name.get(call.function.name)
                    if definition is None:
                        result = {
                            "content": [{"type": "text", "text": json.dumps({
                                "error": f"unknown tool: {call.function.name}"
                            })}],
                            "is_error": True,
                        }
                    else:
                        result = self._run_tool(definition, arguments)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call.id,
                        "name": call.function.name,
                        "content": _tool_result_text(result),
                    })
            else:
                raise AgentExecutionError(f"agent exceeded max_turns={max_turns}")
        except Exception as exc:
            if isinstance(exc, AgentExecutionError):
                raise
            message = str(exc).replace(self.config.api_key, "[redacted]")
            raise AgentExecutionError(message) from exc

        duration_ms = int((time.monotonic() - started) * 1000)
        if progress_fn is not None:
            progress_fn({
                "event": "result", "assistant_messages": turns,
                "num_turns": turns, "is_error": False,
            })
        return AgentResult(
            text="\n".join(texts).strip(),
            structured_output=structured_output,
            num_turns=turns,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=cache_read_tokens,
            anthropic_equivalent_cost_usd=None,
            duration_ms=duration_ms,
            duration_api_ms=duration_ms,
            stop_reason=stop_reason,
            session_id=str(uuid.uuid4()),
            reasoning_tokens=reasoning_tokens,
            response_models=tuple(response_models),
            response_request_ids=tuple(response_request_ids),
            response_diagnostics=tuple(response_diagnostics),
        )
