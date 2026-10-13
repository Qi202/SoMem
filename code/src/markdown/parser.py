"""Convert Topic Markdown files into stable memory units."""

import re
from dataclasses import dataclass
from pathlib import Path

from .models import (
    EvidenceAnnotation,
    MEMORY_ID,
    MemoryUnit,
    TopicFormatError,
    is_valid_temporal_value,
)
from .syntax import (
    ANY_BLOCK_SUFFIX,
    BLOCK_LINK,
    BLOCK_SUFFIX,
    CITATION_GROUP,
    LINK,
    SINGLE_CITATION,
    definitions,
    definition_match,
    paragraphs,
)


_FOOTNOTE_DEFINITION_LIKE = re.compile(r"^\s*\[\^[^\]\s]+\]:")


def _malformed_footnote_definitions(topics: Path) -> list[str]:
    """List every definition-looking line that violates the Topic contract."""
    malformed = []
    for path in sorted(topics.rglob("*.md")):
        relative = path.relative_to(topics).as_posix()
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if _FOOTNOTE_DEFINITION_LIKE.match(line) and not definition_match(line):
                excerpt = " ".join(line.split())[:180]
                malformed.append(f"{relative}:{line_number}={excerpt!r}")
    return malformed


def _duplicate_footnote_definitions(topics: Path) -> list[str]:
    duplicates = []
    for path in sorted(topics.rglob("*.md")):
        locations: dict[str, list[int]] = {}
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if match := definition_match(line):
                locations.setdefault(match.group("id"), []).append(line_number)
        for citation_id, lines in locations.items():
            if len(lines) > 1:
                duplicates.append(
                    f"id={citation_id} file={path.relative_to(topics).as_posix()} "
                    f"lines={lines!r} occurrences={len(lines)}"
                )
    return duplicates


def _definition_line(lines: list[str], citation_id: str) -> int | None:
    return next(
        (number for number, line in enumerate(lines, 1)
         if (match := definition_match(line)) and match.group("id") == citation_id),
        None,
    )


def _trailing_content_errors(topics: Path) -> list[str]:
    """Find every block paragraph with prose or placeholders after its last citation."""
    errors = []
    for path in sorted(topics.rglob("*.md")):
        relative = path.relative_to(topics).as_posix()
        for paragraph, _headings, line_number in paragraphs(
            path.read_text(encoding="utf-8").splitlines(), with_line_numbers=True,
        ):
            block = BLOCK_SUFFIX.search(paragraph)
            if block is None:
                continue
            body = paragraph[:block.start()].rstrip()
            citations = list(CITATION_GROUP.finditer(body))
            if not citations:
                continue
            trailing = body[citations[-1].end():].strip()
            if trailing:
                excerpt = " ".join(trailing.split())[:120]
                errors.append(
                    f"block={block.group('id')}; file={relative}; line={line_number}; "
                    f"trailing_excerpt={excerpt!r}; trailing_chars={len(trailing)}"
                )
    return errors


def _undefined_footnote_citations(topics: Path) -> list[str]:
    """Enumerate missing Topic definitions without mutating or parsing the tree."""
    missing = []
    for path in sorted(topics.rglob("*.md")):
        lines = path.read_text(encoding="utf-8").splitlines()
        defined = {
            match.group("id")
            for line in lines
            if (match := definition_match(line)) is not None
        }
        relative = path.relative_to(topics).as_posix()
        for paragraph, _headings, line_number in paragraphs(
            lines, with_line_numbers=True,
        ):
            block = BLOCK_SUFFIX.search(paragraph)
            body = paragraph[:block.start()] if block else paragraph
            seen_in_paragraph: set[str] = set()
            for citation in SINGLE_CITATION.finditer(body):
                citation_id = citation.group("id")
                # Legacy paragraphs only interpret canonical memory IDs as
                # citations; other labels fail the block-ID check instead.
                if (not block and not re.fullmatch(MEMORY_ID, citation_id)):
                    continue
                if citation_id in defined or citation_id in seen_in_paragraph:
                    continue
                seen_in_paragraph.add(citation_id)
                location = line_number + body.count("\n", 0, citation.start())
                missing.append(
                    f"id={citation_id} file={relative} line={location}"
                    + (f" block={block.group('id')}" if block else "")
                )
    return missing


