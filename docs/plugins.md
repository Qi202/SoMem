# SoMem coding-agent plugin

The plugin shares one SoMem memory format between Claude Code, Codex, and other MCP clients. The connected host acts as Writer and Reader; no additional model API key is required. Memory operations still consume the host's normal context and tool budget.

This first version provides explicit remember, recall, setup, and status workflows. It does not install automatic transcript capture, lifecycle Hooks, or a background Writer.

## Prerequisites

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and make uvx available on the host application's PATH. uv can provision Python 3.12 or later. Restart a desktop host after changing PATH.

Keep a local checkout of this repository. Plugin launchers run the Python package from that checkout through uvx; the first launch installs the lightweight MCP dependencies and may download tokenizer data. Standalone model SDKs and embedding models are optional.

## Claude Code

For a local trial, start Claude Code with:

~~~text
claude --plugin-dir "/absolute/path/to/SoMem"
~~~

For marketplace installation, run these inside Claude Code:

~~~text
/plugin marketplace add /absolute/path/to/SoMem
/plugin install somem@somem
~~~

Reload the session, then run /somem:somem-setup or ask SoMem to check its memory connection. The bundled Skills are somem-setup, somem-recall, somem-remember, and somem-status; Claude Code namespaces plugin skills under somem.

## Codex

On versions with native plugin commands:

~~~text
codex plugin marketplace add "/absolute/path/to/SoMem"
codex plugin add somem@somem
~~~

Reload the session and invoke $somem-setup, or ask the agent to check SoMem. The repository includes both portable plugin.json and a .codex-plugin compatibility manifest.

For versions without native plugin installation, register the same tools directly:

~~~text
codex mcp add somem -- uvx --from "/absolute/path/to/SoMem" somem mcp --workspace "global=~/.somem/memory" --project-workspaces --git-commit off
~~~

This fallback registers tools only. Install the four bundled Skill folders using your Codex version's supported skills directory if you also want the workflows.

## Other MCP clients

Use uvx as the command and these arguments, replacing the checkout path:

~~~json
{
  "mcpServers": {
    "somem": {
      "command": "uvx",
      "args": [
        "--from",
        "/absolute/path/to/SoMem",
        "somem",
        "mcp",
        "--workspace",
        "global=~/.somem/memory",
        "--project-workspaces",
        "--git-commit",
        "off"
      ]
    }
  }
}
~~~

Pass the active project's absolute project_root to every tool call. Generic MCP registration does not install native Skills or automatic session integration.

## Project identity and storage

MCP processes can start in a plugin cache rather than in the user's project. The plugin therefore never guesses a project from its process directory. Each tool accepts an explicit project_root in --project-workspaces mode. The Skills obtain that root from the host context and consistently pass it to the tools.

- Project memory lives at PROJECT_ROOT/.somem-memory and receives project-specific decisions.
- Personal memory lives at ~/.somem/memory and is shared across clients and projects.
- Generated workspaces include a self-ignore file so they are not accidentally committed with application code.
- Plugin launchers pass --git-commit off. They do not automatically commit a user's business repository.
- Without project_root, reads inspect personal memory only. A write must either select a project or explicitly choose layer=global.

Moving a project changes its default project-memory path. Reusing the same project across clients means supplying the same absolute root. Existing fixed-workspace MCP registrations remain supported without --project-workspaces.

## Verify the shared-memory loop

1. In Claude Code, ask: "Remember that this project uses PostgreSQL, and record why."
2. Verify the committed record using SoMem's recall workflow.
3. Open the same project in Codex and ask which database was selected and why.
4. Check that the result includes the original source rather than an invented summary.
5. Ask for status and confirm both clients selected the same project-memory path.

Project decisions should not appear when a different project is selected. Personal preferences saved to the global layer should remain available.

## Optional Python capabilities

~~~text
python -m pip install .
python -m pip install ".[agents]"
python -m pip install ".[embeddings]"
python -m pip install ".[agents,embeddings,dev]"
~~~

The base package serves MCP and lexical retrieval. agents installs the existing standalone model adapters; embeddings installs the optional embedding backend. requirements.txt remains the full framework dependency list. See agent-runtime-support.md for adapter limitations.

## Troubleshooting

If uvx cannot be found, check the host's PATH and restart it. If the first dependency or tokenizer download fails, restore network access and retry the connection. If project_root is rejected, make sure the server includes --project-workspaces and the argument identifies an existing absolute directory. For CONCURRENT_UPDATE, reread the current revision before building a new patch. Embedding search reports EMBEDDING_UNAVAILABLE until its optional backend is installed and configured.

During local development, uvx may reuse a previously built package. After editing Python code without changing its version, refresh it once, then restart the MCP connection:

~~~text
uvx --refresh-package somem-framework --from "/absolute/path/to/SoMem" somem --help
~~~

See [the preview verification record](plugin-validation.md) for completed checks and the remaining host-session validation. Lifecycle automation is a later phase. Native plugin packaging, MCP connectivity, and automatic session-memory behavior are separate capabilities and should be verified per host version.
