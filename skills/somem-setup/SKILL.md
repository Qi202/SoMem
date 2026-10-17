---
name: somem-setup
description: Set up or diagnose the SoMem memory connection and identify the current project's and personal memory workspaces.
---

# Connect SoMem

Use the connected SoMem memory tools. Hosts may add a prefix to their names.

Identify the user's active project's absolute root from the host context. The plugin's installation folder is not the user's project. If the root is unclear, resolve it using the host's workspace or repository tools.

Call memory_status with project_root set to that directory. With no active project, omit project_root to inspect personal memory only. Report the actual selected project and available layers. New project memory is created in .somem-memory under that root; personal memory is shared across clients.

If the tools are absent, explain that the SoMem MCP connection must be installed or reloaded. The plugin requires uvx on the host's PATH and Python 3.12 or later, which uv can provision. Do not claim that a missing connection is ready or modify unrelated host settings.

If project_root is rejected by a manually registered server, enable --project-workspaces in that server's launch arguments or use its explicitly configured workspace. Confirm success with memory_status.