def _independent_topic_errors(topics: Path, *, normalized: bool = True) -> dict[str, list[str]]:
    """Collect block and definition defects even when full parsing is blocked."""
    findings: dict[str, list[str]] = {
        "invalid evidence dates": [],
        "memory source links required": [],
        "invalid block_ids": [],
        "memory block IDs required": [],
        "missing memory content": [],
        "memory source footnotes required": [],
        "duplicate memory_ids": [],
        "unused footnote definitions": [],
    }
    seen_ids: dict[str, str] = {}
    for path in sorted(topics.rglob("*.md")):
        lines = path.read_text(encoding="utf-8").splitlines()
        relative = path.relative_to(topics).as_posix()
        defined: dict[str, int] = {}
        for number, line in enumerate(lines, 1):
            match = definition_match(line)
            if match is None:
                continue
            citation_id = match.group("id")
            defined.setdefault(citation_id, number)
            when = match.group("when")
            if when != "undated" and not is_valid_temporal_value(when):
                findings["invalid evidence dates"].append(
                    f"date={when} file={relative} line={number} footnote={citation_id}"
                )
            if normalized and not LINK.search(match.group("sources")):
                findings["memory source links required"].append(
                    f"id={citation_id} file={relative} line={number}"
                )

        used: set[str] = set()
        for paragraph, _headings, line_number in paragraphs(
            lines, with_line_numbers=True,
        ):
            if _FOOTNOTE_DEFINITION_LIKE.match(paragraph):
                # Malformed definitions have their own diagnostic category.
                continue
            block = BLOCK_SUFFIX.search(paragraph)
            any_block = ANY_BLOCK_SUFFIX.search(paragraph)
            if any_block and not block:
                findings["invalid block_ids"].append(
                    f"id={any_block.group('id')} file={relative} line={line_number}"
                )
                continue
            body = paragraph[:block.start()] if block else paragraph
            if block:
                ids = re.findall(r"\^([A-Za-z0-9-]+)", paragraph[block.start():])
                for memory_id in ids:
                    location = f"{relative}:{line_number}"
                    if memory_id in seen_ids:
                        findings["duplicate memory_ids"].append(
                            f"id={memory_id} first={seen_ids[memory_id]} "
                            f"duplicate={location}"
                        )
                    else:
                        seen_ids[memory_id] = location

            groups = list(CITATION_GROUP.finditer(body))
            if block and not groups:
                findings["memory source footnotes required"].append(
                    f"block={ids[-1]} file={relative} line={line_number}"
                )
            cursor = 0
            legacy_ids: list[str] = []
            for group in groups:
                citation_ids = [
                    match.group("id")
                    for match in SINGLE_CITATION.finditer(group.group(0))
                ]
                if normalized and not block and any(
                    not re.fullmatch(MEMORY_ID, value) for value in citation_ids
                ):
                    continue
                if not body[cursor:group.start()].strip():
                    citation_line = line_number + body.count("\n", 0, group.start())
                    findings["missing memory content"].append(
                        f"footnote={citation_ids[0]} file={relative} "
                        f"line={citation_line}"
                    )
                if not block and normalized:
                    legacy_ids.extend(citation_ids)
                used.update(value for value in citation_ids if value in defined)
                cursor = group.end()
            if not block:
                if normalized and not legacy_ids:
                    excerpt = " ".join(paragraph.split())[:180]
                    findings["memory block IDs required"].append(
                        f"file={relative} line={line_number} paragraph={excerpt!r}"
                    )
                for memory_id in legacy_ids:
                    location = f"{relative}:{line_number}"
                    if memory_id in seen_ids:
                        findings["duplicate memory_ids"].append(
                            f"id={memory_id} first={seen_ids[memory_id]} "
                            f"duplicate={location}"
                        )
                    else:
                        seen_ids[memory_id] = location

        for citation_id in sorted(set(defined) - used):
            findings["unused footnote definitions"].append(
                f"id={citation_id} file={relative} line={defined[citation_id]}"
            )
    return findings


