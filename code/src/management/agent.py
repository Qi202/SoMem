"""Memory Writer and Manager execution through Claude Code."""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..agent_runtime import ClaudeCodeAgent
from .config import MemoryConfig
from .errors import CoreCapacityError, CommitStateError
from .prompts import (
    CORE_CAPACITY_REPAIR_TASK,
    DANGLING_BLOCK_LINK_REPAIR_GUIDANCE,
    LUNA_CORE_CAPACITY_REPAIR_TASK,
    LUNA_CORE_STRUCTURAL_REPAIR_TASK,
    SYSTEM_PROMPT,
)
from .tools import CoreRepairState, management_tools, check_writer_stagnation
from .workspace import MemoryWorkspace
from .source_archive import SourceIntegrityError, assert_session_sources
from ..agent_runtime.source_handles import canonicalize_workspace, source_repair_context, staged_repair_context


@dataclass(frozen=True)
class CommitFailure:
    """A rejected staged commit with its structured underlying exception."""

    exception: Exception
    diagnostics: tuple[str, ...] = ()

    @property
    def message(self) -> str:
        return f"{type(self.exception).__name__}: {self.exception}"


def _failure_message(failure: CommitFailure | Exception | str) -> str:
    if isinstance(failure, CommitFailure):
        return failure.message
    if isinstance(failure, Exception):
        return f"{type(failure).__name__}: {failure}"
    return str(failure)


def _repair_diagnostic_prompt(workspace: MemoryWorkspace, failure: CommitFailure | Exception | str) -> str:
    """Keep every finding on disk; bound only the inline presentation."""
    message = _failure_message(failure)
    findings = (list(failure.diagnostics) if isinstance(failure, CommitFailure)
                and workspace.config.validation_diagnostic_aggregation_enabled else [])
    payload = {"error": message, "findings": findings,
               "coverage": "best_effort; deferred checks rerun after normalization"}
    path = workspace.stage_dir / "repair-diagnostics.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = message[:4000]
    for item in findings:
        summary += "\n" + item[:12000]
        if len(summary) >= 12000:
            break
    return (summary[:12000] + "\n\nFull, unabridged findings: repair-diagnostics.json. "
            "Read with char_offset and char_limit=6000 until next_char_offset is null. "
            "The inline summary may be truncated, NOT the checks or acceptance criteria. "
            "Pending/deferred checks are not content errors. Repair in place, preserve "
            "all supported facts, and never delete information merely to pass validation.\n")


def _core_capacity_error(
    failure: CommitFailure | Exception | str,
) -> CoreCapacityError | None:
    exception = (
        failure.exception if isinstance(failure, CommitFailure) else failure
    )
    return exception if isinstance(exception, CoreCapacityError) else None


def _misplaced_id_guidance(workspace: MemoryWorkspace) -> str:
    """Diagnose misplaced IDs without guessing ownership or changing content."""
    findings = []
    paths = [workspace.stage_dir / "core.md", *sorted((workspace.stage_dir / "topics").rglob("*.md"))]
    for path in paths:
        if not path.is_file():
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not re.match(r"^\s*\[\^[^\]]+\]:", line):
                continue
            ids = re.findall(r"(?<!\S)\^([0-9a-f]{8})(?=\s|$)", line)
            if ids:
                findings.append({"path": path.relative_to(workspace.stage_dir).as_posix(),
                                 "line": number, "misplaced_ids": ids})
    if not findings:
        return ""
    return (
        "\nPRIORITY STRUCTURAL DIAGNOSIS: block IDs occur inside footnote definitions. "
        "An ID can be present as text yet missing from its original fact: an ID on a "
        "Sources line does not identify a prose paragraph. A reported invalid source "
        "such as `. ^id` is misplaced syntax, not an archived message to search for. "
        "Read the staged file and ReadCommitted for the same path to establish each "
        "existing ID's original fact and evidence. In ONE Edit spanning the paragraph "
        "and definition, move that same ID to the end of its original fact paragraph, "
        "after its evidence citations, and remove its occurrence from the definition. "
        "Do not delete the ID first: the tool correctly rejects that intermediate state. "
        "Restore old citations on their original facts from ReadCommitted; do not "
        "replace old evidence with a citation supporting only a newly added fact. "
        "Keep a separate new fact in a separate paragraph without inventing an ID. "
        "Do not rename existing footnote labels. Keep only exact supported source "
        "handles/links in Sources; remove stray sentence punctuation outside links. "
        "If ownership cannot be established, inspect more committed context rather "
        "than assigning the ID to a nearby unrelated paragraph. Shape: "
        "`Original fact.[^old] ^existing-id` followed by a blank line and "
        "`[^old]: Time: `date`; Sources: exact-source`. "
        "These are placeholders, never literal replacement text. Findings: "
        + json.dumps(findings, ensure_ascii=False) + "\n"
    )


def _previous_repair_feedback(audit: list[dict[str, Any]]) -> str:
    last = next((i for i in range(len(audit) - 1, -1, -1)
                 if audit[i].get("tool") == "generic_repair"
                 and audit[i].get("status") == "rejected"), None)
    if last is None:
        return ""
    start = next((i + 1 for i in range(last - 1, -1, -1)
                  if audit[i].get("tool") == "generic_repair"), 0)
    errors: dict[str, int] = {}
    for row in audit[start:last]:
        if row.get("tool") not in {"Edit", "Write"} or row.get("status") != "error":
            continue
        output = row.get("output", {})
        message = str(output.get("error", "")) if isinstance(output, dict) else str(output)
        message = message.split(". This call changed nothing")[0][:400]
        errors[message] = errors.get(message, 0) + 1
    return (
        "\nPREVIOUS REPAIR DID NOT PASS: " + _failure_message(audit[last].get("error", ""))
        + "\nFailed mutation summary: " + json.dumps(errors, ensure_ascii=False)
        + "\nChange the repair action to address the diagnosed defect. Copy old_string "
        "exactly from a fresh Read, never use ellipses as placeholders, and ensure "
        "new_string actually differs. Cosmetic prose edits and renaming labels do "
        "not repair misplaced IDs. Do not repeat rejected deletions.\n"
    )


