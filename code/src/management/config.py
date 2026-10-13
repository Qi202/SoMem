"""Memory writer configuration."""

from dataclasses import dataclass

@dataclass(frozen=True)
class MemoryConfig:
    core_max_tokens: int = 3_000
    core_capacity_limit_enabled: bool = True
    core_repair_target_tokens: int = 2_700
    core_repair_max_checks: int = 8
    core_repair_max_trajectories: int = 2
    core_repair_stagnation_limit: int = 2
    core_capacity_repair_enabled: bool = True
    luna_topic_routing_protocol_enabled: bool = False
    luna_core_capacity_prompt_enabled: bool = False
    luna_core_structural_repair_enabled: bool = False
    luna_core_structural_repair_max_trajectories: int = 2
    luna_strict_validation_repair_enabled: bool = False
    validation_diagnostic_aggregation_enabled: bool = False
    unchecked_writer_commits: bool = False
    # Opt-in because transitioning repair categories changes trajectory policy.
    repair_cross_category_enabled: bool = False
    generic_repair_max_trajectories: int = 3
    luna_optional_maintenance_rejection_noop_enabled: bool = False
    writer_shell_examples: bool = False
    writer_tool_output_max_chars: int | None = None
    recent_limit: int = 50
    writer_stagnation_limit: int = 8
    tool_failure_recovery_enabled: bool = False
    max_turns: int = 20
    max_budget_usd: float | None = None
    shell_backend: str = "auto"

    def __post_init__(self) -> None:
        if (self.writer_tool_output_max_chars is not None
                and self.writer_tool_output_max_chars < 1024):
            raise ValueError("writer_tool_output_max_chars must be at least 1024")
        if self.core_max_tokens < 0:
            raise ValueError("core_max_tokens must be non-negative")
        if self.core_repair_target_tokens < 0:
            raise ValueError("core_repair_target_tokens must be non-negative")
        if self.core_repair_max_checks < 1:
            raise ValueError("core_repair_max_checks must be positive")
        if self.core_repair_max_trajectories < 1:
            raise ValueError("core_repair_max_trajectories must be positive")
        if self.core_repair_stagnation_limit < 1:
            raise ValueError("core_repair_stagnation_limit must be positive")
        if self.luna_core_structural_repair_max_trajectories < 1:
            raise ValueError(
                "luna_core_structural_repair_max_trajectories must be positive"
            )
        if self.generic_repair_max_trajectories < 1:
            raise ValueError("generic_repair_max_trajectories must be positive")
        if self.recent_limit < 0:
            raise ValueError("recent_limit must be non-negative")
        if self.writer_stagnation_limit < 0:
            raise ValueError("writer_stagnation_limit must be nonnegative")
        if self.max_turns < 1:
            raise ValueError("max_turns must be positive")
        if self.max_budget_usd is not None and self.max_budget_usd <= 0:
            raise ValueError("max_budget_usd must be positive")
        if self.shell_backend not in {"auto", "posix-bash", "native"}:
            raise ValueError(
                "shell_backend must be auto, posix-bash, or native"
            )

    @property
    def effective_core_repair_target_tokens(self) -> int:
        """Return a target with roughly ten percent headroom below the limit."""
        headroom_target = int(self.core_max_tokens * 0.9)
        return min(self.core_repair_target_tokens, headroom_target)
