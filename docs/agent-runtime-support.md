# Agent runtime support

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

The MCP plugin uses the connected host as its Writer and Reader, without selecting a separate model runtime. Interactive transactions archive sources under the provider-neutral `somem` origin. Existing source records keep their original attribution.

## Limitations and follow-up

- **No provider-neutral USD budget enforcement for OpenAI Chat.** `OpenAIChatAgent` raises when `max_budget_usd` is set. Chat Completions providers report cost differently, and some do not return a billed cost at all. Keep the budget unset or enforce limits at the provider/account layer; do not assume SoMem enforces a USD cap.
- **No portable in-trajectory resume for OpenAI Chat.** Its conversation state is held in memory for the active call. SoMem's build checkpoints still preserve completed batches, but an interrupted Writer trajectory cannot resume from its prior provider conversation. Claude Code has its own optional session-resume support.
- **End-to-end GPT Writer validation remains outstanding.** The repository contains focused offline tests for OpenAI tool validation and error feedback, but no recorded full memory-construction run using `OpenAIChatAgent`. Treat the adapter as implemented, with provider/model-specific Writer behavior still requiring validation before claiming parity with Claude Code.

The OpenAI Chat adapter does not make every model or endpoint interchangeable. Tool calling, structured output, reasoning controls, usage reporting, and retry behavior depend on the selected provider. Record the requested and returned model/provider for each run.