def _generic_repair_guidance(error: CommitFailure | Exception | str) -> str:
    message = _failure_message(error)
    if "undefined Topic footnotes (repair all):" in message:
        return (
            "\n\nThis is an aggregated undefined-Topic-footnote repair. "
            "Inspect EVERY listed citation at its exact file, line, and block "
            "in this trajectory. Restore an existing definition only when "
            "the committed memory or archived Source proves it; otherwise "
            "remove only the newly staged unsupported citation and dependent "
            "fact. Do not invent or renumber citations, sources, or block IDs, "
            "and do not stop after the first listed item."
        )
    if "duplicate footnote definitions (repair all):" in message:
        return (
            "\n\nThis is an aggregated duplicate-footnote repair. Inspect EVERY "
            "listed file and line in this trajectory. Keep one definition "
            "only when its exact evidence and citation are supported; remove "
            "only redundant copies. Preserve unrelated facts, source links, "
            "citations, and block IDs. Do not stop after the first ID."
        )
    if "unused footnote definitions (repair all):" in message:
        return (
            "\n\nThis is an aggregated unused-footnote repair. Inspect EVERY "
            "listed definition at its file and line. Search the staged Topic "
            "and Core content for its citation. Remove only truly unused "
            "definitions; restore an accidentally removed exact citation "
            "only after checking its supported fact and archived source. "
            "Do not invent or renumber citations or change unrelated IDs."
        )
    if "duplicate memory_ids (repair all):" in message:
        return (
            "\n\nThis is an aggregated duplicate-memory-ID repair. For EVERY "
            "listed ID, compare its first and duplicate file:line locations "
            "with committed memory. Preserve the ID on its original fact "
            "exactly once; remove only the duplicate assignment from staged "
            "content. The Runtime assigns new IDs. Never invent or move an "
            "existing ID to a different fact, and do not stop after one ID."
        )
    if "duplicate Core footnote definitions (repair all):" in message:
        return (
            "\n\nInspect EVERY listed duplicate Core definition and its exact "
            "file:line positions. Keep one supported definition per citation "
            "ID and remove only redundant copies. Preserve Core facts, "
            "existing source links, and unrelated citations."
        )
    if "undefined Core footnotes (repair all):" in message:
        return (
            "\n\nInspect EVERY listed undefined Core citation at its file and "
            "line. Restore a missing definition only when its original source "
            "can be verified; otherwise remove only the unsupported citation "
            "or fact. Preserve unrelated Core facts and existing source links."
        )
    if "unused Core footnotes (repair all):" in message:
        return (
            "\n\nInspect EVERY listed unused Core definition at its line. "
            "Remove only definitions with no supported citation; restore an "
            "accidentally lost exact citation only when archived evidence and "
            "the committed fact prove it. Do not invent sources or IDs."
        )
    if "content after final evidence" in message:
        return (
            "\n\nThis is an aggregated Topic paragraph-format repair. "
            "Repair EVERY listed block in this trajectory, using each block ID, "
            "file, and trailing excerpt to inspect the exact staged paragraph. "
            "Every memory fact must end its prose "
            "with its supporting evidence citation(s), followed only by its "
            "existing block ID suffix. If the reported trailing text is a "
            "supported fact, move its existing citation(s) after that text; "
            "otherwise remove only the unsupported trailing text. Preserve "
            "the fact's meaning, existing footnote definitions, source links, "
            "and block IDs. Do not invent evidence or alter other paragraphs."
        )
    invalid_sources = re.search(
        r"invalid source reference(?:s \(repair all\))?:\s*([^\n]+)", message,
    )
    if invalid_sources is not None:
        return (
            "\n\nThis is an invalid-source-format repair. Repair EVERY invalid "
            "and missing source listed in the error, not just its first handle. "
            "A provider/thread handle is incomplete: a citation must identify "
            "an exact archived message as provider/thread/message. Search the "
            "staged Topic/Core files for every listed handle and inspect the "
            "associated fact. Use the supplied input's exact message handles, "
            "or Grep the archived sources for the fact and inspect the matching "
            "source-id. Never guess a message ID or blindly append a suffix. "
            "Do not read whole large Source files. Replace a handle only when "
            "the exact message supports that fact. If no supporting evidence "
            "exists, remove only the newly staged unsupported fact and its "
            "unused footnote; do not delete committed facts or IDs. Preserve "
            "unrelated facts, IDs, citations and links. Before finishing, check "
            "every listed defect. Handles: " + invalid_sources.group(1).strip()
        )
    malformed = re.search(
        r"malformed footnote definitions \(repair all\):\s*(.+)",
        message,
        re.DOTALL,
    )
    if malformed is not None:
        return (
            "\n\nThis is an aggregated malformed-footnote repair. Repair EVERY "
            "listed definition in this single trajectory: "
            f"{malformed.group(1).strip()} Each definition must use the exact "
            "shape `[^id]: Time: `YYYY-MM-DD`; Sources: [source handle](link)` "
            "(with an allowed lower-precision date or `undated` only when the "
            "evidence truly lacks a date). Preserve each citation ID, supported "
            "fact, source handle, source link, and block ID. Do not stop after "
            "fixing the first entry and do not rewrite unrelated content."
        )
    match = re.search(r"dangling block link:\s*([A-Za-z0-9_-]+)", message)
    if match is not None:
        return "\n\n" + DANGLING_BLOCK_LINK_REPAIR_GUIDANCE.format(
            block_id=match.group(1)
        )
    sources = re.search(
        r"missing source references \(repair all\):\s*(.+)",
        message,
        re.DOTALL,
    )
    if sources is not None:
        return (
            "\n\nThis is an aggregated missing-source repair. Repair EVERY "
            "listed invalid handle in this single trajectory: "
            f"{sources.group(1).strip()} For each handle, locate the exact "
            "staged fact and read the corresponding archived Source Markdown. "
            "Replace it only with an exact existing message handle supporting "
            "that same fact. If no archived source supports the fact, remove "
            "only that unsupported fact and its footnote. Do not guess IDs, "
            "stop after the first entry, or rewrite unrelated content."
        )
    source = re.search(r"missing source reference:\s*(\S+)", message)
    if source is not None:
        return (
            "\n\nThis is a missing-source repair. Locate the exact cited handle `"
            f"{source.group(1)}` in the staged Topic/Core files. Read the "
            "corresponding archived source file and replace only that invalid "
            "handle with the exact real handle supporting the same fact. If no "
            "source supports the fact, remove only that unsupported fact and "
            "its footnote. Never guess or synthesize a message ID."
        )
    footnote = re.search(r"duplicate footnote definition:\s*(\S+)", message)
    if footnote is not None:
        return (
            "\n\nThis is a duplicate-footnote repair. Search for every definition "
            f"of `{footnote.group(1)}`. Keep exactly one byte-identical "
            "definition beside the fact it supports and remove only the "
            "duplicate definition. Do not rewrite, renumber, or move unrelated "
            "paragraphs, footnotes, or block IDs."
        )
    undefined = re.search(r"undefined footnote:\s*(\S+)", message)
    if undefined is not None:
        return (
            "\n\nThis is an undefined-footnote repair. Locate every citation "
            f"use of `{undefined.group(1)}` and inspect the committed workspace "
            "and archived Source Markdown for the exact supported fact. Restore "
            "the exact existing definition only when it is recoverable from "
            "those records. Otherwise remove only the unsupported citation and "
            "the fact that depends on it. Do not invent a definition, source "
            "handle, source link, citation ID, fact, or block ID, and do not "
            "rewrite unrelated content."
        )
    unused = re.search(r"unused footnote definition:\s*(\S+)", message)
    if unused is not None:
        return (
            "\n\nThis is an unused-footnote-definition repair. Locate the "
            f"definition `{unused.group(1)}` and search every staged editable "
            "file for its exact citation. If it is truly unreferenced, remove "
            "only that definition. If its supported paragraph is present and "
            "the citation was accidentally removed, restore only that exact "
            "citation after confirming the fact and source in the committed "
            "workspace or archived Source Markdown. Do not invent or renumber "
            "citations, change sources or block IDs, or rewrite unrelated text."
        )
    duplicate_memory_id = re.search(r"duplicate memory_id:\s*(\S+)", message)
    if duplicate_memory_id is not None:
        return (
            "\n\nThis is a duplicate-memory-ID repair. Search every staged "
            f"editable file for the exact trailing block ID `^{duplicate_memory_id.group(1)}`. "
            "Compare those paragraphs with the committed workspace and preserve "
            "the ID exactly once on its original committed fact. Remove the "
            "duplicated ID from a newly staged duplicate paragraph, or restore "
            "the original paragraph verbatim when it was altered. The Runtime, "
            "not the model, assigns new IDs: never invent, reassign, or replace "
            "an ID, and do not rewrite unrelated facts, citations, or sources."
        )
    removed = re.search(
        r"block ID must not be removed:\s*([^\n]+)",
        message,
    )
    if removed is not None:
        removed_ids = ", ".join(
            block_id.strip(" `")
            for block_id in removed.group(1).split(",")
            if block_id.strip(" `")
        )
        return (
            "\n\nThis is a strictly local removed-block-ID repair. The complete "
            f"set of missing existing IDs is: `{removed_ids}`. Restore EVERY "
            "listed ID in this trajectory to the same staged fact that carried "
            "it before this maintenance pass. Prefer restoring the original "
            "paragraph verbatim from the committed workspace; this is not a new "
            "organization, summarization, split, merge, or rewrite task. Do not "
            "invent an ID, attach an ID to a different fact, alter unrelated "
            "Topic/Core text, footnotes, sources, or block IDs, or stop after "
            "the first listed ID. Before finishing, search for and account for "
            "each listed ID exactly once."
        )
    missing_block = re.search(
        r"memory block ID required:\s*([^;\n]+)(?:;\s*line=\d+)?(?:;\s*paragraph=(.+))?",
        message,
    )
    if missing_block is not None:
        paragraph = missing_block.group(2)
        detail = f" The rejected paragraph is {paragraph}." if paragraph else ""
        return (
            "\n\nThis is a new-paragraph contract repair in staged Topic `"
            f"{missing_block.group(1).strip()}`.{detail} Locate that exact "
            "paragraph. Never write or invent a trailing `^id`; the Runtime "
            "assigns IDs. If it is a memory fact, make it an evidence-bearing "
            "paragraph with a matching footnote definition and exact archived "
            "source handle. If it is commentary, a file introduction, a bare "
            "label, or duplicated prose rather than memory, remove only that "
            "paragraph. Do not add a different fact or touch unrelated IDs."
        )
    return ""


