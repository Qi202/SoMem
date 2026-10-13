import asyncio
import json
import os
import re
from pathlib import Path

import pytest

from src.agent_runtime import AgentResult
from src.management import (
    CoreCapacityError,
    MemoryConfig,
    MemoryWorkspace,
    _run_agent,
    manage_memory,
    organize_topics,
    verify_session,
)
from src.management.agent import (
    CommitFailure,
    _generic_repair_guidance,
    _generic_repair_mode,
    _luna_strict_validation_guidance,
)
from src.management.tools import CoreRepairState, management_tools
from src import build as adapter
from src import management as memory
from src.markdown import TopicFormatError, parse_topic_tree


WORKSPACE_TOOLS = ["Read", "Write", "Edit", "Glob", "Grep", "Move", "Remove"]


class ScriptedAgent:
    def __init__(self, tool_calls=(), *, text="done", structured_output=None):
        self.tool_calls = list(tool_calls)
        self.text = text
        self.structured_output = structured_output
        self.calls = []

    def run(self, **kwargs):
        self.calls.append(kwargs)
        tools = {definition.name: definition for definition in kwargs["tools"]}
        for name, arguments in self.tool_calls:
            asyncio.run(tools[name].handler(arguments))
        return AgentResult(
            text=self.text,
            structured_output=self.structured_output,
            num_turns=max(1, len(self.tool_calls) + 1),
            input_tokens=10,
            output_tokens=5,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
            anthropic_equivalent_cost_usd=0.001,
            duration_ms=20,
            duration_api_ms=15,
            stop_reason="end_turn",
            session_id="test-session",
        )


class QueuedAgent:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def run(self, **kwargs):
        self.calls.append(kwargs)
        response = next(self.responses)
        tools = {definition.name: definition for definition in kwargs["tools"]}
        tool_calls = response.get("tool_calls", [])
        for name, arguments in tool_calls:
            asyncio.run(tools[name].handler(arguments))
        return AgentResult(
            text=response.get("text", ""),
            structured_output=response.get("structured_output"),
            num_turns=max(1, len(tool_calls) + 1),
            input_tokens=10,
            output_tokens=5,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
            anthropic_equivalent_cost_usd=0.001,
            duration_ms=20,
            duration_api_ms=15,
            stop_reason="end_turn",
            session_id="test-session",
        )


def test_written_blocks_counts_builtin_commit_records():
    assert adapter._written_blocks([
        {"tool": "commit", "status": "ok", "count": 3},
        {"tool": "commit", "status": "error", "count": 9},
    ]) == 3


def test_writer_raises_when_repair_commit_is_rejected_twice(
    tmp_path: Path, monkeypatch,
):
    outcomes = iter(["invalid initial edit", "invalid repair edit"])
    monkeypatch.setattr(
        "src.management.agent._commit_turn",
        lambda *_args, **_kwargs: next(outcomes),
    )

    with pytest.raises(RuntimeError, match="memory writer repair was rejected"):
        _run_agent(
            tmp_path,
            agent=QueuedAgent([{}, {}]),
            task="organize",
            config=MemoryConfig(generic_repair_max_trajectories=1),
        )


def test_writer_audit_aggregates_successful_repair_trajectory(
    tmp_path: Path, monkeypatch,
):
    outcomes = iter(["invalid initial edit", None])
    monkeypatch.setattr(
        "src.management.agent._commit_turn",
        lambda *_args, **_kwargs: next(outcomes),
    )
    final_output = []

    audit = _run_agent(
        tmp_path,
        agent=QueuedAgent([{"text": "first"}, {"text": "repaired"}]),
        task="organize",
        final_output=final_output,
    )

    assert audit[-1]["rounds"] == 2
    assert audit[-1]["input_tokens"] == 20
    assert audit[-1]["output_tokens"] == 10
    assert final_output == ["repaired"]


def test_generic_repair_honors_configured_trajectory_limit(
    tmp_path: Path, monkeypatch,
):
    outcomes = iter([
        "invalid initial edit",
        "invalid repair one",
        "invalid repair two",
        None,
    ])
    monkeypatch.setattr(
        "src.management.agent._commit_turn",
        lambda *_args, **_kwargs: next(outcomes),
    )
    agent = QueuedAgent([{}, {}, {}, {}])

    audit = _run_agent(
        tmp_path,
        agent=agent,
        task="organize",
        config=MemoryConfig(generic_repair_max_trajectories=3),
    )

    assert len(agent.calls) == 4
    assert "Repair attempt 1 of 3" in agent.calls[1]["prompt"]
    assert "Repair attempt 3 of 3" in agent.calls[3]["prompt"]
    assert "rejected candidate is still staged" in agent.calls[1]["prompt"]
    assert "Do not redo the original task" in agent.calls[1]["prompt"]
    assert "invalid repair one" in agent.calls[2]["prompt"]
    assert "invalid repair two" in agent.calls[3]["prompt"]
    assert audit[-1]["generic_repair"] == [
        {
            "trajectory": 1,
            "mode": "generic_validation",
            "trigger_error": "invalid initial edit",
            "status": "rejected",
            "error": "invalid repair one",
        },
        {
            "trajectory": 2,
            "mode": "generic_validation",
            "trigger_error": "invalid repair one",
            "status": "rejected",
            "error": "invalid repair two",
        },
        {
            "trajectory": 3,
            "mode": "generic_validation",
            "trigger_error": "invalid repair two",
            "status": "ok",
            "error": None,
        },
    ]


def test_dangling_link_repair_receives_targeted_guidance(
    tmp_path: Path, monkeypatch,
):
    outcomes = iter([
        CommitFailure(ValueError("dangling block link: deadbeef")),
        None,
    ])
    monkeypatch.setattr(
        "src.management.agent._commit_turn",
        lambda *_args, **_kwargs: next(outcomes),
    )
    agent = QueuedAgent([{}, {}])

    audit = _run_agent(tmp_path, agent=agent, task="organize")

    repair_prompt = agent.calls[1]["prompt"]
    assert "missing target ID is `deadbeef`" in repair_prompt
    assert "search every editable Core and Topic Markdown file" in repair_prompt
    assert "Start with that exact source paragraph" in repair_prompt
    assert "Never attach the missing ID to a different fact" in repair_prompt
    assert audit[-1]["generic_repair"][0]["mode"] == "dangling_block_link"


def test_missing_new_paragraph_repair_receives_exact_contract_guidance(
    tmp_path: Path, monkeypatch,
):
    outcomes = iter([
        CommitFailure(TopicFormatError(
            "memory block ID required: people/john.md; "
            "paragraph='Untracked commentary.'"
        )),
        None,
    ])
    monkeypatch.setattr(
        "src.management.agent._commit_turn",
        lambda *_args, **_kwargs: next(outcomes),
    )
    agent = QueuedAgent([{}, {}])

    audit = _run_agent(tmp_path, agent=agent, task="integrate")

    repair_prompt = agent.calls[1]["prompt"]
    assert "people/john.md" in repair_prompt
    assert "Untracked commentary." in repair_prompt
    assert "Never write or invent a trailing `^id`" in repair_prompt
    assert "matching footnote definition" in repair_prompt
    assert "remove only that paragraph" in repair_prompt
    assert (
        audit[-1]["generic_repair"][0]["mode"]
        == "missing_new_paragraph_contract"
    )


def test_malformed_footnotes_repair_receives_aggregated_guidance(
    tmp_path: Path, monkeypatch,
):
    outcomes = iter([
        CommitFailure(TopicFormatError(
            "malformed footnote definitions (repair all): "
            "people/john.md:9='[^e1]: Sources: ...'; "
            "people/maria.md:12='[^e2]: Sources: ...'"
        )),
        None,
    ])
    monkeypatch.setattr(
        "src.management.agent._commit_turn",
        lambda *_args, **_kwargs: next(outcomes),
    )
    agent = QueuedAgent([{}, {}])

    audit = _run_agent(tmp_path, agent=agent, task="integrate")

    repair_prompt = agent.calls[1]["prompt"]
    assert "Repair EVERY listed definition" in repair_prompt
    assert "people/john.md:9" in repair_prompt
    assert "people/maria.md:12" in repair_prompt
    assert "Do not stop after fixing the first entry" in repair_prompt
    assert (
        audit[-1]["generic_repair"][0]["mode"]
        == "malformed_footnote_definitions"
    )


def test_generic_repair_rejects_after_configured_limit(
    tmp_path: Path, monkeypatch,
):
    outcomes = iter(["initial", "repair one", "repair two", "repair three"])
    monkeypatch.setattr(
        "src.management.agent._commit_turn",
        lambda *_args, **_kwargs: next(outcomes),
    )
    agent = QueuedAgent([{}, {}, {}, {}])

    with pytest.raises(RuntimeError, match="after 3 trajectories"):
        _run_agent(
            tmp_path,
            agent=agent,
            task="organize",
            config=MemoryConfig(generic_repair_max_trajectories=3),
            live_audit_path=tmp_path / "writer-live.jsonl",
        )

    assert len(agent.calls) == 4
    repair_events = [
        json.loads(line)
        for line in (tmp_path / "writer-live.jsonl").read_text().splitlines()
        if '"tool": "generic_repair"' in line
    ]
    assert [event["status"] for event in repair_events] == [
        "started", "rejected", "started", "rejected", "started", "rejected",
    ]
    assert repair_events[-1]["error"] == "repair three"


def test_generic_repair_does_not_stop_early_on_repeated_error(
    tmp_path: Path, monkeypatch,
):
    repeated = CommitFailure(ValueError("duplicate footnote definition: e-dead"))
    outcomes = iter([repeated, repeated, repeated, None])
    monkeypatch.setattr(
        "src.management.agent._commit_turn",
        lambda *_args, **_kwargs: next(outcomes),
    )
    agent = QueuedAgent([{}, {}, {}, {}])

    audit = _run_agent(
        tmp_path,
        agent=agent,
        task="organize",
        config=MemoryConfig(generic_repair_max_trajectories=3),
    )

    assert len(agent.calls) == 4
    assert "duplicate-footnote repair" in agent.calls[1]["prompt"]
    guards = [row for row in audit if row.get("tool") == "generic_repair_guard"]
    assert [row["status"] for row in guards] == ["continuing"]


@pytest.mark.parametrize(
    ("failure", "mode", "expected"),
    [
        (
            "undefined footnote: e-dead",
            "undefined_footnote",
            [
                "undefined-footnote repair",
                "Restore the exact existing definition",
                "Do not invent a definition",
            ],
        ),
        (
            "unused footnote definition: e-orphan",
            "unused_footnote_definition",
            [
                "unused-footnote-definition repair",
                "If it is truly unreferenced, remove only that definition",
                "restore only that exact citation",
            ],
        ),
        (
            "duplicate memory_id: dd877f2d",
            "duplicate_memory_id",
            [
                "duplicate-memory-ID repair",
                "preserve the ID exactly once on its original committed fact",
                "The Runtime, not the model, assigns new IDs",
            ],
        ),
    ],
)
def test_structural_repair_receives_targeted_guidance(
    tmp_path: Path, monkeypatch, failure, mode, expected,
):
    outcomes = iter([CommitFailure(TopicFormatError(failure)), None])
    monkeypatch.setattr(
        "src.management.agent._commit_turn",
        lambda *_args, **_kwargs: next(outcomes),
    )
    agent = QueuedAgent([{}, {}])

    audit = _run_agent(tmp_path, agent=agent, task="integrate")

    prompt = agent.calls[1]["prompt"]
    for fragment in expected:
        assert fragment in prompt
    assert audit[-1]["generic_repair"][0]["mode"] == mode


