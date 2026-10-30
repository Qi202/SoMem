# Python API

Use lazy public imports from `somem`; importing an API does not instantiate a model.

## Workspace transactions and inspection

```python
from pathlib import Path
from somem import MemoryWorkspace, TransactionError, inspect

root = Path("memory")
workspace = MemoryWorkspace(root)
revision = workspace.revision()
status = inspect.status(root)
```

`MemoryWorkspace` initializes a missing workspace. Construct it only for the
directory you intend to manage. `inspect.status`, `inspect.read_file` and
`inspect.search` read committed memory:

```python
hits = inspect.search(root, "drink preference", method="bm25", top_k=3)
document = inspect.read_file(root, "topics/preferences.md")
```

Writes use `workspace.update(base_revision=..., patch=..., sources=...,
git_commit="off")`. Supply a unified diff over Topic files; temporary source and
block labels resolve to runtime IDs on commit. The executable
[offline demo](../examples/offline_demo.py) shows the full patch/citation syntax.
New manual-import source IDs use `transaction/<thread>/<message>`; this is an
import namespace, not a model attribution. Existing source IDs and links are
preserved. Reimporting the same source bundle under this namespace retains its
content-derived IDs. Semantic search additionally needs the embedding backend
and model weights.

`TransactionResult` contains `revision`, `source_ids`, `block_ids`, `evidence_ids`,
`changed_files`, `memory_committed` and Git metadata. Rejections raise
`TransactionError` with `code`, `message`, `path` and `details`. Refresh the base
revision after concurrent edits. Sources and derived views are not writable
Topic paths.

## Construct memory

```python
from somem import BuildConfig, MemoryConfig, build_memory

config = BuildConfig(memory_config=MemoryConfig(max_turns=20))
build_memory(conversation, root, agent=writer, model=writer_model,
             config=config, checkpoint_path=root / ".somem/build.json")
```

`conversation` holds consecutive `session_1`, `session_2`, … lists and optional
`session_N_date_time` fields. Turns accept `role`/`content` or `speaker`/`text`.
See [synthetic input](../examples/conversation.json). `agent` implements the
framework's `run(...)` contract. Construction calls the selected provider and may
modify memory before a trajectory is interrupted. Preserve checkpoint and
workspace on failure; partial construction is not a completed build.

## Answer from saved memory

```python
from somem import QueryConfig, Runtime, collect_answer

query = QueryConfig(max_turns=20, search_tools="split")
runtime = Runtime(writer, writer_model, query_agent=reader,
                  query_model=reader_model, query_config=query)
evidence, steps, answer, trace = collect_answer(
    runtime, {"question": "What drink does Alice prefer?"}, root, {}, config=query,
)
```

Returns `(evidence, retrieval_steps, answer, tool_trace)`; `runtime.call_log`
records Reader usage and metadata. The same adapter may serve both roles.
No answer grading occurs. A returned answer alone does not establish correctness.

## Model adapters

```python
from somem import OpenAIChatAgent, OpenAIChatConfig

reader = OpenAIChatAgent(OpenAIChatConfig(
    base_url=endpoint, api_key=key, model=model,
    reasoning_effort=None, force_final_at_max_turns=True,
))
```

Load secrets from the environment. `create_runtime` is also exported as an
adapter factory. The [live demo](../examples/live_demo.py) connects construction
and answering; see [support boundaries](agent-runtime-support.md).
