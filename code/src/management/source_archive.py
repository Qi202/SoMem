"""Append-only source archive and stable source links."""

import hashlib
import os
import re
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import quote

from ..runtime.state import SourceRecord


class SourceIntegrityError(RuntimeError):
    """An archive cannot substantiate the recorded build state."""


def atomic_source_write(path: Path, text: str) -> None:
    # A killed append must leave either the previous or the complete new file.
    fd, name = tempfile.mkstemp(prefix=".source-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def assert_sources_preserved(committed: Path, staged: Path) -> None:
    """Source archives are append-only; reject loss/change before any rename."""
    for old in (committed / "sources").rglob("*.md"):
        new = staged / old.relative_to(committed)
        previous = old.read_text(encoding="utf-8").rstrip()
        candidate = new.read_text(encoding="utf-8") if new.is_file() else ""
        if not new.is_file() or not (not previous or candidate == previous or
                candidate.startswith(previous + "\n")):
            raise SourceIntegrityError(f"source archive removed or changed: {old.name}")


def assert_session_sources(root: Path, sessions: list[dict[str, Any]]) -> None:
    """Verify exact rendered records, not merely the presence of source IDs."""
    cache: dict[Path, str] = {}
    parsed: dict[Path, set[str]] = {}
    errors = []
    for session in sessions:
        turns, refs = session.get("turns", []), session.get("refs", [])
        if len(turns) != len(refs):
            raise SourceIntegrityError("source turns and refs have different lengths")
        date = str(session.get("observation_date", ""))
        for (speaker, content), ref in zip(turns, refs):
            ref = str(ref)
            legacy = re.fullmatch(r"D(\d+):(\d+)", ref)
            if legacy:
                relative = Path("sources") / f"D{legacy.group(1)}.md"
                anchor = ref.lower().replace(":", "-")
                expected = f'<a id="{anchor}"></a>\n[{date}] {speaker}: {content} [{ref}]'
            else:
                location = SourceArchiveMixin._provider_source_location(ref)
                if location is None:
                    raise SourceIntegrityError(f"invalid source ID: {ref}")
                relative, anchor = location
                expected = (f'<a id="{anchor}"></a>\n<!-- source-id:{ref} -->\n'
                            f'[{date}] {speaker}: {content}')
            if relative not in cache:
                path = root / relative
                cache[relative] = path.read_text(encoding="utf-8") if path.is_file() else ""
            # Record boundary prevents accepting truncated/changed content prefixes.
            if relative not in parsed:
                parsed[relative] = {
                    ('<a id="' + record).rstrip()
                    for record in re.split(r'(?m)^<a id="', cache[relative])[1:]
                }
            # read_text applies universal-newline decoding. Compare against the
            # same representation without rewriting archives or relaxing text checks.
            expected = expected.replace("\r\n", "\n").replace("\r", "\n")
            if expected.rstrip() not in parsed[relative]:
                errors.append(ref)
    if errors:
        raise SourceIntegrityError(
            f"missing or changed source records ({len(errors)}): " + ", ".join(errors[:8]))


class SourceArchiveMixin:
    def archive_sessions(self, sessions: list[dict[str, Any]]) -> None:
        grouped: dict[str, list[tuple[str, str, str]]] = {}
        provider_records: list[SourceRecord] = []
        for session in sessions:
            date = str(session.get("observation_date", ""))
            for ordinal, ((speaker, content), raw_ref) in enumerate(
                zip(session.get("turns", []), session.get("refs", [])),
                start=1,
            ):
                ref = str(raw_ref)
                match = re.fullmatch(r"D(\d+):(\d+)", ref)
                if match:
                    grouped.setdefault(f"D{match.group(1)}", []).append(
                        (ref, date, f"{speaker}: {content}")
                    )
                    continue
                if self._provider_source_location(ref) is None:
                    raise ValueError(
                        "refs must be D18:11 or provider/thread/message IDs"
                    )
                provider, thread_id, message_id = ref.split("/", 2)
                provider_records.append(SourceRecord(
                    provider=provider,
                    thread_id=thread_id,
                    message_id=message_id,
                    ordinal=ordinal,
                    role=str(speaker),
                    content=str(content),
                    timestamp=date or None,
                ))
        source_dir = self.memory_dir / "sources"
        source_dir.mkdir(exist_ok=True)
        for conversation, rows in grouped.items():
            path = source_dir / f"{conversation}.md"
            existing = path.read_text(encoding="utf-8") if path.exists() else ""
            known = set(re.findall(r'<a id="(d\d+-\d+)"></a>', existing))
            additions = []
            for ref, date, content in rows:
                anchor = ref.lower().replace(":", "-")
                if anchor not in known:
                    additions.append(
                        f'<a id="{anchor}"></a>\n[{date}] {content} [{ref}]'
                    )
                    known.add(anchor)
            if additions:
                body = existing.rstrip()
                atomic_source_write(path,
                    (body + "\n\n" if body else f"# {conversation}\n\n")
                    + "\n\n".join(additions)
                    + "\n",
                )
        if provider_records:
            self.archive_source_records(provider_records)
        assert_session_sources(self.memory_dir, sessions)
        self._refresh_stage()

    @staticmethod
    def _provider_source_location(ref: str) -> tuple[Path, str] | None:
        parts = ref.split("/", 2)
        if len(parts) != 3 or any(not part for part in parts):
            return None
        provider, thread_id, _message_id = parts
        path = Path("sources") / quote(provider, safe="-_.") / (
            quote(thread_id, safe="-_.") + ".md"
        )
        anchor = "source-" + hashlib.sha256(ref.encode()).hexdigest()[:16]
        return path, anchor

    @staticmethod
    def _source_link(
        topic_path: Path, ref: str, label: str | None = None
    ) -> str:
        legacy = re.fullmatch(r"D(\d+):(\d+)", ref)
        if legacy:
            conversation, turn = legacy.groups()
            target = Path("sources") / f"D{conversation}.md"
            anchor = f"d{conversation}-{turn}"
        else:
            location = SourceArchiveMixin._provider_source_location(ref)
            if location is None:
                raise ValueError(f"invalid source reference: {ref}")
            target, anchor = location
        relative = os.path.relpath(target, topic_path.parent).replace(
            os.sep, "/"
        )
        return f"[{label or ref}]({relative}#{anchor})"

    def archive_source_records(
        self,
        records: list[SourceRecord],
        *,
        root: Path | None = None,
    ) -> list[str]:
        """Append records to the source tree.

        Writes into ``memory_dir`` and refreshes the stage by default. Pass
        ``root=self.stage_dir`` to archive inside an in-progress transaction
        so sources are installed atomically with the topics that cite them.
        """
        target_root = self.memory_dir if root is None else Path(root)
        grouped: dict[Path, list[SourceRecord]] = {}
        for record in sorted(
            records,
            key=lambda value: (
                value.provider,
                value.thread_id,
                value.ordinal,
            ),
        ):
            location = self._provider_source_location(record.source_id)
            if location is None:
                raise ValueError(
                    "provider source IDs require provider/thread/message"
                )
            grouped.setdefault(location[0], []).append(record)
        refs = []
        for relative, rows in grouped.items():
            path = target_root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            text = path.read_text(encoding="utf-8") if path.exists() else ""
            known = set(re.findall(r"<!-- source-id:([^>]+) -->", text))
            additions = []
            for record in rows:
                refs.append(record.source_id)
                if record.source_id in known:
                    continue
                _target, anchor = self._provider_source_location(record.source_id)
                additions.extend([
                    f'<a id="{anchor}"></a>',
                    f"<!-- source-id:{record.source_id} -->",
                    f"[{record.timestamp or ''}] {record.role}: {record.content}",
                    "",
                ])
                known.add(record.source_id)
            if additions:
                body = text.rstrip()
                atomic_source_write(path,
                    (body + "\n\n" if body else f"# {rows[0].thread_id}\n\n")
                    + "\n".join(additions).rstrip()
                    + "\n",
                )
        if root is None:
            self._refresh_stage()
        return refs
