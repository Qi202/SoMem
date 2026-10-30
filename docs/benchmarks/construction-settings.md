# Benchmark construction settings

The [confirmed Writer overrides](../../examples/benchmark-writer-overrides.json)
record decisions for the four public benchmark recipes. All use GPT-6 Luna as
Writer and `MemoryConfig.max_turns=120`. All four enable write verification
after every completed session (`verify_writes=true`, `verify_every_sessions=1`)
and schedule local organization every two completed sessions
(`local_reorg_every_sessions=2`). All four use Core capacity/repair target 4000/3600,
Recent limit 50, and final management enabled.

These are partial overrides: construct `MemoryConfig` from `memory_config`,
then pass it as `memory_config` to `BuildConfig` along with `build_config`.
Pass the model separately to the Writer adapter and builder.
The [batch entry](README.md#batch-commands) applies these settings. The overrides do not alter
generic framework defaults or historical build artifacts. Missing fields are
not an agreement to unify the remaining construction settings.

| Recipe | Writer turn limit | Input token cap | Core capacity / repair target | Verification / local organization |
|---|---:|---:|---|---|
| LoCoMo | 120 | 15000 | 4000 / 3600 | 1 / 2 completed sessions |
| BEAM-100K | 120 | 32768 | 4000 / 3600 | 1 / 2 completed sessions |
| BEAM-1M | 120 | 32768 | 4000 / 3600 | 1 / 2 completed sessions |
| LongMemEval-S | 120 | No independent cap; 1 complete session per batch | 4000 / 3600 | 1 / 2 completed sessions |

## Batching

A configured `writer_input_token_cap` (or calibrated capacity) selects
complete-message token packing. `session_batch` is used only when neither
capacity mechanism is configured. Token packing may split a long session across
batches while preserving individual messages and source references. A message
that cannot fit on its own raises an error rather than being truncated.

The confirmed public recipes use:

| Recipe | Batching policy | Input token cap |
|---|---|---:|
| LoCoMo | Complete-message token packing | 15000 |
| BEAM-100K | Complete-message token packing | 32768 |
| BEAM-1M | Complete-message token packing | 32768 |
| LongMemEval-S | One complete original session per batch | None |

For the token-packed recipes, `session_batch` does not control the batch plan.
LongMemEval-S explicitly uses `session_batch=1`, `writer_input_token_cap=None`
and `calibration_path=None`. Its complete-session input remains subject to the
provider's actual context limit; no automatic token-budget splitting is selected.
These are public recipe choices; historical runs retain their own budgets.

## Local organization

`local_reorg_every_sessions=N` schedules a maintenance pass after each N
completed original sessions, provided recently touched Topics exist and the
build has not reached its last session. It operates on accumulated touched Topic
files: it can adjust headings, split or merge files and fix local links while
preserving facts, evidence, history and block IDs. It is additional to normal
writing, not the interval at which memory is initially written.

Smaller intervals offer more frequent organization but add model calls and
opportunities for rewrites or failed edits. Larger intervals reduce maintenance
calls but allow structural issues to remain longer. No controlled comparison
here establishes a quality advantage for either interval.

## Write verification

`verify_every_sessions=N` applies only when `verify_writes=True`. At each Nth
completed original session, and at the final session regardless of divisibility,
the model selects one source-grounded factual probe from that session, tries to
answer from memory, checks support, and attempts memory repair if unsupported.
It is a sampled retrieval self-check, not exhaustive fact validation or benchmark
Judge scoring. Only the selected session is probed; preceding skipped sessions
are not collectively verified by that call.

Smaller intervals sample more sessions and can expose or repair more retrieval
failures, at additional model cost. Larger intervals sample fewer sessions and
may leave more omissions undetected. Verification failures can be logged and
skipped by the build, so configuration alone does not prove a successful check.

Both intervals count completed original sessions, not token batches. A long
session split into many batches does not trigger maintenance on each fragment;
checks run after its last message has been written. Historical 1-session and
8/4-session intervals therefore cannot be compared by batch counts alone.

The four public recipes now use the same verification/organization intervals
(1/2). Historical runs retain their recorded settings. This choice does not
claim an experimentally established optimum; the historical configuration
differences alone do not establish why they were chosen or whether they improved
results.

## Optional routing and repair guidance

All four public benchmark recipes enable
`luna_topic_routing_protocol_enabled`, `luna_core_structural_repair_enabled`,
and `luna_strict_validation_repair_enabled` by default, as explicitly selected
after reviewing the [Writer option audit](writer-option-audit.md). These choices
are recorded in each preset; generic framework defaults remain separate.
The audit limitations still apply, including the keyword-based structural
repair trigger; enabling the options is not a claim of proven quality gains
or cross-model non-regression.
