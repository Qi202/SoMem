"""Exercise project selection and durable memory over the real MCP transport."""

import asyncio
import json
import os
import subprocess
import sys
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from somem.mcp_server import TOOL_NAMES

REPOSITORY = Path(__file__).resolve().parents[3]
CODE = REPOSITORY / "code"


@asynccontextmanager
async def connected_memory(global_root: Path, process_directory: Path):
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m", "somem.cli", "mcp",
            "--workspace", f"global={global_root}",
            "--project-workspaces", "--git-commit", "off",
        ],
        env={**os.environ, "PYTHONPATH": str(CODE)},
        cwd=process_directory,
    )
    async with stdio_client(parameters) as (reader, writer):
        async with ClientSession(
            reader, writer, read_timeout_seconds=timedelta(seconds=20),
        ) as session:
            await session.initialize()
            yield session


async def call_memory(session: ClientSession, name: str, **arguments):
    result = await session.call_tool(name, arguments)
    assert not result.isError, result
    return json.loads("".join(
        item.text for item in result.content if item.type == "text"
    ))


def new_topic(path: str, claim: str, label: str) -> str:
    lines = [
        "# Decisions", "",
        f"{claim}[^new-evidence-{label}] ^new-block-{label}", "",
        f"[^new-evidence-{label}]: Time: `undated`; Sources: new-source-{label}",
    ]
    return (
        f"--- /dev/null\n+++ b/topics/{path}.md\n"
        f"@@ -0,0 +1,{len(lines)} @@\n"
        + "".join(f"+{line}\n" for line in lines)
    )


def test_two_client_processes_share_project_and_personal_memory(tmp_path):
    project = tmp_path / "project with spaces"
    other_project = tmp_path / "other project"
    plugin_directory = tmp_path / "plugin cache"
    for directory in (project, other_project, plugin_directory):
        directory.mkdir()
    global_root = tmp_path / "personal memory"
    reference = (
        REPOSITORY / "skills/somem-remember/references/transactions.md"
    ).read_text(encoding="utf-8")
    example = json.loads(reference.split("~~~json\n", 1)[1].split("\n~~~", 1)[0])

    async def scenario():
        async with connected_memory(global_root, plugin_directory) as session:
            tools = await session.list_tools()
            assert {tool.name for tool in tools.tools} == set(TOOL_NAMES)
            assert all(
                "project_root" in tool.inputSchema["properties"]
                for tool in tools.tools
            )
            status = await call_memory(
                session, "memory_status", project_root=str(project),
            )
            assert status["ok"]
            assert status["data"]["project_selected"]
            assert set(status["data"]["layers"]) == {"project", "global"}
            arguments = {
                **example, "project_root": str(project),
                "base_revision": status["revision"],
            }
            saved = await call_memory(session, "memory_update", **arguments)
            assert saved["ok"], saved
            assert saved["data"]["memory_committed"]
            assert not saved["data"]["git_committed"]
            assert saved["data"]["layer"] == "project"
            assert saved["data"]["source_ids"]
            assert saved["data"]["block_ids"]
            rejected = await call_memory(session, "memory_update", **arguments)
            assert rejected["error"]["code"] == "CONCURRENT_UPDATE"
            global_status = await call_memory(session, "memory_status")
            preference = await call_memory(
                session, "memory_update", layer="global",
                base_revision=global_status["revision"],
                patch=new_topic("preferences", "The user prefers tea.", "tea"),
                sources=[{
                    "label": "new-source-tea", "role": "user",
                    "content": "I prefer tea.",
                }],
            )
            assert preference["ok"], preference

        async with connected_memory(global_root, plugin_directory) as session:
            recalled = await call_memory(
                session, "memory_search", query="PostgreSQL",
                project_root=str(project),
            )
            assert recalled["ok"]
            assert any(
                "PostgreSQL" in json.dumps(hit)
                for hit in recalled["data"]["results"]
            )
            topic = await call_memory(
                session, "memory_read", path="project:topics/decisions.md",
                project_root=str(project),
            )
            assert topic["ok"]
            assert "PostgreSQL" in json.dumps(topic["data"])
            assert "new-source-user" not in json.dumps(topic["data"])
            assert "../sources/" in json.dumps(topic["data"])
            unrelated = await call_memory(
                session, "memory_search", query="PostgreSQL",
                project_root=str(other_project),
            )
            assert unrelated["data"]["results"] == []
            personal = await call_memory(
                session, "memory_read", path="global:topics/preferences.md",
                project_root=str(other_project),
            )
            assert personal["ok"]
            assert "prefers tea" in json.dumps(personal["data"])

    asyncio.run(asyncio.wait_for(scenario(), timeout=60))
    assert (project / ".somem-memory/topics/decisions.md").is_file()
    assert (project / ".somem-memory/.gitignore").read_text().strip() == "*"
    assert not (plugin_directory / ".somem-memory").exists()