@pytest.mark.parametrize(
    ("failure", "mode", "expected"),
    [
        (
            "undefined Core footnote: e3",
            "undefined_core_footnote",
            "Never invent a footnote, source handle, or block ID",
        ),
        (
            "Source Memory is append-only",
            "source_append_only",
            "byte-for-byte identical to the committed workspace",
        ),
    ],
)
def test_luna_strict_validation_repair_is_opt_in(
    tmp_path: Path, monkeypatch, failure, mode, expected,
):
    outcomes = iter([CommitFailure(ValueError(failure)), None])
    monkeypatch.setattr(
        "src.management.agent._commit_turn",
        lambda *_args, **_kwargs: next(outcomes),
    )
    agent = QueuedAgent([{}, {}])
    audit = _run_agent(
        tmp_path,
        agent=agent,
        task="integrate",
        config=MemoryConfig(luna_strict_validation_repair_enabled=True),
    )
    assert expected in agent.calls[1]["prompt"]
    assert audit[-1]["generic_repair"][0]["mode"] == mode


def test_luna_strict_validation_repair_default_keeps_existing_guidance(
    tmp_path: Path, monkeypatch,
):
    outcomes = iter([
        CommitFailure(ValueError("undefined Core footnote: e3")), None,
    ])
    monkeypatch.setattr(
        "src.management.agent._commit_turn",
        lambda *_args, **_kwargs: next(outcomes),
    )
    agent = QueuedAgent([{}, {}])
    audit = _run_agent(tmp_path, agent=agent, task="integrate")

    assert "strictly local Core citation repair" not in agent.calls[1]["prompt"]
    assert audit[-1]["generic_repair"][0]["mode"] == "generic_validation"


@pytest.mark.parametrize("enabled", [False, True])
def test_validation_diagnostic_aggregation_reaches_repair_only_when_enabled(
    tmp_path: Path, monkeypatch, enabled: bool,
):
    outcomes = iter([
        CommitFailure(
            ValueError("undefined Core footnote: e3"),
            ("Topic sources: missing source references (repair all): D1:99 "
             "file=topics/a.md block=abc123",),
        ),
        None,
    ])
    monkeypatch.setattr(
        "src.management.agent._commit_turn",
        lambda *_args, **_kwargs: next(outcomes),
    )
    agent = QueuedAgent([{}, {}])
    audit = _run_agent(
        tmp_path, agent=agent, task="integrate",
        config=MemoryConfig(validation_diagnostic_aggregation_enabled=enabled),
    )
    prompt = agent.calls[1]["prompt"]
    assert "undefined Core footnote: e3" in prompt
    assert ("file=topics/a.md block=abc123" in prompt) is enabled
    assert audit[-1]["generic_repair"][0]["mode"] == "generic_validation"


def test_validation_diagnostic_aggregation_does_not_truncate_repair_feedback(
    tmp_path: Path, monkeypatch,
):
    final_finding = "file=core.md line=63 footnote=e-final"
    outcomes = iter([
        CommitFailure(
            ValueError("duplicate footnote definitions (repair all): id=e1"),
            ("Sources: missing source references (repair all): "
             + "x" * 6100 + final_finding,),
        ),
        None,
    ])
    monkeypatch.setattr(
        "src.management.agent._commit_turn",
        lambda *_args, **_kwargs: next(outcomes),
    )
    agent = QueuedAgent([{}, {}])
    _run_agent(
        tmp_path, agent=agent, task="integrate",
        config=MemoryConfig(validation_diagnostic_aggregation_enabled=True),
    )
    assert final_finding in agent.calls[1]["prompt"]


def test_aggregated_undefined_topic_footnotes_reach_one_repair_prompt(
    tmp_path: Path, monkeypatch,
):
    findings = (
        "Topic format: undefined Topic footnotes (repair all): "
        "id=e3 file=food/a.md line=12 block=abc123 | "
        "id=e4 file=food/a.md line=14 block=def456 | "
        "id=e1 file=food/b.md line=7 block=fedcba"
    )
    outcomes = iter([
        CommitFailure(
            TopicFormatError(
                "undefined footnote: e3 file=food/a.md line=12 block=abc123"
            ),
            (findings,),
        ),
        None,
    ])
    monkeypatch.setattr(
        "src.management.agent._commit_turn",
        lambda *_args, **_kwargs: next(outcomes),
    )
    agent = QueuedAgent([{}, {}])
    audit = _run_agent(
        tmp_path, agent=agent, task="integrate",
        config=MemoryConfig(validation_diagnostic_aggregation_enabled=True),
    )

    prompt = agent.calls[1]["prompt"]
    assert findings in prompt
    assert "Inspect EVERY listed citation" in prompt
    assert audit[-1]["generic_repair"][0]["mode"] == "undefined_topic_footnotes"


def test_removed_block_id_repair_receives_complete_local_restore_guidance(
    tmp_path: Path, monkeypatch,
):
    outcomes = iter([
        CommitFailure(ValueError(
            "block ID must not be removed: deadbeef, cafe1234, face5678"
        )),
        None,
    ])
    monkeypatch.setattr(
        "src.management.agent._commit_turn",
        lambda *_args, **_kwargs: next(outcomes),
    )
    agent = QueuedAgent([{}, {}])

    audit = _run_agent(tmp_path, agent=agent, task="organize")

    prompt = agent.calls[1]["prompt"]
    assert "`deadbeef, cafe1234, face5678`" in prompt
    assert "Restore EVERY listed ID" in prompt
    assert "restoring the original paragraph verbatim" in prompt
    assert "not a new organization" in prompt
    assert "stop after the first listed ID" in prompt
    assert "each listed ID exactly once" in prompt
    assert audit[-1]["generic_repair"][0]["mode"] == "removed_block_id"


def test_optional_maintenance_discards_rejected_candidate_as_noop(
    tmp_path: Path, monkeypatch,
):
    failure = CommitFailure(ValueError("block ID must not be removed: deadbeef"))
    monkeypatch.setattr(
        "src.management.agent._commit_turn",
        lambda *_args, **_kwargs: failure,
    )
    agent = QueuedAgent([{}, {}, {}, {}])

    audit = _run_agent(
        tmp_path,
        agent=agent,
        task="optional organize",
        config=MemoryConfig(generic_repair_max_trajectories=3),
        allow_rejected_noop=True,
    )

    assert len(agent.calls) == 4
    assert audit[-2] == {
        "tool": "optional_maintenance",
        "status": "discarded_noop",
        "reason": "validation_failed_after_repair_budget",
        "error": "ValueError: block ID must not be removed: deadbeef",
        "repair_trajectories": 3,
    }


def test_core_capacity_prompt_exposes_other_preflight_findings_when_enabled(
    tmp_path: Path, monkeypatch,
):
    outcomes = iter([
        CommitFailure(
            CoreCapacityError(token_count=2_158, limit=2_000),
            (
                "Core: Core Memory exceeds 2000 tokens: 2158",
                "Core: undefined Core footnotes (repair all): "
                "id=e1 file=core.md lines=[3]",
                "Topic format: memory source footnotes required (repair all): "
                "block=abc123 file=a.md line=5",
            ),
        ),
        None,
    ])
    monkeypatch.setattr(
        "src.management.agent._commit_turn",
        lambda *_args, **_kwargs: next(outcomes),
    )
    agent = QueuedAgent([{}, {}])

    _run_agent(
        tmp_path, agent=agent, task="integrate",
        config=MemoryConfig(
            core_max_tokens=2_000,
            validation_diagnostic_aggregation_enabled=True,
        ),
    )

    prompt = agent.calls[1]["prompt"]
    assert "id=e1 file=core.md lines=[3]" in prompt
    assert "block=abc123 file=a.md line=5" in prompt
    assert "passed the ordinary memory checks" not in prompt
    assert "capacity-only" in prompt


def test_core_capacity_repair_is_conditional_and_uses_exact_count_tool(
    tmp_path: Path, monkeypatch,
):
    outcomes = iter([
        CommitFailure(CoreCapacityError(token_count=2_158, limit=2_000)),
        None,
    ])
    monkeypatch.setattr(
        "src.management.agent._commit_turn",
        lambda *_args, **_kwargs: next(outcomes),
    )
    agent = QueuedAgent([
        {"text": "initial"},
        {
            "text": "capacity repaired",
            "tool_calls": [("core_token_count", {})],
        },
    ])

    audit = _run_agent(
        tmp_path,
        agent=agent,
        task="integrate",
        config=MemoryConfig(
            core_max_tokens=2_000,
            core_repair_target_tokens=1_800,
        ),
    )

    assert [tool.name for tool in agent.calls[0]["tools"]] == WORKSPACE_TOOLS
    assert [tool.name for tool in agent.calls[1]["tools"]] == [
        *WORKSPACE_TOOLS, "ReadCommitted", "core_token_count",
    ]
    assert "normally stop near 1800 tokens" in agent.calls[1]["prompt"]
    assert "complete edits remain staged" in agent.calls[1]["prompt"]
    assert "Do not redo the original task" in agent.calls[1]["prompt"]
    assert audit[-1]["core_repair"] == [{
        "trajectory": 1,
        "mode": "incremental_staged",
        "trigger_token_count": 2_158,
        "hard_limit": 2_000,
        "target": 1_800,
        "checks": [0],
        "stagnated": False,
        "status": "ok",
        "error": None,
    }]


def test_luna_topic_routing_protocol_is_prompt_only(tmp_path: Path):
    agent = QueuedAgent([{"text": "core-only initial trajectory"}])

    audit = memory.write_sessions(
        tmp_path,
        agent=agent,
        sessions=[{
            "observation_date": "2026-01-01",
            "turns": [("user", "I like tea.")],
            "refs": ["D1:1"],
        }],
        config=MemoryConfig(luna_topic_routing_protocol_enabled=True),
    )

    assert len(agent.calls) == 1
    prompt = agent.calls[0]["prompt"]
    assert "project updates" in prompt
    assert "stable cross-topic context" in prompt
    assert "active ongoing work" not in prompt
    assert "luna_topic_routing_repair" not in audit[-1]


def test_luna_topic_routing_protocol_is_off_by_default(tmp_path: Path):
    agent = QueuedAgent([{}])

    audit = memory.write_sessions(
        tmp_path,
        agent=agent,
        sessions=[{
            "observation_date": "2026-01-01",
            "turns": [("user", "I like tea.")],
            "refs": ["D1:1"],
        }],
    )

    assert len(agent.calls) == 1
    assert "Luna Topic-routing protocol" not in agent.calls[0]["prompt"]
    assert "luna_topic_routing_repair" not in audit[-1]


def test_luna_topic_routing_protocol_has_distinct_protocol_hash():
    from src.management.api import writer_protocol_sha256

    default_hash = writer_protocol_sha256()
    assert default_hash == writer_protocol_sha256(MemoryConfig())
    assert default_hash != writer_protocol_sha256(MemoryConfig(
        luna_topic_routing_protocol_enabled=True,
    ))


