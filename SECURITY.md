# Security reporting

Do not post API keys, private conversations, personal memory or a sensitive
exploit in a public issue. If GitHub offers private vulnerability reporting for
this repository, use that channel. Otherwise open a minimal issue asking the
maintainer for a private contact without describing the exploit publicly.

For a report include the affected commit, Python/OS, the relevant local/MCP
entry point and a synthetic reproduction. Distinguish filesystem path handling,
transaction validation and provider/credential problems.

SoMem's stdio MCP server is intended for trusted local clients. It is not an
authenticated remote service. Back up valuable memory before changing versions;
retain failed-transaction evidence rather than deleting state to force a retry.

No guaranteed response time or production-service support is implied.
