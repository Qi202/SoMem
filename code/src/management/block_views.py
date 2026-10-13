"""Block-based Topic view synchronization and atomic installation."""

import os
import re
import shutil
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

import tiktoken

from ..runtime.derived_views import rebuild_derived_views
from ..runtime.state import RuntimeStateStore
from ..markdown import (
    definition_match,
    parse_topic_tree,
    render_definition,
)
from ..markdown.models import is_valid_temporal_value
from ..markdown.syntax import LINK, SINGLE_CITATION, definitions
from ..workspace_layout import runtime_dir
from ..shell_backend import is_wsl_drvfs_path
from .errors import CoreCapacityError, CommitStateError


_WINDOWS_RETRY_DELAYS = (0.05, 0.1, 0.2, 0.4, 0.8, 1.6)
_CORE_ENCODING = tiktoken.get_encoding("o200k_base")


def core_token_count(path: Path) -> int:
    """Count Core Memory with the same tokenizer used by validation."""
    if not path.is_file():
        return 0
    return len(_CORE_ENCODING.encode(path.read_text(encoding="utf-8")))


def core_format_diagnostics(core: Path, *, normalized: bool = True) -> tuple[str, ...]:
    """Collect independently detectable Core defects before returning feedback."""
    lines = core.read_text(encoding="utf-8").splitlines()
    errors: dict[str, list[str]] = {
        "malformed Core footnote definitions": [],
        "duplicate Core footnote definitions": [],
        "invalid Core evidence dates": [],
        "Core source links required": [],
        "undefined Core footnotes": [],
        "unused Core footnotes": [],
    }
    definition_lines: dict[str, list[int]] = {}
    citation_lines: dict[str, list[int]] = {}
    for line_number, line in enumerate(lines, 1):
        if match := definition_match(line):
            citation_id = match.group("id")
            definition_lines.setdefault(citation_id, []).append(line_number)
            when = match.group("when")
            if when != "undated" and not is_valid_temporal_value(when):
                errors["invalid Core evidence dates"].append(
                    f"date={when} file=core.md line={line_number} footnote={citation_id}"
                )
            if normalized and not LINK.search(match.group("sources")):
                errors["Core source links required"].append(
                    f"id={citation_id} file=core.md line={line_number}"
                )
            continue
        if re.match(r"^\s*\[\^[^\]\s]+\]:", line):
            errors["malformed Core footnote definitions"].append(
                f"core.md:{line_number}={' '.join(line.split())[:180]!r}"
            )
            continue
        for citation in SINGLE_CITATION.finditer(line):
            citation_lines.setdefault(citation.group("id"), []).append(line_number)

    for citation_id, locations in definition_lines.items():
        if len(locations) > 1:
            errors["duplicate Core footnote definitions"].append(
                f"id={citation_id} file=core.md lines={locations!r} "
                f"occurrences={len(locations)}"
            )
        if citation_id not in citation_lines:
            errors["unused Core footnotes"].append(
                f"id={citation_id} file=core.md line={locations[0]}"
            )
    for citation_id, locations in citation_lines.items():
        if citation_id not in definition_lines:
            errors["undefined Core footnotes"].append(
                f"id={citation_id} file=core.md lines={locations!r}"
            )
    return tuple(
        (("Pending normalization: " if not normalized and category == "duplicate Core footnote definitions" else "")
         + f"{category} (repair all): " + " | ".join(locations))
        for category, locations in errors.items() if locations
    )


def _needs_filesystem_retries(path: Path) -> bool:
    return os.name == "nt" or is_wsl_drvfs_path(path)


def _replace_with_retry(source: Path, destination: Path) -> int:
    """Atomically replace a path, tolerating Windows/DrvFs directory locks."""
    attempts = (
        len(_WINDOWS_RETRY_DELAYS) + 1
        if _needs_filesystem_retries(destination) else 1
    )
    for attempt in range(attempts):
        try:
            os.replace(source, destination)
            return attempt
        except PermissionError:
            if attempt + 1 >= attempts:
                raise
            time.sleep(_WINDOWS_RETRY_DELAYS[attempt])


