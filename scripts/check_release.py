"""Check the installed framework, shipped examples, local doc links and stdio MCP."""

import asyncio
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.parse import unquote, urlsplit
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


def run(*args, cwd=ROOT, env=None):
    result = subprocess.run([sys.executable, *map(str, args)], cwd=cwd,
                            env=env, text=True, capture_output=True, timeout=120)
    if result.returncode:
        raise RuntimeError(f"command failed: {args}\n{result.stdout}\n{result.stderr}")
    return result.stdout


def check_docs():
    files = [ROOT / name for name in ("README.md", "CONTRIBUTING.md", "SECURITY.md", "CHANGELOG.md")]
    files.extend((ROOT / "docs").rglob("*.md"))
    for p in files:
        for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", p.read_text(encoding="utf-8")):
            target = target.strip("<>").split("#", 1)[0]
            if not target or urlsplit(target).scheme:
                continue
            assert (p.parent / unquote(target)).exists(), f"broken link: {p}: {target}"
    ET.parse(ROOT / "docs/assets/architecture.svg")
    for p in (ROOT / "examples").glob("*.json"):
        json.loads(p.read_text(encoding="utf-8"))


async def check_mcp(workspace):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    parameters = StdioServerParameters(command=sys.executable, args=[
        "-m", "somem.cli", "mcp", "--workspace", str(workspace), "--git-commit", "off",
    ], cwd=str(workspace.parent), env=os.environ.copy())
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as client:
            await client.initialize()
            tools = await client.list_tools()
            assert {t.name for t in tools.tools} == {
                "memory_status", "memory_list", "memory_read", "memory_grep",
                "memory_search", "memory_update",
            }
            result = await client.call_tool("memory_status", {})
            payload = json.loads(result.content[0].text)
            assert payload["ok"] and payload["revision"]
            result = await client.call_tool("memory_read", {"path": "topics/preferences.md"})
            assert not result.isError and "jasmine tea" in result.content[0].text
            result = await client.call_tool("memory_grep", {"query": "jasmine", "literal": True})
            assert not result.isError and "jasmine tea" in result.content[0].text


def main():
    from somem import (BuildConfig, MemoryConfig, MemoryWorkspace, OpenAIChatAgent,
                       OpenAIChatConfig, QueryConfig, Runtime, collect_answer, create_runtime)

    check_docs()
    with tempfile.TemporaryDirectory(prefix="somem-release-") as temporary:
        scratch = Path(temporary)
        assert "init" in run("-m", "somem.cli", "--help", cwd=scratch)
        output = run(ROOT / "examples/offline_demo.py", "--workspace", scratch / "memory", cwd=scratch)
        demo = json.loads(output)
        assert demo["source_ids"] and demo["search"]["results"]
        assert json.loads(run("-m", "somem.cli", "validate", "--workspace", scratch / "memory", cwd=scratch))["ok"]
        # Dummy settings exercise argument/configuration handling, never a provider.
        env = os.environ.copy()
        env.update(SOMEM_BASE_URL="https://example.invalid/v1", SOMEM_API_KEY="test-only",
                   SOMEM_WRITER_MODEL="test-only")
        result = json.loads(run(ROOT / "examples/live_demo.py", "--dry-run", "--workspace",
                                scratch / "live", cwd=scratch, env=env))
        assert result["network_calls"] == 0 and not (scratch / "live").exists()
        asyncio.run(check_mcp(scratch / "memory"))
    print("PASS: installed API, docs, CLI, transaction/search demo, live dry-run, MCP stdio")


if __name__ == "__main__":
    main()
