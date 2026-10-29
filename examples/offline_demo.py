"""Commit cited memory and retrieve it without a model or embedding download."""

import argparse
import json
from pathlib import Path

from somem import MemoryWorkspace, inspect

PATCH = """--- /dev/null
+++ b/topics/preferences.md
@@ -0,0 +1,5 @@
+# Preferences
+
+Alice prefers jasmine tea.[^new-evidence-tea] ^new-block-tea
+
+[^new-evidence-tea]: Time: `2026-10-29`; Sources: new-source-tea
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path("demo-memory"))
    args = parser.parse_args()
    root = args.workspace.expanduser().resolve()
    if root.exists() and any(root.iterdir()):
        parser.error("use a new or empty workspace; existing memory is never overwritten")
    workspace = MemoryWorkspace(root)
    result = workspace.update(
        base_revision=workspace.revision(),
        patch=PATCH,
        sources=[{
            "label": "new-source-tea", "role": "user",
            "content": "My name is Alice. I prefer jasmine tea.",
            "observed_at": "2026-10-29T10:00:00+08:00",
        }],
        git_commit="off",
    )
    hits = inspect.search(root, "jasmine tea", method="bm25", top_k=3)
    document = inspect.read_file(root, "topics/preferences.md")
    print(json.dumps({
        "mode": "offline: no model calls",
        "workspace": str(root), "source_ids": result.source_ids,
        "search": hits, "document": document,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
