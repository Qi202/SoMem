# Changelog

## Unreleased

- Validate the synthetic live example with GPT-4o-mini through OpenRouter; record Source integrity, actual retrieval, usage and the limited coverage of the run.
- Align Reader guidance with the development version while retaining SoMem naming and the public adapter selection.
- Index complete Source messages, including paragraphs/lists, in BM25 and embedding retrieval; rebuild older BM25 caches using cache format 5.

- Add installation and API/CLI/MCP guides, architecture overview and configuration examples.
- Add offline memory transaction/retrieval and model-driven conversation examples.
- Export Runtime, create_runtime and OpenAI Chat adapter/config through the lazy `somem` facade.
- Add developer dependencies, CI and installed-package release checks.
- Document deferred P1 paper-reproduction/results work.
- Adopt the MIT License and include it in package distributions.
- Preserve the public core implementation; no historical score is attached to the new examples.

## 0.1.0 framework baseline

The public snapshot includes Topic-first retrieval guidance, selectable runtime
adapters, validated document operations, workspace tools and selected offline
tests. The version appeared in package metadata before this documentation work;
it is not a claim that a corresponding package or Git tag was published.

## Public benchmark protocol configuration (2026-10-30)

- Add four versioned, loadable construction/Reader recipes with explicit defaults.
- Keep Core files in place while allowing evidence-preserving moves into Topics.
- Document benchmark best scores and the remaining dataset-runner/Judge work.