def _luna_strict_validation_guidance(
    error: CommitFailure | Exception | str,
) -> tuple[str, str]:
    """Additional opt-in guidance; never changes validation or the repair budget."""
    message = _failure_message(error)
    topic_footnote = re.search(r"undefined (?:Topic )?footnote:\s*(\S+)", message)
    if topic_footnote is not None or "undefined Topic footnotes (repair all):" in message:
        return (
            "strict_undefined_topic_footnotes",
            "\n\nThis is a strictly local Topic citation repair, not another memory-writing task. "
            "Inspect every reported citation at its exact file, line, and block ID. "
            "Read the staged paragraph and use ReadCommitted on that same Topic to compare "
            "its original fact, citation, and definition. Footnote labels are file-local: "
            "an e1 in another file is not evidence for this e1. Restore a missing original "
            "definition verbatim only when its archived Source supports this same fact. "
            "If this is a newly added citation, read the exact archived message before "
            "adding its correctly formatted definition. Never guess source handles or dates. "
            "If support cannot be verified, undo only the newly staged unsupported addition; "
            "preserve the committed fact, its evidence, and its existing block ID. "
            "Keep definitions as separate lines, not inside another definition or paragraph. "
            "Do not add new facts, expand prose, rename citations globally, or edit unrelated "
            "paragraphs. Finish after fixing the listed citation defects."
        )
    if "undefined Core footnotes (repair all):" in message:
        return (
            "undefined_core_footnotes",
            "\n\nThis is an aggregated Core citation repair. Inspect EVERY "
            "listed citation and its Core line, then search committed "
            "Core/Topic and archived Source for the same supported fact. "
            "Restore an original definition verbatim only if proven; "
            "otherwise remove only the newly staged unsupported citation "
            "and dependent fact. Never invent a footnote, source handle, "
            "or block ID, and do not stop after the first item.",
        )
    core_footnote = re.search(r"undefined Core footnote:\s*(\S+)", message)
    if core_footnote is not None:
        return (
            "undefined_core_footnote",
            "\n\nThis is a strictly local Core citation repair. Find the exact "
            f"Core use of `{core_footnote.group(1)}`. Search the committed "
            "Core/Topic files and archived Source for the same supported fact. "
            "Restore its original definition verbatim only if the evidence "
            "proves that definition. Otherwise remove only the newly staged "
            "unsupported citation and dependent fact. Preserve every existing "
            "block ID and unrelated paragraph. Never invent a footnote, source "
            "handle, or block ID.",
        )
    if "Source Memory is append-only" in message:
        return (
            "source_append_only",
            "\n\nThis is a strictly local Source immutability repair. Files under "
            "sources/ are archived evidence and must be byte-for-byte "
            "identical to the committed workspace. Restore only staged Source "
            "changes from their committed originals; put any supported new "
            "memory fact in Topic/Core with exact existing Source handles. "
            "Do not rewrite, delete, or synthesize archived Source content, "
            "and do not alter unrelated memory or block IDs.",
        )
    return "", ""


