from pathlib import Path

import pytest

from src.management.workspace_tool_backend import (
    WorkspaceToolBackend,
    WorkspaceToolError,
)


@pytest.mark.parametrize("old", ["\n", "\n\n", " \t"])
@pytest.mark.parametrize("replace_all", [False, True])
def test_whitespace_anchor_returns_usable_suffix_without_mutation(tmp_path, old, replace_all):
    (tmp_path / "topics").mkdir()
    path = tmp_path / "topics/a.md"
    original = "Fact.[^e1] ^1234abcd\n\n[^e1]: Time: `2023`; Sources: locomo/a/b\n"
    path.write_text(original)
    backend = WorkspaceToolBackend(tmp_path)
    with pytest.raises(WorkspaceToolError, match="only whitespace") as exc:
        backend.edit("topics/a.md", old, "New fact", replace_all=replace_all)
    assert path.read_text() == original
    anchor = exc.value.recovery["suggested_append_old_string"]
    assert original.endswith(anchor) and original.count(anchor) == 1 and anchor.strip()
    backend.read("topics/a.md")
    addition = "\nNew fact.[^e2]\n\n[^e2]: Time: `2023`; Sources: locomo/a/c\n"
    backend.edit("topics/a.md", anchor, anchor + addition)
    assert path.read_text() == original + addition


def test_shared_workspace_tools_round_trip(tmp_path: Path):
    (tmp_path / "topics").mkdir()
    backend = WorkspaceToolBackend(tmp_path)

    backend.write("topics/person.md", "# Person\n\nOld fact.\n")
    backend.edit("topics/person.md", "Old fact.", "New fact.")

    assert backend.read("topics/person.md")["content"].endswith("New fact.\n")
    assert backend.glob("topics/**/*.md")["paths"] == ["topics/person.md"]
    assert backend.grep("New fact", "topics/**/*.md")["matches"][0]["line"] == 3


def test_write_and_edit_reject_loss_of_existing_block_id(tmp_path: Path):
    (tmp_path / "topics").mkdir()
    topic = tmp_path / "topics/person.md"
    original = "Fact.[^e1] ^1234abcd\n\n[^e1]: Source\n"
    topic.write_text(original)
    backend = WorkspaceToolBackend(tmp_path)

    with pytest.raises(WorkspaceToolError, match="1234abcd"):
        backend.write("topics/person.md", "[^e1]: Source\n")
    backend.read("topics/person.md")
    with pytest.raises(WorkspaceToolError, match="1234abcd"):
        backend.edit("topics/person.md", " ^1234abcd", "")
    assert topic.read_text() == original

    backend.read("topics/person.md")
    backend.edit("topics/person.md", "Fact.", "Updated fact.")
    assert "Updated fact.[^e1] ^1234abcd" in topic.read_text()


def test_block_id_can_move_after_destination_is_written(tmp_path: Path):
    topics = tmp_path / "topics"
    topics.mkdir()
    (topics / "old.md").write_text("Fact.[^e1] ^1234abcd\n")
    backend = WorkspaceToolBackend(tmp_path)
    backend.write("topics/new.md", "Fact.[^e1] ^1234abcd\n")
    backend.write("topics/old.md", "")
    assert not (topics / "old.md").read_text()


@pytest.mark.parametrize("path", ["sources/a.md", "timeline/a.md", "../escape.md"])
def test_shared_workspace_tools_reject_non_authoritative_writes(
    tmp_path: Path, path: str
):
    (tmp_path / "topics").mkdir()
    backend = WorkspaceToolBackend(tmp_path)

    with pytest.raises(WorkspaceToolError):
        backend.write(path, "forbidden")


def test_edit_requires_unique_context(tmp_path: Path):
    (tmp_path / "topics").mkdir()
    (tmp_path / "topics/a.md").write_text("same\nsame\n", encoding="utf-8")
    backend = WorkspaceToolBackend(tmp_path)

    with pytest.raises(WorkspaceToolError, match="not unique"):
        backend.edit("topics/a.md", "same", "changed")


