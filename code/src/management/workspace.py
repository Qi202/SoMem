"""Transactional editable memory workspace."""

import hashlib
import os
import re
import secrets
import shutil
import subprocess
import tempfile
from pathlib import Path

from ..workspace_layout import core_file
from typing import Any
from urllib.parse import unquote

from ..markdown import parse_topic_tree
from ..markdown.parser import scan_topic_contract, topic_format_diagnostics
from ..markdown.syntax import LINK, definition_match, source_reference
from ..shell_backend import resolve_posix_bash
from .block_views import BlockViewsMixin, core_format_diagnostics, core_token_count
from .config import MemoryConfig
from .errors import CoreCapacityError
from .event_writing import EventWritingMixin
from .patching import apply_patch
from .source_archive import SourceArchiveMixin
from .topic_normalization import TopicNormalizationMixin
from .transaction import (
    TransactionError,
    TransactionLimits,
    TransactionResult,
    committed_baseline,
    git_commit_state,
    install_state,
    parse_sources,
    resolve_source_labels,
    source_records,
    workspace_revision,
    workspace_write_lock,
)
from ..workspace_layout import TEMPORARY_PREFIX, is_internal_path, runtime_dir


class MemoryWorkspace(
    TopicNormalizationMixin,
    SourceArchiveMixin,
    EventWritingMixin,
    BlockViewsMixin,
):
    def __init__(
        self,
        memory_dir: str | Path,
        *,
        config: MemoryConfig | None = None,
    ):
        self.memory_dir = Path(memory_dir).resolve()
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        selected_core = core_file(self.memory_dir)
        if not selected_core.exists():
            selected_core.parent.mkdir(parents=True, exist_ok=True)
            selected_core.write_text("# Core\n", encoding="utf-8")
        backup = self.memory_dir / f"{runtime_dir(self.memory_dir).name}-block-backup"
        if backup.exists():
            raise RuntimeError(f"Unresolved installation backup requires recovery: {backup}")
        # Transaction installation uses atomic directory replacement. Windows
        # cannot atomically move a directory from the system temp drive (often
        # C:) into a memory workspace on another drive (for example E:), so the
        # stage must live beside the destination on the same filesystem.
        if os.name == "nt":
            # tempfile.mkdtemp() requests a private Windows ACL. When staged
            # directories are atomically installed, that ACL moves with them
            # and prevents repository monitoring identities from reading the
            # result. A normal mkdir inherits the destination parent's ACL.
            while True:
                candidate = self.memory_dir.parent / (
                    f"{TEMPORARY_PREFIX}topics-{secrets.token_hex(6)}"
                )
                try:
                    candidate.mkdir()
                except FileExistsError:
                    continue
                self.stage_dir = candidate
                break
        else:
            self.stage_dir = Path(tempfile.mkdtemp(
                prefix=f"{TEMPORARY_PREFIX}topics-",
                dir=self.memory_dir.parent,
            ))
        self.pending: dict[str, dict[str, Any]] = {}
        self.config = config or MemoryConfig()
        self.committed = False
        self.last_changed_topics: list[str] = []
        self.last_created_blocks = 0
        self.last_windows_retries = 0
        self.last_validation_diagnostics: tuple[str, ...] = ()
        self.core_edits_allowed = True
        self._refresh_stage()

    def _refresh_stage(self) -> None:
        # Keep the staging root itself stable. On Windows a just-finished Git
        # Bash process can retain a directory handle briefly; removing the root
        # and immediately recreating the same path then raises FileExistsError
        # and masks the validation error that triggered the rollback. Clearing
        # children provides the same transactional reset without that race.
        self.stage_dir.mkdir(parents=True, exist_ok=True)
        for child in self.stage_dir.iterdir():
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
        for name in ("topics", "timeline", "sources"):
            source = self.memory_dir / name
            if source.exists():
                shutil.copytree(source, self.stage_dir / name)
        (self.stage_dir / "topics").mkdir(exist_ok=True)
        recent = self.memory_dir / "recent_events.jsonl"
        if recent.exists():
            shutil.copy2(recent, self.stage_dir / recent.name)
        relations = self.memory_dir / "relations.json"
        if relations.exists():
            shutil.copy2(relations, self.stage_dir / relations.name)
        core = core_file(self.memory_dir)
        if core.exists() and core.parent == self.memory_dir:
            shutil.copy2(core, self.stage_dir / core.name)
        runtime = runtime_dir(self.memory_dir) / "runtime.json"
        staged_runtime = self.stage_dir / runtime.parent.name / "runtime.json"
        staged_runtime.parent.mkdir(parents=True, exist_ok=True)
        if runtime.exists():
            shutil.copy2(runtime, staged_runtime)

    def shell(
        self, command: str, *, allow_correction: bool = False
    ) -> subprocess.CompletedProcess[str]:
        self.last_changed_topics = []
        self.last_created_blocks = 0
        self.last_windows_retries = 0
        before = self._workspace_fingerprint()
        # Built-in Write/Edit calls mutate the stage directly and may leave
        # temporary evidence handles (for example ``D1:1``) that are only
        # expanded at commit time. Parsing that intermediate stage here made
        # even a read-only ``ls`` fail before the shell command could run.
        # Use committed memory as the transaction baseline; if this shell call
        # changes the stage, commit_edits() validates the combined built-in and
        # shell changes atomically below.
        before_units, before_block_ids = committed_baseline(self)
        before_topics = self._topic_fingerprints(self.memory_dir / "topics")
        before_sources = self._tree_fingerprint(self.memory_dir / "sources")
        backend = self.config.shell_backend
        if backend == "auto":
            backend = "posix-bash" if os.name == "nt" else "native"
        if backend == "posix-bash":
            invocation: str | list[str] = [resolve_posix_bash(), "-lc", command]
            use_shell = False
            encoding = "utf-8"
        else:
            invocation = command
            use_shell = True
            encoding = None
        result = subprocess.run(
            invocation,
            cwd=self.stage_dir,
            shell=use_shell,
            text=True,
            encoding=encoding,
            errors="replace",
            capture_output=True,
            timeout=120,
        )
        changed = self._workspace_fingerprint() != before
        if result.returncode != 0 and changed:
            self._refresh_stage()
        elif result.returncode == 0 and changed:
            self.commit_edits(
                before_units, before_block_ids, before_topics, before_sources
            )
        return result

    def baseline(self) -> tuple[list[Any], set[str], dict[str, str], str]:
        """Snapshot the staged tree so an edit can be committed against it."""
        if self.config.unchecked_writer_commits:
            try:
                units = parse_topic_tree(self.stage_dir / "topics", strict=False)
            except ValueError:
                units = scan_topic_contract(self.stage_dir / "topics")
        else:
            units = parse_topic_tree(self.stage_dir / "topics")
        block_ids = {unit.memory_id for unit in units if unit.memory_id}
        core = core_file(self.stage_dir)
        if core.is_file():
            block_ids.update(re.findall(
                r"(?m)\^([A-Za-z0-9-]+)\s*$",
                core.read_text(encoding="utf-8"),
            ))
        return (
            units,
            block_ids,
            self._topic_fingerprints(self.stage_dir / "topics"),
            self._tree_fingerprint(self.stage_dir / "sources"),
        )

    def commit_edits(
        self,
        before_units: list[Any],
        before_block_ids: set[str],
        before_topics: dict[str, str],
        before_sources: str,
        *,
        preserve_rejected: bool = False,
    ) -> None:
        """Assign IDs, validate, rebuild derived views, and install.

        Called per shell command, and once after an agent turn that edited
        the stage through the built-in file tools. A Core-only capacity
        rejection keeps the otherwise valid staged edits so a specialized
        repair can reduce only the excess. Other failures discard the stage
        unless the agent requests preservation for in-place repair. That keeps
        normalized IDs and diagnostic locations aligned with the repair input.
        """
        self.last_changed_topics = []
        self.last_created_blocks = 0
        self.last_validation_diagnostics = ()
        normalized = False
        if self.config.unchecked_writer_commits:
            # Diagnostic-only path: keep the Writer's raw prose even when its
            # citations or IDs cannot be parsed. Never promote this run as a
            # validated memory build.
            warnings: list[str] = []
            try:
                self._normalize_topic_edits(before_block_ids)
            except ValueError as exc:
                warnings.append(f"normalization: {exc}")
            after_topics = self._topic_fingerprints(self.stage_dir / "topics")
            self.last_changed_topics = [
                "topics/" + path
                for path in sorted(set(before_topics) | set(after_topics))
                if before_topics.get(path) != after_topics.get(path)
            ]
            try:
                self._synchronize()
            except (ValueError, CoreCapacityError) as exc:
                warnings.append(f"validation: {exc}")
                observed = scan_topic_contract(self.stage_dir / "topics")
                self.last_created_blocks = len({
                    unit.memory_id for unit in observed if unit.memory_id
                } - before_block_ids)
                self._install_staged_state(len(observed), "unchecked blocks")
            self.last_validation_diagnostics = tuple(warnings)
            return
        try:
            if self._tree_fingerprint(self.stage_dir / "sources") != before_sources:
                committed_sources = self._file_fingerprints(self.memory_dir / "sources")
                staged_sources = self._file_fingerprints(self.stage_dir / "sources")
                changed = sorted(
                    path for path in set(committed_sources) | set(staged_sources)
                    if committed_sources.get(path) != staged_sources.get(path)
                )
                raise ValueError(
                    "Source Memory is append-only; changed files (repair all): "
                    + "; ".join(f"sources/{path}" for path in changed)
                )
            if not self.core_edits_allowed:
                staged_core = core_file(self.stage_dir)
                committed_core = core_file(self.memory_dir)
                staged_bytes = (
                    staged_core.read_bytes() if staged_core.is_file() else None
                )
                committed_bytes = (
                    committed_core.read_bytes()
                    if committed_core.is_file() else None
                )
                if staged_bytes != committed_bytes:
                    raise ValueError(
                        "Core Memory is read-only during Luna Topic-routing "
                        "repair; file=core.md"
                    )
            self._normalize_topic_edits(before_block_ids)
            normalized = True
            self._validate_topic_contract(before_units, before_block_ids)
            after_units = parse_topic_tree(self.stage_dir / "topics")
            after_topics = self._topic_fingerprints(self.stage_dir / "topics")
            changed_topics = [
                "topics/" + path
                for path in sorted(set(before_topics) | set(after_topics))
                if before_topics.get(path) != after_topics.get(path)
            ]
            created_blocks = len(
                {unit.memory_id for unit in after_units}
                - {unit.memory_id for unit in before_units}
            )
            self._synchronize()
            self.last_changed_topics = changed_topics
            self.last_created_blocks = created_blocks
        except CoreCapacityError:
            if self.config.validation_diagnostic_aggregation_enabled:
                try:
                    self.last_validation_diagnostics = (
                        self._collect_validation_diagnostics(before_units, before_block_ids, normalized=normalized)
                    )
                except Exception:
                    self.last_validation_diagnostics = ()
            raise
        except (OSError, RuntimeError):
            # Infrastructure errors must retain the stage and installation evidence.
            raise
        except Exception:
            if self.config.validation_diagnostic_aggregation_enabled:
                try:
                    self.last_validation_diagnostics = (
                        self._collect_validation_diagnostics(before_units, before_block_ids, normalized=normalized)
                    )
                except Exception:
                    # Diagnostic failure must never replace the authoritative rejection.
                    self.last_validation_diagnostics = ()
            if not preserve_rejected:
                self._refresh_stage()
            raise

    def _collect_validation_diagnostics(
        self, before_units: list[Any], before_block_ids: set[str], *, normalized: bool = True
    ) -> tuple[str, ...]:
        """Read-only, best-effort checks of all independently detectable defects."""
        findings: list[str] = []
        committed_sources = self._file_fingerprints(self.memory_dir / "sources")
        staged_sources = self._file_fingerprints(self.stage_dir / "sources")
        changed_sources = sorted(
            path for path in set(committed_sources) | set(staged_sources)
            if committed_sources.get(path) != staged_sources.get(path)
        )
        if changed_sources:
            findings.append(
                "Sources: Source Memory is append-only; changed files (repair all): "
                + "; ".join(f"sources/{path}" for path in changed_sources)
            )
        core = core_file(self.stage_dir)
        if not self.core_edits_allowed:
            committed_core = core_file(self.memory_dir)
            if (core.read_bytes() if core.is_file() else None) != (
                committed_core.read_bytes() if committed_core.is_file() else None
            ):
                findings.append(
                    "Core: Core Memory is read-only during Luna Topic-routing "
                    "repair; file=core.md"
                )
        if core.is_file():
            findings.extend(f"Core: {item}" for item in core_format_diagnostics(core, normalized=normalized))
            if self.config.core_capacity_limit_enabled:
                token_count = core_token_count(core)
                if token_count > self.config.core_max_tokens:
                    findings.append(
                        "Core: Core Memory exceeds "
                        f"{self.config.core_max_tokens} tokens: {token_count}"
                    )
            try:
                if normalized:
                    self._validate_core_sources(core)
            except ValueError as exc:
                if f"Core: {exc}" not in findings:
                    findings.append(f"Core: {exc}")
        # Source handles can be checked directly from well-formed definitions,
        # even when duplicate footnotes prevent a full Topic parse. This also
        # catches Core sources hidden behind an earlier Core structural error.
        refs: list[str] = []
        locations: dict[str, list[str]] = {}
        if core.is_file():
            for line_number, line in enumerate(
                core.read_text(encoding="utf-8").splitlines(), 1
            ):
                if match := definition_match(line):
                    for label, target in LINK.findall(match.group("sources")):
                        ref = source_reference(label, target)
                        refs.append(ref)
                        locations.setdefault(ref, []).append(
                            f"file=core.md line={line_number} footnote={match.group('id')}"
                        )
        topics = self.stage_dir / "topics"
        for path in sorted(topics.rglob("*.md")):
            relative = path.relative_to(topics).as_posix()
            for line_number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1
            ):
                if match := definition_match(line):
                    for label, target in LINK.findall(match.group("sources")):
                        ref = source_reference(label, target)
                        refs.append(ref)
                        locations.setdefault(ref, []).append(
                            f"file=topics/{relative} line={line_number} footnote={match.group('id')}"
                        )
        if refs:
            try:
                self._validate_source_references(refs, locations)
            except ValueError as exc:
                findings.append(f"Sources: {exc}")
        findings.extend(
            f"Topic format: {item}" for item in topic_format_diagnostics(topics, normalized=normalized)
        )
        if not normalized:
            findings.append(
                "Pending normalization: Runtime-assigned IDs and rendered source links "
                "have not been finalized. Do not invent IDs or treat bare source handles "
                "as missing links. Duplicate local labels also await normalization. "
                "Strict validation runs after normalization succeeds."
            )
            findings.extend(self._topic_contract_diagnostics(
                scan_topic_contract(topics), before_units, before_block_ids,
            ))
            return tuple(dict.fromkeys(findings))
        try:
            units = parse_topic_tree(topics)
        except ValueError as exc:
            if f"Topic format: {exc}" not in findings:
                findings.append(f"Topic format: {exc}")
            # Evidence syntax must not gate ID preservation or link checks.
            # These observations are diagnostic only, never committed units.
            findings.extend(self._topic_contract_diagnostics(
                scan_topic_contract(topics), before_units, before_block_ids,
            ))
        else:
            findings.extend(self._topic_contract_diagnostics(
                units, before_units, before_block_ids,
            ))
            try:
                self._validate_topic_contract(before_units, before_block_ids)
            except ValueError as exc:
                if f"Topic contract: {exc}" not in findings:
                    findings.append(f"Topic contract: {exc}")
        return tuple(findings)

    def _topic_contract_diagnostics(
        self,
        units: list[Any],
        before_units: list[Any],
        before_block_ids: set[str],
    ) -> tuple[str, ...]:
        """List all independently checkable cross-file Topic defects."""
        findings: list[str] = []
        before = {unit.memory_id: unit for unit in before_units}
        surviving = {unit.memory_id for unit in units if unit.memory_id is not None}
        core = core_file(self.stage_dir)
        if core.is_file():
            surviving.update(re.findall(
                r"(?m)\^([A-Za-z0-9-]+)\s*$",
                core.read_text(encoding="utf-8"),
            ))
        lost = sorted(before_block_ids - surviving)
        if lost:
            locations = [
                f"id={block_id} file=topics/{before[block_id].topic_path}"
                for block_id in lost if block_id in before
            ]
            findings.append(
                "Topic contract: block ID must not be removed (repair all): "
                + ", ".join(lost)
                + ("; original_locations: " + " | ".join(locations)
                   if locations else "")
            )

        invalid_links: list[str] = []
        dangling_links: list[str] = []
        ids = {unit.memory_id for unit in units if unit.memory_id is not None}
        for unit in units:
            previous = before.get(unit.memory_id)
            line = getattr(unit, "line_number", None)
            location = f"file=topics/{unit.topic_path}"
            if line is not None:
                location += f" line={line}"
            location += f" block={unit.memory_id or '<unresolved>'}"
            if unit.evidence is None and previous is not None and unit.content == previous.content:
                findings.append(
                    "Topic diagnostic coverage: evidence-change comparison unavailable; "
                    f"{location}; fix evidence syntax and recheck"
                )
            if (previous is None or unit.content != previous.content
                    or (unit.evidence is not None and unit.evidence != previous.evidence)):
                for target in re.findall(r"\[[^]\n]+\]\(([^)\n]+)\)", unit.content):
                    path, separator, fragment = target.partition("#")
                    if (
                        not path or Path(path).is_absolute()
                        or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", path)
                    ):
                        continue
                    relative = Path(os.path.normpath(
                        str(Path(unit.topic_path).parent / unquote(path))
                    ))
                    if (
                        relative.suffix.lower() == ".md"
                        and ".." not in relative.parts
                        and (
                            not separator
                            or re.fullmatch(r"\^[A-Za-z0-9-]+", fragment) is None
                        )
                    ):
                        invalid_links.append(
                            f"{location} target={target}"
                        )
            for target in sorted(set(unit.relation_targets) - ids):
                dangling_links.append(
                    f"target={target} source_file=topics/{unit.topic_path} "
                    f"source_block={unit.memory_id or '<unresolved>'}"
                    + (f" line={line}" if line is not None else "")
                )
        if invalid_links:
            findings.append(
                "Topic contract: Topic-to-Topic links must target #^block-id "
                "(repair all): " + " | ".join(invalid_links)
            )
        if dangling_links:
            findings.append(
                "Topic contract: dangling block links (repair all): "
                + " | ".join(dangling_links)
            )
        return tuple(findings)

    def stage_is_dirty(self) -> bool:
        """Whether the stage differs from what is committed on disk."""
        staged = {
            path.relative_to(self.stage_dir).as_posix(): path.read_bytes()
            for path in sorted(self.stage_dir.rglob("*"))
            if path.is_file()
            and not is_internal_path(path.relative_to(self.stage_dir))
        }
        committed = {
            path.relative_to(self.memory_dir).as_posix(): path.read_bytes()
            for path in sorted(self.memory_dir.rglob("*"))
            if path.is_file()
            and not is_internal_path(path.relative_to(self.memory_dir))
        }
        return staged != committed

    def revision(self) -> str:
        """Fingerprint of the committed workspace, for optimistic concurrency."""
        return workspace_revision(self.memory_dir)

    def update(
        self,
        *,
        base_revision: str,
        patch: str,
        sources: Any = None,
        commit_message: str | None = None,
        git_commit: str = "auto",
        limits: TransactionLimits | None = None,
    ) -> TransactionResult:
        """Apply sources and a topic patch as one atomic transaction.

        Sources are archived into the stage rather than ``memory_dir`` so that
        evidence and the topics citing it become visible in the same install.
        """
        if git_commit not in ("auto", "on", "off"):
            raise TransactionError(
                "INVALID_ARGUMENT", "git_commit must be auto, on or off"
            )
        limits = limits or TransactionLimits()
        if len(patch.encode("utf-8")) > limits.max_patch_bytes:
            raise TransactionError(
                "INVALID_ARGUMENT",
                f"patch exceeds {limits.max_patch_bytes} bytes",
            )
        message = (commit_message or "Update memory").replace("\n", " ").strip()
        message = "".join(
            char for char in message if char.isprintable()
        )[:limits.max_commit_message_chars] or "Update memory"
        parsed_sources = parse_sources(sources, limits)

        with workspace_write_lock(self.memory_dir):
            current = workspace_revision(self.memory_dir)
            if base_revision != current:
                raise TransactionError(
                    "CONCURRENT_UPDATE",
                    "workspace changed since base_revision was read",
                    details={"base_revision": base_revision, "revision": current},
                )
            self._refresh_stage()
            before_files = self._committed_files()
            before_units, before_block_ids = committed_baseline(self)
            try:
                records = source_records(parsed_sources)
                mapping = {
                    item.label: record.source_id
                    for item, record in zip(parsed_sources, records)
                }
                if records:
                    self.archive_source_records(records, root=self.stage_dir)
                resolved = resolve_source_labels(patch, mapping)
                changed = apply_patch(self.stage_dir, resolved)
                install_state(self, before_units, before_block_ids)
            except TransactionError:
                self._refresh_stage()
                raise
            except Exception as exc:
                self._refresh_stage()
                raise TransactionError(
                    "INVALID_TOPIC_FORMAT", str(exc)
                ) from exc

            after_files = self._committed_files()
            changed_files = sorted(
                set(changed)
                | {
                    path
                    for path in set(before_files) | set(after_files)
                    if before_files.get(path) != after_files.get(path)
                }
            )
            result = TransactionResult(
                revision=workspace_revision(self.memory_dir),
                source_ids=mapping,
                block_ids=dict(getattr(self, "last_block_id_map", {}) or {}),
                evidence_ids=dict(
                    getattr(self, "last_evidence_id_map", {}) or {}
                ),
                changed_files=changed_files,
            )
            if git_commit == "off":
                return result
            try:
                commit = git_commit_state(self.memory_dir, message)
            except Exception as exc:
                if git_commit == "on":
                    raise TransactionError(
                        "GIT_COMMIT_FAILED",
                        # Files are already installed; a failed commit does not
                        # undo them and must not be reported as a rollback.
                        f"memory committed but git commit failed: {exc}",
                        details={"memory_committed": True, "git_committed": False},
                    ) from exc
                return result
            if commit is None and git_commit == "on":
                raise TransactionError(
                    "GIT_COMMIT_FAILED",
                    "memory committed but workspace is not a git repository",
                    details={"memory_committed": True, "git_committed": False},
                )
            result.git_commit = commit
            result.git_committed = commit is not None
            result.revision = workspace_revision(self.memory_dir)
            return result

    def _committed_files(self) -> dict[str, str]:
        root = self.memory_dir
        result = {}
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            relative = path.relative_to(root)
            if is_internal_path(relative):
                continue
            result[relative.as_posix()] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
        return result

    def _workspace_fingerprint(self) -> str:
        digest = hashlib.sha256()
        for path in sorted(self.stage_dir.rglob("*")):
            if path.is_file():
                digest.update(path.relative_to(self.stage_dir).as_posix().encode())
                digest.update(path.read_bytes())
        return digest.hexdigest()

    def structure(self) -> str:
        """List the workspace, with each Topic file's headings and block IDs.

        A bare path list tells the writer nothing about what a file already
        holds, so it reads every file before editing any of them. Showing the
        headings and IDs up front answers that question without a shell call.
        """
        lines = []
        topics = self.stage_dir / "topics"
        for path in sorted(self.stage_dir.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(self.stage_dir)
            if is_internal_path(relative):
                continue
            lines.append(relative.as_posix())
            if path.suffix != ".md" or (
                topics not in path.parents and path.name != "core.md"
            ):
                continue
            heading = ""
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.startswith("#"):
                    heading = line.strip()
                    continue
                block = re.search(r"\s\^([A-Za-z0-9-]+)\s*$", line)
                if not block:
                    continue
                summary = re.sub(r"\[\^[^]]+\]", "", line[: block.start()])
                summary = " ".join(summary.split())
                if len(summary) > 90:
                    summary = summary[:87] + "..."
                lines.append(
                    f"    ^{block.group(1)}"
                    + (f"  [{heading.lstrip('# ')}]" if heading else "")
                    + f"  {summary}"
                )
                heading = ""
        return "\n".join(lines) or "(empty workspace)"
