"""Public SoMem retrieval API, with optional model runtimes loaded on demand."""

from importlib import import_module
from typing import Any

_EXPORTS = {
    "collect_answer": "agent",
    "QueryConfig": "config",
    "EvidenceEntry": "evidence_ledger",
    "EvidenceLedger": "evidence_ledger",
    "build_evidence_packet": "evidence_packet",
    "evidence_state": "evidence_packet",
    "build_evidence_gate": "evidence_gate",
    "ANSWER_PROMPT": "prompts",
    "PipelineConfig": "pipeline",
    "build_pipeline_context": "pipeline",
    "question_contract": "pipeline",
    "retrieve_pipeline": "pipeline",
    "supplement_pipeline_rows": "pipeline",
    "ReasoningLedger": "reasoning_ledger",
    "RetrievalPlan": "retrieval_plan",
    "Runtime": "runtime",
    "create_runtime": "runtime",
    "CONDITION_VIEWS": "schemas",
    "MEMORY_COMPONENTS": "schemas",
    "TOOL_DEFINITIONS": "schemas",
    "execute_workspace_bash": "shell",
    "normalize_workspace_command": "shell",
    "validate_read_only_command": "shell",
    "memory_files": "views",
    "read_memory_file": "views",
    "tools_for": "views",
}

__all__ = [
    "ANSWER_PROMPT",
    "CONDITION_VIEWS",
    "EvidenceEntry",
    "EvidenceLedger",
    "MEMORY_COMPONENTS",
    "PipelineConfig",
    "QueryConfig",
    "ReasoningLedger",
    "RetrievalPlan",
    "Runtime",
    "TOOL_DEFINITIONS",
    "collect_answer",
    "build_pipeline_context",
    "build_evidence_packet",
    "build_evidence_gate",
    "evidence_state",
    "create_runtime",
    "execute_workspace_bash",
    "memory_files",
    "normalize_workspace_command",
    "read_memory_file",
    "question_contract",
    "retrieve_pipeline",
    "supplement_pipeline_rows",
    "tools_for",
    "validate_read_only_command",
]


def __getattr__(name: str) -> Any:
    try:
        module_name = _EXPORTS[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    value = getattr(import_module(f".{module_name}", __name__), name)
    globals()[name] = value
    return value