def _generic_repair_mode(error: CommitFailure | Exception | str) -> str:
    message = _failure_message(error).lower()
    if "undefined topic footnotes (repair all)" in message:
        return "undefined_topic_footnotes"
    if "undefined core footnotes (repair all)" in message:
        return "undefined_core_footnotes"
    if "unused core footnotes (repair all)" in message:
        return "unused_core_footnotes"
    if "duplicate core footnote definitions (repair all)" in message:
        return "duplicate_core_footnote_definitions"
    if "duplicate footnote definitions (repair all)" in message:
        return "duplicate_footnote_definitions"
    if "unused footnote definitions (repair all)" in message:
        return "unused_footnote_definitions"
    if "duplicate memory_ids (repair all)" in message:
        return "duplicate_memory_ids"
    if "malformed footnote definitions" in message:
        return "malformed_footnote_definitions"
    if "dangling block link" in message:
        return "dangling_block_link"
    if "invalid source reference" in message:
        return "invalid_source_reference"
    if "missing source reference" in message:
        return "missing_source_reference"
    if "duplicate footnote definition" in message:
        return "duplicate_footnote_definition"
    if "undefined footnote" in message:
        return "undefined_footnote"
    if "unused footnote definition" in message:
        return "unused_footnote_definition"
    if "duplicate memory_id" in message:
        return "duplicate_memory_id"
    if "block id must not be removed" in message:
        return "removed_block_id"
    if "memory block id required" in message:
        return "missing_new_paragraph_contract"
    return "generic_validation"


_CORE_STRUCTURAL_ERROR_PATTERNS = (
    "block id",
    "memory_id",
    "footnote",
    "source reference",
    "dangling block link",
)


def _is_core_structural_error(
    failure: CommitFailure | Exception | str,
) -> bool:
    """Return whether a post-compression failure is safe for targeted repair."""
    message = _failure_message(failure).lower()
    return any(pattern in message for pattern in _CORE_STRUCTURAL_ERROR_PATTERNS)


def render_conversation(
    turns: list[tuple[str, str]], refs: list[str]
) -> str:
    return "\n".join(
        f"[{ref}] {speaker}: {text}"
        for (speaker, text), ref in zip(turns, refs)
    )


def _baseline(workspace: MemoryWorkspace) -> tuple[Any, ...] | None:
    """Snapshot the stage, or None if it cannot be parsed.

    A turn whose edits were rejected leaves the stage rolled back, but a
    malformed file that reached disk another way would make this raise. That
    is a reason to skip committing, never to abort the whole build.
    """
    try:
        return workspace.baseline()
    except Exception:
        workspace._refresh_stage()
        try:
            return workspace.baseline()
        except Exception:
            return None


def _committed_baseline(
    workspace: MemoryWorkspace,
) -> tuple[Any, ...] | None:
    """Snapshot committed memory without disturbing staged built-in edits."""
    committed = MemoryWorkspace(workspace.memory_dir, config=workspace.config)
    try:
        return _baseline(committed)
    finally:
        shutil.rmtree(committed.stage_dir, ignore_errors=True)


