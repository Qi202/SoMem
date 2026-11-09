# Results and provenance

## Best reported configurations

The highlighted scores were checked against the manuscript's backbone comparison,
Writer–Reader matrix, scaling tables, and evaluation appendix. No paper-source
repository links are published here; the arXiv link will be added when available.

| Result | Manuscript evidence | Writer | Reader | Configuration and scope |
|---|---|---|---|---|
| LoCoMo 93.25% LJ accuracy | Writer–Reader matrix, GPT-5.6 Luna Writer / Qwen 3.8 Flash Reader row | GPT-5.6 Luna | Qwen 3.8 Flash | All 1,540 category 1–4 questions; the same Writer's frozen memory is reused across Readers |
| LongMemEval-S 90.20% LJ accuracy | LongMemEval-S comparison, additional-backbone row | DeepSeek V4 Flash | DeepSeek V4 Flash | All 500 questions; agent-controlled search, up to 50 QA turns |
| LoCoMo 95.00% LJ accuracy | LoCoMo scaling details, GPT-6.1 Sol / xHigh row | Not specified for the sweep | GPT-6.1 Sol | Fixed 100-question subset; requested effort xHigh |
| BEAM-100K 70.83% mean nugget score | BEAM-100K scaling details, Qwen 3.8 Flash / None row | Not specified for the sweep | Qwen 3.8 Flash | Fixed 200-question subset; requested effort None |

All four use GPT-4o-mini judging. The manuscript does not explicitly report Reader
reasoning effort for the two highlighted full-set configurations. Its statement
that primary comparisons use no reasoning effort is not assumed to cover these
additional-backbone or cross-model runs. The scaling analysis reuses memory and
questions within each sweep but does not identify its Writer. The two subset
maxima must not be compared as if they were the full-set scores below.

### Full-set LoCoMo cross-model reuse

Overall LJ accuracy (%) on all 1,540 questions. Each row uses one Writer's frozen
memory with four Readers. This is a comparison of model configurations within
SoMem, not a ranking against other memory methods.

| Writer / Reader | GPT-5.6 Luna | Gemini 3 Flash | DeepSeek V4 Flash | Qwen 3.8 Flash |
|---|---:|---:|---:|---:|
| GPT-5.6 Luna | 92.34 | 92.79 | 93.18 | **93.25** |
| Gemini 3 Flash | 91.95 | 91.17 | 93.12 | 91.23 |
| DeepSeek V4 Flash | 92.01 | 92.86 | 92.73 | 90.13 |

The same-model LoCoMo rows in the main table report 92.73% for DeepSeek V4 Flash,
92.34% for GPT-5.6 Luna, and 91.17% for Gemini 3 Flash. The cross-model result
93.25% is higher than these same-model configurations.

## Primary manuscript comparison

The README reports the primary comparison in the manuscript.
A public paper link will be added when the arXiv identifier is available.
The values are transcribed from these tables:

| Benchmark | Manuscript table | Metric | Questions | SoMem | Strongest comparator | Gain (pp) |
|---|---|---|---:|---:|---|---:|
| LoCoMo | Main results | Overall LJ accuracy (%) | 1,540 | 88.96 | E-mem, 86.30 | +2.66 |
| BEAM-100K | Method comparison | Mean nugget score (%) | 400 | 54.02 | Full Context, 50.92 | +3.10 |
| LongMemEval-S | Method comparison | Overall LJ accuracy (%) | 500 | 76.50 | LightMem, 74.50 | +2.00 |

The primary memory-method rows use GPT-6 Luna as Writer and Reader and GPT-4o-mini
as judge. Full Context directly answers from the original conversation and has
no memory construction stage. LoCoMo evaluates categories 1–4. BEAM-100K also
reports a SoMem pass rate of 61.50%; this is a different metric from its 54.02%
mean nugget score. Scores are not averaged across benchmarks.

The manuscript includes additional model configurations, component ablations,
cross-model reuse, and Reader-effort experiments. Refer to the individual tables
for their settings and comparison scope.

## Previously reported best observed scores

The previous README at code revision
[`0467141`](https://github.com/Qi202/SoMem/blob/0467141/README.md)
reported the following best observed scores across evaluated configurations:

| Benchmark | Metric | Best score (%) | Questions |
|---|---|---:|---:|
| LoCoMo | LJ accuracy | 93.25 | 1,540 |
| LongMemEval-S | Accuracy | 90.20 | 500 |
| BEAM-100K | Mean nugget score | 61.19 | 400 |
| BEAM-1M | Mean nugget score | 63.24 | 700 |

The LoCoMo and LongMemEval-S scores above now have verified model mappings in
the manuscript, as documented under Best reported configurations. The historical
README alone does not establish the remaining configurations.

That README specifies GPT-4o-mini judging and identifies the BEAM-100K value as
the **w/o Derived** configuration. It does not provide a complete per-row mapping
to Writer, Reader, frozen memory, run outputs, and configuration for this table.
These values are retained here as previously reported results, separately from
the manuscript's primary comparison. Do not treat them as scores from a single
shared configuration or as verified outputs of the current public runner.

## Public recipes and historical results

The [public benchmark guide](benchmarks/README.md) supplies dataset adaptation,
loadable protocols, batch construction, batch answering, and pinned Judge
adapters. These are executable public recipes, not a claim that the historical
manuscript scores have been rerun with the current code and settings.

One concrete distinction is the Reader turn budget: the current public recipes
use 20 turns for LoCoMo and 120 for BEAM and LongMemEval-S, while the manuscript's
primary evaluation describes 20 for LoCoMo and 50 for BEAM-100K and LongMemEval-S.
See the manuscript evaluation details
and [current public Reader settings](benchmarks/reader-settings.md).

The runner writes individual answers, verdicts, and run metadata; it does not
provide automatic resumption or cross-question score aggregation. Record the
dataset identity, code revision, Writer/Reader/Judge models, effective settings,
memory origin, and full question coverage when reporting a new result.
