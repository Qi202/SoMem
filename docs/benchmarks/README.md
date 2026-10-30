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
Judge adapters verify the source hashes/reference revision. BEAM scoring source
is included; the LightMem sources are supplied externally as described below.

## Batch commands

Install the framework from this checkout. Add the optional dataset dependency
only for BEAM Arrow/Parquet input:

```bash
pip install -e ".[benchmarks]"
mkdir -p work
```

Prepare user-supplied raw data. Supported inputs are the original LoCoMo JSON
list, LongMemEval-S JSON list, and BEAM Arrow/Parquet or JSON/JSONL rows containing
`chat` and `probing_questions`. Already converted BEAM conversation/QA JSON is
also accepted. The input path selects the BEAM scale; no dataset download occurs.

```bash
somem-benchmark prepare --benchmark locomo --input /data/locomo10.json --output work/locomo.json
somem-benchmark prepare --benchmark beam-100k --input /data/beam-100K.arrow --output work/beam100k.json
somem-benchmark prepare --benchmark beam-1m --input /data/beam-1M.arrow --output work/beam1m.json
somem-benchmark prepare --benchmark longmemeval-s --input /data/longmemeval_s_cleaned.json --output work/lme.json
```

Preparation checks the agreed sample/question counts; LoCoMo excludes category
5. For an intentionally smaller input, use `--allow-subset`, which labels the
prepared dataset as a subset. No gold answers, rubrics, categories or annotated
evidence are included in Writer/Reader input. LongMemEval Readers additionally
receive the question date, and its original session dates are retained.

Set `SOMEM_BASE_URL` and `SOMEM_API_KEY` in the environment. Optional
`SOMEM_WRITER_MODEL` and `SOMEM_READER_MODEL` override the default Luna6 models.
Readers can use separate `SOMEM_READER_BASE_URL`/`SOMEM_READER_API_KEY` values.
Then run construction and answering independently (substitute any prepared file):

```bash
somem-benchmark build --input work/beam100k.json --output work/beam100k-memory --workers 1
somem-benchmark qa --input work/beam100k.json --memory-root work/beam100k-memory --output work/beam100k-qa --workers 1
```

Each sample has a numbered directory. Build outputs include memory and a
completion marker; QA validates its binding to the prepared data. QA writes
complete individual answers, a per-sample `answers.json`, and call metadata.
Exceptions are recorded as errors and produce a nonzero exit. No empty or
turn-limit-failed response is silently converted to an incorrect answer.

### Fixed Judge adapters

Set `OPENROUTER_API_KEY`. Judge model and generation settings are fixed by the
selected protocol. No automatic scoring retries or result aggregation are added.
For BEAM, the repository includes the unchanged hash-locked evaluator source;
the adapter executes its original prompts and parsing functions without importing
private experiment drivers:

```bash
somem-benchmark judge --benchmark beam-100k --answers work/beam100k-qa --output work/beam100k-judged
```

For LoCoMo, supply the original hash-pinned LightMem `llm_judge.py` described in
[the protocol](locomo.md); the adapter calls its `evaluate_llm_judge` directly:

```bash
somem-benchmark judge --benchmark locomo --answers work/locomo-qa --locomo-judge /path/to/llm_judge.py --output work/locomo-judged
```

For LongMemEval-S, supply a LightMem source checkout at the pinned revision
`8449d574df6bae1bdf3314a1564da65e2f37e046`. The adapter verifies all four file
hashes, executes the original prompt dispatch/preprocessing and templates, and
uses the original correctness expression. It does not need LightMem's full
inference dependency stack:

```bash
somem-benchmark judge --benchmark longmemeval-s --answers work/lme-qa --lightmem-dir /path/to/LightMem --output work/lme-judged
```

A source hash mismatch stops before scoring. The adapter saves per-question
scores and raw provider responses, including usage/returned model data. Failed
requests or invalid strictJudge labels are pending items with a nonzero exit,
not incorrect answers. `--answers` can also select one sample's `answers.json`.
Full-set coverage remains the caller's responsibility when selecting saved files;
Judge run metadata records how many answer records were supplied.

### Scope

The public entry now provides dataset adaptation, protocol loading, batch build,
batch QA, and fixed Judge adapters. It deliberately adds no cross-question result
summary or automatic resume: use your own analysis and orchestration. Every output
file/directory must be new, so previous runs are not overwritten. Framework build
checkpoints remain available as ordinary artifacts, but this CLI does not expose
resume. Judge adapters read saved answers and never regenerate QA.

These are current public recipes. They are not a claim that historical best
scores have been rerun under the unified configuration. Live benchmark runs
were not performed during this implementation. LightMem scoring source is an
explicit external dependency; no alternate prompt is substituted if absent.

Further details: [construction](construction-settings.md),
[Reader](reader-settings.md), [LoCoMo](locomo.md), and
[optional Writer switch audit](writer-option-audit.md).