def _rmtree_with_retry(path: Path, *, ignore_errors: bool = False) -> None:
    attempts = (
        len(_WINDOWS_RETRY_DELAYS) + 1
        if _needs_filesystem_retries(path) else 1
    )
    for attempt in range(attempts):
        try:
            shutil.rmtree(path)
            return
        except FileNotFoundError:
            return
        except PermissionError:
            if attempt + 1 >= attempts:
                if ignore_errors:
                    return
                raise
            time.sleep(_WINDOWS_RETRY_DELAYS[attempt])


class BlockViewsMixin:
    def _synchronize(self) -> str:
        core = self.stage_dir / "core.md"
        if core.exists():
            # The Luna routing experiment is prone to producing an oversized
            # Core and malformed citations in the same candidate. Surface the
            # existing structural/source error first so capacity does not mask
            # it. Keep the established default ordering for every other run.
            if self.config.luna_topic_routing_protocol_enabled:
                self._validate_core_sources(core)
            if self.config.core_capacity_limit_enabled:
                limit = self.config.core_max_tokens
                token_count = core_token_count(core)
                if token_count > limit:
                    raise CoreCapacityError(
                        token_count=token_count,
                        limit=limit,
                    )
            if not self.config.luna_topic_routing_protocol_enabled:
                self._validate_core_sources(core)
        return self._synchronize_block_topics(
            parse_topic_tree(self.stage_dir / "topics")
        )

    def _validate_source_reference(self, ref: str) -> None:
        legacy = re.fullmatch(r"D(\d+):(\d+)", ref)
        if legacy:
            conversation, turn = legacy.groups()
            source = self.stage_dir / "sources" / f"D{conversation}.md"
            anchor = f'<a id="d{conversation}-{turn}"></a>'
        else:
            location = self._provider_source_location(ref)
            if location is None:
                raise ValueError(f"invalid source reference: {ref}")
            relative, source_anchor = location
            source = self.stage_dir / relative
            anchor = f'<a id="{source_anchor}"></a>'
        if not source.exists() or anchor not in source.read_text(encoding="utf-8"):
            raise ValueError(f"missing source reference: {ref}")

    def _validate_source_references(
        self, refs: list[str] | tuple[str, ...],
        locations: dict[str, list[str]] | None = None,
    ) -> None:
        """Surface every malformed/missing handle in one rejected candidate."""
        missing: list[str] = []
        invalid: list[str] = []
        for ref in dict.fromkeys(refs):
            try:
                self._validate_source_reference(ref)
            except ValueError as exc:
                if str(exc).startswith("missing source reference:"):
                    missing.append(ref)
                    continue
                if str(exc).startswith("invalid source reference:"):
                    invalid.append(ref)
                    continue
                raise
        errors = []
        def describe(ref: str) -> str:
            places = list(dict.fromkeys((locations or {}).get(ref, [])))
            return ref + (" " + " | ".join(places) if places else "")

        if invalid:
            errors.append("invalid source references (repair all): "
                          + "; ".join(map(describe, invalid)))
        if missing:
            errors.append("missing source references (repair all): "
                          + "; ".join(map(describe, missing)))
        if errors:
            raise ValueError("\n".join(errors))

    def _validate_core_sources(self, core: Path) -> None:
        lines = core.read_text(encoding="utf-8").splitlines()
        definition_lines: dict[str, list[int]] = {}
        for line_number, line in enumerate(lines, 1):
            if match := definition_match(line):
                definition_lines.setdefault(match.group("id"), []).append(line_number)
        duplicate_definitions = [
            f"id={citation_id} file=core.md lines={positions!r} occurrences={len(positions)}"
            for citation_id, positions in definition_lines.items()
            if len(positions) > 1
        ]
        if duplicate_definitions:
            raise ValueError(
                "duplicate Core footnote definitions (repair all): "
                + " | ".join(duplicate_definitions)
            )
        annotations = definitions(lines, source_path="core.md")
        citation_lines: dict[str, list[int]] = {}
        for line_number, line in enumerate(lines, 1):
            if definition_match(line) is None:
                for match in SINGLE_CITATION.finditer(line):
                    citation_lines.setdefault(match.group("id"), []).append(line_number)
        used = set(citation_lines)
        missing = used - set(annotations)
        if missing:
            raise ValueError(
                "undefined Core footnotes (repair all): "
                + " | ".join(
                    f"id={citation_id} file=core.md lines={citation_lines[citation_id]!r}"
                    for citation_id in sorted(missing)
                )
            )
        unused = set(annotations) - used
        if unused:
            raise ValueError(
                "unused Core footnotes (repair all): "
                + " | ".join(
                    f"id={citation_id} file=core.md line={definition_lines[citation_id][0]}"
                    for citation_id in sorted(unused)
                )
            )
        locations: dict[str, list[str]] = {}
        for line_number, line in enumerate(lines, 1):
            if match := definition_match(line):
                for ref in annotations[match.group("id")][1]:
                    locations.setdefault(ref, []).append(
                        f"file=core.md line={line_number} footnote={match.group('id')}"
                    )
        self._validate_source_references(tuple(
            ref for _when, refs, _links in annotations.values() for ref in refs
        ), locations)

    def _uses_block_topic_format(self, units: list[Any]) -> bool:
        if any(unit.evidence for unit in units):
            return True
        suffix = re.compile(r"(?m)\s\^[A-Za-z0-9-]+\s*$")
        for root in (self.stage_dir / "topics", self.memory_dir / "topics"):
            if root.exists() and any(
                suffix.search(path.read_text(encoding="utf-8"))
                for path in root.rglob("*.md")
            ):
                return True
        return False

    def _rewrite_block_links(self, units: list[Any]) -> None:
        """Refresh relative source and block targets after Topic files move."""
        topics = self.stage_dir / "topics"
        target_paths = {unit.memory_id: unit.topic_path for unit in units}
        evidence_by_path = {
            path: {
                annotation.citation_id: annotation
                for unit in units if unit.topic_path == path
                for annotation in unit.evidence
            }
            for path in {unit.topic_path for unit in units}
        }
        block_link = re.compile(
            r"(?P<prefix>\[[^]\n]+\]\()(?P<path>[^)\n#]*)"
            r"#\^(?P<id>[A-Za-z0-9-]+)(?P<suffix>\))"
        )
        for path in sorted(topics.rglob("*.md")):
            relative = path.relative_to(topics).as_posix()
            topic_path = Path("topics") / relative
            annotations = evidence_by_path.get(relative, {})
            rendered = []
            for line in path.read_text(encoding="utf-8").splitlines():
                match = definition_match(line)
                if match and match.group("id") in annotations:
                    annotation = annotations[match.group("id")]
                    labels = re.findall(
                        r"\[([^]]+)\]\([^)]+\)", match.group("sources")
                    )
                    if len(labels) != len(annotation.source_refs):
                        labels = list(annotation.source_refs)
                    line = render_definition(
                        match.group("id"),
                        annotation.when,
                        (
                            self._source_link(topic_path, ref, label)
                            for ref, label in zip(annotation.source_refs, labels)
                        ),
                    )

                def replace_block(match: re.Match[str]) -> str:
                    target = target_paths.get(match.group("id"))
                    if target is None:
                        return match.group(0)
                    relative_target = os.path.relpath(
                        Path("topics") / target, topic_path.parent
                    ).replace(os.sep, "/")
                    return (
                        match.group("prefix")
                        + quote(relative_target, safe="/._-")
                        + "#^"
                        + match.group("id")
                        + match.group("suffix")
                    )

                rendered.append(block_link.sub(replace_block, line))
            normalized = "\n".join(rendered).rstrip() + "\n"
            if normalized != path.read_text(encoding="utf-8"):
                path.write_text(normalized, encoding="utf-8")

    def _synchronize_block_topics(self, units: list[Any]) -> str:
        # Link rendering otherwise raises on the first malformed handle,
        # exhausting repair attempts one bad reference at a time.
        locations: dict[str, list[str]] = {}
        for unit in units:
            for ref in unit.source_refs:
                locations.setdefault(ref, []).append(
                    f"file=topics/{unit.topic_path} block={unit.memory_id}"
                )
        self._validate_source_references(tuple(
            ref for unit in units for ref in unit.source_refs
        ), locations)
        self._rewrite_block_links(units)
        units = parse_topic_tree(self.stage_dir / "topics")
        ids = {unit.memory_id for unit in units}
        for unit in units:
            missing_targets = sorted(set(unit.relation_targets) - ids)
            if missing_targets:
                missing_target = missing_targets[0]
                raise ValueError(
                    f"dangling block link: {missing_target}; "
                    f"source_file=topics/{unit.topic_path}; "
                    f"source_block={unit.memory_id}; "
                    f"source_headings={list(unit.headings)!r}; "
                    f"relation_targets={sorted(set(unit.relation_targets))!r}; "
                    f"missing_targets={missing_targets!r}"
                )

        limit = self.config.recent_limit
        state_store = RuntimeStateStore(self.stage_dir)
        state = state_store.load()
        derived = rebuild_derived_views(
            self.stage_dir,
            units,
            recent_limit=limit,
            creation_order=state.creation_order,
        )
        state.creation_order = derived.creation_order
        state_store.save(state)
        return self._install_staged_state(len(units), "blocks")

    def _install_staged_state(
        self,
        count: int,
        noun: str,
    ) -> str:
        backup = self.memory_dir / f"{runtime_dir(self.memory_dir).name}-block-backup"
        if backup.exists():
            raise RuntimeError(f"Unresolved installation backup requires recovery: {backup}")
        from .source_archive import assert_sources_preserved
        assert_sources_preserved(self.memory_dir, self.stage_dir)
        backup.mkdir()
        replace_retries = 0
        relatives = (
            # Sources are staged by the structured transaction so that new
            # evidence installs together with the topics citing it. The
            # experiment path stages an unmodified copy, so including it here
            # is a no-op there.
            Path("sources"),
            Path("topics"),
            Path("timeline"),
            Path("recent_events.jsonl"),
            Path("relations.json"),
            Path("core.md"),
            Path(runtime_dir(self.memory_dir).name) / "runtime.json",
        )
        moved: list[Path] = []
        installed: list[Path] = []
        cleanup_backup = True
        try:
            for relative in relatives:
                destination = self.memory_dir / relative
                if destination.exists():
                    saved = backup / relative
                    saved.parent.mkdir(parents=True, exist_ok=True)
                    replace_retries += _replace_with_retry(destination, saved)
                    moved.append(relative)
            for relative in relatives:
                source = self.stage_dir / relative
                if not source.exists():
                    continue
                destination = self.memory_dir / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                replace_retries += _replace_with_retry(source, destination)
                installed.append(relative)
        except BaseException:
            # A second failure during rollback must retain the remaining originals.
            cleanup_backup = False
            for relative in reversed(installed):
                destination = self.memory_dir / relative
                if destination.is_dir():
                    shutil.rmtree(destination)
                elif destination.exists():
                    destination.unlink()
            for relative in reversed(moved):
                destination = self.memory_dir / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                replace_retries += _replace_with_retry(
                    backup / relative, destination
                )
            cleanup_backup = True
            raise
        finally:
            if cleanup_backup:
                _rmtree_with_retry(backup, ignore_errors=True)
        self.pending.clear()
        self.committed = True
        self.last_windows_retries = replace_retries
        try:
            self._refresh_stage()
        except Exception as exc:
            raise CommitStateError(
                f"Memory installed but stage refresh failed: {exc}; do not replay Writer",
                commit_state="committed",
            ) from exc
        return f"committed {count} {noun}"
