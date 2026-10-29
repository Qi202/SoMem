# Configuration

The framework takes Python configuration objects. `.env.example` describes the
variables read by `examples/live_demo.py`, not universal framework switches.

| Variable | Meaning |
|---|---|
| `SOMEM_BASE_URL` | Writer endpoint, including API prefix |
| `SOMEM_API_KEY` | Writer credential |
| `SOMEM_WRITER_MODEL` | Exact Writer model ID |
| `SOMEM_READER_BASE_URL`, `SOMEM_READER_API_KEY`, `SOMEM_READER_MODEL` | Empty values inherit the respective Writer values |
| `SOMEM_WRITER_EFFORT`, `SOMEM_READER_EFFORT` | Independent effort settings; empty means omit |

The example never prints keys or puts them in its result JSON. Review provider
errors before sharing logs. `.env` and generated demo directories are ignored.

## Writer and context settings

| Option | Public-core default | Meaning |
|---|---|---|
| `BuildConfig.session_batch` | `1` | Sessions per Writer batch |
| `BuildConfig.local_reorg_every_sessions` | `5` | Organization interval; `0` disables it |
| `BuildConfig.verify_writes` | `True` | Verify written memory |
| `BuildConfig.final_manage` | `True` | Final organization pass |
| `MemoryConfig.core_max_tokens` | `3000` | Core token capacity |
| `MemoryConfig.recent_limit` | `50` | Recent event-view capacity |
| `MemoryConfig.max_turns` | `20` | Writer trajectory turn limit |

The live example retains these defaults. Core tokenizer counts are distinct
from provider-billed input tokens. Advanced experimental switches remain off.

## Reader and call settings

| Option | Live-example value | Meaning |
|---|---|---|
| `QueryConfig.max_turns` | `20` | Reader trajectory limit |
| `QueryConfig.search_tools` | `"split"` | Separate BM25/embedding tools; `"fused"` exposes fused search |
| `QueryConfig.verify_sources` | `True` | Source-verification guidance |
| `QueryConfig.memory_components` | `None` | Normal visible components; explicit subsets change available memory |
| `OpenAIChatConfig.max_output_tokens` | `8192` | Requested completion limit per call |
| `OpenAIChatConfig.force_final_at_max_turns` | `True` | Ask for an answer at the last turn |

These are usage settings, not finalized paper settings. An accepted effort field
does not prove that a relay honored it. Reasoning, tool and usage support depend
on the provider.

`create_runtime(..., runtime_type="openai-chat")` currently requests effort
`"none"`. For explicit effort, construct `OpenAIChatAgent(OpenAIChatConfig(...))`
and pass it to `Runtime`, as the live example does. Set `enable_thinking` and
`thinking_budget` only according to your provider's documented API.

## Recovery and costs

Build checkpoints preserve completed batches. OpenAI Chat does not persist an
interrupted in-memory conversation. The demo starts with an empty output and
does not automatically resume a partially modified Writer workspace; inspect
that workspace before recovery.

OpenAI Chat rejects `max_budget_usd` and does not enforce a portable dollar cap.
Turn/token limits and reported usage differ from provider-billed cost. Use
provider spending controls where needed.
