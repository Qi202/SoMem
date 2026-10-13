"""Public writing and maintenance operations."""

import hashlib
import json
from pathlib import Path
from typing import Any

from .config import MemoryConfig
from .read_policy import WRITER_READ_POLICY_VERSION
from .agent import _run_agent, render_conversation
from .prompts import (
    DEFAULT_CORE_ROUTING_GUIDANCE,
    LUNA_TOPIC_ROUTING_GUIDANCE,
    LOCAL_MANAGER_TASK,
    MANAGER_TASK,
    SYSTEM_PROMPT,
    TOOLS,
    WRITER_BATCH_TASK,
    WRITER_TASK,
)


def _apply_luna_topic_routing(task: str) -> str:
    if task.count(DEFAULT_CORE_ROUTING_GUIDANCE) != 1:
        raise RuntimeError("default Core routing guidance is not uniquely replaceable")
    return task.replace(
        DEFAULT_CORE_ROUTING_GUIDANCE,
        LUNA_TOPIC_ROUTING_GUIDANCE,
    )


def render_writer_task(
    sessions: list[dict[str, Any]], *, config: MemoryConfig | None = None,
) -> str:
    rendered = []
    for number, session in enumerate(sessions, start=1):
        rendered.append(
            f"## Session {number}\nObservation date: "
            f"{session['observation_date']}\n\n"
            f"{render_conversation(session['turns'], session['refs'])}"
        )
    task = WRITER_BATCH_TASK.format(sessions="\n\n".join(rendered))
    if config is not None and config.luna_topic_routing_protocol_enabled:
        task = _apply_luna_topic_routing(task)
    return task


def render_writer_input(
    sessions: list[dict[str, Any]], *, config: MemoryConfig | None = None,
) -> str:
    """Render all fixed and session-specific text sent to the Writer."""
    return f"{SYSTEM_PROMPT}\n\n{render_writer_task(sessions, config=config)}"


def writer_protocol_sha256(config: MemoryConfig | None = None) -> str:
    protocol = {
        "system": SYSTEM_PROMPT,
        "writer_batch": WRITER_BATCH_TASK,
        "tools": TOOLS[:1],
        "runtime": "provider-neutral-somem-mcp-v1",
        "contract": "topic-core-v3-runtime-ids",
    }
    if config is not None and config.luna_topic_routing_protocol_enabled:
        protocol["writer_batch"] = _apply_luna_topic_routing(
            WRITER_BATCH_TASK
        )
    if config is not None and config.writer_tool_output_max_chars is not None:
        protocol["writer_tool_output"] = {
            "version": WRITER_READ_POLICY_VERSION,
            "max_chars": config.writer_tool_output_max_chars,
        }
    payload = json.dumps(
        protocol,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def write_session(
    memory_dir: str | Path,
    *,
    agent: Any,
    observation_date: str,
    turns: list[tuple[str, str]],
    refs: list[str],
    usage_logger: Any | None = None,
    config: MemoryConfig | None = None,
) -> list[dict[str, Any]]:
    config = config or MemoryConfig()
    task = WRITER_TASK.format(
        observation_date=observation_date,
        conversation=render_conversation(turns, refs),
    )
    if config.luna_topic_routing_protocol_enabled:
        task = _apply_luna_topic_routing(task)
    return _run_agent(memory_dir, agent=agent, task=task,
                      source_sessions=[{
                          "observation_date": observation_date,
                          "turns": turns,
                          "refs": refs,
                      }],
                      usage_logger=usage_logger, config=config)


def write_sessions(
    memory_dir: str | Path,
    *,
    agent: Any,
    sessions: list[dict[str, Any]],
    usage_logger: Any | None = None,
    live_audit_path: str | Path | None = None,
    config: MemoryConfig | None = None,
) -> list[dict[str, Any]]:
    config = config or MemoryConfig()
    task = render_writer_task(sessions, config=config)
    return _run_agent(
        memory_dir,
        agent=agent,
        task=task,
        source_sessions=sessions,
        usage_logger=usage_logger,
        live_audit_path=live_audit_path,
        config=config,
    )


def manage_memory(
    memory_dir: str | Path,
    *,
    agent: Any,
    usage_logger: Any | None = None,
    config: MemoryConfig | None = None,
) -> list[dict[str, Any]]:
    config = config or MemoryConfig()
    return _run_agent(memory_dir, agent=agent, task=MANAGER_TASK,
                      usage_logger=usage_logger,
                      config=config,
                      allow_rejected_noop=(
                          config.luna_optional_maintenance_rejection_noop_enabled
                      ))


def organize_topics(
    memory_dir: str | Path,
    *,
    agent: Any,
    touched: set[str] | None = None,
    final: bool = False,
    usage_logger: Any | None = None,
    config: MemoryConfig | None = None,
) -> list[dict[str, Any]]:
    config = config or MemoryConfig()
    """Compatibility entry point for existing manager callers."""
    if final or touched is None:
        return manage_memory(
            memory_dir, agent=agent, usage_logger=usage_logger,
            config=config,
        )
    normalized = sorted({
        Path(path).as_posix()
        for path in touched
        if Path(path).as_posix().startswith("topics/")
    })
    if not normalized:
        return []
    return _run_agent(
        memory_dir,
        agent=agent,
        task=LOCAL_MANAGER_TASK.format(topic_paths="\n".join(normalized)),
        usage_logger=usage_logger,
        config=config,
        allow_rejected_noop=(
            config.luna_optional_maintenance_rejection_noop_enabled
        ),
    )
