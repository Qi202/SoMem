# Scoring source attribution

These are unchanged scoring-source snapshots from ZJUNLP/LightMem, distributed
under the accompanying MIT license. Repository: https://github.com/zjunlp/LightMem
License: https://github.com/zjunlp/LightMem/blob/main/LICENSE

The four LongMemEval files are pinned to revision
8449d574df6bae1bdf3314a1564da65e2f37e046. The LoCoMo file preserves the exact local
strictJudge implementation used by the experiment protocol. Its function and
prompt are unmodified. SHA-256 locks are enforced in `somem.benchmark_judge`.

The runtime calls the original LoCoMo `evaluate_llm_judge`. For LongMemEval,
it loads the original dispatch, preprocessing, templates and parser expression
without importing the upstream inference dependency stack. These snapshots are
scoring dependencies, not an additional memory framework requirement.
