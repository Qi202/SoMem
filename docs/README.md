# SoMem documentation

## Start here

| Guide | What it covers |
|---|---|
| [Getting started](getting-started.md) | Installation, the conversation-to-answer example, an offline example, and troubleshooting |
| [Configuration](configuration.md) | Model endpoints, credentials, effort, and budgets |
| [Python API](python-api.md) | Memory construction, answering, inspection, and validated edits |
| [MCP integration](mcp.md) | Agent tools, client setup, and workspace layers |
| [Runtime support](agent-runtime-support.md) | Adapter support and validation boundaries |

## Research and evaluation

| Guide | What it covers |
|---|---|
| [Results and provenance](results.md) | Manuscript comparisons, additional reported scores, and configuration differences |
| [Benchmark guide](benchmarks/README.md) | Data preparation, construction, answering, and fixed Judge commands |
| [LoCoMo protocol](benchmarks/locomo.md) | Dataset scope, Writer/Reader settings, and scoring |
| [Construction settings](benchmarks/construction-settings.md) | Input batching and Writer configuration |
| [Reader settings](benchmarks/reader-settings.md) | Search interface, visible memory, and turn budgets |

## Development

See [Contributing](../CONTRIBUTING.md) for development setup, checks, and
packaging, and [Security](../SECURITY.md) for reporting sensitive issues.

<details>
<summary><b>Maintenance records and implementation audits</b></summary>

- [Release scope](release-scope.md): framework alignment history and remaining release work.
- [Validation record](validation.md): recorded local checks and their limits.
- [Writer option audit](benchmarks/writer-option-audit.md): optional switches and their interpretation.
- [Changelog](../CHANGELOG.md): changes to the public framework.

</details>
