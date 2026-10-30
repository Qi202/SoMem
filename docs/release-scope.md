# Release scope and remaining work

## Version baseline

This work extends the public `Qi202/SoMem` framework at
`de445936eeee4337838572a5eefe53e6d4d2b69b` (2026-10-17). It adds documentation,
usage examples, packaging/development checks and lazy public API exports. The
existing `code/src/` algorithm implementation is retained. A release tag or a
paper-aligned version has not been published by these local changes.

The initial comparison with the research tree found 33 different same-path core
files. Most differences were naming changes; that count was not a count of
algorithm changes. The framework selection below resolves the intended local
alignment. Internal experiment state and credentials are not part of this
repository.

Subsequent local alignment uses development Reader instructions and complete
Source-message parsing, while retaining SoMem naming and the public adapter
selection. BM25 cache version 5 rebuilds older indexes. These changes were
published in commit `8a7bf37`. Source-archive newline compatibility is a separate
remaining difference.
The synthetic Writer → memory → Reader example passed locally with GPT-4o-mini
through OpenRouter; its scope and unexercised paths are recorded in validation.

## Priorities

| Work | Priority | State |
|---|---|---|
| Installation, README and API/CLI/MCP guides | P0 | Implemented; local release checks passed |
| Offline transaction/retrieval example | P0 | Implemented; validated by release checks |
| Live conversation → memory → answer example | P0 | Passed locally with GPT-4o-mini/OpenRouter: exact Source preservation, Core writes, BM25 retrieval, correct answer and scratch validation |
| License | P0 | MIT confirmed and added, including distribution metadata |
| Select and locally align the public framework version | P0 | Complete locally: SoMem naming, public adapters, development Reader instructions and full-message Source parsing; 222 tests passed |
| Paper reproduction entry points | **P1** | [Four protocols and package loader](benchmarks/README.md) implemented; dataset adapters, benchmark launchers and pinned Judge integration remain pending |
| Results and provenance bundle | **P1** | Deferred until tables and result selection are final |
| CI, packaging and environment snapshot | P1 | Baseline 304291f passed GitHub CI; current protocol/Core changes passed 238 local offline tests and a wheel protocol-loading check |
| Provider compatibility and platform matrix | P1 | One GPT-4o-mini/OpenRouter example verified; additional providers, platforms, semantic citation coverage and unexercised management paths remain |
| Source-archive newline compatibility | P1 | Remaining CRLF normalization difference; separate compatibility repair |
| Limit automatic Git commits to memory files | P1 | Confirmed unrelated staged-file inclusion; regression and repair pending |
| Correct transaction source attribution | P1 | Confirmed manual imports receive claude-code source IDs; code and documentation repair pending |
| Citation metadata and repository About/topics | P1 | Final paper/author metadata and remote update deferred |
| Short video, notebook and broader integrations | P2 | Future work |

Observed local checks and their limits are recorded in [validation](validation.md).

The local unified experiment candidate is separate work. Its offline checks do
not establish that historical paper results were reproduced with that runner.
No reproduction commands or scores are fabricated to fill the deferred items.

## Publication checklist

- Review documentation, installed-package checks and the generated distribution.
- Preserve the MIT license and copyright notice in release distributions.
- Choose a version and tag only after checks pass; record core changes separately.
- Validate the live example with each provider/model before marking it tested.
- Update repository description/homepage/topics when these local changes are approved for publication.
- Keep private workspaces, model keys, caches and experiment logs out of the release.
