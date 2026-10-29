# CLI and MCP integration

## CLI commands

```bash
somem --help
somem init ./memory
somem validate --workspace ./memory
somem mcp --workspace ./memory --git-commit off
```

`init` creates a workspace or reports an existing one without overwriting it.
`validate` rebuilds/checks a scratch copy, preserving the original workspace.
`mcp` runs a stdio server; it is not an HTTP service or an interactive shell.
Missing MCP workspaces are initialized automatically.

`--git-commit off` disables automatic commits. The default `auto` commits when
the target workspace is a Git repository; `on` requires Git. Choose the setting
explicitly when connecting an agent.

## Connect a client

Adapt [the client configuration](../examples/mcp-client.json) to your MCP host.
Use absolute paths to the installed `somem` command and workspace. The JSON is
the common `mcpServers` form; hosts may require a different configuration location
or schema. No client-specific installation has been claimed verified here.

```json
{
  "mcpServers": {
    "somem": {
      "command": "/absolute/path/to/.venv/bin/somem",
      "args": ["mcp", "--workspace", "/absolute/path/to/memory", "--git-commit", "off"]
    }
  }
}
```

The MCP server has no model credentials of its own. The connected agent decides
what to remember and which tools to call. Tool responses contain a JSON envelope
with `ok`, `data` or `error`, and a workspace revision.

## Tools

| Tool | Purpose | Main arguments |
|---|---|---|
| `memory_status` | Counts and current revision | None |
| `memory_list` | List visible files | `prefix`, `include_derived`, `limit` |
| `memory_read` | Read a file/block/window | `path`, optional `heading`, `block_id`, `offset`, `limit` |
| `memory_grep` | Literal/regex search | `query`, `prefix`, `literal`, `case_sensitive`, `limit` |
| `memory_search` | Rank memory | `query`, `method`, `top_k`, `path_prefix`, optional date bounds |
| `memory_update` | Commit cited Topic edits and sources | `base_revision`, `patch`, optional `sources`, `commit_message`, `layer` |

Typical sequence: call `memory_status`, inspect/search existing memory, then
submit `memory_update` with the returned revision. The patch uses the same
transaction format as the [offline example](../examples/offline_demo.py).
Use the new revision from a successful response for later edits. Sources remain
read-only; derived views are rebuilt rather than edited directly.

`memory_search` supports `bm25` and `embedding`. Embedding unavailability is an
explicit error, not a silent fallback. The interactive surface does not expose
the experimental Reader's fused-search tool as an additional MCP method.

## Project and global layers

```bash
somem mcp --workspace project=/absolute/project/.memory \
          --workspace global=/absolute/personal/memory --git-commit off
```

With several workspaces, every entry needs a name. Reads span layers; qualified
paths such as `global:topics/preferences.md` select one layer. Writes target the
first layer by default, or the specified `layer`. Relative workspace paths resolve
against the nearest Git project root, falling back to the working directory.

Keep personal preferences in the global layer and project decisions in the
project layer. This is a usage convention, not an automatic semantic router.

## Scope

These tools operate local files with the host process's filesystem permissions.
Workspace-path checks and validated Topic updates are implemented. The stdio
server does not provide remote authentication, multi-tenant authorization or a
hosted service. Use it within a trusted local agent session.
