"""Path-safe durable storage for Claude Code session data."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path


# Historical store kept Claude session files inside cloud storage. That was still
# too long: Claude Code nests ``projects/<encoded-cwd>/.../tool-results`` under
# the config root, and the encoded cwd is the original experiment path.
# cloud storage on macOS then refuses the upload. ``.nosync`` tells the Mac client
# not to upload the folder; local resume and archives still use the same tree.
OLD_STORE_DIR_NAME = ".cc"
STORE_DIR_NAME = ".cc.nosync"


def durable_claude_config_root(checkpoint_dir: str | Path) -> Path:
    """Return a stable, shallow config root for checkpoints under code/results.

    Claude Code nests its own project path and session UUID below
    ``CLAUDE_CONFIG_DIR``. Keeping that directory inside an already-deep result
    name can exceed cloud storage's path limit. For repository result checkpoints,
    place the Claude-owned subtree in a flat store at
    ``code/results/.cc.nosync/<stable-id>``. The ID is derived from the path
    relative to ``code/results``, so it remains stable if the checkout moves.

    Checkpoints outside a ``code/results`` tree retain the legacy layout. This
    keeps the adapter portable and avoids writing test or third-party data into
    the repository store.
    """

    checkpoint = Path(checkpoint_dir).resolve()
    results_root = _code_results_root(checkpoint)
    if results_root is None:
        return checkpoint / "claude-config"

    return results_root / STORE_DIR_NAME / _storage_id(checkpoint, results_root)


def migrate_legacy_claude_config(checkpoint_dir: str | Path) -> Path:
    """Move one legacy config tree into the shallow store when needed."""

    checkpoint = Path(checkpoint_dir).resolve()
    target = durable_claude_config_root(checkpoint)
    sources = [
        source
        for source in _legacy_claude_config_sources(checkpoint)
        if source != target and source.exists()
    ]
    if not sources:
        return target
    if target.exists() or len(sources) > 1:
        raise FileExistsError(
            "both legacy and shallow Claude config directories exist: "
            + " and ".join(str(path) for path in (*sources, target))
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(sources[0]), str(target))
    return target


def relocate_sync_unsafe_store(results_root: str | Path) -> int:
    """Move ``code/results/.cc`` into the cloud storage-excluded store.

    Returns the number of config trees moved. Existing ``.cc.nosync`` ids win;
    leftover ``.cc`` copies are removed so cloud storage stops retrying long paths.
    """

    results_root = Path(results_root).resolve()
    old_store = results_root / OLD_STORE_DIR_NAME
    new_store = results_root / STORE_DIR_NAME
    if not old_store.exists():
        return 0

    new_store.mkdir(parents=True, exist_ok=True)
    moved = 0
    for child in sorted(old_store.iterdir()):
        dest = new_store / child.name
        if dest.exists():
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
            continue
        shutil.move(str(child), str(dest))
        moved += 1
    if old_store.exists() and not any(old_store.iterdir()):
        old_store.rmdir()
    return moved


def _storage_id(checkpoint: Path, results_root: Path) -> str:
    relative = checkpoint.relative_to(results_root).as_posix()
    return hashlib.sha256(relative.encode("utf-8")).hexdigest()[:20]


def _legacy_claude_config_sources(checkpoint: Path) -> list[Path]:
    sources = [checkpoint / "claude-config"]
    results_root = _code_results_root(checkpoint)
    if results_root is not None:
        storage_id = _storage_id(checkpoint, results_root)
        sources.append(results_root / OLD_STORE_DIR_NAME / storage_id)
    return sources


def _code_results_root(path: Path) -> Path | None:
    for candidate in (path, *path.parents):
        if candidate.name == "results" and candidate.parent.name == "code":
            return candidate
    return None
