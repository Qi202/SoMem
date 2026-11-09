# Results and provenance

## Primary manuscript comparison

The README reports the primary comparison in the manuscript snapshot
[`b7eea94`](https://github.com/fzkuji2026/2027-arXiv-Zichuan-Fu-Agent-Memory/tree/b7eea94).
The values are transcribed from these tables:

| Benchmark | Manuscript source | Metric | Questions | SoMem | Strongest comparator | Gain (pp) |
|---|---|---|---:|---:|---|---:|
| LoCoMo | [Main results](https://github.com/fzkuji2026/2027-arXiv-Zichuan-Fu-Agent-Memory/blob/b7eea94/tables/locomo_by_subset.tex) | Overall LJ accuracy (%) | 1,540 | 88.96 | E-mem, 86.30 | +2.66 |
| BEAM-100K | [Method comparison](https://github.com/fzkuji2026/2027-arXiv-Zichuan-Fu-Agent-Memory/blob/b7eea94/tables/beam_method_comparison.tex) | Mean nugget score (%) | 400 | 54.02 | Full Context, 50.92 | +3.10 |
| LongMemEval-S | [Method comparison](https://github.com/fzkuji2026/2027-arXiv-Zichuan-Fu-Agent-Memory/blob/b7eea94/tables/longmemeval_comparison.tex) | Overall LJ accuracy (%) | 500 | 76.50 | LightMem, 74.50 | +2.00 |

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
See the [manuscript evaluation details](https://github.com/fzkuji2026/2027-arXiv-Zichuan-Fu-Agent-Memory/blob/b7eea94/6ExperimentAppendix.tex)
and [current public Reader settings](benchmarks/reader-settings.md).

The runner writes individual answers, verdicts, and run metadata; it does not
provide automatic resumption or cross-question score aggregation. Record the
dataset identity, code revision, Writer/Reader/Judge models, effective settings,
memory origin, and full question coverage when reporting a new result.