def test_plugin_requires_explicit_project_for_project_writes(tmp_path):
    async def scenario():
        async with connected_memory(tmp_path / "global", tmp_path) as session:
            status = await call_memory(session, "memory_status")
            assert status["ok"]
            assert not status["data"]["project_selected"]
            update = await call_memory(
                session, "memory_update", base_revision=status["revision"],
                patch="",
            )
            assert update["error"]["code"] == "PROJECT_REQUIRED"
            relative = await call_memory(
                session, "memory_status", project_root="relative/project",
            )
            assert relative["error"]["code"] == "INVALID_PROJECT"
            missing = await call_memory(
                session, "memory_status", project_root=str(tmp_path / "missing"),
            )
            assert missing["error"]["code"] == "INVALID_PROJECT"
    asyncio.run(asyncio.wait_for(scenario(), timeout=60))
    assert not (tmp_path / ".somem-memory").exists()


def test_plugin_import_and_write_do_not_require_model_sdks(tmp_path):
    script = """
import importlib.abc
import json
import sys
from pathlib import Path

class RejectModelDependencies(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {
            'anthropic', 'openai', 'claude_agent_sdk',
            'sentence_transformers', 'torch',
        }:
            raise AssertionError(f'optional model dependency loaded: {fullname}')

sys.meta_path.insert(0, RejectModelDependencies())
from somem.cli import ensure_workspace
from somem import MemoryWorkspace
from src.management.transaction import TransactionLimits, parse_sources, source_records
from src.retrieval import inspect
root = Path(sys.argv[1])
ensure_workspace(root)
workspace = MemoryWorkspace(root)
request = json.loads(sys.argv[2])
result = workspace.update(
    base_revision=workspace.revision(), patch=request["patch"],
    sources=request["sources"], git_commit="off",
)
assert result.memory_committed
records = source_records(parse_sources([
    {'label':'new-source-user','role':'user','content':'A portable memory.'}
], TransactionLimits()))
assert records[0].provider == 'somem'
assert inspect.status(root)['core_exists']
print(json.dumps({'ok': True}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path / "memory"), json.dumps({
            "patch": new_topic("portable", "A portable memory.", "portable"),
            "sources": [{
                "label": "new-source-portable", "role": "user",
                "content": "A portable memory.",
            }],
        })],
        cwd=tmp_path, env={**os.environ, "PYTHONPATH": str(CODE)},
        capture_output=True, text=True, timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["ok"]


def test_fixed_workspace_mode_keeps_the_existing_protocol(tmp_path):
    from somem.cli import ensure_workspace
    from somem.mcp_server import build_server

    ensure_workspace(tmp_path)
    server = build_server(tmp_path, git_commit="off")

    async def scenario():
        result = await server.call_tool("memory_status", {})
        content = result[0] if isinstance(result, tuple) else result
        payload = json.loads(content[0].text)
        assert payload["ok"]
        assert "project_selected" not in payload["data"]
        result = await server.call_tool(
            "memory_update", {
                "base_revision": payload["revision"],
                "patch": new_topic("legacy", "Fixed workspace memory.", "legacy"),
                "sources": [{
                    "label": "new-source-legacy", "role": "user",
                    "content": "Fixed workspace memory.",
                }],
            },
        )
        content = result[0] if isinstance(result, tuple) else result
        assert json.loads(content[0].text)["ok"]
    asyncio.run(asyncio.wait_for(scenario(), timeout=60))