def test_luna_topic_routing_guidance_resolves_core_ambiguity():
    from src.management.prompts import LUNA_TOPIC_ROUTING_GUIDANCE

    assert "project updates" in (
        LUNA_TOPIC_ROUTING_GUIDANCE
    )
    assert "stable cross-topic context" in (
        LUNA_TOPIC_ROUTING_GUIDANCE
    )
    assert "Core summary never substitutes" in (
        LUNA_TOPIC_ROUTING_GUIDANCE
    )
    assert "Classify information by how it must be accessed later" in (
        LUNA_TOPIC_ROUTING_GUIDANCE
    )
    assert "durable identity or self-definition" in LUNA_TOPIC_ROUTING_GUIDANCE
    assert "should not be omitted from Core" in LUNA_TOPIC_ROUTING_GUIDANCE
    assert "do not create Core content to satisfy a quota" in (
        LUNA_TOPIC_ROUTING_GUIDANCE
    )
    assert "keep only its concise, currently stable meaning in Core" in (
        LUNA_TOPIC_ROUTING_GUIDANCE
    )
    assert "favor Topic" in LUNA_TOPIC_ROUTING_GUIDANCE
    assert "do not use uncertainty as a reason to omit" in (
        LUNA_TOPIC_ROUTING_GUIDANCE
    )
    assert "Before finishing, check every supplied session" in (
        LUNA_TOPIC_ROUTING_GUIDANCE
    )
    assert "Do not add filler or enforce a paragraph quota" in (
        LUNA_TOPIC_ROUTING_GUIDANCE
    )


def test_luna_validates_core_sources_before_capacity(tmp_path: Path):
    (tmp_path / "core.md").write_text(
        ("Unsupported persistent fact. " * 100) + "[^e1]\n",
        encoding="utf-8",
    )
    workspace = memory.MemoryWorkspace(
        tmp_path,
        config=MemoryConfig(
            core_max_tokens=1,
            luna_topic_routing_protocol_enabled=True,
        ),
    )

    with pytest.raises(ValueError, match="undefined Core footnote"):
        workspace._synchronize()


def test_core_reports_all_undefined_footnotes_with_lines(tmp_path: Path):
    (tmp_path / "core.md").write_text(
        "First unsupported fact.[^a]\nSecond unsupported fact.[^b]\n",
        encoding="utf-8",
    )
    workspace = memory.MemoryWorkspace(tmp_path)
    with pytest.raises(ValueError, match="undefined Core footnotes") as exc:
        workspace._validate_core_sources(workspace.stage_dir / "core.md")
    assert "id=a file=core.md lines=[1]" in str(exc.value)
    assert "id=b file=core.md lines=[2]" in str(exc.value)


def test_source_append_only_error_names_changed_file(tmp_path: Path):
    workspace = MemoryWorkspace(tmp_path)
    workspace.archive_sessions([{
        "observation_date": "2026-01-01",
        "turns": [("user", "Archived evidence")],
        "refs": ["D1:1"],
    }])
    baseline = workspace.baseline()
    source = workspace.stage_dir / "sources" / "D1.md"
    source.write_text(source.read_text(encoding="utf-8") + "tampered\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Source Memory is append-only") as exc:
        workspace.commit_edits(*baseline)
    assert "sources/D1.md" in str(exc.value)


@pytest.mark.parametrize(
    ("message", "mode", "guidance"),
    [
        (
            "duplicate footnote definitions (repair all): "
            "id=e1 file=a.md lines=[2, 3] occurrences=2",
            "duplicate_footnote_definitions", "EVERY listed file and line",
        ),
        (
            "unused footnote definitions (repair all): id=e2 file=a.md line=4",
            "unused_footnote_definitions", "EVERY listed definition",
        ),
        (
            "duplicate memory_ids (repair all): "
            "id=abc first=a.md:1 duplicate=b.md:3",
            "duplicate_memory_ids", "EVERY listed ID",
        ),
        (
            "unused Core footnotes (repair all): id=e3 file=core.md line=5",
            "unused_core_footnotes", "EVERY listed unused Core definition",
        ),
    ],
)
def test_aggregated_diagnostics_keep_matching_repair_modes(message, mode, guidance):
    assert _generic_repair_mode(message) == mode
    assert guidance in _generic_repair_guidance(message)


def test_aggregated_core_missing_uses_strict_repair_guidance():
    mode, guidance = _luna_strict_validation_guidance(
        "undefined Core footnotes (repair all): "
        "id=e1 file=core.md lines=[2] | id=e2 file=core.md lines=[4]"
    )
    assert mode == "undefined_core_footnotes"
    assert "EVERY listed citation" in guidance


def test_default_keeps_capacity_check_before_core_sources(tmp_path: Path):
    (tmp_path / "core.md").write_text(
        ("Unsupported persistent fact. " * 100) + "[^e1]\n",
        encoding="utf-8",
    )
    workspace = memory.MemoryWorkspace(
        tmp_path,
        config=MemoryConfig(core_max_tokens=1),
    )

    with pytest.raises(CoreCapacityError):
        workspace._synchronize()


def test_disabled_core_capacity_limit_keeps_core_source_validation(tmp_path: Path):
    (tmp_path / "core.md").write_text(
        ("Unsupported persistent fact. " * 100) + "[^e1]\n",
        encoding="utf-8",
    )
    workspace = memory.MemoryWorkspace(
        tmp_path,
        config=MemoryConfig(
            core_max_tokens=1,
            core_capacity_limit_enabled=False,
            luna_topic_routing_protocol_enabled=True,
        ),
    )

    with pytest.raises(ValueError, match="Core") as caught:
        workspace._synchronize()
    assert not isinstance(caught.value, CoreCapacityError)


def test_disabled_core_capacity_limit_accepts_oversized_valid_core(tmp_path: Path):
    (tmp_path / "sources").mkdir()
    (tmp_path / "sources" / "D1.md").write_text(
        '<a id="d1-1"></a>\n', encoding="utf-8",
    )
    (tmp_path / "core.md").write_text(
        ("A stable preference " * 100)
        + ".[^e1]\n\n[^e1]: Time: `undated`; Sources: [D1:1](sources/D1.md#d1-1)\n",
        encoding="utf-8",
    )
    workspace = memory.MemoryWorkspace(
        tmp_path,
        config=MemoryConfig(core_max_tokens=1, core_capacity_limit_enabled=False),
    )

    workspace._synchronize()


def test_core_capacity_repair_honors_full_trajectory_limit(
    tmp_path: Path, monkeypatch,
):
    outcomes = iter([
        CommitFailure(CoreCapacityError(token_count=2_665, limit=2_000)),
        CommitFailure(CoreCapacityError(token_count=2_239, limit=2_000)),
        CommitFailure(CoreCapacityError(token_count=2_158, limit=2_000)),
    ])
    monkeypatch.setattr(
        "src.management.agent._commit_turn",
        lambda *_args, **_kwargs: next(outcomes),
    )
    agent = QueuedAgent([{}, {}, {}])

    with pytest.raises(
        RuntimeError, match="core-capacity repair was rejected"
    ) as captured:
        _run_agent(
            tmp_path,
            agent=agent,
            task="integrate",
            config=MemoryConfig(
                core_max_tokens=2_000,
                core_repair_target_tokens=1_800,
            ),
        )

    assert len(agent.calls) == 3
    repair_records = [
        row for row in captured.value.somem_audit
        if row.get("tool") == "core_capacity_repair"
    ]
    assert [row["trajectory"] for row in repair_records] == [1, 2]


def test_core_capacity_repair_can_be_disabled(
    tmp_path: Path, monkeypatch,
):
    monkeypatch.setattr(
        "src.management.agent._commit_turn",
        lambda *_args, **_kwargs: CommitFailure(
            CoreCapacityError(token_count=4_001, limit=4_000)
        ),
    )
    agent = QueuedAgent([{}])

    with pytest.raises(
        RuntimeError, match="automatic capacity repair is disabled"
    ) as captured:
        _run_agent(
            tmp_path,
            agent=agent,
            task="integrate",
            config=MemoryConfig(
                core_max_tokens=4_000,
                core_repair_target_tokens=3_600,
                core_capacity_repair_enabled=False,
            ),
        )

    assert "4001" in str(captured.value)
    assert len(agent.calls) == 1


def test_luna_core_capacity_prompt_requires_verbatim_moves(
    tmp_path: Path, monkeypatch,
):
    outcomes = iter([
        CommitFailure(CoreCapacityError(token_count=3_764, limit=2_000)),
        None,
    ])
    monkeypatch.setattr(
        "src.management.agent._commit_turn",
        lambda *_args, **_kwargs: next(outcomes),
    )
    agent = QueuedAgent([{}, {}])

    _run_agent(
        tmp_path,
        agent=agent,
        task="integrate",
        config=MemoryConfig(
            core_max_tokens=2_000,
            core_repair_target_tokens=1_800,
            luna_core_capacity_prompt_enabled=True,
        ),
    )

    prompt = agent.calls[1]["prompt"]
    assert "move whole Core paragraphs" in prompt
    assert "Move each paragraph verbatim" in prompt
    assert "do not create a second definition" in prompt
    assert "Structural metadata never counts as expendable text" in prompt
    assert "at or below 1800" in prompt


def test_luna_repairs_structural_error_only_after_core_compression(
    tmp_path: Path, monkeypatch,
):
    outcomes = iter([
        CommitFailure(CoreCapacityError(token_count=2_158, limit=2_000)),
        CommitFailure(ValueError("block ID must not be removed: deadbeef")),
        None,
    ])
    monkeypatch.setattr(
        "src.management.agent._commit_turn",
        lambda *_args, **_kwargs: next(outcomes),
    )
    agent = QueuedAgent([{}, {}, {"text": "minimal structural repair"}])

    audit = _run_agent(
        tmp_path,
        agent=agent,
        task="integrate",
        config=MemoryConfig(
            core_max_tokens=2_000,
            core_repair_target_tokens=1_800,
            luna_core_structural_repair_enabled=True,
        ),
    )

    assert len(agent.calls) == 3
    prompt = agent.calls[2]["prompt"]
    assert "block ID must not be removed: deadbeef" in prompt
    assert "smallest possible edit" in prompt
    assert "not a new compression or a rewrite" in prompt
    assert "Do not redo the original Writer task" in prompt
    assert audit[-1]["luna_core_structural_repair"] == [{
        "trajectory": 1,
        "mode": "minimal_post_compression",
        "trigger_error": (
            "ValueError: block ID must not be removed: deadbeef"
        ),
        "status": "ok",
        "error": None,
    }]


def test_core_structural_repair_stays_disabled_for_other_models(
    tmp_path: Path, monkeypatch,
):
    outcomes = iter([
        CommitFailure(CoreCapacityError(token_count=2_158, limit=2_000)),
        CommitFailure(ValueError("block ID must not be removed: deadbeef")),
    ])
    monkeypatch.setattr(
        "src.management.agent._commit_turn",
        lambda *_args, **_kwargs: next(outcomes),
    )
    agent = QueuedAgent([{}, {}])

    with pytest.raises(RuntimeError, match="core-capacity repair was rejected"):
        _run_agent(
            tmp_path,
            agent=agent,
            task="integrate",
            config=MemoryConfig(
                core_max_tokens=2_000,
                core_repair_target_tokens=1_800,
            ),
        )

    assert len(agent.calls) == 2


