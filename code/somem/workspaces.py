"""Select a project explicitly, independently of the plugin process directory."""

from pathlib import Path

from src.management.transaction import TransactionError
from src.retrieval.layers import Layer, LayeredMemory


class ProjectWorkspaces:
    def __init__(self, layers: list[Layer], *, enabled: bool = False):
        self.layers = layers
        self.enabled = enabled
        self.global_memory = LayeredMemory(layers)
        if enabled and any(layer.name == "project" for layer in layers):
            raise ValueError("project is reserved when --project-workspaces is enabled")

    def resolve(self, project_root: str | None) -> LayeredMemory:
        if project_root is None:
            return self.global_memory
        if not self.enabled:
            raise TransactionError(
                "INVALID_ARGUMENT",
                "project_root requires starting the server with --project-workspaces",
            )
        root = Path(project_root).expanduser()
        if not root.is_absolute() or not root.is_dir():
            raise TransactionError(
                "INVALID_PROJECT", "project_root must be an existing absolute directory",
            )
        root = root.resolve()
        memory_root = root / ".somem-memory"
        if memory_root.is_symlink():
            raise TransactionError(
                "INVALID_PROJECT", "project memory must not be a symlink",
            )
        if any(layer.root == memory_root for layer in self.layers):
            raise TransactionError(
                "INVALID_PROJECT", "project memory is already configured as another layer",
            )
        from .cli import ensure_workspace

        ensure_workspace(memory_root, self_ignore=True)
        return LayeredMemory([Layer(name="project", root=memory_root), *self.layers])
