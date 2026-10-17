---
name: somem-status
description: Inspect SoMem's active project, personal and project memory layers, revisions, and available retrieval capabilities.
---

# Inspect memory status

Resolve the active project's absolute root from host context and call memory_status with that project_root. Omit the argument for a personal-only inspection.

Report the selected project, layer locations, topic/source counts, revision, and embedding availability from the actual response. Explain missing or unreadable layers. Use memory_list only when the user wants file details; status alone does not require reading private source contents.

The default plugin uses lexical retrieval and the connected host model. Installing embeddings or standalone agent adapters is optional. No continuous transcript capture or background Writer is enabled by this initial plugin.