def test_paged_read_reassembles_large_unicode_file_without_loss(tmp_path: Path):
    content = "记忆🙂" * 10000 + "\nlast line"
    (tmp_path / "large.md").write_text(content, encoding="utf-8")
    backend = WorkspaceToolBackend(tmp_path, max_output_chars=1024)
    pieces, offset = [], 0
    while True:
        page = backend.read("large.md", char_offset=offset, char_limit=999999)
        assert len(page["content"]) <= 1024
        pieces.append(page["content"])
        offset = page["next_char_offset"]
        if offset is None:
            break
    assert "".join(pieces) == content
    with pytest.raises(WorkspaceToolError):
        backend.read("large.md", char_offset=-1)


def test_paged_grep_preserves_all_matches_and_exact_read_offsets(tmp_path: Path):
    content = "".join("x" * 2000 + f"needle-{i}\n" for i in range(15))
    (tmp_path / "large.md").write_text(content, encoding="utf-8")
    backend = WorkspaceToolBackend(tmp_path, max_output_chars=1024)
    matches, offset = [], 0
    while True:
        page = backend.grep("needle", match_offset=offset)
        matches.extend(page["matches"])
        offset = page["next_match_offset"]
        if offset is None:
            break
    assert [item["line"] for item in matches] == list(range(1, 16))
    for item in matches:
        assert "needle" in item["text"]
        assert item["truncated"] is True
        exact = backend.read("large.md", char_offset=item["char_offset"],
                             char_limit=len(item["text"]))
        assert exact["content"] == item["text"]


def test_output_pagination_is_explicit_protocol_change():
    from src.management.api import writer_protocol_sha256
    from src.management.config import MemoryConfig

    assert writer_protocol_sha256() == writer_protocol_sha256(MemoryConfig())
    assert writer_protocol_sha256() != writer_protocol_sha256(
        MemoryConfig(writer_tool_output_max_chars=12000))


def test_validation_diagnostics_switch_does_not_change_writer_protocol():
    from src.management.api import writer_protocol_sha256
    from src.management.config import MemoryConfig

    assert MemoryConfig().validation_diagnostic_aggregation_enabled is False
    assert writer_protocol_sha256() == writer_protocol_sha256(
        MemoryConfig(validation_diagnostic_aggregation_enabled=True)
    )


def test_validation_diagnostics_are_opt_in_and_preserve_primary_error(tmp_path: Path):
    from src.management import MemoryConfig, MemoryWorkspace

    for enabled in (False, True):
        workspace = MemoryWorkspace(
            tmp_path / str(enabled),
            config=MemoryConfig(validation_diagnostic_aggregation_enabled=enabled),
        )
        baseline = workspace.baseline()
        (workspace.stage_dir / "core.md").write_text(
            "Fact.[^absent]\n", encoding="utf-8"
        )
        (workspace.stage_dir / "topics" / "bad.md").write_text(
            "# Bad\n\nBroken fact. ^bad\n", encoding="utf-8"
        )
        with pytest.raises(ValueError, match="memory block ID required"):
            workspace.commit_edits(*baseline)
        if enabled:
            assert any("Core:" in item for item in workspace.last_validation_diagnostics)
            assert any("Topic format:" in item for item in workspace.last_validation_diagnostics)
        else:
            assert workspace.last_validation_diagnostics == ()
        assert not (workspace.stage_dir / "core.md").exists()


