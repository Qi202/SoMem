"""Deterministic canonicalization for archived LoCoMo source handles."""

from __future__ import annotations

import re
import json
from pathlib import Path

from ..workspace_layout import core_file


SOURCE_ID_RE = re.compile(r"source-id:([^\s]+)")
HANDLE_RE = re.compile(r"locomo/thread_[0-9a-f]+/msg_[0-9a-f]+")


def staged_repair_context(workspace: Path, error: str) -> str:
    """Resolve validator topic-relative paths and expose only staged evidence."""
    findings = []
    seen = set()
    for match in re.finditer(r"(?P<path>(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.md)(?:(?: line=|:)(?P<line>\d+))?", error):
        reported = match.group("path")
        line_number = int(match.group("line") or 1)
        relative = Path(reported)
        if ".." in relative.parts:
            continue
        candidates = [workspace / relative]
        if relative.parts[0] not in {"topics", "sources"} and reported != "core.md":
            candidates.append(workspace / "topics" / relative)
        for path in candidates:
            if not path.resolve().is_relative_to(workspace.resolve()) or not path.is_file():
                continue
            exact = path.relative_to(workspace).as_posix()
            key = (exact, line_number)
            if key in seen:
                continue
            seen.add(key)
            content = path.read_text(encoding="utf-8")
            lines = content.splitlines()
            start = max(0, line_number - 4)
            end = min(len(lines), line_number + 3)
            excerpt = "\n".join(lines[start:end])
            findings.append({"exact_tool_path": exact, "reported_line": line_number,
                             "start_line": start + 1, "end_line": end,
                             "staged_content": excerpt[:4000],
                             "truncated": len(excerpt) > 4000 or start > 0 or end < len(lines),
                             "literal_backslash_n_count": excerpt.count("\\n")})
            if len(findings) >= 8:
                break
        if len(findings) >= 8:
            break
    if not findings:
        return ""
    return (
        "\n\nReported files resolved against the staged workspace (read-only):\n"
        + json.dumps(findings, ensure_ascii=False)
        + "\nUse these exact tool paths; do not create a different file to repair an "
        "existing file. Excerpts are bounded, not a complete issue list; Read the "
        "reported ranges as needed. Literal backslash-n is diagnostic only: never "
        "globally decode escapes or alter legitimate code examples. Repair the "
        "existing file. For a wrong source, Edit the existing footnote definition "
        "in place, preserving its label. Do not append a second definition. For "
        "conflicting duplicate definitions, inspect their archived sources and "
        "retain exactly one supported definition for the existing claim.\n"
    )


def source_repair_context(workspace: Path, error: str) -> str:
    """Offer archived evidence for wrong-thread handles without editing memory.

    An exact message component is a lookup key, not permission to retarget a
    citation: all matching messages are returned for the Writer to verify.
    """
    if "source reference" not in error:
        return ""
    invalid = list(dict.fromkeys(re.findall(
        r"[A-Za-z0-9_-]+/thread_[A-Za-z0-9_-]+/msg_[A-Za-z0-9_-]+", error,
    )))[:8]
    if not invalid:
        return ""
    wanted = {handle.rsplit("/", 1)[-1] for handle in invalid}
    matches: dict[str, list[dict[str, str]]] = {key: [] for key in wanted}
    for path in sorted((workspace / "sources").rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        headers = list(re.finditer(r"<!-- source-id:([^\s]+) -->", text))
        for index, match in enumerate(headers):
            handle = match.group(1)
            message = handle.rsplit("/", 1)[-1]
            if message not in matches or len(matches[message]) >= 3:
                continue
            end = headers[index + 1].start() if index + 1 < len(headers) else len(text)
            body = re.sub(r'<a id="[^"]+"></a>\s*$', '',
                          text[match.end():end].strip()).strip()
            matches[message].append({
                "source_handle": handle,
                "path": path.relative_to(workspace).as_posix(),
                "source_excerpt": body[:1800],
            })
    authored = sorted((workspace / "topics").rglob("*.md"))
    if (core_file(workspace)).is_file():
        authored = sorted(set([*authored, core_file(workspace)]))
    occurrences = {handle: [] for handle in invalid}
    for path in authored:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for handle in invalid:
                if handle in line and len(occurrences[handle]) < 20:
                    occurrences[handle].append({"path": path.relative_to(workspace).as_posix(),
                                                "line": number, "text": line[:2400]})
    findings = [{"invalid_handle": handle,
                 "all_staged_occurrences": occurrences[handle],
                 "archived_candidates": matches[handle.rsplit("/", 1)[-1]]}
                for handle in invalid]
    return (
        "\n\nRead-only archive lookup for the reported handles. These are "
        "candidates, not automatic corrections. Read the staged claim and "
        "verify that the quoted source actually supports it before replacing "
        "its handle. Repair EVERY listed occurrence in both Core and Topics; "
        "the validator may have reported only the first file. Preserve each "
        "existing evidence label and block ID. If "
        "there is no supporting candidate, search the archive; never guess "
        "a handle. No file has been changed by this lookup.\n"
        + json.dumps(findings, ensure_ascii=False)
    )


def _edit_distance(left: str, right: str) -> int:
    previous = list(range(len(right) + 1))
    for left_index, left_char in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_char in enumerate(right, start=1):
            current.append(min(
                current[-1] + 1,
                previous[right_index] + 1,
                previous[right_index - 1] + (left_char != right_char),
            ))
        previous = current
    return previous[-1]


def canonicalize_workspace(workspace: Path) -> dict[str, object]:
    valid = {
        match.group(1)
        for path in (workspace / "sources").rglob("*.md")
        for match in SOURCE_ID_RE.finditer(path.read_text(encoding="utf-8"))
    }
    by_thread: dict[str, list[str]] = {}
    for handle in valid:
        thread, _, _message = handle.rpartition("/")
        by_thread.setdefault(thread, []).append(handle)

    authored = list((workspace / "topics").rglob("*.md"))
    core = core_file(workspace)
    if core.is_file():
        authored = sorted(set([*authored, core]))
    corrections: list[dict[str, str | int]] = []
    unresolved: list[dict[str, object]] = []
    for path in authored:
        original = path.read_text(encoding="utf-8")
        for handle in sorted(set(HANDLE_RE.findall(original))):
            if handle in valid:
                continue
            thread, _, _message = handle.rpartition("/")
            candidates = by_thread.get(thread, [])
            ranked = sorted(
                (_edit_distance(handle, candidate), candidate)
                for candidate in candidates
            )
            if not ranked:
                unresolved.append({"path": str(path), "handle": handle})
                continue
            best_distance = ranked[0][0]
            best = [candidate for distance, candidate in ranked
                    if distance == best_distance]
            if best_distance > 2 or len(best) != 1:
                unresolved.append({
                    "path": str(path),
                    "handle": handle,
                    "best_distance": best_distance,
                    "candidate_count": len(best),
                })
                continue
            # Hash similarity is not evidence that two messages are identical.
            # Offer a candidate for inspection, never silently retarget a claim.
            unresolved.append({
                "path": str(path.relative_to(workspace)),
                "handle": handle,
                "suggested_handle": best[0],
                "edit_distance": best_distance,
                "requires_source_verification": True,
            })
    return {
        "status": "ok" if not unresolved else "unresolved",
        "valid_source_handles": len(valid),
        "corrections": corrections,
        "unresolved": unresolved,
    }