def test_luna_core_structural_repair_does_not_handle_unrelated_failure(
    tmp_path: Path, monkeypatch,
):
    outcomes = iter([
        CommitFailure(CoreCapacityError(token_count=2_158, limit=2_000)),
        CommitFailure(ValueError("unexpected unrelated validation")),
    ])
    monkeypatch.setattr(
        "src.management.agent._commit_turn",
        lambda *_args, **_kwargs: next(outcomes),
    )
    agent = QueuedAgent([{}, {}])

    with pytest.raises(RuntimeError, match="core-capacity repair was rejected"):
        _run_agent(
            tmp_path,
            agent=agent,
            task="integrate",
            config=MemoryConfig(
                core_max_tokens=2_000,
                core_repair_target_tokens=1_800,
                luna_core_structural_repair_enabled=True,
            ),
        )

    assert len(agent.calls) == 2


def test_core_token_tool_enforces_check_and_stagnation_limits(tmp_path: Path):
    workspace = MemoryWorkspace(tmp_path)
    state = CoreRepairState(
        limit=2_000,
        target=1_800,
        max_checks=3,
        stagnation_limit=2,
    )
    audit = []
    tools = {
        definition.name: definition
        for definition in management_tools(
            workspace, audit, core_repair_state=state
        )
    }

    first = asyncio.run(tools["core_token_count"].handler({}))
    second = asyncio.run(tools["core_token_count"].handler({}))
    third = asyncio.run(tools["core_token_count"].handler({}))
    exhausted = asyncio.run(tools["core_token_count"].handler({}))

    assert first["is_error"] is False
    assert second["is_error"] is False
    assert third["is_error"] is False
    assert state.checks == [0, 0, 0]
    assert state.stagnated is True
    assert exhausted["is_error"] is True
    assert audit[-1]["status"] == "error"


def test_writer_commits_file_edits_against_existing_committed_memory(tmp_path: Path):
    first = "# A\n\nFirst fact.[^e1]\n\n[^e1]: Time: `2026-01-01`; Sources: D1:1\n"
    _run_agent(
        tmp_path, agent=ScriptedAgent([("Write", {"path": "topics/a.md", "content": first})]),
        task="initial write", source_sessions=[{
            "observation_date": "2026-01-02",
            "turns": [("user", "First fact."), ("user", "Second fact.")],
            "refs": ["D1:1", "D1:2"],
        }],
    )
    first_id = parse_topic_tree(tmp_path / "topics")[0].memory_id
    agent = ScriptedAgent([
        ("Edit", {"path": "topics/a.md", "old_string": "First fact.",
                  "new_string": "Updated first fact."}),
        ("Write", {"path": "topics/b.md", "content":
                   "# B\n\nSecond fact.[^e2]\n\n"
                   "[^e2]: Time: `2026-01-02`; Sources: D1:2\n"}),
    ])
    audit = _run_agent(tmp_path, agent=agent, task="incremental update")
    units = {unit.topic_path: unit for unit in parse_topic_tree(tmp_path / "topics")}
    assert units["a.md"].memory_id == first_id
    assert units["a.md"].content == "Updated first fact."
    assert units["b.md"].content == "Second fact."
    assert [record["tool"] for record in audit].count("commit") == 1


