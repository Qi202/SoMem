"""Persistent runtime cursors and deterministic maintenance triggers."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from ..workspace_layout import runtime_dir


@dataclass(frozen=True)
class SourceRecord:
    provider: str
    thread_id: str
    message_id: str
    ordinal: int
    role: str
    content: str
    timestamp: str | None = None

    @property
    def source_id(self) -> str:
        return f"{self.provider}/{self.thread_id}/{self.message_id}"


@dataclass
class RuntimeState:
    cursors: dict[str, dict[str, object]] = field(default_factory=dict)
    creation_order: dict[str, int] = field(default_factory=dict)
    write_commits_since_global: int = 0
    local_batches: int = 0
    local_tokens: int = 0
    last_global_at: str | None = None

    def advance_cursor(self, thread_id: str, message_id: str, *, ordinal: int) -> None:
        current = self.cursors.get(thread_id)
        if current is not None and int(current["ordinal"]) > ordinal:
            raise ValueError("cursor cannot move backwards")
        self.cursors[thread_id] = {"message_id": message_id, "ordinal": ordinal}


class RuntimeStateStore:
    def __init__(self, memory_dir: Path):
        self.memory_dir = Path(memory_dir)
        self.path = runtime_dir(self.memory_dir) / "runtime.json"

    def load(self) -> RuntimeState:
        if not self.path.exists():
            return RuntimeState()
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        return RuntimeState(**payload)

    def save(self, state: RuntimeState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(asdict(state), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.path)

    def git_commit(self, message: str) -> str | None:
        try:
            subprocess.run(
                ["git", "rev-parse", "--is-inside-work-tree"],
                cwd=self.memory_dir,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except subprocess.CalledProcessError:
            return None
        memory_paths = (
            "core.md", "recent_events.jsonl", "relations.json",
            "sources", "topics", "timeline",
        )
        tracked = subprocess.check_output(
            ["git", "ls-files", "--cached", "-z", "--", *memory_paths],
            cwd=self.memory_dir, text=True, encoding="utf-8", errors="replace",
        ).split("\0")
        to_stage = [value for value in memory_paths
                    if (self.memory_dir / value).exists() or any(
                        path == value or path.startswith(value + "/")
                        for path in tracked)]
        tracked.extend(subprocess.check_output(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=D",
             "--relative", "-z", "--", *memory_paths],
            cwd=self.memory_dir, text=True, encoding="utf-8", errors="replace",
        ).split("\0"))
        # Keep tracked deletions even when a whole memory directory is gone.
        candidates = [value for value in memory_paths
                      if (self.memory_dir / value).exists() or any(
                          path == value or path.startswith(value + "/")
                          for path in tracked)]
        if not candidates:
            return None
        if to_stage:
            subprocess.run(
                ["git", "add", "-A", "--", *to_stage],
                cwd=self.memory_dir,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        changed = subprocess.run(
            ["git", "diff", "--cached", "--quiet", "--", *candidates],
            cwd=self.memory_dir,
        )
        if changed.returncode not in (0, 1):
            changed.check_returncode()
        if changed.returncode == 0:
            return None
        subprocess.run(
            # --only commits these worktree paths, leaving other index entries
            # (including partially staged user files) out of this commit.
            ["git", "commit", "--only", "-m", message, "--", *candidates],
            cwd=self.memory_dir,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=self.memory_dir,
            text=True,
            encoding="utf-8",
            errors="replace",
        ).strip()


def should_incremental_write(
    unprocessed_tokens: int,
    last_message_at: datetime,
    now: datetime,
    *,
    token_threshold: int,
    idle_after: timedelta = timedelta(hours=1),
) -> bool:
    return unprocessed_tokens >= token_threshold or (
        unprocessed_tokens > 0 and now - last_message_at >= idle_after
    )


def should_local_reorganize(
    new_batches: int,
    new_tokens: int,
    *,
    batch_threshold: int,
    token_threshold: int,
) -> bool:
    return (
        batch_threshold > 0 and new_batches >= batch_threshold
    ) or (
        token_threshold > 0 and new_tokens >= token_threshold
    )


def should_global_manage(
    last_global_at: datetime | None,
    now: datetime,
    *,
    new_write_commits: int,
) -> bool:
    if new_write_commits <= 0:
        return False
    return last_global_at is None or now - last_global_at >= timedelta(days=1)
