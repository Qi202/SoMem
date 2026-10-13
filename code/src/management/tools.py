"""In-process form of the provider-neutral memory workspace tools."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from claude_agent_sdk import tool

from .workspace import MemoryWorkspace
from .block_views import core_token_count
from .workspace_tool_backend import WorkspaceToolBackend


class WriterStagnationError(RuntimeError):
    """Stop a trajectory that repeatedly fails against unchanged file contents."""


class WriterContextBoundary(WriterStagnationError):
    """Rotate context after a bounded attempt; progress decides its budget."""


def mutation_error_kind(payload: dict[str, Any]) -> str:
    recovery = payload.get("recovery", {})
    if recovery.get("reason"):
        return str(recovery["reason"])
    message = str(payload.get("error", ""))
    for marker, kind in (("remove existing memory block", "id_removal"),
                         ("old_string was not found", "missing_anchor"),
                         ("old_string is not unique", "ambiguous_anchor"),
                         ("fresh Read is required", "needs_read"),
                         ("identical to current text", "no_change")):
        if marker in message:
            return kind
    return "other_mutation_error"


def check_writer_stagnation(
    workspace: MemoryWorkspace, audit: list[dict[str, Any]],
) -> None:
    reason = getattr(workspace, "writer_stagnation_reason", None)
    if reason:
        error = WriterStagnationError(reason)
        error.somem_audit = list(audit)
        raise error


@dataclass
class MutationWatch:
    # Reads and errors on other files must not disguise an unchanged target.
    failures: dict[tuple[str, str], tuple[str, int]] = field(default_factory=dict)
    limit: int = 8

    def observe(self, path: str, before: str, after: str, kind: str = "mutation_error") -> str | None:
        if before != after:
            self.failures = {k: v for k, v in self.failures.items() if k[0] != path}
            return None
        key = (path, kind)
        previous, count = self.failures.get(key, (before, 0))
        count = count + 1 if previous == before else 1
        self.failures[key] = (before, count)
        if self.limit and count >= self.limit:
            return (f"writer stopped: {count} failed or no-op mutations ({kind}) of "
                    f"unchanged {path}; preserve checkpoint and inspect tool errors")
        return None


@dataclass
class CoreRepairState:
    """Per-trajectory capacity-check state exposed only to repair agents."""

    limit: int
    target: int
    max_checks: int
    stagnation_limit: int
    checks: list[int] = field(default_factory=list)
    non_decreasing_checks: int = 0

    @property
    def stagnated(self) -> bool:
        return self.non_decreasing_checks >= self.stagnation_limit

    def record(self, token_count: int) -> None:
        if self.checks:
            if token_count >= self.checks[-1]:
                self.non_decreasing_checks += 1
            else:
                self.non_decreasing_checks = 0
        self.checks.append(token_count)


def management_tools(
    workspace: MemoryWorkspace,
    audit: list[dict[str, Any]],
    live_audit_path: str | Path | None = None,
    core_repair_state: CoreRepairState | None = None,
    repair_context: bool = False,
) -> list[Any]:
    output_cap = workspace.config.writer_tool_output_max_chars
    backend = WorkspaceToolBackend(
        workspace.stage_dir, max_output_chars=output_cap,
        unchecked=workspace.config.unchecked_writer_commits,
    )

    watch = getattr(workspace, "mutation_watch", None)
    if watch is None:
        watch = workspace.mutation_watch = MutationWatch(limit=workspace.config.writer_stagnation_limit)

    def fingerprint(path: str) -> str:
        target = backend._path(path)
        if not target.is_file():
            return "missing"
        return hashlib.sha256(target.read_bytes()).hexdigest()

    def record_tool(
        name: str, arguments: dict[str, Any], operation: Any
    ) -> dict[str, Any]:
        record: dict[str, Any] = {
            "round": len(audit), "tool": name, "arguments": arguments,
        }
        tracked_path = None
        before = None
        try:
            check_writer_stagnation(workspace, audit)
            if name in {"Edit", "Write"}:
                tracked_path = backend._path(str(arguments["path"])).relative_to(
                    backend.root
                ).as_posix()
                before = fingerprint(tracked_path)
            payload = operation()
            record.update({"status": "ok", "output": payload})
            is_error = False
        except Exception as exc:
            payload = {"error": f"{type(exc).__name__}: {exc}"}
            if getattr(exc, "recovery", None):
                payload["recovery"] = exc.recovery
            record.update({"status": "error", "output": payload})
            is_error = True
        if (tracked_path is not None and before is not None
                and payload.get("recovery", {}).get("retryable") is not False):
            reason = watch.observe(tracked_path, before, fingerprint(tracked_path),
                                   mutation_error_kind(payload) if is_error else "no_change")
            if reason:
                workspace.writer_stagnation_reason = reason
                payload = {"error": reason, "stopped": True}
                record.update({"status": "error", "output": payload})
                is_error = True
        audit.append(record)
        if live_audit_path is not None:
            path = Path(live_audit_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        return {
            "content": [{
                "type": "text",
                "text": json.dumps(payload, ensure_ascii=False),
            }],
            "is_error": is_error,
        }

    @tool("Read", "Read one UTF-8 file relative to the memory workspace." + (
        " Output is paginated; use next_char_offset as char_offset to continue. "
        "Read only the relevant range; char_limit cannot exceed the page cap."
        if output_cap is not None else ""
    ), {
        "type": "object", "properties": {"path": {"type": "string"}, **(
            {"char_offset": {"type": "integer", "minimum": 0},
             "char_limit": {"type": "integer", "minimum": 1}}
            if output_cap is not None else {}
        )},
        "required": ["path"], "additionalProperties": False,
    })
    async def read_file(arguments: dict[str, Any]) -> dict[str, Any]:
        return record_tool(
            "Read", arguments, lambda: backend.read(
                arguments["path"], char_offset=arguments.get("char_offset", 0),
                char_limit=arguments.get("char_limit"),
            )
        )

    @tool("Write", "Create or replace the ENTIRE core.md or topics/ file; this does "
          "not append. Include all retained prose AND its footnote definitions. "
          "To add a footnote to an existing file, use Edit instead of overwriting "
          "the file with only footnotes. Each Topic fact paragraph must end with "
          "its evidence citation, e.g. Fact.[^e1], with a matching definition "
          "[^e1]: Time: `2023`; Sources: exact archived message handle. "
          "Do not invent block IDs; preserve IDs already present on their original facts. "
          "After a rejected mutation, Read the target before retrying.", {
        "type": "object", "properties": {
            "path": {"type": "string"}, "content": {"type": "string"},
        }, "required": ["path", "content"], "additionalProperties": False,
    })
    async def write_file(arguments: dict[str, Any]) -> dict[str, Any]:
        return record_tool("Write", arguments, lambda: backend.write(
            arguments["path"], arguments["content"]
        ))

    @tool("Edit", "Replace non-empty exact text in core.md or a file under topics/. "
          "Use text from a successful Read. A failed call changes nothing; Read the "
          "target again before retrying. Preserve existing IDs and footnote definitions. "
          "Never replace a bare citation label to insert prose or a definition.", {
        "type": "object", "properties": {
            "path": {"type": "string"},
            "old_string": {"type": "string"},
            "new_string": {"type": "string"},
            "replace_all": {"type": "boolean", "default": False},
        }, "required": ["path", "old_string", "new_string"],
        "additionalProperties": False,
    })
    async def edit_file(arguments: dict[str, Any]) -> dict[str, Any]:
        return record_tool("Edit", arguments, lambda: backend.edit(
            arguments["path"], arguments["old_string"], arguments["new_string"],
            bool(arguments.get("replace_all", False)),
        ))

    @tool("Glob", "List files matching a relative glob pattern.", {
        "type": "object", "properties": {"pattern": {"type": "string"}},
        "required": ["pattern"], "additionalProperties": False,
    })
    async def glob_files(arguments: dict[str, Any]) -> dict[str, Any]:
        return record_tool(
            "Glob", arguments, lambda: backend.glob(arguments["pattern"])
        )

    @tool("Grep", "Search UTF-8 workspace files with a regular expression." + (
        " Results are paginated; use next_match_offset as match_offset. Long "
        "matching lines are excerpts; Read at char_offset to inspect them."
        if output_cap is not None else ""
    ), {
        "type": "object", "properties": {
            "query": {"type": "string"},
            "glob": {"type": "string", "default": "**/*"},
            **({"match_offset": {"type": "integer", "minimum": 0}}
               if output_cap is not None else {}),
        }, "required": ["query"], "additionalProperties": False,
    })
    async def grep_files(arguments: dict[str, Any]) -> dict[str, Any]:
        return record_tool("Grep", arguments, lambda: backend.grep(
            arguments["query"], arguments.get("glob", "**/*"),
            match_offset=arguments.get("match_offset", 0),
        ))

    @tool("Move", "Move a path within topics/ or move core.md into topics/.", {
        "type": "object", "properties": {
            "source": {"type": "string"}, "destination": {"type": "string"},
        }, "required": ["source", "destination"], "additionalProperties": False,
    })
    async def move_path(arguments: dict[str, Any]) -> dict[str, Any]:
        return record_tool("Move", arguments, lambda: backend.move(
            arguments["source"], arguments["destination"]
        ))

    @tool("Remove", "Remove a file or directory under topics/.", {
        "type": "object", "properties": {"path": {"type": "string"}},
        "required": ["path"], "additionalProperties": False,
    })
    async def remove_path(arguments: dict[str, Any]) -> dict[str, Any]:
        return record_tool(
            "Remove", arguments, lambda: backend.remove(arguments["path"])
        )

    result = [
        read_file, write_file, edit_file, glob_files, grep_files,
        move_path, remove_path,
    ]
    if repair_context:
        committed = WorkspaceToolBackend(
            workspace.memory_dir, max_output_chars=output_cap or 12000,
        )

        @tool("ReadCommitted", "Read the last committed version of a memory file. "
              "Read-only and paginated; use next_char_offset to continue. "
              "Use this to recover original paragraphs, IDs and footnotes.", {
            "type": "object", "properties": {
                "path": {"type": "string"},
                "char_offset": {"type": "integer", "minimum": 0},
                "char_limit": {"type": "integer", "minimum": 1},
            }, "required": ["path"], "additionalProperties": False,
        }, annotations={"readOnlyHint": True,
                        "somem_committed_root": str(workspace.memory_dir)})
        async def read_committed(arguments: dict[str, Any]) -> dict[str, Any]:
            return record_tool("ReadCommitted", arguments, lambda: committed.read(
                arguments["path"], char_offset=arguments.get("char_offset", 0),
                char_limit=arguments.get("char_limit"),
            ))

        result.append(read_committed)
    if core_repair_state is None:
        return result

    @tool(
        "core_token_count",
        (
            "Count staged core.md with the Runtime's exact tokenizer during "
            "a Core-capacity repair. This tool is read-only. Call it after "
            "each Core edit and keep the final count at or below the target."
        ),
        {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    )
    async def count_core(_arguments: dict[str, Any]) -> dict[str, Any]:
        record: dict[str, Any] = {
            "round": len(audit),
            "tool": "core_token_count",
        }
        if len(core_repair_state.checks) >= core_repair_state.max_checks:
            payload = {
                "error": "core token check limit reached",
                "max_checks": core_repair_state.max_checks,
                "checks": list(core_repair_state.checks),
            }
            record.update({"status": "error", "output": payload})
            is_error = True
        else:
            count = core_token_count(workspace.stage_dir / "core.md")
            core_repair_state.record(count)
            payload = {
                "token_count": count,
                "hard_limit": core_repair_state.limit,
                "target": core_repair_state.target,
                "remaining_to_limit": core_repair_state.limit - count,
                "remaining_to_target": core_repair_state.target - count,
                "checks_used": len(core_repair_state.checks),
                "checks_remaining": (
                    core_repair_state.max_checks
                    - len(core_repair_state.checks)
                ),
                "stagnated": core_repair_state.stagnated,
            }
            record.update({"status": "ok", "output": payload})
            is_error = False
        audit.append(record)
        if live_audit_path is not None:
            path = Path(live_audit_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        return {
            "content": [{
                "type": "text",
                "text": json.dumps(payload, ensure_ascii=False),
            }],
            "is_error": is_error,
        }

    result.append(count_core)
    return result