def topic_format_diagnostics(topics: Path, *, normalized: bool = True) -> tuple[str, ...]:
    """Report independent Topic format defects without stopping at the first kind."""
    findings: list[str] = []
    if malformed := _malformed_footnote_definitions(topics):
        findings.append("malformed footnote definitions (repair all): " + "; ".join(malformed))
    if duplicates := _duplicate_footnote_definitions(topics):
        findings.append(("" if normalized else "Pending normalization: ")
                        + "duplicate footnote definitions (repair all): " + " | ".join(duplicates))
    if trailing := _trailing_content_errors(topics):
        findings.append("content after final evidence (repair all): " + " | ".join(trailing))
    if undefined := _undefined_footnote_citations(topics):
        findings.append("undefined Topic footnotes (repair all): " + " | ".join(undefined))
    for category, errors in _independent_topic_errors(topics, normalized=normalized).items():
        if errors:
            findings.append(f"{category} (repair all): " + " | ".join(errors))
    return tuple(findings)


@dataclass(frozen=True)
class TopicDiagnosticUnit:
    """Syntax-only observation; never a validated/committable MemoryUnit."""

    memory_id: str | None
    content: str
    topic_path: str
    line_number: int
    relation_targets: tuple[str, ...]
    # None means evidence cannot be interpreted, not an empty evidence list.
    evidence: tuple[EvidenceAnnotation, ...] | None = None


def scan_topic_contract(topics: Path) -> list[TopicDiagnosticUnit]:
    """Recover IDs and links independently of evidence validity, for diagnostics.

    Each paragraph is a recovery boundary. Preserve aliases and duplicates,
    never guess IDs, and retain anonymous paragraphs so their links are checked.
    Strict parse_topic_tree remains the only source of committable units.
    """
    result: list[TopicDiagnosticUnit] = []
    for path in sorted(topics.rglob("*.md")):
        relative = path.relative_to(topics).as_posix()
        lines = path.read_text(encoding="utf-8").splitlines()
        known: dict = {}
        seen_definitions: set[str] = set()
        ambiguous: set[str] = set()
        for line in lines:
            match = definition_match(line)
            if match is None:
                continue
            citation_id = match.group("id")
            if citation_id in seen_definitions:
                ambiguous.add(citation_id)
            seen_definitions.add(citation_id)
            try:
                known.update(definitions([line]))
            except TopicFormatError:
                ambiguous.add(citation_id)
        for citation_id in ambiguous:
            known.pop(citation_id, None)
        # Preserve line numbers while preventing malformed definitions from
        # swallowing adjacent prose or masquerading as memory/Topic links.
        prose_lines = [
            "" if _FOOTNOTE_DEFINITION_LIKE.match(line) else line
            for line in lines
        ]
        for paragraph, headings, line_number in paragraphs(
            prose_lines, with_line_numbers=True,
        ):
            block = BLOCK_SUFFIX.search(paragraph)
            body = paragraph[:block.start()].rstrip() if block else paragraph
            if block:
                ids = re.findall(r"\^([A-Za-z0-9-]+)", paragraph[block.start():])
                evidence = None
                try:
                    parsed = _block_unit(
                        paragraph, headings, path, topics, known, 0,
                        line_number, strict=True,
                    )
                    if parsed:
                        evidence = parsed[0][0].evidence
                except TopicFormatError:
                    pass  # Identity and links do not depend on footnote parsing.
                content = SINGLE_CITATION.sub("", body).strip()
                targets = tuple(m.group("id") for m in BLOCK_LINK.finditer(body))
                result.extend(TopicDiagnosticUnit(
                    value, content, relative, line_number, targets, evidence,
                ) for value in ids)
                continue
            # Legacy IDs belong to citation groups, not every footnote mention.
            cursor = 0
            legacy_found = False
            if not ANY_BLOCK_SUFFIX.search(paragraph):
                for group in CITATION_GROUP.finditer(paragraph):
                    ids = [m.group("id") for m in SINGLE_CITATION.finditer(group.group(0))]
                    if any(not re.fullmatch(MEMORY_ID, value) for value in ids):
                        continue
                    content = paragraph[cursor:group.start()].strip()
                    result.extend(TopicDiagnosticUnit(
                        value, content, relative,
                        line_number + paragraph.count("\n", 0, cursor), (), (),
                    ) for value in ids)
                    cursor = group.end()
                    legacy_found = True
            if not legacy_found:
                result.append(TopicDiagnosticUnit(
                    None, paragraph, relative, line_number,
                    tuple(m.group("id") for m in BLOCK_LINK.finditer(paragraph)),
                ))
    return result


