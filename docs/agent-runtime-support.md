# Agent runtime support

## Support matrix

| Entry point | Implementation | Validation boundary |
|---|---|---|
| Local workspace API and CLI | Implemented | Offline transaction/document checks and release smoke checks |
| Stdio MCP | Implemented; six tools and layers | Protocol initialize/list/read/status smoke; host-specific integration still needs testing |
| Claude Code runtime | Implemented; default adapter | SDK required; provider-backed run behavior depends on model and SDK |
| OpenAI Chat runtime | Implemented for Writer and Reader | Offline tool-validation/recovery tests; local synthetic GPT-4o-mini/OpenRouter Writer → Core → BM25 → answer example passed on 2026-10-29 |
| Anthropic Messages runtime | Implemented | Live provider parity not established by these release checks |

The live example was checked against the public framework plus the local Reader
prompt/full-message parsing alignment, using frozen code hashes. See
[validation](validation.md) for observed results and unexercised paths.
Successful research runs on another snapshot do not establish validation of
this checkout. No blanket model/platform compatibility claim follows from an
adapter being present.

## Implemented

SoMem keeps Claude Code as the default runtime. `OpenAIChatAgent` is also implemented for endpoints that support the OpenAI Chat Completions tool-call interface. It validates tool arguments, runs the same SoMem tool handlers, returns tool errors to the model, and records response model and request metadata.

`create_runtime` uses the selected `runtime_type` for the Builder and Reader when they share one model. Set `runtime_type="openai-chat"` to use an OpenAI-compatible model for both roles. When the Reader uses a separate model, `query_runtime_type` selects its adapter; it defaults to `runtime_type`.

```python
from src.retrieval.runtime import create_runtime

runtime = create_runtime(
    base_url="https://api.example.com/v1",
    api_key="...",  # load from the environment in applications
    model="provider/model-name",
    runtime_type="openai-chat",
    build_config=build_config,
    query_config=query_config,
)
```

For a Claude Code Builder and an OpenAI-compatible Reader, pass `query_runtime_type="openai-chat"` along with `query_base_url`, `query_api_key`, and `query_model`. The same `query_runtime_type` selector is available when choosing a different supported adapter for the Reader.

`build_memory` also accepts an Agent object directly, so applications can construct `OpenAIChatAgent` with provider-specific options and pass it to the Writer without using `create_runtime`.

## Limitations and follow-up

- **No provider-neutral USD budget enforcement for OpenAI Chat.** `OpenAIChatAgent` raises when `max_budget_usd` is set. Chat Completions providers report cost differently, and some do not return a billed cost at all. Keep the budget unset or enforce limits at the provider/account layer; do not assume SoMem enforces a USD cap.
- **No portable in-trajectory resume for OpenAI Chat.** Its conversation state is held in memory for the active call. SoMem's build checkpoints still preserve completed batches, but an interrupted Writer trajectory cannot resume from its prior provider conversation. Claude Code has its own optional session-resume support.
- **Live coverage is limited.** One GPT-4o-mini/OpenRouter synthetic example completed construction, retrieval and answering. It wrote only Core; Topic reorganization, final management, embedding search and live failure recovery were not exercised. A generated meeting-preference clause also retained an incomplete citation. Broader provider parity and semantic citation quality still require validation.
- **Transaction source attribution needs cleanup before wider use.** Manual imports through `MemoryWorkspace.update` reach a helper in `management/transaction.py` that labels generated records as `claude-code`, irrespective of their actual origin. This is a confirmed follow-up issue; the live example's separate conversation-archive path does not fix it.

The OpenAI Chat adapter does not make every model or endpoint interchangeable. Tool calling, structured output, reasoning controls, usage reporting, and retry behavior depend on the selected provider. Record the requested and returned model/provider for each run.
