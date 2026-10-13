"""Structured memory-management failures."""

from __future__ import annotations


class CommitStateError(RuntimeError):
    """Infrastructure failure, never a request for model content repair."""

    def __init__(self, message: str, *, commit_state: str) -> None:
        self.commit_state = commit_state
        super().__init__(message)


class CoreCapacityError(ValueError):
    """The staged Core Memory exceeds its configured token budget."""

    def __init__(self, *, token_count: int, limit: int) -> None:
        self.token_count = int(token_count)
        self.limit = int(limit)
        super().__init__(
            f"Core Memory exceeds {self.limit} tokens: {self.token_count}"
        )
