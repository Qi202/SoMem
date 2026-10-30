# Local release validation

## Subsequent local alignment (2026-10-29, not yet pushed)

Reader instructions now use the development content with SoMem naming and the
public adapter-selection interface retained. Source parsing now indexes each
complete marked message, including paragraphs, lists and internal headings.
BM25 cache format advances from 4 to 5 so older cached fragments are reindexed.
Embedding retrieval uses the same Source parser.

All **222 tests passed**, including five new regression cases covering LF/CRLF
message boundaries, complete content, v4 cache rebuilding, embedding input, and
legacy unmarked references. These checks use synthetic data and a fake encoder;
they do not call a provider or rerun benchmarks. Original Source bytes are
checked unchanged.

### Real-model synthetic example

The unmodified `examples/live_demo.py` also completed on 2026-10-29 using
`openai/gpt-4o-mini` for both Writer and Reader through
`https://openrouter.ai/api/v1`. It used two synthetic sessions containing three
messages, the example's default build/query settings, and a frozen copy of this
local code. No historical experiment inputs or answers were changed.

| Check | Observed result |
|---|---|
| Writer construction | Both batches completed; all three original messages preserved exactly |
| Reader retrieval | Two successful BM25 calls, one over Topics/Core and one over Sources |
| Answer | `Alice prefers jasmine tea.` |
| Source integrity | Source hashes unchanged by Reader execution |
| Workspace validation | Passed in a scratch copy; original workspace unchanged |
| Citation structure | Core citation links resolve to existing Source anchors |
| Provider calls | 17 successful HTTP requests: 15 Writer/verification, 2 Reader |
| Usage returned by provider | 27,234 input tokens; 731 output tokens; reported cost USD 0.002891295 |

The synthetic example establishes a working conversation → memory → retrieval →
answer path for this one configuration. It does not certify benchmark quality,
other providers, or semantic support for every generated clause. In particular,
the Writer added a meeting-preference clause while retaining only the first
session's citation. The drink answer is directly supported by that source, but
the additional clause's citation coverage needs separate quality checks.

Only Core was written. Non-Core Topic blocks, embedding search, Recent entries,
graph relations and reorganization were not exercised. The build checkpoint is
`complete`, with `event_count=0`; current code gates final management on that
count, leaving `final_management=pending`. This is not evidence of a completed
final-management run. No live errors occurred, so failure recovery was not
exercised; offline recovery tests remain the evidence for that path.

Local audit artifacts include frozen input/code hashes, request/response bodies
without authentication headers, Writer logs, answer traces, Source hashes and a
completion audit. They are kept outside the public repository under
`docs/experiments/audits/somem-release-live-smoke-20261029` in the research
workspace. The raw observer log labels the two Reader HTTP calls with trajectory
number `9`; the completion audit records that role binding without rewriting the
original logs. Provider-reported cost is not an independent billing statement.

## Published onboarding baseline (304291f)

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

Not validated by the published baseline checks: live Writer/Reader provider behavior, benchmark
reproduction, hosted MCP integrations, Linux/Windows execution, GPU performance,
or GitHub Actions execution. The workflow is prepared locally; an observed local
pass is not a remote CI result. The live example is a recipe, not a reproduced
paper run. No release/tag/package publication was performed.

## Protocol publication checks (2026-10-30)

All 238 offline tests passed, including Core file preservation, evidence-preserving
Core-to-Topic moves, and four validated protocol configurations wired into Runtime.
A wheel was built and all four protocols loaded directly from the wheel, checking
that package data is distributed. No live model call, dataset-wide rerun or
benchmark Judge invocation was performed. These checks establish configuration
and packaging behavior, not reproduced paper scores.
