# Local release validation

Validation date: 2026-10-29. Platform: macOS arm64, CPython 3.12.13.
Dependencies were installed into a new isolated environment. See the observed
[constraints snapshot](../constraints/macos-arm64-python312.txt).

| Check | Observed result |
|---|---|
| Source installation with development dependencies | Passed |
| Existing public-core tests | **217 passed** |
| Public `somem` imports and CLI help | Passed |
| Cited transaction, BM25 retrieval and scratch validation | Passed on synthetic data |
| Live example `--dry-run` | Passed with dummy settings; no model requests or output workspace |
| Real stdio MCP initialize/list/status/read/grep | Passed |
| Wheel and source distribution build | Passed |
| Wheel imports and release smoke from outside the checkout | Passed; imports resolved to the wheel installation |
| Source distribution guides and examples | Present |
| Local Markdown targets and architecture SVG parsing | Passed |
| Existing core compared with remote baseline | No changes under `code/src/` |

Initial runs in the synchronized checkout were blocked reading OneDrive bytecode
and Git-object placeholders. Validation was repeated on a byte-identical source
copy excluding caches and Git internals. Baseline comparison used a fresh clone
of `de445936eeee4337838572a5eefe53e6d4d2b69b`. Neither the original research
tree nor benchmark answers were modified.

Not validated by these checks: live Writer/Reader provider behavior, benchmark
reproduction, hosted MCP integrations, Linux/Windows execution, GPU performance,
or GitHub Actions execution. The workflow is prepared locally; an observed local
pass is not a remote CI result. The live example is a recipe, not a reproduced
paper run. No release/tag/package publication was performed.
