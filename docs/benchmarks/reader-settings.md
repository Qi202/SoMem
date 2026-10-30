# Shared benchmark Reader settings

The public benchmark recipes use separate `bm25_search` and `embedding_search`
tools (`search_tools="split"`), with current Topic-first guidance and Source
verification enabled. Both search tools accept optional `path_prefix`, for
example `topics/`, `sources/`, or `topics/people/`. This argument filters search
scope before ranking; it is chosen per tool call, not a fixed global folder.
Omitting it allows search across the visible indexed files.

| Benchmark | Reader turn limit |
|---|---:|
| LoCoMo | 20 |
| BEAM-100K | 120 |
| BEAM-1M | 120 |
| LongMemEval-S | 120 |

The machine-readable [Reader presets](../../examples/benchmark-reader-configs.json)
contain `QueryConfig` fields. Load the selected entry with
`QueryConfig(**presets[benchmark])` when wiring the benchmark runner. These are
public recipe settings, not a relabeling of historical runs. The generic
`QueryConfig` already defaults to split search and retains its 20-turn default;
benchmark-specific budgets are supplied by the presets.

Forced final answering is an adapter setting, separate from `QueryConfig`.
Its default remains off; this Reader-budget update does not enable it.
Full answers are saved without dual fields or compression.

## Construction status

All four public construction recipes now use the same confirmed settings:
Luna6 Writer, 120 turns, Core capacity /
repair target 4000/3600, verification every session, and local organization every
two completed sessions. Dataset-specific batching is confirmed in the [construction settings](construction-settings.md)
and Writer overrides record the full agreed profile. Benchmark runners remain
to be packaged; historical builds retain their original configurations.

Confirmed Writer overrides and interval semantics are recorded in
[construction settings](construction-settings.md).
