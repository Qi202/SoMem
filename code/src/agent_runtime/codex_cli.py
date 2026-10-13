"""Synchronous adapter for the official Codex CLI."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .claude_code import AgentExecutionError, AgentResult
from .source_handles import canonicalize_workspace


ProgressFunction = Callable[[dict[str, Any]], None]


@dataclass(frozen=True)
class CodexCliConfig:
    model: str
    cli_path: str = "codex"
    reasoning_effort: str = "none"
    inactivity_timeout_s: float | None = 1_800.0
    event_log_path: str | None = None
    source_handle_mcp_path: str | None = None
    workspace_mcp_path: str | None = None
    retrieval_mcp_path: str | None = None
    retrieval_trace_dir: str | None = None
    retrieval_evidence_ledger_enabled: bool = True
    provider_name: str | None = None
    base_url: str | None = None
    api_key: str | None = None


class CodexCliAgent:
    """Run one ephemeral Codex CLI process per SoMem trajectory."""

    def __init__(self, config: CodexCliConfig) -> None:
        self.config = config
        self._invocation = 0
        self.last_retrieval_trace_path: Path | None = None

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
        del max_turns, max_budget_usd
        resolved_cwd = Path(cwd).resolve()
        if not resolved_cwd.is_dir():
            raise ValueError(
                f"agent working directory does not exist: {resolved_cwd}"
            )
        self._invocation += 1
        effective_system_prompt = system_prompt
        command = [
            self.config.cli_path,
            "exec",
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
            "--skip-git-repo-check",
            "--sandbox",
            "workspace-write",
            "--model",
            self.config.model,
            "--json",
            "--cd",
            str(resolved_cwd),
            "--config",
            f"developer_instructions={json.dumps(effective_system_prompt)}",
            "--config",
            f"model_reasoning_effort={json.dumps(self.config.reasoning_effort)}",
        ]
        child_env = None
        if self.config.provider_name is not None:
            if not self.config.base_url or not self.config.api_key:
                raise ValueError(
                    "base_url and api_key are required for a custom provider"
                )
            provider = self.config.provider_name
            command.extend([
                "--config", f"model_provider={json.dumps(provider)}",
                "--config",
                f"model_providers.{provider}.name={json.dumps(provider)}",
                "--config",
                f"model_providers.{provider}.base_url={json.dumps(self.config.base_url)}",
                "--config",
                f"model_providers.{provider}.env_key=\"SOMEM_CODEX_PROVIDER_KEY\"",
                "--config",
                f"model_providers.{provider}.wire_api=\"responses\"",
            ])
            child_env = dict(os.environ)
            child_env["SOMEM_CODEX_PROVIDER_KEY"] = self.config.api_key
        # A retrieval MCP must be the sole query tool surface. Otherwise Codex
        # can bypass the Evidence Ledger through its built-in shell.
        retrieval_only = bool(tools and self.config.retrieval_mcp_path is not None)
        if not builtin_tools_enabled or retrieval_only:
            command.extend([
                "--disable", "shell_tool",
                "--disable", "unified_exec",
                "--disable", "code_mode",
            ])
        if self.config.workspace_mcp_path is not None:
            server_path = str(Path(self.config.workspace_mcp_path).resolve())
            python_path = sys.executable
            enabled = ["Read", "Write", "Edit", "Glob", "Grep", "Move", "Remove"]
            workspace_args = [server_path, "--workspace", str(resolved_cwd)]
            for definition in tools or []:
                if getattr(definition, "name", "") == "ReadCommitted":
                    root = (getattr(definition, "annotations", None) or {}).get(
                        "somem_committed_root"
                    )
                    if root:
                        workspace_args.extend(["--committed-workspace", str(root)])
                        enabled.append("ReadCommitted")
            command.extend([
                "--config",
                f"mcp_servers.somem.command={json.dumps(python_path)}",
                "--config",
                "mcp_servers.somem.args=" + json.dumps(workspace_args),
                "--config", "mcp_servers.somem.required=true",
                "--config", "mcp_servers.somem.enabled_tools=" + json.dumps(enabled),
                "--config",
                "mcp_servers.somem.default_tools_approval_mode=\"approve\"",
            ])
        if self.config.source_handle_mcp_path is not None:
            server_path = str(Path(self.config.source_handle_mcp_path).resolve())
            python_path = sys.executable
            command.extend([
                "--config",
                f"mcp_servers.source_handles.command={json.dumps(python_path)}",
                "--config",
                "mcp_servers.source_handles.args=" + json.dumps([
                    server_path, "--workspace", str(resolved_cwd),
                ]),
                "--config",
                "mcp_servers.source_handles.required=true",
                "--config",
                "mcp_servers.source_handles.enabled_tools=[\"canonicalize_source_handles\"]",
                "--config",
                "mcp_servers.source_handles.default_tools_approval_mode=\"approve\"",
            ])
        if tools and self.config.retrieval_mcp_path is not None:
            if self.config.retrieval_trace_dir is None:
                raise ValueError("retrieval_trace_dir is required for retrieval MCP")
            trace_dir = Path(self.config.retrieval_trace_dir).resolve()
            trace_dir.mkdir(parents=True, exist_ok=True)
            trace_path = trace_dir / f"retrieval-tools.{self._invocation:06d}.jsonl"
            self.last_retrieval_trace_path = trace_path
            server_path = str(Path(self.config.retrieval_mcp_path).resolve())
            python_path = sys.executable
            enabled = [getattr(definition, "name", "") for definition in tools]
            command.extend([
                "--config", f"mcp_servers.agent_memory.command={json.dumps(python_path)}",
                "--config", "mcp_servers.agent_memory.args=" + json.dumps([
                    server_path,
                    "--memory", str(resolved_cwd),
                    "--trace", str(trace_path),
                    "--model", self.config.model,
                    "--evidence-ledger",
                    "true" if self.config.retrieval_evidence_ledger_enabled else "false",
                ]),
                "--config", "mcp_servers.agent_memory.required=true",
                "--config", "mcp_servers.agent_memory.enabled_tools=" + json.dumps(enabled),
                "--config", "mcp_servers.agent_memory.default_tools_approval_mode=\"approve\"",
            ])
        if output_schema is not None:
            raise NotImplementedError(
                "Codex CLI structured output is not needed by the Writer smoke"
            )
        command.extend(["-"])
        started = time.monotonic()
        try:
            completed = subprocess.run(
                command,
                input=prompt,
                text=True,
                capture_output=True,
                timeout=self.config.inactivity_timeout_s,
                check=False,
                env=child_env,
            )
        except subprocess.TimeoutExpired as exc:
            raise AgentExecutionError(
                "Codex CLI exceeded the trajectory timeout"
            ) from exc

        if self.config.event_log_path is not None:
            configured = Path(self.config.event_log_path)
            log_path = configured.with_name(
                f"{configured.stem}.{self._invocation:03d}{configured.suffix}"
            )
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(completed.stdout, encoding="utf-8")
            log_path.with_suffix(log_path.suffix + ".stderr").write_text(
                completed.stderr, encoding="utf-8"
            )

        usage: dict[str, int] = {}
        text = ""
        session_id = ""
        turns = 0
        events: list[dict[str, Any]] = []
        for raw_line in completed.stdout.splitlines():
            try:
                event = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            events.append(event)
            event_type = str(event.get("type", ""))
            if event_type == "thread.started":
                session_id = str(event.get("thread_id", ""))
            elif event_type == "turn.started":
                turns += 1
            elif event_type == "item.completed":
                item = event.get("item") or {}
                if item.get("type") == "agent_message":
                    text = str(item.get("text", ""))
                if progress_fn is not None:
                    progress_fn({
                        "event": "codex_item_completed",
                        "item_type": item.get("type"),
                    })
            elif event_type == "turn.completed":
                usage = event.get("usage") or {}

        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout[-2_000:]
            raise AgentExecutionError(
                f"Codex CLI exited with {completed.returncode}: {detail}"
            )
        if self.config.source_handle_mcp_path is not None:
            source_audit = canonicalize_workspace(resolved_cwd)
            if self.config.event_log_path is not None:
                configured = Path(self.config.event_log_path)
                source_audit_path = configured.with_name(
                    f"source-handle-audit.{self._invocation:03d}.json"
                )
                source_audit_path.write_text(
                    json.dumps(source_audit, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            if progress_fn is not None:
                progress_fn({
                    "event": "source_handle_guard",
                    "status": source_audit["status"],
                    "corrections": len(source_audit["corrections"]),
                    "unresolved": len(source_audit["unresolved"]),
                })
        duration_ms = round((time.monotonic() - started) * 1_000)
        return AgentResult(
            text=text,
            structured_output=None,
            num_turns=max(turns, 1),
            input_tokens=int(usage.get("input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
            cache_creation_input_tokens=int(
                usage.get("cache_write_input_tokens", 0)
            ),
            cache_read_input_tokens=int(usage.get("cached_input_tokens", 0)),
            anthropic_equivalent_cost_usd=None,
            duration_ms=duration_ms,
            duration_api_ms=duration_ms,
            stop_reason="completed",
            session_id=session_id,
            reasoning_tokens=int(usage.get("reasoning_output_tokens", 0)),
        )
