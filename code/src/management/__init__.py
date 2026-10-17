"""SoMem memory-writing API, with optional agent adapters loaded on demand."""

from importlib import import_module
from typing import Any

_EXPORTS = {
    "manage_memory": "api",
    "organize_topics": "api",
    "write_session": "api",
    "write_sessions": "api",
    "MemoryConfig": "config",
    "CoreCapacityError": "errors",
    "_run_agent": "agent",
    "render_conversation": "agent",
    "CORE_CAPACITY_REPAIR_TASK": "prompts",
    "LOCAL_MANAGER_TASK": "prompts",
    "MANAGER_TASK": "prompts",
    "SYSTEM_PROMPT": "prompts",
    "TOOLS": "prompts",
    "VERIFICATION_PROBE_TASK": "prompts",
    "VERIFICATION_REPAIR_TASK": "prompts",
    "VERIFICATION_RETRIEVAL_TASK": "prompts",
    "WRITER_BATCH_TASK": "prompts",
    "WRITER_TASK": "prompts",
    "verify_session": "verification",
    "MemoryWorkspace": "workspace",
}

__all__ = [
    "LOCAL_MANAGER_TASK",
    "MANAGER_TASK",
    "MemoryConfig",
    "CoreCapacityError",
    "CORE_CAPACITY_REPAIR_TASK",
    "MemoryWorkspace",
    "SYSTEM_PROMPT",
    "TOOLS",
    "VERIFICATION_PROBE_TASK",
    "VERIFICATION_REPAIR_TASK",
    "VERIFICATION_RETRIEVAL_TASK",
    "WRITER_BATCH_TASK",
    "WRITER_TASK",
    "manage_memory",
    "organize_topics",
    "verify_session",
    "write_session",
    "write_sessions",
]


def __getattr__(name: str) -> Any:
    try:
        module_name = _EXPORTS[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    value = getattr(import_module(f".{module_name}", __name__), name)
    globals()[name] = value
    return value
