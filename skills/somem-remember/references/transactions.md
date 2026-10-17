# Memory transactions

Call memory_status first. Its outer revision belongs in base_revision. Include the same project_root throughout the read/write operation; otherwise the selected layers and revision can differ.

## Add a new project topic

This complete example creates topics/decisions.md. Substitute the current project's absolute root and the actual revision. Use it only if that file does not exist; edit an existing file using a contextual unified diff instead.

~~~json
{
  "project_root": "/absolute/path/to/project",
  "layer": "project",
  "base_revision": "COPY_THE_REVISION_FROM_MEMORY_STATUS",
  "patch": "--- /dev/null\n+++ b/topics/decisions.md\n@@ -0,0 +1,5 @@\n+# Decisions\n+\n+This project uses PostgreSQL.[^new-evidence-db] ^new-block-database\n+\n+[^new-evidence-db]: Time: `undated`; Sources: new-source-user\n",
  "sources": [
    {
      "label": "new-source-user",
      "role": "user",
      "content": "This project uses PostgreSQL."
    }
  ]
}
~~~

The runtime assigns stable block IDs, evidence IDs, and source links. Keep existing IDs unchanged when editing. New source labels match new-source-[a-z0-9-]+; every source needs label, role, and content. Allowed roles are user, assistant, system, and tool. observed_at is optional and must be ISO 8601 when supplied.

New Topic blocks use ^new-block-<name> and cite a footnote such as [^new-evidence-db]. Its definition has Time and Sources fields. Use undated when the event date is unknown. The source's capture timestamp is not necessarily the date of the event.

Only Topic Markdown and Core can be patched. Sources are immutable and archived through the sources argument. Timeline, relations, and recent events are derived by the runtime. Do not write these access views directly.

## Personal preferences and Core

Use layer=global for personal preferences. With no active project, omit project_root and obtain a global-only revision first. If a project is selected, keep the same project_root even when writing to the global layer.

Core lives at topics/core.md in new workspaces, with root-level core.md supported in legacy workspaces. Read the actual Core path before editing. Core can cite new evidence using the same footnote format, without Topic block IDs.

After a successful update, use the returned IDs or list/read the changed files to verify the committed result. If memory_committed is true but git_committed is false, do not claim the memory rolled back. Plugin launchers disable automatic Git commits.