def test_memory_workspace_runs_normal_shell_commands_from_memory_dir(tmp_path: Path):
    workspace = MemoryWorkspace(tmp_path)
    workspace.archive_sessions([{
        "observation_date": "2026-01-01",
        "turns": [("user", "three tanks")],
        "refs": ["D1:1"],
    }])

    result = workspace.shell(
        "mkdir -p topics/aquarium && "
        "printf '%s\\n' '# Tanks' '###### Acquisition history' '' "
        "'On 2026-01-01, there were three tanks."
        "[^new-evidence-tanks] ^new-block-tanks' '' "
        "'[^new-evidence-tanks]: Time: `2026-01-01`; Sources: D1:1' "
        "> topics/aquarium/tanks.md && "
        "find topics -type f -print"
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "topics/aquarium/tanks.md"
    assert "###### Acquisition history" in (
        workspace.stage_dir / "topics/aquarium/tanks.md"
    ).read_text()
    assert (tmp_path / "topics/aquarium/tanks.md").exists()


def test_memory_workspace_decodes_bash_output_as_utf8(tmp_path: Path):
    workspace = MemoryWorkspace(tmp_path)

    result = workspace.shell("printf '%s' '中文记忆'")

    assert result.returncode == 0
    assert result.stdout == "中文记忆"


def test_read_only_shell_accepts_uncommitted_builtin_topic_edits(tmp_path: Path):
    workspace = MemoryWorkspace(tmp_path)
    workspace.archive_sessions([{
        "observation_date": "2026-01-01",
        "turns": [("user", "A staged fact")],
        "refs": ["D1:1"],
    }])
    topic = workspace.stage_dir / "topics/a.md"
    topic.write_text(
        "# A\n\nA staged fact.[^e1]\n\n"
        "[^e1]: Time: `2026-01-01`; Sources: D1:1\n",
        encoding="utf-8",
    )

    result = workspace.shell("ls topics")

    assert result.returncode == 0
    assert result.stdout.strip() == "a.md"
    assert "Sources: D1:1" in topic.read_text(encoding="utf-8")
    assert not (tmp_path / "topics/a.md").exists()


def test_shell_change_commits_pending_builtin_topic_edits(tmp_path: Path):
    workspace = MemoryWorkspace(tmp_path)
    workspace.archive_sessions([{
        "observation_date": "2026-01-01",
        "turns": [("user", "A staged fact")],
        "refs": ["D1:1"],
    }])
    topic = workspace.stage_dir / "topics/a.md"
    topic.write_text(
        "# A\n\nA staged fact.[^e1]\n\n"
        "[^e1]: Time: `2026-01-01`; Sources: D1:1\n",
        encoding="utf-8",
    )

    result = workspace.shell(
        "mkdir -p topics/moved && mv topics/a.md topics/moved/a.md"
    )

    assert result.returncode == 0
    committed = (tmp_path / "topics/moved/a.md").read_text(encoding="utf-8")
    assert "Sources: [D1:1](../../sources/" in committed
    assert "[^e1]" not in committed


def test_normalization_scopes_reused_local_evidence_labels_by_occurrence(
    tmp_path: Path,
):
    workspace = MemoryWorkspace(tmp_path)
    workspace.archive_sessions([{
        "observation_date": "2026-01-01",
        "turns": [("user", "First fact"), ("user", "Second fact")],
        "refs": ["D1:1", "D1:2"],
    }])
    topic = workspace.stage_dir / "topics/a.md"
    topic.parent.mkdir(parents=True, exist_ok=True)
    topic.write_text(
        "# A\n\n"
        "First fact.[^e1]\n\n"
        "[^e1]: Time: `2026-01-01`; Sources: D1:1\n\n"
        "Second fact.[^e1]\n\n"
        "[^e1]: Time: `2026-01-02`; Sources: D1:2\n",
        encoding="utf-8",
    )

    workspace._normalize_topic_edits(set())

    rendered = topic.read_text(encoding="utf-8")
    evidence_ids = re.findall(r"(?m)^\[\^(e-[a-f0-9]+)\]:", rendered)
    assert len(evidence_ids) == 2
    assert len(set(evidence_ids)) == 2
    assert "[^e1]" not in rendered
    assert len(parse_topic_tree(workspace.stage_dir / "topics")) == 2


@pytest.mark.parametrize("label", ["1", "s1", "e-NEW1"])
def test_normalization_accepts_provider_short_evidence_labels(
    tmp_path: Path, label: str
):
    workspace = MemoryWorkspace(tmp_path)
    topic = workspace.stage_dir / "topics" / "people" / "person.md"
    topic.parent.mkdir(parents=True)
    topic.write_text(
        f"# Person\n\nA grounded fact.[^{label}]\n\n"
        f"[^{label}]: Time: `2026-01-01`; Sources: D1:1\n",
        encoding="utf-8",
    )

    workspace._normalize_topic_edits(set())

    rendered = topic.read_text(encoding="utf-8")
    assert f"[^{label}]" not in rendered
    assert re.search(r"\[\^e-[0-9a-f]{10}\]", rendered)
    assert re.search(r"\^[0-9a-f]{8}$", rendered, re.MULTILINE)
    assert len(parse_topic_tree(workspace.stage_dir / "topics")) == 1


def test_normalization_scopes_same_local_label_across_topic_files(
    tmp_path: Path,
):
    workspace = MemoryWorkspace(tmp_path)
    workspace.archive_sessions([{
        "observation_date": "2026-01-01",
        "turns": [("user", "Fact A"), ("user", "Fact B")],
        "refs": ["D1:1", "D1:2"],
    }])
    topics = workspace.stage_dir / "topics"
    topics.mkdir(parents=True, exist_ok=True)
    (topics / "a.md").write_text(
        "# A\n\nFact A.[^e1]\n\n"
        "[^e1]: Time: `2026-01-01`; Sources: D1:1\n",
        encoding="utf-8",
    )
    (topics / "b.md").write_text(
        "# B\n\nFact B.[^e1]\n\n"
        "[^e1]: Time: `2026-01-02`; Sources: D1:2\n",
        encoding="utf-8",
    )

    workspace._normalize_topic_edits(set())

    units = parse_topic_tree(topics)
    assert len(units) == 2
    assert len({item.evidence[0].citation_id for item in units}) == 2


def test_posix_bash_backend_supports_process_substitution(tmp_path: Path):
    workspace = MemoryWorkspace(
        tmp_path, config=MemoryConfig(shell_backend="posix-bash")
    )

    result = workspace.shell(
        "comm -23 <(printf '%s\\n' alpha beta) <(printf '%s\\n' beta)"
    )

    assert result.returncode == 0
    assert result.stdout == "alpha\n"


def test_refresh_stage_keeps_the_root_directory_stable(tmp_path: Path):
    workspace = MemoryWorkspace(tmp_path)
    stage = workspace.stage_dir
    assert stage.parent == tmp_path.parent
    (stage / "discard-me.txt").write_text("staged", encoding="utf-8")

    workspace._refresh_stage()

    assert workspace.stage_dir == stage
    assert stage.is_dir()
    assert not (stage / "discard-me.txt").exists()


def test_writer_delegates_tool_protocol_and_errors_to_agent(tmp_path: Path):
    valid = ("# Pets\n\nThe user bought a tank.[^e1]\n\n"
             "[^e1]: Time: `2023-05-23`; Sources: D1:1\n")
    agent = ScriptedAgent([
        ("Write", {"path": "sources/forbidden.md", "content": valid}),
        ("Write", {"path": "topics/pets.md", "content": valid}),
    ])

    audit = memory.write_session(
        tmp_path,
        agent=agent,
        observation_date="2023-05-23",
        turns=[("user", "I bought a tank")],
        refs=["D1:1"],
    )

    assert len(agent.calls) == 1
    assert [definition.name for definition in agent.calls[0]["tools"]] == WORKSPACE_TOOLS
    writes = [record for record in audit if record["tool"] == "Write"]
    assert [record["status"] for record in writes] == ["error", "ok"]
    assert "writes are allowed only" in writes[0]["output"]["error"]
    assert not (tmp_path / "sources/forbidden.md").exists()
    assert any(record["tool"] == "commit" and record["status"] == "ok"
               for record in audit)
    assert audit[-1]["tool"] == "agent"
    assert audit[-1]["status"] == "ok"
    assert audit[-1]["rounds"] == 3
    assert (tmp_path / "topics/pets.md").is_file()


def test_shell_keeps_time_metadata_out_of_natural_topic_prose(tmp_path: Path):
    workspace = MemoryWorkspace(tmp_path)
    workspace.archive_sessions([{
        "observation_date": "2023-05-08",
        "turns": [("user", "I painted that lake sunrise last year.")],
        "refs": ["D1:1"],
    }])

    result = workspace.shell(
        "mkdir -p topics/people && "
        "printf '%s\\n' '# Melanie' '' "
        "'Melanie painted a lake sunrise last year.  "
        "[^new-evidence-painting]^new-block-painting' '' "
        "'[^new-evidence-painting]: Time: `2022`; Sources: D1:1' "
        "> topics/people/melanie.md"
    )

    assert result.returncode == 0
    topic = (tmp_path / "topics/people/melanie.md").read_text()
    assert re.search(
        r"Melanie painted a lake sunrise last year\.\[\^e-[0-9a-f]{10}\] "
        r"\^[0-9a-f]{8}$",
        topic,
        re.MULTILINE,
    )
    unit = parse_topic_tree(tmp_path / "topics")[0]
    assert unit.content == "Melanie painted a lake sunrise last year."
    assert unit.evidence[0].when == "2022"


def test_shell_normalizes_an_unquoted_evidence_time(tmp_path: Path):
    workspace = MemoryWorkspace(tmp_path)
    workspace.archive_sessions([{
        "observation_date": "2026-01-01",
        "turns": [("user", "Remember this fact.")],
        "refs": ["D1:1"],
    }])

    workspace.shell(
        "mkdir -p topics/calibration && "
        "printf '%s\\n' '# Calibration' '' "
        "'Remember this fact.[^new-evidence-fact] ^new-block-fact' '' "
        "'[^new-evidence-fact]: Time: 2026-01-01; Sources: D1:1' "
        "> topics/calibration/facts.md"
    )

    topic = (tmp_path / "topics/calibration/facts.md").read_text()
    assert "Time: `2026-01-01`; Sources:" in topic
    assert parse_topic_tree(tmp_path / "topics")[0].evidence[0].when == "2026-01-01"


def test_shell_replaces_model_invented_block_id(tmp_path: Path):
    workspace = MemoryWorkspace(tmp_path)
    workspace.archive_sessions([{
        "observation_date": "2023-05-03",
        "turns": [("user", "I work at a local garage.")],
        "refs": ["D1:1"],
    }])

    workspace.shell(
        "mkdir -p topics/people && "
        "printf '%s\\n' '# Dave' '' "
        "'Dave works at a local garage.[^new-evidence-work] "
        "^1478d194b29awork' '' "
        "'[^new-evidence-work]: Time: `2023-05-03`; Sources: D1:1' "
        "> topics/people/dave.md"
    )

    topic = (tmp_path / "topics/people/dave.md").read_text()
    assert "1478d194b29awork" not in topic
    assert re.search(r"\^[0-9a-f]{8}$", topic, re.MULTILINE)


def test_shell_rejects_topic_file_link_without_block_target(tmp_path: Path):
    workspace = MemoryWorkspace(tmp_path)
    workspace.archive_sessions([{
        "observation_date": "2026-01-01",
        "turns": [("user", "Caroline and Melanie are friends.")],
        "refs": ["D1:1"],
    }])

    with pytest.raises(
        ValueError,
        match=r"Topic-to-Topic link must target #\^block-id",
    ):
        workspace.shell(
            "mkdir -p topics/people && "
            "printf '%s\\n' '# Caroline' '' "
            "'On 2026-01-01 Caroline was friends with [Melanie](melanie.md)."
            "[^new-evidence-friend] ^new-block-caroline' '' "
            "'[^new-evidence-friend]: Time: `2026-01-01`; Sources: D1:1' "
            "> topics/people/caroline.md && "
            "printf '%s\\n' '# Melanie' '' "
            "'On 2026-01-01 Melanie was friends with Caroline."
            "[^new-evidence-friend] ^new-block-melanie' '' "
            "'[^new-evidence-friend]: Time: `2026-01-01`; Sources: D1:1' "
            "> topics/people/melanie.md"
        )

    assert not (tmp_path / "topics/people/caroline.md").exists()


def test_shell_resolves_temporary_cross_topic_block_link(tmp_path: Path):
    workspace = MemoryWorkspace(tmp_path)
    workspace.archive_sessions([{
        "observation_date": "2026-01-01",
        "turns": [
            ("user", "Caroline and Melanie are friends."),
            ("user", "Melanie and Caroline are friends."),
        ],
        "refs": ["D1:1", "D1:2"],
    }])

    workspace.shell(
        "mkdir -p topics/people && "
        "printf '%s\\n' '# Caroline' '' "
        "'On 2026-01-01 Caroline was friends with Melanie.[^e1]' '' "
        "'[^e1]: Time: `2026-01-01`; Sources: D1:1' "
        "> topics/people/caroline.md && "
        "printf '%s\\n' '# Melanie' '' "
        "'On 2026-01-01 Melanie was friends with Caroline.[^e2]' '' "
        "'[^e2]: Time: `2026-01-01`; Sources: D1:2' "
        "> topics/people/melanie.md"
    )

    units = {unit.topic_path: unit for unit in parse_topic_tree(tmp_path / "topics")}
    target = units["people/melanie.md"]
    result = workspace.shell(
        "sed 's|friends with Melanie\\.|friends with "
        f"[Melanie](melanie.md#^{target.memory_id}).|' "
        "topics/people/caroline.md > topics/people/caroline.tmp && "
        "mv topics/people/caroline.tmp topics/people/caroline.md"
    )
    assert result.returncode == 0

    units = {unit.topic_path: unit for unit in parse_topic_tree(tmp_path / "topics")}
    source = units["people/caroline.md"]
    assert f"melanie.md#^{target.memory_id}" in source.content
    relations = json.loads((tmp_path / "relations.json").read_text())
    assert relations["outbound"][source.memory_id] == [target.memory_id]
    assert relations["backlinks"][target.memory_id] == [source.memory_id]


def test_save_memory_normalizes_topic_root_prefix(tmp_path: Path):
    workspace = MemoryWorkspace(tmp_path)
    workspace.archive_sessions([{
        "observation_date": "2023-05-20",
        "turns": [("user", "I started yoga.")],
        "refs": ["D1:1"],
    }])
    workspace.save_memory([{
        "when": "2023-05-20",
        "content": "The user started yoga.",
        "refs": ["D1:1"],
        "topic_path": "topics/health/yoga.md",
        "headings": ["Health", "Yoga"],
    }])

    assert (workspace.stage_dir / "topics/health/yoga.md").exists()
    assert not (workspace.stage_dir / "topics/topics").exists()


def test_save_memory_retries_colliding_eight_digit_block_ids(tmp_path: Path):
    workspace = MemoryWorkspace(tmp_path)
    workspace.archive_sessions([{
        "observation_date": "2026-01-01",
        "turns": [("user", "collision source")],
        "refs": ["D1:1"],
    }])
    events = [
        {
            "when": "2026-01-01",
            "content": f"Collision fact {index}.",
            "refs": ["D1:1"],
            "topic_path": "facts.md",
            "headings": ["Facts"],
        }
        for index in (99938, 168633)
    ]

    workspace.save_memory(events)
    units = parse_topic_tree(tmp_path / "topics")

    assert len(units) == 2
    assert all(re.fullmatch(r"[0-9a-f]{8}", unit.memory_id) for unit in units)
    assert len({unit.memory_id for unit in units}) == 2

    workspace.save_memory(events)
    assert len(parse_topic_tree(tmp_path / "topics")) == 2


def test_append_event_reuses_existing_heading_prefix(tmp_path: Path):
    path = tmp_path / "topics/projects/budget-tracker.md"
    base = {
        "when": "2024-03-15",
        "refs": ["D1:1"],
        "topic_path": "projects/budget-tracker.md",
    }
    MemoryWorkspace._append_event(path, {
        **base,
        "event_id": "ev_1111111111111111",
        "content": "Initial architecture.",
        "headings": ["Architecture", "Initial architecture"],
    })
    MemoryWorkspace._append_event(path, {
        **base,
        "event_id": "ev_2222222222222222",
        "content": "Application modules.",
        "headings": ["architecture", "Application modules"],
    })

    text = path.read_text()
    assert len(re.findall(r"(?mi)^#\s+architecture\s*$", text)) == 1
    assert text.count("## Initial architecture\n") == 1
    assert text.count("## Application modules\n") == 1


def test_manager_exposes_workspace_tools_and_uses_twenty_turn_limit(tmp_path):
    agent = ScriptedAgent()

    audit = manage_memory(tmp_path, agent=agent)

    assert [definition.name for definition in agent.calls[0]["tools"]] == WORKSPACE_TOOLS
    assert agent.calls[0]["max_turns"] == 20
    assert audit[-1]["status"] == "ok"


def test_local_organizer_receives_only_touched_topic_scope(tmp_path):
    agent = ScriptedAgent()

    organize_topics(
        tmp_path,
        agent=agent,
        touched={"topics/projects/a.md", "topics/projects/b.md"},
    )

    task = agent.calls[0]["prompt"]
    assert "Limit this maintenance pass to these topic files" in task
    assert "topics/projects/a.md" in task
    assert "topics/projects/b.md" in task


def test_writer_may_read_but_must_not_modify_archived_sources():
    from src.management.prompts import SYSTEM_PROMPT, WRITER_BATCH_TASK, WRITER_TASK

    for prompt in (WRITER_TASK, WRITER_BATCH_TASK):
        assert (
            "Inspect whichever existing Topic, Core, or Source files are useful."
            in prompt
        )
        assert "Do not modify files under sources/." in prompt
    assert "[^e1]" in SYSTEM_PROMPT
    assert (
        "[^e1]: Time: `<time>`; Sources: provider/thread_id/message_id"
        in SYSTEM_PROMPT
    )
    assert "Never invent a block ID." in SYSTEM_PROMPT


def test_workspace_structure_lists_all_shell_visible_memory_views(tmp_path):
    for relative in ("topics/home.md", "timeline/2026.md", "sources/D1.md"):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
    (tmp_path / "recent_events.jsonl").write_text("{}\n", encoding="utf-8")

    structure = MemoryWorkspace(tmp_path).structure()

    assert structure.splitlines() == [
        "recent_events.jsonl",
        "sources/D1.md",
        "timeline/2026.md",
        "topics/home.md",
    ]


def test_shell_can_read_all_memory_views(tmp_path):
    for relative in ("topics/home.md", "timeline/2026.md", "sources/D1.md"):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "# Home\n" if relative == "topics/home.md" else relative,
            encoding="utf-8",
        )
    (tmp_path / "recent_events.jsonl").write_text("recent", encoding="utf-8")
    workspace = MemoryWorkspace(tmp_path)

    result = workspace.shell(
        "cat topics/home.md timeline/2026.md sources/D1.md recent_events.jsonl"
    )

    assert result.returncode == 0
    assert result.stdout == "# Home\ntimeline/2026.mdsources/D1.mdrecent"