def test_validation_diagnostics_report_topic_duplicate_and_core_source_together(
    tmp_path: Path,
):
    from src.management import MemoryConfig, MemoryWorkspace

    workspace = MemoryWorkspace(
        tmp_path / "memory",
        config=MemoryConfig(validation_diagnostic_aggregation_enabled=True),
    )
    baseline = workspace.baseline()
    (workspace.stage_dir / "core.md").write_text(
        "Fact.[^c]\n[^c]: Time: `undated`; Sources: "
        "[locomo/thread_x/msg_missing](sources/x.md#msg_missing)\n",
        encoding="utf-8",
    )
    (workspace.stage_dir / "topics" / "bad.md").write_text(
        "# Bad\n\nFact.[^e] ^abcdef12\n"
        "[^e]: Time: `undated`; Sources: [D1:1](../sources/D1.md#d1-1)\n"
        "[^e]: Time: `undated`; Sources: [D1:1](../sources/D1.md#d1-1)\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        workspace.commit_edits(*baseline)
    findings = "\n".join(workspace.last_validation_diagnostics)
    assert "duplicate footnote definitions (repair all)" in findings
    assert "missing source references (repair all)" in findings
    assert "file=core.md line=2 footnote=c" in findings
    assert "file=topics/bad.md line=4 footnote=e" in findings
    assert "not checked because Topic parsing failed" not in findings
    assert not (workspace.stage_dir / "core.md").exists()


def test_validation_diagnostics_include_every_uncited_topic_block(tmp_path: Path):
    from src.management import MemoryConfig, MemoryWorkspace

    workspace = MemoryWorkspace(
        tmp_path / "memory",
        config=MemoryConfig(validation_diagnostic_aggregation_enabled=True),
    )
    (workspace.memory_dir / "topics").mkdir(parents=True, exist_ok=True)
    (workspace.memory_dir / "topics" / "bad.md").write_text(
        "First fact.[^e1] ^first-id\n\n"
        "Second fact.[^e2] ^second-id\n\n"
        "[^e1]: Time: `undated`; Sources: [D1:1](../sources/D1.md#d1-1)\n"
        "[^e2]: Time: `undated`; Sources: [D1:2](../sources/D1.md#d1-2)\n",
        encoding="utf-8",
    )
    workspace._refresh_stage()
    baseline = workspace.baseline()
    (workspace.stage_dir / "topics" / "bad.md").write_text(
        "First without evidence. ^first-id\n\n"
        "Second without evidence. ^second-id\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="memory source footnote required"):
        workspace.commit_edits(*baseline)

    findings = "\n".join(workspace.last_validation_diagnostics)
    assert "block=first-id file=bad.md line=1" in findings
    assert "block=second-id file=bad.md line=3" in findings
    assert "not checked because Topic parsing failed" not in findings
    assert "block ID must not be removed" not in findings
    assert "First fact.[^e1] ^first-id" in (
        workspace.stage_dir / "topics" / "bad.md"
    ).read_text(encoding="utf-8")


def test_validation_diagnostics_scan_core_sources_and_topics_together(tmp_path: Path):
    from src.management import MemoryConfig, MemoryWorkspace

    workspace = MemoryWorkspace(
        tmp_path / "memory",
        config=MemoryConfig(
            validation_diagnostic_aggregation_enabled=True,
            core_max_tokens=1,
        ),
    )
    baseline = workspace.baseline()
    (workspace.stage_dir / "sources").mkdir(exist_ok=True)
    (workspace.stage_dir / "sources" / "D1.md").write_text(
        '<a id="d1-1"></a>\n', encoding="utf-8",
    )
    (workspace.stage_dir / "core.md").write_text(
        "First.[^missing] Second.[^ok]\n\n"
        "[^ok]: Time: `2026-02-30`; Sources: [D1:2](sources/D1.md#d1-2)\n"
        "[^ok]: Time: `undated`; Sources: [D1:1](sources/D1.md#d1-1)\n"
        "[^no-link]: Time: `undated`; Sources: D1:3\n"
        "[^broken]: Sources: D1:4\n",
        encoding="utf-8",
    )
    (workspace.stage_dir / "topics" / "bad.md").write_text(
        "No citation. ^abc123\n", encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Source Memory is append-only"):
        workspace.commit_edits(*baseline)

    findings = "\n".join(workspace.last_validation_diagnostics)
    assert "Sources: Source Memory is append-only" in findings
    assert "Core: Pending normalization: duplicate Core footnote definitions" in findings
    assert "Core: invalid Core evidence dates" in findings
    assert "Core: Core source links required" not in findings
    assert "Pending normalization" in findings
    assert "Core: malformed Core footnote definitions" in findings
    assert "Core: undefined Core footnotes" in findings
    assert "Core: Core Memory exceeds 1 tokens" in findings
    assert "Sources: missing source references" in findings
    assert "Topic format: memory source footnotes required" in findings


def test_validation_diagnostics_collect_all_topic_link_errors(tmp_path: Path):
    from src.management import MemoryConfig, MemoryWorkspace

    workspace = MemoryWorkspace(
        tmp_path / "memory",
        config=MemoryConfig(validation_diagnostic_aggregation_enabled=True),
    )
    (workspace.stage_dir / "topics" / "a.md").write_text(
        "One [bad](b.md) and [missing](b.md#^lost-one).[^e1] ^a11111\n\n"
        "Two [bad](c.md) and [missing](c.md#^lost-two).[^e2] ^a22222\n\n"
        "[^e1]: Time: `undated`; Sources: [D1:1](../sources/D1.md#d1-1)\n"
        "[^e2]: Time: `undated`; Sources: [D1:2](../sources/D1.md#d1-2)\n",
        encoding="utf-8",
    )

    findings = "\n".join(workspace._collect_validation_diagnostics([], set()))

    assert "target=b.md" in findings
    assert "target=c.md" in findings
    assert "target=lost-one" in findings
    assert "target=lost-two" in findings


@pytest.mark.parametrize("bad_definition", [
    "[^e]: Sources: broken\n",
    "[^e]: Time: `2026-02-30`; Sources: [D1:1](../sources/D1.md#d1-1)\n",
    "[^e]: Time: `undated`; Sources: [D1:1](../sources/D1.md#d1-1)\n" * 2,
    "",  # Undefined footnote.
])
def test_malformed_evidence_does_not_block_contract_diagnostics(
    tmp_path: Path, bad_definition: str,
):
    from src.management import MemoryConfig, MemoryWorkspace

    workspace = MemoryWorkspace(tmp_path / "memory", config=MemoryConfig(
        validation_diagnostic_aggregation_enabled=True,
        core_capacity_limit_enabled=False,
    ))
    topic = workspace.memory_dir / "topics" / "a.md"
    topic.parent.mkdir(parents=True, exist_ok=True)
    original = (
        "Kept.[^e] ^kept-id ^alias-id\n\n"
        "Lost.[^e] ^lost-id\n\n"
        "[^e]: Time: `undated`; Sources: [D1:1](../sources/D1.md#d1-1)\n"
    )
    topic.write_text(original, encoding="utf-8")
    workspace._refresh_stage()
    baseline = workspace.baseline()
    (workspace.stage_dir / "topics" / "a.md").write_text(
        "Changed [bad](b.md) [gone](b.md#^gone-id).[^e] ^kept-id ^alias-id\n\n"
        + bad_definition,
        encoding="utf-8",
    )
    (workspace.stage_dir / "topics" / "b.md").write_text(
        "No ID [bad](a.md) [alias](a.md#^alias-id).\n", encoding="utf-8",
    )

    with pytest.raises(ValueError):
        workspace.commit_edits(*baseline)

    findings = "\n".join(workspace.last_validation_diagnostics)
    assert "Topic format:" in findings
    assert "block ID must not be removed (repair all): lost-id" in findings
    assert "id=lost-id file=topics/a.md" in findings
    assert "file=topics/a.md line=1 block=kept-id target=b.md" in findings
    assert "file=topics/b.md line=1 block=<unresolved> target=a.md" in findings
    assert "target=gone-id source_file=topics/a.md" in findings
    assert "target=alias-id source_file=" not in findings
    assert "not checked because Topic parsing failed" not in findings
    assert topic.read_text(encoding="utf-8") == original
    assert not (workspace.memory_dir / "topics" / "b.md").exists()


def test_broken_evidence_does_not_invent_change_to_legacy_link(tmp_path: Path):
    from src.management import MemoryConfig, MemoryWorkspace
    from src.markdown import parse_topic_tree

    workspace = MemoryWorkspace(tmp_path / "memory", config=MemoryConfig(
        validation_diagnostic_aggregation_enabled=True,
    ))
    topic = workspace.stage_dir / "topics" / "a.md"
    topic.write_text(
        "Unchanged [old link](b.md).[^e] ^kept-id\n\n"
        "[^e]: Time: `undated`; Sources: [D1:1](../sources/D1.md#d1-1)\n",
        encoding="utf-8",
    )
    before = parse_topic_tree(topic.parent)
    topic.write_text("Unchanged [old link](b.md).[^e] ^kept-id\n", encoding="utf-8")
    findings = "\n".join(workspace._collect_validation_diagnostics(before, {"kept-id"}))
    assert "evidence-change comparison unavailable" in findings
    assert "Topic-to-Topic links must target" not in findings
    assert "block ID must not be removed" not in findings


def test_capacity_rejection_also_records_independent_diagnostics(tmp_path: Path):
    from src.management import CoreCapacityError, MemoryConfig, MemoryWorkspace

    workspace = MemoryWorkspace(
        tmp_path / "memory",
        config=MemoryConfig(
            validation_diagnostic_aggregation_enabled=True,
            core_max_tokens=1,
        ),
    )
    baseline = workspace.baseline()
    (workspace.stage_dir / "core.md").write_text(
        "Unsupported fact.[^missing]\n", encoding="utf-8",
    )

    with pytest.raises(CoreCapacityError):
        workspace.commit_edits(*baseline)

    findings = "\n".join(workspace.last_validation_diagnostics)
    assert "Core: Core Memory exceeds 1 tokens" in findings
    assert "Core: undefined Core footnotes" in findings


def test_management_tool_pagination_is_opt_in_and_audited(tmp_path: Path):
    import asyncio
    import json
    from src.management import MemoryConfig, MemoryWorkspace
    from src.management.tools import management_tools

    workspace = MemoryWorkspace(tmp_path, config=MemoryConfig(
        writer_tool_output_max_chars=1024))
    (workspace.stage_dir / "large.md").write_text("x" * 3000)
    audit = []
    tools = {definition.name: definition for definition in management_tools(workspace, audit)}
    assert "char_offset" in tools["Read"].input_schema["properties"]
    result = asyncio.run(tools["Read"].handler({"path": "large.md", "char_offset": 1024}))
    payload = json.loads(result["content"][0]["text"])
    assert payload["next_char_offset"] == 2048
    assert payload == audit[-1]["output"]
    default = MemoryWorkspace(tmp_path / "default")
    legacy_tools = {t.name: t for t in management_tools(default, [])}
    assert "char_offset" not in legacy_tools["Read"].input_schema["properties"]


@pytest.mark.parametrize('operation', ['write', 'edit'])
def test_mutation_cannot_drop_still_used_definition(tmp_path, operation):
    (tmp_path / 'topics').mkdir()
    path = tmp_path / 'topics/a.md'
    definition = '[^e-1234567890]: Time: `2023`; Sources: locomo/a/b\n'
    original = 'Fact.[^e-1234567890] ^1234abcd\n\n' + definition
    path.write_text(original)
    backend = WorkspaceToolBackend(tmp_path)
    with pytest.raises(WorkspaceToolError) as exc:
        if operation == 'write':
            backend.write('topics/a.md', original.replace(definition, ''))
        else:
            backend.edit('topics/a.md', definition, '')
    assert exc.value.recovery['reason'] == 'referenced_definition_removal'
    assert exc.value.recovery['required_definitions']['e-1234567890'] == [definition.rstrip()]
    assert path.read_text() == original
    backend.read('topics/a.md')
    # Renaming both citation and definition together is a valid atomic edit.
    backend.edit('topics/a.md', 'e-1234567890', 'e2', replace_all=True)
    assert 'e-1234567890' not in path.read_text()


@pytest.mark.parametrize('same_evidence', [False, True])
def test_stable_evidence_id_cannot_gain_duplicate_definition(tmp_path, same_evidence):
    (tmp_path / 'topics').mkdir()
    path = tmp_path / 'topics/a.md'
    definition = '[^e-1234567890]: Time: `2023`; Sources: locomo/a/b\n'
    original = 'Fact.[^e-1234567890] ^1234abcd\n\n' + definition
    path.write_text(original)
    added = definition if same_evidence else definition.replace('2023', '2024').replace('a/b', 'a/c')
    backend = WorkspaceToolBackend(tmp_path)
    with pytest.raises(WorkspaceToolError) as exc:
        backend.write('topics/a.md', original + added)
    assert exc.value.recovery['reason'] == 'duplicate_stable_footnote'
    assert path.read_text() == original
    backend.read('topics/a.md')
    backend.write('topics/a.md', original + '\nNew fact.[^e2]\n\n' + added.replace('e-1234567890', 'e2'))
    assert 'New fact.[^e2]' in path.read_text()


def test_existing_duplicate_can_be_repaired_and_unused_definition_removed(tmp_path):
    (tmp_path / 'topics').mkdir()
    path = tmp_path / 'topics/a.md'
    definition = '[^e-1234567890]: Time: `2023`; Sources: locomo/a/b\n'
    other = definition.replace('a/b', 'a/c')
    original = 'Fact.[^e-1234567890] ^1234abcd\n\n' + definition + other
    path.write_text(original)
    backend = WorkspaceToolBackend(tmp_path)
    backend.edit('topics/a.md', other, '')
    assert path.read_text() == original.replace(other, '')
    unused = '[^e9]: Time: `2023`; Sources: locomo/a/c\n'
    backend.write('topics/a.md', path.read_text() + unused)
    backend.edit('topics/a.md', unused, '')
    # Writer-local labels with per-occurrence definitions remain supported.
    backend.write('topics/b.md', 'One.[^e1]\n\n' + definition.replace('e-1234567890', 'e1')
                  + '\nTwo.[^e1]\n\n' + other.replace('e-1234567890', 'e1'))


def test_missing_anchor_reports_exact_block_not_a_fuzzy_replacement(tmp_path):
    (tmp_path / 'topics').mkdir()
    path = tmp_path / 'topics/a.md'
    current = 'Actual current fact.[^e1] ^1234abcd'
    path.write_text(current + '\n\n[^e1]: Time: `2023`; Sources: locomo/a/b\n')
    before = path.read_bytes()
    backend = WorkspaceToolBackend(tmp_path)
    with pytest.raises(WorkspaceToolError) as exc:
        backend.edit('topics/a.md', 'Invented old fact.[^e1] ^1234abcd', 'Replacement')
    assert exc.value.recovery['current_block_anchors'] == [
        {'block_ids': '1234abcd', 'current_text': current}]
    assert exc.value.recovery['read_arguments'] == {
        'path': 'topics/a.md', 'char_offset': 0}
    assert path.read_bytes() == before
    backend.read('topics/a.md')
    backend.edit('topics/a.md', current, current.replace('Actual current', 'Updated'))


def test_missing_anchor_points_read_into_large_staged_file(tmp_path):
    (tmp_path / 'topics').mkdir()
    path = tmp_path / 'topics/a.md'
    path.write_text('prefix\n\n' + 'x' * 20000 + '\n\nCurrent fact. ^1234abcd')
    backend = WorkspaceToolBackend(tmp_path, max_output_chars=1024)
    with pytest.raises(WorkspaceToolError) as exc:
        backend.edit('topics/a.md', 'Stale fact. ^1234abcd', 'replacement')
    args = exc.value.recovery['read_arguments']
    assert args['char_offset'] > 19000
    assert 'Current fact.' in backend.read(**args)['content']


def test_remove_cannot_orphan_memory_ids(tmp_path):
    (tmp_path / 'topics').mkdir()
    path = tmp_path / 'topics/a.md'
    path.write_text('Retained fact. ^1234abcd')
    backend = WorkspaceToolBackend(tmp_path)
    with pytest.raises(WorkspaceToolError, match='remove existing memory block ID'):
        backend.remove('topics/a.md')
    assert path.read_text() == 'Retained fact. ^1234abcd'
    backend.read('topics/a.md')
    backend.write('topics/b.md', 'Retained fact. ^1234abcd')
    assert backend.remove('topics/a.md')['removed'] is True
