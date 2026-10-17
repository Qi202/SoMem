---
name: somem-remember
description: Save or correct a project decision, task fact, or personal preference in SoMem when the user asks to remember it for later sessions.
---

# Remember with evidence

Identify the active project's absolute root from host context and pass it to every memory tool. Choose layer=project for project facts and layer=global for personal preferences. For a personal-only operation with no project, explicitly choose layer=global.

Call memory_status, search for related memory, and read the existing target before editing. Use the returned revision for memory_update. Read [the transaction format](references/transactions.md) when constructing a write.

Archive the actual supporting statement in sources and connect each new claim to that source through the Topic footnote format. Preserve existing block IDs and keep topics/core.md small. Record only task-relevant evidence, omitting credentials and unrelated transcript content. Do not invent event dates; undated is valid when the event date is unknown.

Submit one scoped transaction. On CONCURRENT_UPDATE, read the latest revision and target, then rebuild the patch; make at most two retries. For other failures, inspect the reported problem and correct the input rather than repeating it unchanged.

Read the committed memory back. Confirm what was actually stored and its layer only after a successful write. The plugin does not automatically capture the whole conversation or run a background Writer.
