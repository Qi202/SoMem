"""Isolated synchronous adapter for the Claude Agent SDK."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    create_sdk_mcp_server,
    query as sdk_query,
)

from .checkpoint_storage import migrate_legacy_claude_config


class AgentExecutionError(RuntimeError):
    """Claude Code could not complete an agent trajectory."""


def _signature_failure_diagnostic(exc: Exception, *, thinking_disabled: bool = True) -> dict[str, Any] | None:
    """Retain response shape only; never store thinking/text or credentials."""
    if "Missing required field in assistant message: 'signature'" not in str(exc):
        return None
    data = getattr(exc, "data", None)
    message = data.get("message") if isinstance(data, dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    blocks = content if isinstance(content, list) else []
    unsigned = [block for block in blocks if isinstance(block, dict)
                and block.get("type") == "thinking" and "signature" not in block]
    nonempty = sum(bool(block.get("thinking")) for block in unsigned)
    return {
        "classification": (
            "unsigned_nonempty_thinking" if nonempty else
            "unsigned_empty_thinking" if unsigned else "missing_signature_unknown"
        ),
        "raw_shape_available": isinstance(content, list),
        "unsigned_thinking_blocks": len(unsigned),
        "nonempty_unsigned_thinking_blocks": nonempty,
        "thinking_disabled_requested": thinking_disabled,
        "action": "stop_preserve_checkpoint_no_signature_fabrication",
    }


@dataclass(frozen=True)
class ClaudeCodeConfig:
    base_url: str
    api_key: str
    model: str
    cli_path: str | None = None
    inactivity_timeout_s: float | None = 1_800.0
    max_buffer_size: int = 20 * 1024 * 1024
    workspace_mcp_path: str | None = None
    force_final_at_max_turns: bool = False
    session_checkpoint_dir: str | None = None
    max_transport_resumes: int = 0
    transport_retry_backoff_s: float = 120.0
    autocompact_tokens: int | None = None
    empty_thinking_compat_enabled: bool = False
    context_diagnostics_enabled: bool = False
    thinking_mode: str = "disabled"
    effort: str | None = None

    def __post_init__(self) -> None:
        if not self.base_url.strip():
            raise ValueError("base_url is required")
        if not self.api_key:
            raise ValueError("api_key is required")
        if not self.model.strip():
            raise ValueError("model is required")
        if self.cli_path is not None and not self.cli_path.strip():
            raise ValueError("cli_path must not be empty")
        if (
            self.inactivity_timeout_s is not None
            and self.inactivity_timeout_s <= 0
        ):
            raise ValueError("inactivity_timeout_s must be positive")
        if self.max_buffer_size < 1:
            raise ValueError("max_buffer_size must be positive")
        if self.max_transport_resumes < 0:
            raise ValueError("max_transport_resumes must be nonnegative")
        if self.transport_retry_backoff_s < 0:
            raise ValueError("transport_retry_backoff_s must be nonnegative")
        if self.autocompact_tokens is not None and not (
            100_000 <= self.autocompact_tokens <= 1_000_000
        ):
            raise ValueError("autocompact_tokens must be between 100000 and 1000000")
        if self.max_transport_resumes and not self.session_checkpoint_dir:
            raise ValueError(
                "transport resume requires a durable session checkpoint directory"
            )
        if self.thinking_mode not in {"disabled", "adaptive"}:
            raise ValueError("thinking_mode must be disabled or adaptive")
        if self.effort is not None and self.effort not in {"low", "medium", "high", "xhigh", "max"}:
            raise ValueError("unsupported Claude effort")


@dataclass(frozen=True)
class AgentResult:
    text: str
    structured_output: Any
    num_turns: int
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int
    # Priced by the SDK against Anthropic's own rate table. When the run is
    # routed elsewhere via ANTHROPIC_BASE_URL this is not the amount billed;
    # it is only "what this token volume would cost on Anthropic".
    anthropic_equivalent_cost_usd: float | None
    duration_ms: int
    duration_api_ms: int
    stop_reason: str | None
    session_id: str
    reasoning_tokens: int = 0
    forced_finalized: bool = False
    response_models: tuple[str, ...] = ()
    response_request_ids: tuple[str, ...] = ()
    response_diagnostics: tuple[dict[str, Any], ...] = ()


def _result_from_message(
    final: ResultMessage,
    texts: list[str],
    *,
    initial_final: ResultMessage | None = None,
    response_models: tuple[str, ...] = (),
) -> AgentResult:
    """Retain usage even when Claude Code returns a terminal error result."""
    usage = final.usage or {}
    initial_usage = initial_final.usage or {} if initial_final else {}
    return AgentResult(
        text=(final.result or "\n".join(texts)).strip(),
        structured_output=final.structured_output,
        num_turns=int(final.num_turns) + int(
            initial_final.num_turns if initial_final else 0
        ),
        input_tokens=int(usage.get("input_tokens", 0) or 0)
        + int(initial_usage.get("input_tokens", 0) or 0),
        output_tokens=int(usage.get("output_tokens", 0) or 0)
        + int(initial_usage.get("output_tokens", 0) or 0),
        cache_creation_input_tokens=int(
            usage.get("cache_creation_input_tokens", 0) or 0
        ) + int(initial_usage.get("cache_creation_input_tokens", 0) or 0),
        cache_read_input_tokens=int(
            usage.get("cache_read_input_tokens", 0) or 0
        ) + int(initial_usage.get("cache_read_input_tokens", 0) or 0),
        anthropic_equivalent_cost_usd=final.total_cost_usd,
        duration_ms=int(final.duration_ms) + int(
            initial_final.duration_ms if initial_final else 0
        ),
        duration_api_ms=int(final.duration_api_ms) + int(
            initial_final.duration_api_ms if initial_final else 0
        ),
        stop_reason=final.stop_reason,
        session_id=final.session_id,
        reasoning_tokens=int(
            usage.get("reasoning_tokens", 0)
            or (usage.get("completion_tokens_details") or {}).get(
                "reasoning_tokens", 0
            )
            or 0
        ) + int(
            initial_usage.get("reasoning_tokens", 0)
            or (initial_usage.get("completion_tokens_details") or {}).get(
                "reasoning_tokens", 0
            )
            or 0
        ),
        forced_finalized=initial_final is not None,
        response_models=response_models,
    )


QueryFunction = Callable[..., AsyncIterator[Any]]
ProgressFunction = Callable[[dict[str, Any]], None]


class ClaudeCodeAgent:
    """Run one non-persistent Claude Code process per trajectory."""

    def __init__(
        self,
        config: ClaudeCodeConfig,
        *,
        query_fn: QueryFunction | None = None,
    ) -> None:
        self.config = config
        self._query = query_fn or sdk_query

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
        progress_fn: ProgressFunction | None = None,
        builtin_tools_enabled: bool = True,
    ) -> AgentResult:
        if max_turns < 1:
            raise ValueError("max_turns must be positive")
        if max_budget_usd is not None and max_budget_usd <= 0:
            raise ValueError("max_budget_usd must be positive")
        # Resolve before building the coroutine: an exception raised while
        # evaluating these arguments would leave _run() created but never
        # awaited, which surfaces as a RuntimeWarning far from its cause.
        resolved_cwd = Path(cwd).resolve()
        checkpoint = self._load_or_create_checkpoint()
        resume_count = int(checkpoint.get("transport_resumes", 0))
        while True:
            session_id = str(checkpoint["session_id"])
            is_resume = checkpoint.get("status") in {"running", "transport_error"}
            checkpoint.update({
                "status": "running",
                "transport_resumes": resume_count,
                "updated_at": time.time(),
            })
            self._write_checkpoint(checkpoint)
            try:
                result = asyncio.run(self._run(
                    prompt=(
                        "Continue the interrupted task from this session. Do not "
                        "repeat completed searches or tool calls."
                        if is_resume else prompt
                    ),
                    system_prompt=system_prompt,
                    cwd=resolved_cwd,
                    tools=tools or [],
                    max_turns=max_turns,
                    max_budget_usd=max_budget_usd,
                    output_schema=output_schema,
                    progress_fn=progress_fn,
                    builtin_tools_enabled=builtin_tools_enabled,
                    session_id=session_id,
                    resume=is_resume,
                    checkpoint=checkpoint,
                ))
                checkpoint.update({
                    "status": "complete",
                    "session_id": result.session_id or session_id,
                    "updated_at": time.time(),
                })
                self._write_checkpoint(checkpoint)
                return result
            except AgentExecutionError as exc:
                checkpoint.update({
                    "status": "transport_error",
                    "last_error": self._redact(str(exc)),
                    "updated_at": time.time(),
                })
                diagnostic = getattr(exc, "somem_diagnostic", None)
                if diagnostic is not None:
                    checkpoint["last_error_diagnostic"] = diagnostic
                self._write_checkpoint(checkpoint)
                if (
                    not self._is_retryable_transport_error(str(exc))
                    or resume_count >= self.config.max_transport_resumes
                ):
                    raise
                resume_count += 1
                if progress_fn is not None:
                    progress_fn({
                        "event": "transport_resume",
                        "session_id": session_id,
                        "attempt": resume_count,
                        "backoff_seconds": self.config.transport_retry_backoff_s,
                    })
                if self.config.transport_retry_backoff_s:
                    time.sleep(self.config.transport_retry_backoff_s)

    def _checkpoint_path(self) -> Path | None:
        if self.config.session_checkpoint_dir is None:
            return None
        return Path(self.config.session_checkpoint_dir).resolve() / "state.json"

    def _load_or_create_checkpoint(self) -> dict[str, Any]:
        path = self._checkpoint_path()
        if path is not None and path.is_file():
            value = json.loads(path.read_text(encoding="utf-8"))
            if value.get("status") != "complete" and value.get("session_id"):
                return value
        return {
            "schema": "somem-qa-session-checkpoint/v1",
            "session_id": str(uuid.uuid4()),
            "status": "new",
            "transport_resumes": 0,
            "last_error": None,
            "updated_at": time.time(),
        }

    def _write_checkpoint(self, value: dict[str, Any]) -> None:
        path = self._checkpoint_path()
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(
            prefix=".state.", suffix=".tmp", dir=path.parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    def _redact(self, message: str) -> str:
        return message.replace(self.config.api_key, "[redacted]")

    @staticmethod
    def _is_retryable_transport_error(message: str) -> bool:
        lowered = message.lower()
        return any(marker in lowered for marker in (
            "failedtoopensocket", "failed to open socket", "connection reset",
            "certificate", "timed out", "timeout", "stream stalled",
            "tail hang", "tail-hang", "http 504", "http 524",
            "api status 504", "api status 524",
        ))

    async def _run(
        self,
        *,
        prompt: str,
        system_prompt: str,
        cwd: Path,
        tools: list[Any],
        max_turns: int,
        max_budget_usd: float | None,
        output_schema: dict[str, Any] | None,
        progress_fn: ProgressFunction | None,
        builtin_tools_enabled: bool,
        session_id: str,
        resume: bool,
        checkpoint: dict[str, Any],
    ) -> AgentResult:
        if not cwd.is_dir():
            raise ValueError(f"agent working directory does not exist: {cwd}")
        server_name = (
            "somem"
            if self.config.workspace_mcp_path is not None
            else "agent_memory"
        )
        mcp_servers = {}
        # The workspace is a scratch directory, so the built-in file tools are
        # safe here and are what the model is trained to reach for. Editing
        # through them beats scripting the same change in a shell heredoc.
        builtin_tools = (
            ["Read", "Edit", "Write", "Grep", "Glob"]
            if builtin_tools_enabled else []
        )
        allowed_tools = list(builtin_tools)
        if tools:
            # The caller's tools carry repair-only capabilities and audit hooks.
            # A configured standalone server must not hide those callbacks.
            mcp_servers[server_name] = create_sdk_mcp_server(
                server_name, tools=tools
            )
            allowed_tools += [
                f"mcp__{server_name}__{definition.name}"
                for definition in tools
            ]
        elif self.config.workspace_mcp_path is not None:
            server_path = str(Path(self.config.workspace_mcp_path).resolve())
            python_path = sys.executable
            mcp_servers[server_name] = {
                "type": "stdio",
                "command": python_path,
                "args": [server_path, "--workspace", str(cwd)],
            }
            allowed_tools += [
                f"mcp__{server_name}__{name}"
                for name in ("Read", "Write", "Edit", "Glob", "Grep", "Move", "Remove")
            ]

        hostname = (urlparse(self.config.base_url).hostname or "").lower()
        # Meta API's Claude Code compatibility endpoint authenticates via
        # ANTHROPIC_AUTH_TOKEN even when it serves non-Claude models such as
        # DeepSeek.  Other gateways used by this project retain x-api-key.
        uses_auth_token = hostname in {
            "meta-api.vip",
            "cn.meta-api.vip",
            "llm-all.cf-cdn.site",
        }

        if self.config.session_checkpoint_dir is None:
            config_context = tempfile.TemporaryDirectory(
                prefix="agent-memory-claude-config-"
            )
        else:
            persistent_root = migrate_legacy_claude_config(
                self.config.session_checkpoint_dir
            )
            persistent_root.mkdir(parents=True, exist_ok=True)
            config_context = nullcontext(str(persistent_root))
        with config_context as config_root:
            response_models: list[str] = []
            options = ClaudeAgentOptions(
                # `tools` is what puts schemas on the wire; `allowed_tools` only
                # filters what may run. Leaving this empty left weaker models
                # with the MCP shell as their sole visible tool, so they wrote
                # tool names into shell commands instead of calling the tools.
                tools=builtin_tools,
                allowed_tools=allowed_tools,
                system_prompt=system_prompt,
                mcp_servers=mcp_servers,
                permission_mode="dontAsk",
                model=self.config.model,
                session_id=None if resume else session_id,
                resume=session_id if resume else None,
                thinking={"type": self.config.thinking_mode},
                effort=self.config.effort,
                include_partial_messages=self.config.context_diagnostics_enabled,
                max_turns=max_turns,
                max_budget_usd=max_budget_usd,
                max_buffer_size=self.config.max_buffer_size,
                cwd=cwd,
                cli_path=self.config.cli_path,
                setting_sources=[],
                env={
                    "ANTHROPIC_BASE_URL": self.config.base_url,
                    "ANTHROPIC_API_KEY": (
                        "" if uses_auth_token else self.config.api_key
                    ),
                    "ANTHROPIC_AUTH_TOKEN": (
                        self.config.api_key if uses_auth_token else ""
                    ),
                    "CLAUDE_CONFIG_DIR": config_root,
                },
                extra_args={
                    "bare": None,
                    "strict-mcp-config": None,
                    **(
                        {"autocompact": str(self.config.autocompact_tokens)}
                        if self.config.autocompact_tokens is not None
                        else {}
                    ),
                    **(
                        {}
                        if self.config.force_final_at_max_turns
                        else {"no-session-persistence": None}
                    ),
                },
                output_format=(
                    {"type": "json_schema", "schema": output_schema}
                    if output_schema is not None
                    else None
                ),
            )
            texts: list[str] = []
            final: ResultMessage | None = None
            assistant_messages = 0
            try:
                query_options: dict[str, Any] = {}
                if self.config.empty_thinking_compat_enabled or self.config.context_diagnostics_enabled:
                    from .empty_thinking_compat import EmptyThinkingCompatTransport
                    query_options["transport"] = EmptyThinkingCompatTransport(
                        prompt=prompt, options=options, on_compat=progress_fn,
                        on_context=progress_fn if self.config.context_diagnostics_enabled else None,
                        filter_empty=self.config.empty_thinking_compat_enabled,
                    )
                messages = self._query(
                    prompt=prompt, options=options, **query_options
                ).__aiter__()
                while True:
                    try:
                        next_message = messages.__anext__()
                        if self.config.inactivity_timeout_s is None:
                            message = await next_message
                        else:
                            # Keep generator entry and aclose in this task:
                            # the SDK owns task-local AnyIO cleanup scopes.
                            async with asyncio.timeout(self.config.inactivity_timeout_s):
                                message = await next_message
                    except StopAsyncIteration:
                        break
                    except TimeoutError as exc:
                        if progress_fn is not None:
                            progress_fn({
                                "event": "sdk_stream_timeout",
                                "assistant_messages": assistant_messages,
                                "timeout_seconds": (
                                    self.config.inactivity_timeout_s
                                ),
                                "classification": (
                                    "tail_hang_after_assistant_activity"
                                    if assistant_messages
                                    else "no_sdk_activity"
                                ),
                            })
                        raise AgentExecutionError(
                            "Claude Code SDK stream stalled for "
                            f"{self.config.inactivity_timeout_s:g} seconds "
                            + (
                                "after assistant activity without a final "
                                "Result message"
                                if assistant_messages
                                else "before any assistant activity"
                            )
                        ) from exc
                    if isinstance(message, AssistantMessage):
                        assistant_messages += 1
                        if message.model and message.model not in response_models:
                            response_models.append(message.model)
                        if message.session_id:
                            checkpoint.update({
                                "session_id": message.session_id,
                                "assistant_messages": assistant_messages,
                                "updated_at": time.time(),
                            })
                            self._write_checkpoint(checkpoint)
                        texts.extend(
                            block.text
                            for block in message.content
                            if isinstance(block, TextBlock)
                        )
                        if progress_fn is not None:
                            progress_fn({
                                "event": "assistant_message",
                                "assistant_messages": assistant_messages,
                                "content_blocks": len(message.content),
                            })
                    elif isinstance(message, ResultMessage):
                        final = message
                        if progress_fn is not None:
                            progress_fn({
                                "event": "result",
                                "assistant_messages": assistant_messages,
                                "num_turns": int(message.num_turns),
                                "is_error": bool(message.is_error),
                            })
                        # ResultMessage is terminal. Some gateway/SDK streams
                        # remain open afterwards; do not wait another inactivity
                        # window before validating already-completed edits.
                        break
            except Exception as exc:
                if final is None:
                    message = str(exc).replace(self.config.api_key, "[redacted]")
                    error = AgentExecutionError(message)
                    diagnostic = _signature_failure_diagnostic(
                        exc, thinking_disabled=self.config.thinking_mode == "disabled"
                    )
                    if diagnostic is not None:
                        setattr(error, "somem_diagnostic", diagnostic)
                        if progress_fn is not None:
                            progress_fn({"event": "signature_protocol_failure",
                                         **diagnostic})
                    raise error from exc
            finally:
                # A timeout cancels the pending ``__anext__`` call, but not all
                # SDK iterator implementations immediately tear down their CLI
                # subprocess. Explicitly close it so the abandoned process
                # cannot keep retrying the local proxy in the background.
                close = getattr(locals().get("messages"), "aclose", None)
                if close is not None:
                    try:
                        await close()
                    except Exception:  # noqa: BLE001
                        pass

            if final is None:
                raise AgentExecutionError(
                    "Claude Code ended without a result message"
                )
            initial_final: ResultMessage | None = None
            if final.is_error:
                details = "; ".join(final.errors or []) or (
                    final.result or final.subtype
                )
                if getattr(final, "api_error_status", None) is not None:
                    details += f"; API status {final.api_error_status}"
                details = details.replace(self.config.api_key, "[redacted]")
                if not (
                    self.config.force_final_at_max_turns
                    and "maximum number of turns" in details.lower()
                    and final.session_id
                ):
                    error = AgentExecutionError(details)
                    setattr(
                        error,
                        "somem_result",
                        _result_from_message(final, texts, response_models=tuple(response_models)),
                    )
                    raise error
                initial_final = final
                forced_options = ClaudeAgentOptions(
                    tools=[],
                    allowed_tools=[],
                    disallowed_tools=allowed_tools,
                    system_prompt=(
                        "Give the best concise answer from evidence already present "
                        "in this conversation. Do not use tools, search, inspect files, "
                        "or request more information. Prefer the most likely grounded "
                        "answer even if evidence is incomplete. Return only "
                        "<answer>...</answer>."
                    ),
                    mcp_servers={},
                    permission_mode="dontAsk",
                    model=self.config.model,
                    thinking={"type": self.config.thinking_mode},
                    effort=self.config.effort,
                    resume=final.session_id,
                    max_turns=2,
                    max_buffer_size=self.config.max_buffer_size,
                    cwd=cwd,
                    cli_path=self.config.cli_path,
                    setting_sources=[],
                    env=options.env,
                    extra_args={"bare": None, "strict-mcp-config": None},
                    output_format=(
                        {"type": "json_schema", "schema": output_schema}
                        if output_schema is not None
                        else None
                    ),
                )
                forced_final: ResultMessage | None = None
                forced_texts: list[str] = []
                forced_messages = self._query(
                    prompt=(
                        "Stop retrieving now. Based only on information already "
                        "collected in this session, answer the original question."
                    ),
                    options=forced_options,
                ).__aiter__()
                try:
                    while True:
                        try:
                            next_message = forced_messages.__anext__()
                            if self.config.inactivity_timeout_s is None:
                                message = await next_message
                            else:
                                async with asyncio.timeout(self.config.inactivity_timeout_s):
                                    message = await next_message
                        except StopAsyncIteration:
                            break
                        if isinstance(message, AssistantMessage):
                            if message.model and message.model not in response_models:
                                response_models.append(message.model)
                            forced_texts.extend(
                                block.text
                                for block in message.content
                                if isinstance(block, TextBlock)
                            )
                        elif isinstance(message, ResultMessage):
                            forced_final = message
                            break
                finally:
                    close = getattr(forced_messages, "aclose", None)
                    if close is not None:
                        await close()
                if forced_final is None or forced_final.is_error:
                    raise AgentExecutionError(
                        "Gemini forced finalization did not produce a result"
                    )
                final = forced_final
                texts = forced_texts
                if progress_fn is not None:
                    progress_fn({
                        "event": "forced_finalization",
                        "at_turn": max_turns,
                        "session_id": final.session_id,
                    })
            return _result_from_message(
                final,
                texts,
                initial_final=initial_final,
                response_models=tuple(response_models),
            )
