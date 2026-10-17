"""Public SoMem core API, loading model runtimes only when requested."""

from importlib import import_module
from typing import Any

_EXPORTS = {
    "BuildConfig": "build",
    "build_memory": "build",
    "MemoryConfig": "management",
    "MemoryWorkspace": "management",
    "QueryConfig": "retrieval",
    "collect_answer": "retrieval",
}

__all__ = [
    "BuildConfig",
    "MemoryConfig",
    "MemoryWorkspace",
    "QueryConfig",
    "build_memory",
    "collect_answer",
]


def __getattr__(name: str) -> Any:
    try:
        module_name = _EXPORTS[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    value = getattr(import_module(f".{module_name}", __name__), name)
    globals()[name] = value
    return value