def _block_unit(
    paragraph: str,
    headings: tuple[str, ...],
    path: Path,
    topics: Path,
    known_definitions: dict,
    created_order: int,
    line_number: int | None = None,
    *,
    strict: bool,
) -> tuple[list[MemoryUnit], set[str]] | None:
    block = BLOCK_SUFFIX.search(paragraph)
    any_block = ANY_BLOCK_SUFFIX.search(paragraph)
    if any_block and not block:
        raise TopicFormatError(
            f"invalid block_id: {any_block.group('id')} "
            f"file={path.relative_to(topics).as_posix()} line={line_number}"
        )
    if not block:
        return None
    # A merge keeps every absorbed ID on one paragraph. The last is this
    # paragraph's identity; the rest are aliases the caller re-emits so
    # links pointing at them still resolve.
    absorbed = re.findall(r"\^([A-Za-z0-9-]+)", paragraph[block.start():])
    memory_id = absorbed[-1]
    body = paragraph[:block.start()].rstrip()
    evidence = []
    used: set[str] = set()
    cursor = 0
    for group in CITATION_GROUP.finditer(body):
        quote = body[cursor:group.start()].strip()
        ids = [match.group("id") for match in SINGLE_CITATION.finditer(group.group(0))]
        if not quote:
            raise TopicFormatError(
                f"missing memory content before {ids[0]} "
                f"file={path.relative_to(topics).as_posix()} line={line_number}"
            )
        for citation_id in ids:
            if citation_id not in known_definitions:
                raise TopicFormatError(
                    f"undefined footnote: {citation_id} "
                    f"file={path.relative_to(topics).as_posix()} "
                    f"line={line_number} block={memory_id}"
                )
            when, refs, links = known_definitions[citation_id]
            evidence.append(EvidenceAnnotation(
                citation_id=citation_id,
                quote=quote,
                when=when,
                source_refs=refs,
                source_links=links,
            ))
            used.add(citation_id)
        cursor = group.end()
    if strict and not evidence:
        raise TopicFormatError(
            f"memory source footnote required: {memory_id} "
            f"file={path.relative_to(topics).as_posix()} line={line_number}"
        )
    trailing = body[cursor:].strip()
    if strict and trailing:
        relative = path.relative_to(topics).as_posix()
        excerpt = " ".join(trailing.split())[:120]
        raise TopicFormatError(
            f"content after final evidence: {memory_id}; "
            f"file={relative}; line={line_number}; trailing_excerpt={excerpt!r}; "
            f"trailing_chars={len(trailing)}"
        )
    refs: list[str] = []
    links: list[str] = []
    for annotation in evidence:
        for ref, link in zip(annotation.source_refs, annotation.source_links):
            if ref not in refs:
                refs.append(ref)
                links.append(link)
    shared = dict(
        content=SINGLE_CITATION.sub("", body).strip(),
        when=next((item.when for item in evidence if item.when is not None), None),
        source_refs=tuple(refs),
        source_links=tuple(links),
        topic_path=path.relative_to(topics).as_posix(),
        headings=headings,
        created_order=created_order,
        evidence=tuple(evidence),
        relation_targets=tuple(
            match.group("id") for match in BLOCK_LINK.finditer(body)
        ),
    )
    return [
        MemoryUnit(memory_id=value, **shared) for value in absorbed
    ], used


def _legacy_units(
    paragraph: str,
    headings: tuple[str, ...],
    path: Path,
    topics: Path,
    known_definitions: dict,
    created_order: int,
    line_number: int | None = None,
) -> tuple[list[MemoryUnit], set[str]]:
    units = []
    used = set()
    cursor = 0
    for group in CITATION_GROUP.finditer(paragraph):
        content = paragraph[cursor:group.start()].strip()
        ids = [match.group("id") for match in SINGLE_CITATION.finditer(group.group(0))]
        if any(not re.fullmatch(MEMORY_ID, memory_id) for memory_id in ids):
            continue
        if not content:
            raise TopicFormatError(
                f"missing memory content before {ids[0]} "
                f"file={path.relative_to(topics).as_posix()} line={line_number}"
            )
        for memory_id in ids:
            if memory_id not in known_definitions:
                raise TopicFormatError(
                    f"undefined footnote: {memory_id} "
                    f"file={path.relative_to(topics).as_posix()} "
                    f"line={line_number}"
                )
            when, refs, links = known_definitions[memory_id]
            units.append(MemoryUnit(
                memory_id=memory_id,
                content=content,
                when=when,
                source_refs=refs,
                source_links=links,
                topic_path=path.relative_to(topics).as_posix(),
                headings=headings,
                created_order=created_order + len(units),
            ))
            used.add(memory_id)
        cursor = group.end()
    return units, used