def test_agent_needs_no_final_persistence_action_when_model_finishes(tmp_path):
    audit = _run_agent(
        tmp_path,
        agent=ScriptedAgent(),
        task="organize",
    )

    assert len(audit) == 1
    assert audit[0]["tool"] == "agent"
    assert audit[0]["status"] == "ok"


def test_save_memory_commits_linked_views(tmp_path: Path):
    workspace = MemoryWorkspace(tmp_path)
    workspace.archive_sessions([{
        "observation_date": "2023-05-23",
        "turns": [("user", "I bought a quarantine tank.")],
        "refs": ["D18:11"],
    }])

    saved = workspace.save_memory([
        {
            "when": "2023-05-23",
            "content": "User bought a quarantine tank.",
            "refs": ["D18:11"],
            "topic_path": "pets/aquarium.md",
            "headings": ["Pets", "Aquarium"],
        }
    ])
    workspace._synchronize()

    topic = (tmp_path / "topics" / "pets" / "aquarium.md").read_text()
    timeline = (tmp_path / "timeline" / "2023" / "05" / "23.md").read_text()
    recent = json.loads((tmp_path / "recent_events.jsonl").read_text())
    source = (tmp_path / "sources" / "D18.md").read_text()

    assert saved == "saved 1 event"
    assert "<!-- memory-event:" not in topic
    memory_id = recent["memory_id"]
    assert f"^{memory_id}" in topic
    assert f"#^{memory_id}" in timeline
    assert "topics/pets/aquarium.md" in timeline
    assert "sources/D18.md#d18-11" in topic
    assert '<a id="d18-11"></a>' in source
    assert recent["topic_path"] == "topics/pets/aquarium.md"
    assert recent["headings"] == ["Pets", "Aquarium"]


def test_save_memory_synchronizes_all_views_immediately(tmp_path: Path):
    workspace = MemoryWorkspace(tmp_path)
    workspace.archive_sessions([{
        "observation_date": "2023-05-23",
        "turns": [("user", "I bought a quarantine tank.")],
        "refs": ["D18:11"],
    }])

    workspace.save_memory([{
        "when": "2023-05-23",
        "content": "User bought a quarantine tank.",
        "refs": ["D18:11"],
        "topic_path": "pets/aquarium.md",
        "headings": ["Pets", "Aquarium"],
    }])

    assert (tmp_path / "topics/pets/aquarium.md").exists()
    assert (tmp_path / "timeline/2023/05/23.md").exists()
    assert (tmp_path / "recent_events.jsonl").exists()


def test_recent_memory_keeps_only_the_latest_fifty_events_by_default(tmp_path: Path):
    workspace = MemoryWorkspace(tmp_path)
    turns = [("user", f"event {index}") for index in range(1, 52)]
    refs = [f"D1:{index}" for index in range(1, 52)]
    workspace.archive_sessions([{
        "observation_date": "2023-05-23",
        "turns": turns,
        "refs": refs,
    }])

    workspace.save_memory([{
        "when": "2023-05-23",
        "content": f"Event {index}.",
        "refs": [f"D1:{index}"],
        "topic_path": "events.md",
        "headings": ["Events"],
    } for index in range(1, 52)])

    recent = [
        json.loads(line)
        for line in (tmp_path / "recent_events.jsonl").read_text().splitlines()
    ]
    assert len(recent) == 50
    assert recent[0]["content"] == "Event 2."
    assert recent[-1]["content"] == "Event 51."


def test_shell_core_memory_changes_persist(tmp_path: Path):
    workspace = MemoryWorkspace(tmp_path)

    result = workspace.shell("printf '%s\\n' '# Core Memory' 'Stable preference' > core.md")

    assert result.returncode == 0
    assert (tmp_path / "core.md").read_text() == (
        "# Core Memory\nStable preference\n"
    )


def test_shell_normalizes_and_validates_core_source_references(tmp_path: Path):
    workspace = MemoryWorkspace(tmp_path)
    workspace.archive_sessions([{
        "observation_date": "2026-08-03",
        "turns": [("user", "My stable preference is jasmine tea.")],
        "refs": ["diagnostic/thread-1/msg-1"],
    }])

    workspace.shell(
        "cat > core.md <<'EOF'\n"
        "# Core Memory\n\n"
        "My stable preference is jasmine tea.[^new-evidence-tea] "
        "^new-block-tea\n\n"
        "[^new-evidence-tea]: Time: `2026-08-03`; Sources: "
        "diagnostic/thread-1/msg-1\n"
        "EOF"
    )

    text = (tmp_path / "core.md").read_text(encoding="utf-8")
    assert "new-evidence" not in text
    assert "new-block" not in text
    assert re.search(r"\^[0-9a-f]{8}$", text, re.MULTILINE)
    assert (
        "[diagnostic/thread-1/msg-1]"
    ) in text


def test_shell_rejects_missing_core_source_reference(tmp_path: Path):
    workspace = MemoryWorkspace(tmp_path)

    with pytest.raises(ValueError, match="missing source reference"):
        workspace.shell(
            "cat > core.md <<'EOF'\n"
            "# Core Memory\n\n"
            "Unsupported claim.[^new-evidence-claim] ^new-block-claim\n\n"
            "[^new-evidence-claim]: Time: `undated`; Sources: "
            "diagnostic/missing/msg-1\n"
            "EOF"
        )

    assert not (tmp_path / "core.md").exists()


def test_source_validation_aggregates_all_missing_handles(tmp_path: Path):
    workspace = MemoryWorkspace(tmp_path)

    with pytest.raises(ValueError, match="repair all") as exc:
        workspace.shell(
            "mkdir -p topics && cat > topics/a.md <<'EOF'\n"
            "# A\n\nOne.[^e1] ^abc123\n\nTwo.[^e2] ^def456\n\n"
            "[^e1]: Time: `undated`; Sources: diagnostic/missing/msg-1\n"
            "[^e2]: Time: `undated`; Sources: diagnostic/missing/msg-2\n"
            "EOF"
        )

    message = str(exc.value)
    assert "diagnostic/missing/msg-1" in message
    assert "diagnostic/missing/msg-2" in message


def test_aggregated_missing_sources_receive_one_repair_instruction(
    tmp_path: Path, monkeypatch,
):
    outcomes = iter([
        CommitFailure(ValueError(
            "missing source references (repair all): "
            "diagnostic/missing/msg-1; diagnostic/missing/msg-2"
        )),
        None,
    ])
    monkeypatch.setattr(
        "src.management.agent._commit_turn",
        lambda *_args, **_kwargs: next(outcomes),
    )
    agent = QueuedAgent([{}, {}])

    audit = _run_agent(tmp_path, agent=agent, task="integrate")

    prompt = agent.calls[1]["prompt"]
    assert "Repair EVERY listed invalid handle" in prompt
    assert "diagnostic/missing/msg-1" in prompt
    assert "diagnostic/missing/msg-2" in prompt
    assert "Do not guess IDs" in prompt
    assert audit[-1]["generic_repair"][0]["mode"] == "missing_source_reference"