def _commit_turn(
    workspace: MemoryWorkspace,
    baseline: tuple[Any, ...] | None,
    audit: list[dict[str, Any]],
) -> CommitFailure | None:
    """Install what the turn staged. Return a structured rejection on failure."""
    check_writer_stagnation(workspace, audit)
    if baseline is None:
        workspace._refresh_stage()
        return CommitFailure(ValueError("Committed memory baseline could not be parsed"))
    if not workspace.stage_is_dirty():
        return None
    # Source suggestions are diagnostic only; similarity cannot prove evidence.
    source_audit = canonicalize_workspace(workspace.stage_dir)
    if source_audit["corrections"] or source_audit["unresolved"]:
        audit.append({
            "tool": "source_handle_guard",
            "status": source_audit["status"],
            "corrections": source_audit["corrections"],
            "unresolved": source_audit["unresolved"],
        })
    try:
        workspace.commit_edits(*baseline, preserve_rejected=True)
    except SourceIntegrityError as exc:
        setattr(exc, "somem_audit", list(audit))
        raise
    except Exception as exc:
        if not isinstance(exc, ValueError):
            audit.append({
                "tool": "commit", "status": "infrastructure_error",
                "commit_state": getattr(exc, "commit_state", "unknown"),
                "output": str(exc),
            })
            setattr(exc, "somem_audit", list(audit))
            raise
        failure = CommitFailure(exc, workspace.last_validation_diagnostics)
        audit.append({
            "tool": "commit", "status": "error", "output": failure.message,
        })
        if failure.diagnostics:
            audit.append({
                "tool": "validation_diagnostics",
                "status": "reported",
                "findings": list(failure.diagnostics),
                "complete": False,
            })
        return failure
    audit.append({
        "tool": "commit",
        "status": "ok",
        "count": workspace.last_created_blocks,
        "topic_paths": workspace.last_changed_topics,
    })
    return None


def _successful_topic_paths(audit: list[dict[str, Any]]) -> set[str]:
    """Return Topic paths installed by successful commits in this trajectory."""
    return {
        str(path)
        for record in audit
        if record.get("status") == "ok"
        for path in record.get("topic_paths", [])
        if str(path).startswith("topics/")
    }


