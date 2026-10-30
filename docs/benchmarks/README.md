# Public benchmark protocols

Four agreed protocols are shipped with the Python package as
[`benchmarks-v1.json`](../../code/somem/protocol_data/benchmarks-v1.json).
`load_benchmark_protocol` loads validated construction and retrieval settings
without making a model call. It pins all current BuildConfig, MemoryConfig and
QueryConfig values, including otherwise implicit defaults.

| Benchmark | Questions | Writer input batching | Writer / Reader turns |
|---|---:|---|---|
| LoCoMo | 1540, categories 1–4 | 15000-token complete-message batches | 120 / 20 |
| BEAM-100K | 400 | 32768-token complete-message batches | 120 / 120 |
| BEAM-1M | 700 | 32768-token complete-message batches | 120 / 120 |
| LongMemEval-S | 500 | One complete session; no independent token cap | 120 / 120 |

All use Luna6/effort none by default, Core capacity/repair target 4000/3600,
verification every session, local organization every two sessions, Recent 50,
final management, and the three confirmed Luna-named routing/repair switches.
Readers use Topic-first guidance, split BM25/embedding with optional per-call
`path_prefix`, Source verification, and all six memory components. Forced final
answering is off. Save the complete single answer without compression.

## Load and apply the protocol

```python
import os
from dataclasses import asdict
from somem import (
    load_benchmark_protocol, OpenAIChatAgent, OpenAIChatConfig,
    Runtime, build_memory, collect_answer,
)

protocol = load_benchmark_protocol("beam-100k")
endpoint = os.environ["SOMEM_BASE_URL"]
key = os.environ["SOMEM_API_KEY"]
writer = OpenAIChatAgent(OpenAIChatConfig(
    base_url=endpoint, api_key=key, **protocol.writer_options,
))
reader = OpenAIChatAgent(OpenAIChatConfig(
    base_url=endpoint, api_key=key, **protocol.reader_options,
))
runtime = Runtime(
    writer, protocol.writer_options["model"], query_agent=reader,
    query_model=protocol.reader_options["model"],
    build_config=protocol.build_config, query_config=protocol.query_config,
)

# Supply a conversation converted to the framework's session/message format.
# build_memory(conversation, "memory", agent=writer,
#              model=protocol.writer_options["model"],
#              config=protocol.build_config,
#              checkpoint_path="build-checkpoint.json")
# evidence, steps, answer, trace = collect_answer(
#     runtime, {"question": question}, "memory", {},
#     config=protocol.query_config,
# )

# Persist these alongside the exact code revision and run outputs.
effective_settings = {
    "protocol_version": protocol.version,
    "build": asdict(protocol.build_config),
    "query": asdict(protocol.query_config),
    "writer": protocol.writer_options,
    "reader": protocol.reader_options,
}
```

Changing a model or endpoint is supported through the adapter configuration;
record the effective values used. Credentials are not stored in protocol files.
The [Python API](../python-api.md) describes accepted construction input.
The short example JSON files are editable summaries; the bundled full protocol
is the authoritative loadable default for version `public-v1-20261030`.

## Scoring and completion boundary

The bundled metadata records the user-selected Judge protocols: LightMem
strictJudge for LoCoMo; Mem0 rubric-nugget for both BEAM scales; the pinned
LightMem evaluator for LongMemEval-S. All use GPT-4o-mini and full saved answers.
Judge source hashes/reference revision are recorded, but the implementations
are not packaged here. A loader is not a scoring implementation.

**Completed:** agreed configuration, package loader, API wiring instructions,
and offline configuration checks.

**Still required for a complete paper-reproduction entry:** raw-dataset adapters,
benchmark-wide build/QA launchers and resume handling, packaging or resolving the
pinned evaluators, and coverage/aggregation commands. Existing framework build
checkpoints do not substitute for a complete benchmark runner. No new full
benchmark run or historical best-result reproduction is claimed.

Further details: [construction](construction-settings.md),
[Reader](reader-settings.md), [LoCoMo](locomo.md), and
[optional Writer switch audit](writer-option-audit.md).
