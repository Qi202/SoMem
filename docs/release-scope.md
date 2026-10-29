# Release scope and remaining work

## Version baseline

This work extends the public `Qi202/SoMem` framework at
`de445936eeee4337838572a5eefe53e6d4d2b69b` (2026-10-17). It adds documentation,
usage examples, packaging/development checks and lazy public API exports. The
existing `code/src/` algorithm implementation is retained. A release tag or a
paper-aligned version has not been published by these local changes.

The working research tree differs in 33 same-path core files as audited on
2026-10-29. Those differences require a scoped review and validation; they are
not automatically merged. Internal experiment state and credentials are not
part of this repository.

## Priorities

| Work | Priority | State |
|---|---|---|
| Installation, README and API/CLI/MCP guides | P0 | Implemented; local release checks passed |
| Offline transaction/retrieval example | P0 | Implemented; validated by release checks |
| Live conversation → memory → answer example | P0 | Recipe and dry-run provided; live provider validation pending |
| License | P0 | MIT confirmed and added, including distribution metadata |
| Align public core with selected paper version | P0 | Version differences identified; selection deferred pending review |
| Paper reproduction entry points | **P1** | Deferred until experimental structure/settings are final |
| Results and provenance bundle | **P1** | Deferred until tables and result selection are final |
| CI, packaging and environment snapshot | P1 | Added locally; GitHub execution awaits publication |
| Provider compatibility and platform matrix | P1 | Document implemented vs verified support; additional live tests pending |
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