def parse_topic_tree(topics: Path, *, strict: bool = True) -> list[MemoryUnit]:
    """Return memory units in their current Markdown occurrence order."""
    topics = Path(topics)
    if strict and (malformed := _malformed_footnote_definitions(topics)):
        raise TopicFormatError(
            "malformed footnote definitions (repair all): " + "; ".join(malformed)
        )
    if strict and (duplicates := _duplicate_footnote_definitions(topics)):
        raise TopicFormatError(
            "duplicate footnote definitions (repair all): "
            + " | ".join(duplicates)
        )
    if strict and (trailing := _trailing_content_errors(topics)):
        raise TopicFormatError(
            "content after final evidence (repair all): " + " | ".join(trailing)
        )
    units: list[MemoryUnit] = []
    seen: set[str] = set()
    seen_locations: dict[str, str] = {}
    duplicate_ids: list[str] = []
    unused_definitions: list[str] = []
    for path in sorted(topics.rglob("*.md")):
        lines = path.read_text(encoding="utf-8").splitlines()
        known_definitions = definitions(
            lines, source_path=path.relative_to(topics).as_posix()
        )
        used_definitions: set[str] = set()
        for paragraph, headings, line_number in paragraphs(lines, with_line_numbers=True):
            parsed = _block_unit(
                paragraph,
                headings,
                path,
                topics,
                known_definitions,
                len(units),
                line_number,
                strict=strict,
            )
            if parsed:
                block_units, used = parsed
                for unit in block_units:
                    if unit.memory_id in seen:
                        duplicate_ids.append(
                            f"id={unit.memory_id} first={seen_locations[unit.memory_id]} "
                            f"duplicate={path.relative_to(topics).as_posix()}:{line_number}"
                        )
                        continue
                    units.append(unit)
                    seen.add(unit.memory_id)
                    seen_locations[unit.memory_id] = (
                        f"{path.relative_to(topics).as_posix()}:{line_number}"
                    )
                used_definitions.update(used)
                continue
            legacy, used = _legacy_units(
                paragraph,
                headings,
                path,
                topics,
                known_definitions,
                len(units),
                line_number,
            )
            if strict and not legacy:
                excerpt = " ".join(paragraph.split())[:180]
                raise TopicFormatError(
                    "memory block ID required: "
                    f"{path.relative_to(topics)}; line={line_number}; "
                    f"paragraph={excerpt!r}"
                )
            for unit in legacy:
                if unit.memory_id in seen:
                    duplicate_ids.append(
                        f"id={unit.memory_id} first={seen_locations[unit.memory_id]} "
                        f"duplicate={path.relative_to(topics).as_posix()}:{line_number}"
                    )
                    continue
                units.append(unit)
                seen.add(unit.memory_id)
                seen_locations[unit.memory_id] = (
                    f"{path.relative_to(topics).as_posix()}:{line_number}"
                )
            used_definitions.update(used)
        unused = set(known_definitions) - used_definitions
        if strict and unused:
            for citation_id in sorted(unused):
                unused_definitions.append(
                    f"id={citation_id} file={path.relative_to(topics).as_posix()} "
                    f"line={_definition_line(lines, citation_id)}"
                )
    if duplicate_ids:
        raise TopicFormatError(
            "duplicate memory_ids (repair all): " + " | ".join(duplicate_ids)
        )
    if strict and unused_definitions:
        raise TopicFormatError(
            "unused footnote definitions (repair all): "
            + " | ".join(unused_definitions)
        )
    return units


def topic_prose(topics: Path) -> str:
    """Return normalized non-structural Topic prose, including unbound text."""
    values = []
    for path in sorted(Path(topics).rglob("*.md")):
        lines = path.read_text(encoding="utf-8").splitlines()
        for paragraph, _headings in paragraphs(lines):
            value = SINGLE_CITATION.sub("", paragraph)
            value = BLOCK_SUFFIX.sub("", value)
            values.append(" ".join(value.split()))
    return "\n".join(values)
