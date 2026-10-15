"""Offline regressions for tool validation, feedback, and safe model retries."""

from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from src.agent_runtime.claude_code import AgentExecutionError
from src.agent_runtime.openai_chat import OpenAIChatAgent, OpenAIChatConfig
from src.management import MemoryWorkspace
from src.management.tools import management_tools


class ToolCall:
    def __init__(self, name, arguments, call_id="call-1"):
        self.id = call_id
        self.function = SimpleNamespace(name=name, arguments=arguments)

    def model_dump(self, exclude_none=True):
        return {"id": self.id, "type": "function", "function": {
            "name": self.function.name, "arguments": self.function.arguments,
        }}


def scripted_agent(steps):
    requests = []

    def create(**kwargs):
        requests.append(deepcopy(kwargs))
        step = steps[len(requests) - 1]
        message = SimpleNamespace(
            content=step if isinstance(step, str) else None,
            tool_calls=[] if isinstance(step, str) else step,
        )
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message, finish_reason="stop")], usage=None,
        )

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    return OpenAIChatAgent(OpenAIChatConfig(
        base_url="https://example.test/v1", api_key="test-secret", model="test-model",
    ), client=client), requests


def editable_workspace(tmp_path):
    workspace = MemoryWorkspace(tmp_path)
    path = workspace.stage_dir / "topics/example.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# Example\nAlpha Alpha\n")
    audit = []
    tools = {tool.name: tool for tool in management_tools(workspace, audit)}
    return path, tools, audit


@pytest.mark.parametrize("change", [
    {"replace_all": "false"}, {"replace_all": 0}, {"path": 123},
    {"new_string": None}, {"extra_argument": True},
])
def test_invalid_edit_never_reaches_handler_or_changes_file(tmp_path, change):
    path, tools, audit = editable_workspace(tmp_path)
    original = path.read_bytes()
    args = {"path": "topics/example.md", "old_string": "Alpha", "new_string": "Beta"}
    result = OpenAIChatAgent._run_tool(tools["Edit"], {**args, **change})
    assert result["is_error"] is True
    assert "Input validation error:" in result["content"][0]["text"]
    assert path.read_bytes() == original
    assert audit == []


@pytest.mark.parametrize("args", [{}, [], None, "wrong"])
def test_missing_or_nonobject_arguments_do_not_write(tmp_path, args):
    path, tools, audit = editable_workspace(tmp_path)
    original = path.read_bytes()
    assert OpenAIChatAgent._run_tool(tools["Write"], args)["is_error"] is True
    assert path.read_bytes() == original
    assert audit == []


def test_model_can_correct_invalid_edit_without_hidden_replay(tmp_path):
    path, tools, audit = editable_workspace(tmp_path)
    args = {"path": "topics/example.md", "old_string": "Alpha", "new_string": "Beta"}
    agent, requests = scripted_agent([
        [ToolCall("Edit", json.dumps({**args, "replace_all": "false"}), "bad")],
        [ToolCall("Edit", json.dumps({**args, "replace_all": True}), "fixed")],
        "done",
    ])
    result = agent.run(prompt="edit", system_prompt="system", cwd=tmp_path,
                       tools=list(tools.values()), max_turns=3)
    error = requests[1]["messages"][-1]
    assert error["tool_call_id"] == "bad"
    assert json.loads(error["content"])["is_error"] is True
    assert "boolean" in error["content"]
    assert requests[2]["messages"][-1]["tool_call_id"] == "fixed"
    assert path.read_text() == "# Example\nBeta Beta\n"
    assert len(audit) == 1 and audit[0]["status"] == "ok"
    assert result.text == "done"


@pytest.mark.parametrize("failure", ["bad_json", "unknown", "exception", "flagged"])
def test_tool_errors_return_to_model_and_allow_a_followup_call(tmp_path, failure):
    executed = []

    async def handler(args):
        executed.append(args)
        if len(executed) == 1 and failure == "exception":
            raise OSError("temporary test-secret error")
        if len(executed) == 1 and failure == "flagged":
            return {"content": [{"type": "text", "text": "Read first"}], "is_error": True}
        return {"content": [{"type": "text", "text": "success"}]}

    tool = SimpleNamespace(name="lookup", description="lookup", handler=handler,
                           input_schema={"type": "object", "properties": {}})
    first = ToolCall("missing" if failure == "unknown" else "lookup",
                     "{" if failure == "bad_json" else "{}", "bad")
    agent, requests = scripted_agent([
        [first], [ToolCall("lookup", "{}", "fixed")], "done",
    ])
    result = agent.run(prompt="lookup", system_prompt="system", cwd=tmp_path,
                       tools=[tool], max_turns=3)
    error = requests[1]["messages"][-1]
    assert error["tool_call_id"] == "bad"
    assert json.loads(error["content"])["is_error"] is True
    assert "test-secret" not in error["content"]
    assert requests[2]["messages"][-1]["content"] == "success"
    assert len(executed) == (1 if failure in {"bad_json", "unknown"} else 2)
    assert result.text == "done"


def test_persistent_tool_error_still_obeys_turn_limit(tmp_path):
    agent, _ = scripted_agent([[ToolCall("missing", "{}")]] * 2)
    with pytest.raises(AgentExecutionError, match="max_turns=2"):
        agent.run(prompt="lookup", system_prompt="system", cwd=tmp_path, max_turns=2)


def test_keyboard_interrupt_is_not_converted_to_tool_feedback():
    async def handler(args):
        raise KeyboardInterrupt()

    tool = SimpleNamespace(input_schema={}, handler=handler)
    with pytest.raises(KeyboardInterrupt):
        OpenAIChatAgent._run_tool(tool, {})


def test_source_protection_survives_tool_error_feedback(tmp_path):
    workspace = MemoryWorkspace(tmp_path)
    source = workspace.stage_dir / "sources/example.md"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("original evidence")
    tools = {tool.name: tool for tool in management_tools(workspace, [])}
    result = OpenAIChatAgent._run_tool(tools["Write"], {
        "path": "sources/example.md", "content": "replacement",
    })
    assert result["is_error"] is True
    assert source.read_text() == "original evidence"