def _run_agent(
    memory_dir: str | Path,
    *,
    agent: ClaudeCodeAgent,
    task: str,
    source_sessions: list[dict[str, Any]] | None = None,
    usage_logger: Any | None = None,
    final_output: list[str] | None = None,
    live_audit_path: str | Path | None = None,
    config: MemoryConfig | None = None,
    allow_rejected_noop: bool = False,
) -> list[dict[str, Any]]:
    config = config or MemoryConfig()
    workspace = MemoryWorkspace(memory_dir, config=config)
    audit: list[dict[str, Any]] = []
    finished = False
    progress_path = (
        Path(str(live_audit_path) + ".progress.json")
        if live_audit_path is not None else None
    )

    def record_progress(event: dict[str, Any]) -> None:
        check_writer_stagnation(workspace, audit)
        if event.get("event") in {
            "signature_protocol_failure", "empty_unsigned_thinking_removed",
            "context_diagnostic",
        }:
            record_live_event({"tool": "transport_compatibility", **event})
        if progress_path is None:
            return
        payload = {
            **event,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        progress_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = progress_path.with_suffix(progress_path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, progress_path)

    def record_live_event(event: dict[str, Any]) -> None:
        """Durably retain repair lifecycle events, including terminal failures."""
        if live_audit_path is None:
            return
        path = Path(live_audit_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            **event,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    try:
        if source_sessions:
            from .transaction import workspace_revision
            archive_before = workspace_revision(workspace.memory_dir)
            workspace.archive_sessions(source_sessions)
            workspace._refresh_stage()
            archive_boundary = {
                "tool": "source_archive_boundary", "status": "ok",
                "revision_before": archive_before,
                "revision_after": workspace_revision(workspace.memory_dir),
            }
            audit.append(archive_boundary)
            record_live_event(archive_boundary)
        task = f"{task}\n\nCurrent workspace structure:\n{workspace.structure()}"
        system_prompt = SYSTEM_PROMPT
        tool_recoveries = 0
        if config.tool_failure_recovery_enabled:
            from .tool_recovery import RecoveryBudget, run_with_tool_recovery
            from .tools import WriterStagnationError, WriterContextBoundary
            recovery_budget = RecoveryBudget(config.generic_repair_max_trajectories)

            def bounded_progress(event):
                record_progress(event)
                if event.get("assistant_messages", 0) >= min(config.max_turns, 60):
                    workspace.writer_stagnation_reason = "Writer attempt reached local message recovery boundary"
                    check_writer_stagnation(workspace, audit)

            def run_attempt(prompt, recovery, *, core_repair_state=None, repair_context=False):
                try:
                    return agent.run(
                        prompt=prompt, system_prompt=system_prompt, cwd=workspace.stage_dir,
                        tools=management_tools(workspace, audit, live_audit_path=live_audit_path,
                                               repair_context=recovery or repair_context,
                                               core_repair_state=core_repair_state),
                        max_turns=config.max_turns, max_budget_usd=config.max_budget_usd,
                        progress_fn=bounded_progress, builtin_tools_enabled=False,
                    )
                except Exception as exc:
                    if getattr(workspace, "writer_stagnation_reason", None):
                        error_type = (WriterContextBoundary if
                            workspace.writer_stagnation_reason == "Writer attempt reached local message recovery boundary"
                            else WriterStagnationError)
                        error = error_type(workspace.writer_stagnation_reason)
                        if hasattr(exc, "somem_result"):
                            error.somem_result = exc.somem_result
                        raise error from exc
                    raise

            trajectory_results, tool_recoveries = run_with_tool_recovery(
                workspace, audit, task, run_attempt,
                usage_logger or (lambda result: None), record_live_event,
                budget=recovery_budget,
            )
        else:
            try:
                result = agent.run(
                    prompt=task,
                    system_prompt=system_prompt,
                    cwd=workspace.stage_dir,
                    tools=management_tools(
                        workspace, audit, live_audit_path=live_audit_path
                    ),
                    max_turns=config.max_turns,
                    max_budget_usd=config.max_budget_usd,
                    progress_fn=record_progress,
                    builtin_tools_enabled=False,
                )
            except Exception as exc:
                # The staged workspace is intentionally discarded, but callers
                # still need the tool trace to diagnose loops and platform errors.
                failed_result = getattr(exc, "somem_result", None)
                if (config.unchecked_writer_commits
                        and failed_result is not None
                        and "maximum number of turns" in str(exc).lower()):
                    # The unchecked build may retain the edits already staged
                    # when the model exhausts its turn budget. The audit makes
                    # this partial batch explicit; other provider errors fail.
                    audit.append({
                        "tool": "unchecked_turn_limit",
                        "status": "partial",
                        "error": str(exc),
                    })
                    result = failed_result
                else:
                    if failed_result is not None and usage_logger is not None:
                        try:
                            usage_logger(failed_result)
                        except Exception as usage_exc:  # noqa: BLE001
                            setattr(exc, "somem_usage_error", str(usage_exc))
                    setattr(exc, "somem_audit", list(audit))
                    raise
            if usage_logger is not None:
                usage_logger(result)
            trajectory_results = [result]

        def run_repair(prompt, *, core_repair_state=None):
            """All repair categories use the same retained candidate and budget."""
            def remember(result):
                if usage_logger is not None:
                    usage_logger(result)
                trajectory_results.append(result)

            if config.tool_failure_recovery_enabled:
                recovery_budget.repair()
                results, _ = run_with_tool_recovery(
                    workspace, audit, prompt,
                    lambda text, recovering: run_attempt(
                        text, recovering, repair_context=True,
                        core_repair_state=core_repair_state,
                    ),
                    remember, record_live_event, budget=recovery_budget,
                )
                return results[-1]
            result = agent.run(
                prompt=prompt, system_prompt=system_prompt, cwd=workspace.stage_dir,
                tools=management_tools(workspace, audit, live_audit_path=live_audit_path,
                                       repair_context=True, core_repair_state=core_repair_state),
                max_turns=config.max_turns, max_budget_usd=config.max_budget_usd,
                progress_fn=record_progress, builtin_tools_enabled=False,
            )
            remember(result)
            return result

        core_repair_records: list[dict[str, Any]] = []
        luna_core_structural_repair_records: list[dict[str, Any]] = []
        generic_repair_records: list[dict[str, Any]] = []
        # Shell calls commit immediately, while built-in file tools leave edits
        # staged until the trajectory ends. Snapshot the current committed tree
        # now, so shell work from this same turn is not mistaken for a new edit.
        baseline = _committed_baseline(workspace)
        error = _commit_turn(workspace, baseline, audit)
        if error is not None:
            capacity_error = _core_capacity_error(error)
            if capacity_error is not None:
                if not config.core_capacity_repair_enabled:
                    exc = RuntimeError(
                        "memory writer Core capacity exceeded while automatic "
                        "capacity repair is disabled: "
                        + _failure_message(error)
                    )
                    setattr(exc, "somem_audit", list(audit))
                    raise exc
                repair_error: CommitFailure | Exception | str | None = error
                for repair_number in range(
                    1, config.core_repair_max_trajectories + 1
                ):
                    if config.tool_failure_recovery_enabled and recovery_budget.repairs >= recovery_budget.limit:
                        break
                    current_capacity = _core_capacity_error(repair_error)
                    if current_capacity is None:
                        break
                    # Core-only capacity failures preserve the otherwise valid
                    # staged turn. Repair the excess in place and validate the
                    # result against the original committed baseline.
                    repair_baseline = baseline
                    state = CoreRepairState(
                        limit=current_capacity.limit,
                        target=config.effective_core_repair_target_tokens,
                        max_checks=config.core_repair_max_checks,
                        stagnation_limit=config.core_repair_stagnation_limit,
                    )
                    capacity_prompt = (
                        LUNA_CORE_CAPACITY_REPAIR_TASK
                        if config.luna_core_capacity_prompt_enabled
                        else CORE_CAPACITY_REPAIR_TASK
                    )
                    repair_prompt = capacity_prompt.format(
                        limit=current_capacity.limit,
                        token_count=current_capacity.token_count,
                        target=state.target,
                        max_checks=state.max_checks,
                    )
                    if (config.validation_diagnostic_aggregation_enabled
                            and isinstance(repair_error, CommitFailure)):
                        other_findings = [
                            item for item in repair_error.diagnostics
                            if "Core Memory exceeds" not in item
                        ]
                        if other_findings:
                            repair_prompt = repair_prompt.replace(
                                "passed the ordinary memory checks but could "
                                "not be installed because",
                                "could not be installed because",
                            )
                            repair_prompt += (
                                "\n\nOther independent validation findings for "
                                "this same staged candidate are recorded below. "
                                "This trajectory remains Core-capacity-only; "
                                "other defects will be rechecked after the "
                                "capacity reduction and must not be ignored:\n"
                                + "\n".join(other_findings)
                            )
                    try:
                        repair = run_repair(prompt=repair_prompt, core_repair_state=state)
                    except Exception as exc:
                        setattr(exc, "somem_audit", list(audit))
                        raise
                    repair_error = _commit_turn(
                        workspace, repair_baseline, audit
                    )
                    core_repair_records.append({
                        "trajectory": repair_number,
                        "mode": "incremental_staged",
                        "trigger_token_count": current_capacity.token_count,
                        "hard_limit": state.limit,
                        "target": state.target,
                        "checks": list(state.checks),
                        "stagnated": state.stagnated,
                        "status": (
                            "ok" if repair_error is None else "rejected"
                        ),
                        "error": (
                            _failure_message(repair_error)
                            if repair_error is not None else None
                        ),
                    })
                    audit.append({
                        "tool": "core_capacity_repair",
                        "status": core_repair_records[-1]["status"],
                        **core_repair_records[-1],
                    })
                    if repair_error is None:
                        break
                    if _core_capacity_error(repair_error) is None:
                        break
                if (
                    repair_error is not None
                    and config.luna_core_structural_repair_enabled
                    and _core_capacity_error(repair_error) is None
                    and _is_core_structural_error(repair_error)
                ):
                    for structural_number in range(
                        1,
                        config.luna_core_structural_repair_max_trajectories + 1,
                    ):
                        if config.tool_failure_recovery_enabled and recovery_budget.repairs >= recovery_budget.limit:
                            break
                        trigger_message = _failure_message(repair_error)
                        record_live_event({
                            "tool": "luna_core_structural_repair",
                            "trajectory": structural_number,
                            "status": "started",
                            "trigger_error": trigger_message,
                        })
                        try:
                            structural_repair = run_repair(
                                prompt=LUNA_CORE_STRUCTURAL_REPAIR_TASK.format(
                                    validation_error=trigger_message,
                                    limit=config.core_max_tokens,
                                ),
                            )
                        except Exception as exc:
                            setattr(exc, "somem_audit", list(audit))
                            raise
                        repair_error = _commit_turn(
                            workspace, repair_baseline, audit
                        )
                        record = {
                            "trajectory": structural_number,
                            "mode": "minimal_post_compression",
                            "trigger_error": trigger_message,
                            "status": (
                                "ok" if repair_error is None else "rejected"
                            ),
                            "error": (
                                _failure_message(repair_error)
                                if repair_error is not None else None
                            ),
                        }
                        luna_core_structural_repair_records.append(record)
                        record_live_event({
                            "tool": "luna_core_structural_repair", **record,
                        })
                        audit.append({
                            "tool": "luna_core_structural_repair", **record,
                        })
                        if repair_error is None:
                            break
                        if (
                            _core_capacity_error(repair_error) is not None
                            or not _is_core_structural_error(repair_error)
                        ):
                            break
                if repair_error is not None and (
                    _core_capacity_error(repair_error) is not None
                    or not config.repair_cross_category_enabled
                ):
                    exc = RuntimeError(
                        "memory writer core-capacity repair was rejected: "
                        + _failure_message(repair_error)
                    )
                    setattr(exc, "somem_audit", list(audit))
                    raise exc
                error = repair_error
            if error is not None:
                repair_error: CommitFailure | Exception | str | None = error
                # Cross-category repair continues the same candidate and budget,
                # never restarts the original task or resets spent trajectories.
                spent = (recovery_budget.repairs if config.tool_failure_recovery_enabled else
                         tool_recoveries + len(core_repair_records) + len(luna_core_structural_repair_records))
                for repair_number in range(
                    spent + 1, config.generic_repair_max_trajectories + 1
                ):
                    if config.tool_failure_recovery_enabled and recovery_budget.repairs >= recovery_budget.limit:
                        break
                    # Keep refining the rejected candidate in place. Re-running
                    # the whole Writer/Manager task from committed memory made
                    # local format errors recur and could discard unrelated IDs.
                    repair_baseline = baseline
                    guidance = _generic_repair_guidance(repair_error)
                    capacity = _core_capacity_error(repair_error)
                    if capacity is not None:
                        if not config.core_capacity_repair_enabled:
                            break
                        guidance += (
                            f"\nCore capacity limit is {capacity.limit} tokens. "
                            "Reduce Core redundancy locally; preserve supported facts "
                            "in Topics and preserve IDs and evidence. Do not discard facts."
                        )
                    repair_mode = _generic_repair_mode(repair_error)
                    if config.luna_strict_validation_repair_enabled:
                        strict_mode, strict_guidance = (
                            _luna_strict_validation_guidance(repair_error)
                        )
                        if strict_guidance:
                            repair_mode, guidance = strict_mode, strict_guidance
                    trigger_message = _failure_message(repair_error)
                    diagnostic_hint = ""
                    aggregated_undefined = None
                    if (config.validation_diagnostic_aggregation_enabled
                            and isinstance(repair_error, CommitFailure)
                            and repair_error.diagnostics):
                        aggregated_undefined = next(
                            (item for item in repair_error.diagnostics
                             if "undefined Topic footnotes (repair all):" in item),
                            None,
                        )
                        diagnostic_hint = (
                            "\n\nAdditional read-only preflight findings for this "
                            "candidate (checks blocked by malformed input are "
                            "marked; recheck everything after repair):\n"
                            + "\n".join(repair_error.diagnostics)
                        )
                    if aggregated_undefined:
                        guidance += _generic_repair_guidance(aggregated_undefined)
                        diagnostic_hint += _generic_repair_guidance(
                            aggregated_undefined
                        )
                        if repair_mode == "undefined_footnote":
                            repair_mode = "undefined_topic_footnotes"
                    structural_guidance = _misplaced_id_guidance(workspace)
                    if structural_guidance:
                        repair_mode = "misplaced_footnote_block_id"
                        guidance = structural_guidance
                    guidance += _previous_repair_feedback(audit)
                    record_live_event({
                        "tool": "generic_repair",
                        "trajectory": repair_number,
                        "mode": repair_mode,
                        "status": "started",
                        "trigger_error": trigger_message,
                    })
                    try:
                        repair = run_repair(
                            prompt=(
                                "Original task for context only; its edits are already staged. "
                                "Do not execute it again:\n<original_task>\n"
                                f"{task}\n</original_task>\n\n"
                                "Your edits have not been committed:\n\n"
                                f"{_repair_diagnostic_prompt(workspace, repair_error)}\n\n"
                                "The rejected candidate is still staged. Repair "
                                "only the exact reported defect(s) in place. Do not "
                                "redo the original task, broadly rewrite files, "
                                "or change unrelated prose, footnotes, source "
                                "handles, links, or block IDs. Never Remove and "
                                "recreate a Topic containing existing memory IDs; "
                                "use exact local Edits instead."
                                " Use Read to inspect the rejected staged file. "
                                "Use ReadCommitted only to restore a previously "
                                "committed paragraph, ID, or footnote. A new file "
                                "has no committed version; if ReadCommitted says "
                                "file not found, inspect the staged file with Read."
                                f"{guidance}\n\n"
                                f"{staged_repair_context(workspace.stage_dir, trigger_message + diagnostic_hint)}"
                                f"{source_repair_context(workspace.stage_dir, _failure_message(repair_error))}"
                                f"Repair attempt {repair_number} of "
                                f"{config.generic_repair_max_trajectories}.\n\n"
                                "Fix the reported defects in the existing staged files now."
                            ),
                        )
                    except Exception as exc:
                        record_live_event({
                            "tool": "generic_repair",
                            "trajectory": repair_number,
                            "mode": repair_mode,
                            "status": "agent_error",
                            "trigger_error": trigger_message,
                            "error": _failure_message(exc),
                        })
                        setattr(exc, "somem_audit", list(audit))
                        raise
                    repair_error = _commit_turn(
                        workspace, repair_baseline, audit
                    )
                    generic_repair_records.append({
                        "trajectory": repair_number,
                        "mode": repair_mode,
                        "trigger_error": trigger_message,
                        "status": "ok" if repair_error is None else "rejected",
                        "error": (
                            _failure_message(repair_error)
                            if repair_error is not None else None
                        ),
                    })
                    record_live_event({
                        "tool": "generic_repair",
                        **generic_repair_records[-1],
                    })
                    audit.append({
                        "tool": "generic_repair",
                        **generic_repair_records[-1],
                    })
                    if repair_error is None:
                        break
                    if (_core_capacity_error(repair_error) is not None
                            and not config.repair_cross_category_enabled):
                        break
                    if len(generic_repair_records) > 1 and (
                        _failure_message(repair_error)
                        == generic_repair_records[-2].get("error")
                    ):
                        record_live_event({
                            "tool": "generic_repair_guard",
                            "trajectory": repair_number,
                            "status": "continuing",
                            "reason": "repeated_validation_error_with_budget",
                            "error": _failure_message(repair_error),
                        })
                        audit.append({
                            "tool": "generic_repair_guard",
                            "trajectory": repair_number,
                            "status": "continuing",
                            "reason": "repeated_validation_error_with_budget",
                            "error": _failure_message(repair_error),
                        })
                if repair_error is not None:
                    if allow_rejected_noop:
                        record = {
                            "tool": "optional_maintenance",
                            "status": "discarded_noop",
                            "reason": "validation_failed_after_repair_budget",
                            "error": _failure_message(repair_error),
                            "repair_trajectories": (
                                config.generic_repair_max_trajectories
                            ),
                        }
                        record_live_event(record)
                        audit.append(record)
                        repair_error = None
                    else:
                        exc = RuntimeError(
                            "memory writer repair was rejected after "
                            f"{config.generic_repair_max_trajectories} "
                            "trajectories: " + _failure_message(repair_error)
                        )
                        setattr(exc, "somem_audit", list(audit))
                        raise exc
        if source_sessions:
            assert_session_sources(workspace.memory_dir, source_sessions)
        if final_output is not None:
            final_output.append(trajectory_results[-1].text)
        audit.append({
            "tool": "agent",
            "status": "ok",
            "reason": trajectory_results[-1].stop_reason or "complete",
            "rounds": sum(item.num_turns for item in trajectory_results),
            "input_tokens": sum(item.input_tokens for item in trajectory_results),
            "output_tokens": sum(item.output_tokens for item in trajectory_results),
            "anthropic_equivalent_cost_usd": sum(
                item.anthropic_equivalent_cost_usd or 0.0
                for item in trajectory_results
            ),
            **({"core_repair": core_repair_records}
               if core_repair_records else {}),
            **({
                "luna_core_structural_repair":
                    luna_core_structural_repair_records
            } if luna_core_structural_repair_records else {}),
            **({"generic_repair": generic_repair_records}
               if generic_repair_records else {}),
        })
        finished = True
        return audit
    finally:
        if finished:
            shutil.rmtree(workspace.stage_dir, ignore_errors=True)
        else:
            # Retain the actual candidate even when persistence itself fails.
            # Never mask the original exception with an audit-write failure.
            import sys
            failure = sys.exc_info()[1]
            record = {"tool": "retained_candidate", "path": str(workspace.stage_dir),
                      "status": "failed", "error": str(failure)}
            audit.append(record)
            if failure is not None:
                setattr(failure, "somem_audit", list(audit))
            try:
                record_live_event(record)
                (workspace.stage_dir / "failure-audit.json").write_text(
                    json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8",
                )
            except Exception:
                pass
