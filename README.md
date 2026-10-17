# SoMem: Self-Organized Document Memory

Framework code for a persistent, document-based agent memory.

## Framework

SoMem separates immutable conversation records, editable semantic documents, and bounded context supplied before search. A Writer organizes and revises Markdown documents with stable references to source evidence. Deterministic validation checks document structure and references before accepted edits rebuild the access views. A Reader decides which documents to search and read, including returning to original records when necessary.

The same model can perform both roles, or separate models can share the workspace. The framework includes agent adapters, document operations, lexical and semantic retrieval, workspace validation, and derived views.

Runtime support and remaining adapter limitations are documented in [`docs/agent-runtime-support.md`](docs/agent-runtime-support.md).

## Code organization

| Directory | Responsibility |
|---|---|
| `code/src/markdown/` | Parse and render memory documents, blocks, and references |
| `code/src/management/` | Writer tools, semantic edits, source preservation, and validated transactions |
| `code/src/runtime/` | Context capacity, token accounting, and deterministic access views |
| `code/src/retrieval/` | Reader context, searches, document access, and tool dispatch |
| `code/src/agent_runtime/` | Model and agent interfaces |
| `code/src/build.py` | Incremental memory construction and checkpoint boundaries |
| `code/somem/` | Workspace interface and MCP integration |
| `code/tests/` | Offline framework integrity checks |

## Workspace representation

```text
memory/
  sources/             immutable conversation records
  topics/              editable semantic documents with source references
    core.md            bounded context
  recent_events.jsonl  recent event view
  timeline/            temporal access views
  relations/           relation access views
  .somem/              local implementation state
```

Derived views are access structures. Their content is rebuilt from accepted memory documents rather than supplied as an independent source of facts. Search tools return evidence for the Reader; they do not judge answers.

## Scope

This snapshot was synchronized with the working framework on 2026-10-15. New workspaces keep Core in `topics/core.md`; existing workspaces with root-level `core.md` remain supported.

This repository contains framework implementation and selected offline tests. It does not contain benchmark data, experimental launchers, run configurations, evaluation/Judge protocols, experimental results, or paper files. Internal agent instructions remain part of the implementation; they are not a benchmark execution recipe.

The snapshot uses neutral package and commit metadata and contains no inherited repository history. It does not claim to reproduce the paper's experiment runs independently of their omitted settings.

Dependencies are declared in `pyproject.toml` and `requirements.txt`. Credentials, model endpoints, generated workspaces, caches, and runtime outputs are not distributed.
