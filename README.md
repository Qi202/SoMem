# SoMem: Self-Organized Document Memory

Persistent, document-based memory for agents. SoMem keeps original conversations
as immutable Sources, lets a Writer organize cited Markdown documents, and gives
a Reader tools to find and verify relevant evidence.

[Get started](docs/getting-started.md) · [Python API](docs/python-api.md) ·
[MCP integration](docs/mcp.md) · [Configuration](docs/configuration.md) ·
[Runtime support](docs/agent-runtime-support.md) · [Development](CONTRIBUTING.md)

![SoMem architecture: immutable Sources feed Writer-managed Topics and nested Core; validated documents supply derived views and Reader retrieval.](docs/assets/architecture.svg)

## What SoMem provides

- **Readable memory:** editable Topics with stable references to original Sources; bounded Core lives in `topics/core.md`.
- **Validated updates:** transactions check document structure and references before installing edits and rebuilding access views.
- **Flexible reading:** lexical and semantic retrieval, source inspection, and separate Writer/Reader models sharing one workspace.
- **Agent integration:** Python API, workspace CLI, and stdio MCP server with six memory tools and optional workspace layers.

## Install

Use Python **3.12+**. From a source checkout:

```bash
git clone https://github.com/Qi202/SoMem.git
cd SoMem
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
somem --help
```

This installs the framework and runtime dependencies, including the embedding
stack. A model API key is only needed for model-driven writing or answering.
See [installation and troubleshooting](docs/getting-started.md).

## Try it without an API key

```bash
python examples/offline_demo.py --workspace demo-memory
somem validate --workspace demo-memory
```

The demo commits a cited preference, retrieves it with BM25, and prints the saved
document and source reference. It uses real transaction and search code with a
manually supplied Topic edit. There are no model calls or embedding-model
downloads. The first tokenizer use may download its encoding table. Choose a new
workspace for each run.

For model-driven **conversation → memory → answer**, follow the
[live example](docs/getting-started.md#model-driven-example), with separate
Writer/Reader settings and a `--dry-run` input check.

## Connect an agent through MCP

Add this entry to your MCP client, using absolute paths:

```json
{
  "mcpServers": {
    "somem": {
      "command": "/absolute/path/to/SoMem/.venv/bin/somem",
      "args": ["mcp", "--workspace", "/absolute/path/to/memory", "--git-commit", "off"]
    }
  }
}
```

Tools: `memory_status`, `memory_list`, `memory_read`, `memory_grep`,
`memory_search`, `memory_update`. The server uses the host agent's model; it does
not start another model. See [MCP tools and layers](docs/mcp.md).

## Memory layout

```text
memory/
  sources/             immutable conversation records
  topics/              editable semantic documents
    core.md            bounded context
  recent_events.jsonl  derived recent-event view
  timeline/            derived temporal views
  relations.json       derived relation view
  .somem/              local implementation state
```

Derived views are rebuilt from accepted documents. They are access structures,
not independent factual sources. Legacy root-level `core.md` remains supported.

## Documentation

| Guide | Contents |
|---|---|
| [Getting started](docs/getting-started.md) | Installation, offline/live examples, troubleshooting |
| [Python API](docs/python-api.md) | Transactions, construction, reading and return values |
| [Configuration](docs/configuration.md) | Credentials, models, effort and budgets |
| [CLI and MCP](docs/mcp.md) | Commands, client configuration, tools and layers |
| [Runtime support](docs/agent-runtime-support.md) | Adapters and validation boundaries |
| [Release scope](docs/release-scope.md) | Public snapshot and deferred experiment material |
| [Local validation](docs/validation.md) | Checks passed and remaining validation boundaries |
| [Contributing](CONTRIBUTING.md) | Tests, documentation and packaging |

## Project status

This is the `0.1.0` framework line. The public core was synchronized on
2026-10-15, followed by Topic-first and runtime-selection updates on 2026-10-17.
Current local experiments use additional changes. This is not yet a
paper-reproduction release; benchmark launchers, final settings, Judge protocols
and result bundles will be handled separately.

The live example is a usage recipe, not verified support for every provider.
See [runtime support](docs/agent-runtime-support.md). A package version here does
not imply a PyPI release or a published Git tag.

## License and citation

SoMem is distributed under the [MIT License](LICENSE). Dependencies retain their
own licenses.

Until paper metadata is finalized, cite the repository URL and the commit used.
No paper identifier or performance claim is inferred here.