def test_invalid_topic_handles_are_aggregated_before_link_rendering(tmp_path: Path):
    workspace = MemoryWorkspace(tmp_path)
    topic = workspace.stage_dir / "topics" / "a.md"
    topic.parent.mkdir(exist_ok=True)
    topic.write_text(
        "One.[^e1] ^abc123\n\nTwo.[^e2] ^def456\n\n"
        "[^e1]: Time: `undated`; Sources: [locomo/thread_111](../sources/a.md#x), "
        "[locomo/thread_222](../sources/b.md#y)\n"
        "[^e2]: Time: `undated`; Sources: [locomo/thread_333/msg_444](../sources/c.md#z)\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError) as caught:
        workspace._synchronize_block_topics(parse_topic_tree(topic.parent))
    message = str(caught.value)
    assert "invalid source references (repair all):" in message
    assert "locomo/thread_111 file=topics/a.md block=abc123" in message
    assert "locomo/thread_222 file=topics/a.md block=abc123" in message
    assert ("missing source references (repair all): locomo/thread_333/msg_444 "
            "file=topics/a.md block=def456") in message
    assert not (tmp_path / "topics" / "a.md").exists()


def test_invalid_sources_receive_targeted_repair(tmp_path: Path, monkeypatch):
    outcomes = iter([CommitFailure(ValueError(
        "invalid source references (repair all): locomo/thread_a; locomo/thread_b"
    )), None])
    monkeypatch.setattr("src.management.agent._commit_turn", lambda *_a, **_kw: next(outcomes))
    agent = QueuedAgent([{}, {}])
    audit = _run_agent(tmp_path, agent=agent, task="integrate")
    prompt = agent.calls[1]["prompt"]
    assert "Repair EVERY invalid" in prompt
    assert "Never guess a message ID" in prompt
    assert "do not delete committed facts or IDs" in prompt
    assert audit[-1]["generic_repair"][0]["mode"] == "invalid_source_reference"


def test_commit_reports_all_bare_thread_handles_before_normalizing(tmp_path: Path):
    workspace = MemoryWorkspace(tmp_path)
    baseline = workspace.baseline()
    topic = workspace.stage_dir / "topics" / "a.md"
    topic.parent.mkdir(exist_ok=True)
    topic.write_text(
        "One.[^e1]\n\nTwo.[^e2]\n\n"
        "[^e1]: Time: `undated`; Sources: locomo/thread_111\n"
        "[^e2]: Time: `undated`; Sources: locomo/thread_222\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError) as caught:
        workspace.commit_edits(*baseline)
    assert "locomo/thread_111 file=topics/a.md line=5 footnote=e1" in str(caught.value)
    assert "locomo/thread_222 file=topics/a.md line=6 footnote=e2" in str(caught.value)
    assert not (tmp_path / "topics" / "a.md").exists()


def test_core_memory_rejects_content_over_token_limit(tmp_path: Path):
    workspace = MemoryWorkspace(
        tmp_path, config=memory.MemoryConfig(core_max_tokens=1)
    )

    with pytest.raises(ValueError, match="Core Memory exceeds"):
        workspace.shell("printf '%s\\n' 'alpha beta' > core.md")

    assert not (tmp_path / "core.md").exists()


def test_core_capacity_rejection_preserves_stage_for_incremental_repair(
    tmp_path: Path,
):
    workspace = MemoryWorkspace(
        tmp_path, config=memory.MemoryConfig(core_max_tokens=80)
    )
    workspace.archive_sessions([{
        "observation_date": "2026-01-01",
        "turns": [("user", "A stable preference")],
        "refs": ["D1:1"],
    }])
    workspace._refresh_stage()
    baseline = workspace.baseline()
    core = workspace.stage_dir / "core.md"
    core.write_text(
        "# Core Memory\n\n"
        + "A stable preference " * 40
        + ".[^e1]\n\n"
        "[^e1]: Time: `2026-01-01`; Sources: D1:1\n",
        encoding="utf-8",
    )

    with pytest.raises(CoreCapacityError):
        workspace.commit_edits(*baseline)

    assert workspace.stage_is_dirty()
    staged = core.read_text(encoding="utf-8")
    assert "A stable preference" in staged

    shortened = re.sub(
        r"(?s)A stable preference .*?(?=\.\[\^)",
        "A stable preference",
        staged,
        count=1,
    )
    core.write_text(shortened, encoding="utf-8")
    workspace.commit_edits(*baseline)

    assert (tmp_path / "core.md").is_file()
    assert not workspace.stage_is_dirty()


def test_core_memory_restores_previous_content_when_commit_fails(
    tmp_path: Path, monkeypatch
):
    (tmp_path / "core.md").write_text("old core\n", encoding="utf-8")
    workspace = MemoryWorkspace(tmp_path)
    original_replace = os.replace

    def fail_core_install(source, destination):
        if (
            Path(source) == workspace.stage_dir / "core.md"
            and Path(destination) == tmp_path / "core.md"
        ):
            raise OSError("injected core install failure")
        return original_replace(source, destination)

    monkeypatch.setattr(
        "src.management.block_views.os.replace",
        fail_core_install,
    )

    with pytest.raises(OSError, match="injected core install failure"):
        workspace.shell("printf '%s\\n' 'new core' > core.md")

    assert (tmp_path / "core.md").read_text() == "old core\n"


def test_recent_memory_rejects_negative_capacity():
    with pytest.raises(ValueError, match="recent_limit must be non-negative"):
        memory.MemoryConfig(recent_limit=-1)


def test_save_memory_rejects_non_source_refs(tmp_path: Path):
    workspace = MemoryWorkspace(tmp_path)

    with pytest.raises(ValueError, match="complete source references"):
        workspace.save_memory([{
            "when": "2023-05-23",
            "content": "User bought a tank.",
            "refs": ["Session 1", "D18:1-D18:2"],
            "topic_path": "pets.md",
            "headings": ["Pets"],
        }])


def test_save_memory_expands_compact_source_ranges(tmp_path: Path):
    workspace = MemoryWorkspace(tmp_path)
    workspace.archive_sessions([{
        "observation_date": "2023-05-23",
        "turns": [("user", "one"), ("assistant", "two")],
        "refs": ["D18:1", "D18:2"],
    }])

    workspace.save_memory([{
        "when": "2023-05-23",
        "content": "A two-turn event.",
        "refs": ["D18:1-D18:2"],
        "topic_path": "event.md",
        "headings": ["Event"],
    }])
    workspace._synchronize()

    recent = json.loads((tmp_path / "recent_events.jsonl").read_text())
    assert recent["refs"] == ["D18:1", "D18:2"]


def test_commit_repairs_links_after_shell_reorganizes_topics(tmp_path: Path):
    first = MemoryWorkspace(tmp_path)
    first.archive_sessions([{
        "observation_date": "2023-05-23",
        "turns": [("user", "I bought a quarantine tank.")],
        "refs": ["D18:11"],
    }])
    first.save_memory([{
        "when": "2023-05-23",
        "content": "User bought a quarantine tank.",
        "refs": ["D18:11"],
        "topic_path": "pets/aquarium.md",
        "headings": ["Pets", "Aquarium"],
    }])
    first._synchronize()

    organizer = MemoryWorkspace(tmp_path)
    result = organizer.shell(
        "mkdir -p topics/life && mv topics/pets/aquarium.md topics/life/pets.md"
    )
    assert result.returncode == 0
    organizer._synchronize()

    timeline = (tmp_path / "timeline/2023/05/23.md").read_text()
    recent = json.loads((tmp_path / "recent_events.jsonl").read_text())
    topic = (tmp_path / "topics/life/pets.md").read_text()
    assert "topics/life/pets.md" in timeline
    assert "topics/pets/aquarium.md" not in timeline
    assert recent["topic_path"] == "topics/life/pets.md"
    assert "sources/D18.md#d18-11" in topic


def test_shell_move_rewrites_cross_topic_block_link_and_installs_relations(
    tmp_path: Path,
):
    workspace = MemoryWorkspace(tmp_path)
    workspace.archive_sessions([{
        "observation_date": "2026-01-01",
        "turns": [("user", "source"), ("user", "target")],
        "refs": ["D1:1", "D1:2"],
    }])
    workspace.save_memory([
        {"when": "2026-01-01", "content": "On 2026-01-01, Source fact.", "refs": ["D1:1"],
         "topic_path": "a.md", "headings": ["A"]},
        {"when": "2026-01-02", "content": "On 2026-01-02, Target fact.", "refs": ["D1:2"],
         "topic_path": "b.md", "headings": ["B"]},
    ])
    units = parse_topic_tree(tmp_path / "topics")
    source_id, target_id = [unit.memory_id for unit in units]
    result = workspace.shell(
        "sed 's/Source fact\\./Source fact linked to "
        f"[target](b.md#^{target_id})./' topics/a.md > topics/a.tmp && "
        "mv topics/a.tmp topics/a.md"
    )

    assert result.returncode == 0
    result = workspace.shell("mkdir -p topics/moved && mv topics/b.md topics/moved/b.md")
    assert result.returncode == 0

    assert f"[target](moved/b.md#^{target_id})" in (
        tmp_path / "topics/a.md"
    ).read_text()
    relations = json.loads((tmp_path / "relations.json").read_text())
    assert relations["outbound"][source_id] == [target_id]
    assert relations["backlinks"][target_id] == [source_id]


def test_deleting_a_topic_block_is_rejected(tmp_path: Path):
    """A block ID is how every view reaches a memory, so it cannot vanish."""
    workspace = MemoryWorkspace(tmp_path)
    workspace.archive_sessions([{
        "observation_date": "2026-01-01",
        "turns": [("user", "fact")],
        "refs": ["D1:1"],
    }])
    workspace.save_memory([{
        "when": "2026-01-01", "content": "Fact.", "refs": ["D1:1"],
        "topic_path": "a.md", "headings": ["A"],
    }])

    with pytest.raises(ValueError, match="block ID must not be removed"):
        workspace.shell("rm topics/a.md")

    assert (tmp_path / "topics/a.md").is_file()
    assert json.loads((tmp_path / "relations.json").read_text())["outbound"]


def test_recent_fifo_keeps_full_creation_order_outside_the_window(
    tmp_path: Path,
):
    workspace = MemoryWorkspace(
        tmp_path, config=memory.MemoryConfig(recent_limit=2)
    )
    workspace.archive_sessions([{
        "observation_date": "2026-01-01",
        "turns": [("user", "one"), ("user", "two"), ("user", "three")],
        "refs": ["D1:1", "D1:2", "D1:3"],
    }])
    workspace.save_memory([
        {"when": f"2026-01-0{index}",
         "content": f"On 2026-01-0{index}, Fact {index}.",
         "refs": [f"D1:{index}"], "topic_path": "facts.md", "headings": ["Facts"]}
        for index in range(1, 4)
    ])
    ids = [unit.memory_id for unit in parse_topic_tree(tmp_path / "topics")]

    workspace.shell("sed -i 's/Fact 1/Updated fact 1/' topics/facts.md")

    recent = [
        json.loads(line)
        for line in (tmp_path / "recent_events.jsonl").read_text().splitlines()
    ]
    assert [row["memory_id"] for row in recent] == ids[1:]


def test_block_transaction_restores_every_installed_view_on_install_failure(
    tmp_path: Path, monkeypatch
):
    workspace = MemoryWorkspace(tmp_path)
    workspace.archive_sessions([{
        "observation_date": "2026-01-01",
        "turns": [("user", "old fact")],
        "refs": ["D1:1"],
    }])
    workspace.save_memory([{
        "when": "2026-01-01", "content": "On 2026-01-01, Old fact.",
        "refs": ["D1:1"],
        "topic_path": "a.md", "headings": ["A"],
    }])
    tracked = [
        "topics/a.md", "timeline/2026/01/01.md", "recent_events.jsonl",
        "relations.json", ".somem/runtime.json",
    ]
    before = {name: (tmp_path / name).read_bytes() for name in tracked}
    original_replace = os.replace

    def fail_relations_install(source, destination):
        if (
            Path(source).name == "relations.json"
            and workspace.stage_dir in Path(source).parents
            and Path(destination) == tmp_path / "relations.json"
        ):
            raise OSError("injected relations install failure")
        return original_replace(source, destination)

    monkeypatch.setattr(
        "src.management.block_views.os.replace",
        fail_relations_install,
    )

    with pytest.raises(OSError, match="injected relations install failure"):
        baseline = workspace.baseline()
        topic = workspace.stage_dir / "topics/a.md"
        topic.write_text(topic.read_text().replace("Old fact", "Updated fact"))
        workspace.commit_edits(*baseline)

    assert {name: (tmp_path / name).read_bytes() for name in tracked} == before


def test_block_install_retries_transient_windows_permission_error(
    tmp_path: Path, monkeypatch
):
    workspace = MemoryWorkspace(tmp_path)
    workspace.archive_sessions([{
        "observation_date": "2026-01-01",
        "turns": [("user", "A retryable fact")],
        "refs": ["D1:1"],
    }])
    original_replace = os.replace
    attempts = 0

    def transient_timeline_lock(source, destination):
        nonlocal attempts
        if (
            Path(source).name == "timeline"
            and workspace.stage_dir in Path(source).parents
            and Path(destination) == tmp_path / "timeline"
            and attempts < 2
        ):
            attempts += 1
            raise PermissionError(5, "injected transient directory lock")
        return original_replace(source, destination)

    monkeypatch.setattr(
        "src.management.block_views.os.replace",
        transient_timeline_lock,
    )
    monkeypatch.setattr(
        "src.management.block_views._needs_filesystem_retries",
        lambda _path: True,
    )
    workspace.save_memory([{
        "when": "2026-01-01",
        "content": "A retryable fact.",
        "refs": ["D1:1"],
        "topic_path": "retry.md",
        "headings": ["Retry"],
    }])

    assert attempts == 2
    assert workspace.last_windows_retries == 2
    assert (tmp_path / "timeline/2026/01/01.md").is_file()


def test_shell_move_synchronizes_paths_and_links_immediately(tmp_path: Path):
    workspace = MemoryWorkspace(tmp_path)
    workspace.archive_sessions([{
        "observation_date": "2023-05-23",
        "turns": [("user", "I bought a quarantine tank.")],
        "refs": ["D18:11"],
    }])
    workspace.save_memory([{
        "when": "2023-05-23",
        "content": "User bought a quarantine tank.",
        "refs": ["D18:11"],
        "topic_path": "pets/aquarium.md",
        "headings": ["Pets", "Aquarium"],
    }])

    result = workspace.shell(
        "mkdir -p topics/life && mv topics/pets/aquarium.md topics/life/pets.md"
    )

    assert result.returncode == 0
    assert (tmp_path / "topics/life/pets.md").exists()
    recent = json.loads((tmp_path / "recent_events.jsonl").read_text())
    assert recent["topic_path"] == "topics/life/pets.md"


def test_shell_revision_synchronizes_event_content_to_all_views(tmp_path: Path):
    workspace = MemoryWorkspace(tmp_path)
    workspace.archive_sessions([{
        "observation_date": "2023-05-23",
        "turns": [("user", "I wake up at 6:30 AM.")],
        "refs": ["D1:1"],
    }])
    workspace.save_memory([{
        "when": "2023-05-23",
        "content": "On 2023-05-23, the user wakes up at 7:00 AM.",
        "refs": ["D1:1"],
        "topic_path": "routines/daily.md",
        "headings": ["Daily routine", "Morning"],
    }])

    result = workspace.shell(
        "sed 's/wakes up at 7:00 AM/wakes up at 6:30 AM/' "
        "topics/routines/daily.md > topics/routines/daily.tmp && "
        "mv topics/routines/daily.tmp topics/routines/daily.md"
    )

    assert result.returncode == 0
    topic = (tmp_path / "topics/routines/daily.md").read_text()
    timeline = (tmp_path / "timeline/2023/05/23.md").read_text()
    recent = json.loads((tmp_path / "recent_events.jsonl").read_text())
    assert "wakes up at 6:30 AM" in topic
    assert "wakes up at 6:30 AM" in timeline
    assert recent["content"] == (
        "On 2023-05-23, the user wakes up at 6:30 AM."
    )


def test_somem_build_verifies_each_session_before_next_write(
    tmp_path: Path, monkeypatch
):
    calls = []
    conv = {
        "session_1": [("user", "first")],
        "session_1_date_time": "2023-05-01",
        "session_2": [("user", "second")],
        "session_2_date_time": "2023-05-02",
    }

    monkeypatch.setattr(
        adapter.memory,
        "write_sessions",
        lambda *args, **kwargs: calls.append(
            ("write", kwargs["sessions"][0]["observation_date"])
        ) or [{"tool": "save_memory", "count": 1}],
    )
    monkeypatch.setattr(
        adapter.memory,
        "verify_session",
        lambda *args, **kwargs: calls.append(
            ("verify", kwargs["observation_date"])
        ) or {"repaired": False},
    )
    monkeypatch.setattr(
        adapter.memory,
        "manage_memory",
        lambda *args, **kwargs: calls.append(("manage", None)) or [],
    )

    _, events = adapter.build_memory(
        conv, tmp_path, agent=object(), model="test"
    )

    assert events == 2
    assert calls == [
        ("write", "2023-05-01"),
        ("verify", "2023-05-01"),
        ("write", "2023-05-02"),
        ("verify", "2023-05-02"),
        ("manage", None),
    ]


def test_somem_build_replaces_locomo_sequence_refs_with_opaque_source_ids(
    tmp_path: Path, monkeypatch
):
    captured = []
    conv = {
        "session_1": [{
            "speaker": "Melanie",
            "text": "I painted a lake sunrise last year.",
            "dia_id": "D1:14",
        }],
        "session_1_date_time": "2023-05-08",
    }
    monkeypatch.setattr(
        adapter.memory,
        "write_sessions",
        lambda *args, **kwargs: captured.extend(kwargs["sessions"]) or [],
    )

    adapter.build_memory(
        conv,
        tmp_path,
        agent=object(),
        model="test",
        config=adapter.BuildConfig(verify_writes=False, final_manage=False),
    )

    ref = captured[0]["refs"][0]
    assert re.fullmatch(
        r"locomo/thread_[0-9a-f]{12}/msg_[0-9a-f]{12}", ref
    )
    assert "D1:14" not in ref


def test_somem_build_runs_local_reorganization_at_fixed_session_intervals(
    tmp_path: Path, monkeypatch
):
    conv = {}
    for index in range(1, 6):
        conv[f"session_{index}"] = [("user", f"message {index}")]
        conv[f"session_{index}_date_time"] = f"2023-05-0{index}"
    local_calls = []

    monkeypatch.setattr(
        adapter.memory,
        "write_sessions",
        lambda *args, **kwargs: [{
            "tool": "save_memory",
            "status": "ok",
            "count": 1,
            "topic_paths": [
                f"topics/topic-{kwargs['sessions'][0]['observation_date']}.md"
            ],
        }],
    )
    monkeypatch.setattr(
        adapter.memory,
        "verify_session",
        lambda *args, **kwargs: {"repaired": False},
    )
    monkeypatch.setattr(
        adapter.memory,
        "organize_topics",
        lambda *args, **kwargs: local_calls.append(set(kwargs["touched"])) or [],
    )
    monkeypatch.setattr(
        adapter.memory,
        "manage_memory",
        lambda *args, **kwargs: [],
    )

    adapter.build_memory(
        conv,
        tmp_path,
        agent=object(),
        model="test",
        config=adapter.BuildConfig(local_reorg_every_sessions=2),
    )

    assert local_calls == [
        {
            "topics/topic-2023-05-01.md",
            "topics/topic-2023-05-02.md",
        },
        {
            "topics/topic-2023-05-03.md",
            "topics/topic-2023-05-04.md",
        },
    ]


@pytest.mark.parametrize(
    ("audit", "final_manage", "expected_calls"),
    [
        ([], True, 0),
        ([{"tool": "save_memory", "status": "ok", "count": 1}], False, 0),
        ([{"tool": "save_memory", "status": "ok", "count": 1}], True, 1),
    ],
)
def test_somem_final_management_requires_new_memory_and_can_be_disabled(
    tmp_path: Path, monkeypatch, audit, final_manage, expected_calls
):
    conv = {
        "session_1": [("user", "message")],
        "session_1_date_time": "2023-05-01",
    }
    calls = []
    monkeypatch.setattr(
        adapter.memory,
        "write_sessions",
        lambda *args, **kwargs: audit,
    )
    monkeypatch.setattr(
        adapter.memory,
        "verify_session",
        lambda *args, **kwargs: {"repaired": False},
    )
    monkeypatch.setattr(
        adapter.memory,
        "manage_memory",
        lambda *args, **kwargs: calls.append("manage") or [],
    )

    adapter.build_memory(
        conv,
        tmp_path,
        agent=object(),
        model="test",
        config=adapter.BuildConfig(final_manage=final_manage),
    )

    assert len(calls) == expected_calls


def test_somem_build_can_disable_write_verification(tmp_path: Path, monkeypatch):
    conv = {
        "session_1": [("user", "message")],
        "session_1_date_time": "2023-05-01",
    }
    monkeypatch.setattr(
        adapter.memory,
        "write_sessions",
        lambda *args, **kwargs: [
            {"tool": "save_memory", "status": "ok", "count": 1}
        ],
    )
    monkeypatch.setattr(
        adapter.memory,
        "verify_session",
        lambda *args, **kwargs: pytest.fail("verification should be disabled"),
    )

    _seconds, events = adapter.build_memory(
        conv,
        tmp_path,
        agent=object(),
        model="test",
        config=adapter.BuildConfig(verify_writes=False, final_manage=False),
    )

    assert events == 1
    verification = json.loads((tmp_path / "verification.jsonl").read_text())
    assert verification == {"skipped": True, "reason": "disabled"}


def test_somem_build_batches_writes_and_samples_verification(
    tmp_path: Path, monkeypatch
):
    conv = {}
    for index in range(1, 6):
        conv[f"session_{index}"] = [("user", f"message {index}")]
        conv[f"session_{index}_date_time"] = f"2023-05-0{index}"
    writes = []
    verifications = []

    monkeypatch.setattr(
        adapter.memory,
        "write_sessions",
        lambda *args, **kwargs: writes.append([
            session["observation_date"] for session in kwargs["sessions"]
        ]) or [{"tool": "save_memory", "status": "ok", "count": 1}],
    )
    monkeypatch.setattr(
        adapter.memory,
        "verify_session",
        lambda *args, **kwargs: verifications.append(kwargs["observation_date"])
        or {"repaired": False},
    )

    adapter.build_memory(
        conv,
        tmp_path,
        agent=object(),
        model="test",
        config=adapter.BuildConfig(
            session_batch=2,
            verify_every_sessions=2,
            final_manage=False,
        ),
    )

    assert writes == [
        ["2023-05-01", "2023-05-02"],
        ["2023-05-03", "2023-05-04"],
        ["2023-05-05"],
    ]
    assert verifications == ["2023-05-02", "2023-05-04", "2023-05-05"]


def test_verify_session_does_not_repair_when_memory_answers_probe(
    tmp_path, monkeypatch
):
    del monkeypatch
    source_workspace = MemoryWorkspace(tmp_path)
    source_workspace.archive_sessions([{
        "observation_date": "2023-05-23",
        "turns": [("user", "I wake up at 6:30 AM.")],
        "refs": ["D1:1"],
    }])
    agent = QueuedAgent([
        {"structured_output": {
            "question": "What time does the user wake up?",
            "expected_answer": "6:30 AM",
            "refs": ["D1:1"],
        }},
        {"text": "<answer>6:30 AM</answer>"},
        {"structured_output": {"supported": True}},
    ])
    before = list(tmp_path.rglob("*"))

    result = verify_session(
        tmp_path,
        agent=agent,
        observation_date="2023-05-23",
        turns=[("user", "I wake up at 6:30 AM.")],
        refs=["D1:1"],
    )

    assert result["repaired"] is False
    assert result["initial"]["answer"] == "6:30 AM"
    assert result["post_repair"] is None
    assert list(tmp_path.rglob("*")) == before
    assert agent.calls[0]["output_schema"]["required"] == [
        "question", "expected_answer", "refs"
    ]
    assert agent.calls[1].get("output_schema") is None
    assert agent.calls[2]["output_schema"]["required"] == ["supported"]


def test_verify_session_repairs_then_retries_same_question(tmp_path):
    source_workspace = MemoryWorkspace(tmp_path)
    source_workspace.archive_sessions([{
        "observation_date": "2023-05-23",
        "turns": [("user", "I wake up at 6:30 AM.")],
        "refs": ["D1:1"],
    }])
    repair_content = (
        "# Daily routine\n## Morning\n\n"
        "On 2023-05-23, the user wakes up at 6:30 AM.[^e1]\n\n"
        "[^e1]: Time: `2023-05-23`; Sources: D1:1\n"
    )
    agent = QueuedAgent([
        {"structured_output": {
            "question": "What time does the user wake up?",
            "expected_answer": "6:30 AM",
            "refs": ["D1:1"],
        }},
        {"text": "<answer>No information available.</answer>"},
        {"structured_output": {"supported": False}},
        {"tool_calls": [("Write", {"path": "topics/routines/daily.md",
                                   "content": repair_content})]},
        {"text": "<answer>6:30 AM</answer>"},
        {"structured_output": {"supported": True}},
    ])

    result = verify_session(
        tmp_path,
        agent=agent,
        observation_date="2023-05-23",
        turns=[("user", "I wake up at 6:30 AM.")],
        refs=["D1:1"],
    )

    assert result["repaired"] is True
    assert result["initial"]["supported"] is False
    assert result["post_repair"]["supported"] is True
    assert result["post_repair"]["question"] == result["probe"]["question"]
    assert (tmp_path / "topics/routines/daily.md").exists()
    assert (tmp_path / "timeline/2023/05/23.md").exists()
