# Plugin preview verification

Verified on October 17, 2026, on Windows with Python 3.12.10. The implementation starts from upstream commit de44593 on the local codex/plugin-foundation branch.

## Completed checks

- All 221 repository tests pass, including the 217 existing offline tests and four new plugin integration tests.
- The four plugin tests also pass in a separate installation with only the base package and development tools. anthropic, openai, claude-agent-sdk, sentence-transformers, and torch are absent.
- Two independent stdio MCP server processes write and recall the same project decision and personal preference. The test uses paths containing spaces and a process directory representing a plugin cache. It checks project isolation, source links, stable IDs, stale-revision rejection, and the absence of memory under the plugin cache.
- The complete memory_update example in the remember Skill is executed by the integration test. Invalid or missing project selection and the original fixed-workspace protocol are also checked.
- The portable plugin.json and mcp.json validate against the official Agent Plugins 1.0.0 JSON schemas. Claude Code validates its native plugin and marketplace manifests. All four Skill folders pass the skill-creator validator.
- A clean preview ZIP is extracted to a separate directory. Its portable mcp.json launches uvx offline using cached dependencies, discovers all six tools, saves the documented project decision, and recalls it successfully. Only the personal-memory path is redirected to a temporary test directory.
- A Python wheel and source distribution build successfully; the wheel keeps model SDKs in optional extras and the source distribution includes requirements-core.txt.

The Windows MCP test exposed a first-import stall when NumPy loaded inside a tool worker. The required lexical retrieval backend now loads during server startup, and the real transport test verifies retrieval after a process restart.

## Validation boundary

These checks exercise the real MCP transport and memory operations. They do not represent a live Claude Code conversation followed by a live Codex conversation. Client installation, plugin discovery, model-driven Skill selection, and host permission prompts still need that final manual check on the intended host versions.

No transcript Hooks or background Writer are included. The plugin requires uvx on the host application PATH; the test runtime installs it only in an isolated environment.
