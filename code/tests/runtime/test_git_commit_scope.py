"""Automatic memory commits must not consume the user's unrelated index."""
import shutil
import subprocess
from pathlib import Path
import sys

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.runtime.state import RuntimeStateStore


def git(root, *args):
    return subprocess.check_output(['git', *args], cwd=root, text=True).strip()


def initialize(root, nested='', *, initial_commit=True):
    git(root, 'init', '-q')
    git(root, 'config', 'user.name', 'Test')
    git(root, 'config', 'user.email', 'test@example.invalid')
    git(root, 'config', 'commit.gpgsign', 'false')
    memory = root / nested
    (memory / 'topics').mkdir(parents=True)
    (memory / 'topics/a.md').write_text('# Original\n')
    (root / 'README.md').write_text('baseline\n')
    if initial_commit:
        git(root, 'add', '.')
        git(root, 'commit', '-qm', 'baseline')
    return memory


@pytest.mark.parametrize('nested', ['', 'memory space'])
def test_memory_commit_keeps_unrelated_partial_staging_and_worktree(tmp_path, nested):
    memory = initialize(tmp_path, nested)
    readme = tmp_path / 'README.md'
    readme.write_text('staged content\n')
    git(tmp_path, 'add', 'README.md')
    staged = git(tmp_path, 'show', ':README.md')
    readme.write_text('staged content\nunstaged content\n')
    (memory / 'topics/a.md').write_text('# Updated\n')
    revision = RuntimeStateStore(memory).git_commit('memory only')
    assert revision == git(tmp_path, 'rev-parse', 'HEAD')
    expected = f'{nested}/topics/a.md' if nested else 'topics/a.md'
    assert git(tmp_path, 'diff-tree', '--no-commit-id', '--name-only', '-r', 'HEAD') == expected
    assert git(tmp_path, 'show', 'HEAD:README.md') == 'baseline'
    assert git(tmp_path, 'show', ':README.md') == staged
    assert readme.read_text() == 'staged content\nunstaged content\n'
    assert git(tmp_path, 'diff', '--cached', '--name-only') == 'README.md'


def test_unrelated_staging_alone_creates_no_memory_commit(tmp_path):
    memory = initialize(tmp_path)
    original = git(tmp_path, 'rev-parse', 'HEAD')
    (tmp_path / 'README.md').write_text('staged\n')
    git(tmp_path, 'add', 'README.md')
    assert RuntimeStateStore(memory).git_commit('must not commit') is None
    assert git(tmp_path, 'rev-parse', 'HEAD') == original
    assert git(tmp_path, 'show', ':README.md') == 'staged'


def test_memory_commit_in_unborn_repo_excludes_unrelated_staged_file(tmp_path):
    memory = initialize(tmp_path, initial_commit=False)
    git(tmp_path, 'add', 'README.md')
    assert RuntimeStateStore(memory).git_commit('first memory commit')
    assert git(tmp_path, 'ls-tree', '-r', '--name-only', 'HEAD') == 'topics/a.md'
    assert git(tmp_path, 'diff', '--cached', '--name-only') == 'README.md'


@pytest.mark.parametrize("already_staged", [False, True])
def test_tracked_whole_directory_deletion_is_committed(tmp_path, already_staged):
    memory = initialize(tmp_path, 'memory')
    shutil.rmtree(memory / 'topics')
    if already_staged:
        git(tmp_path, 'add', '-A', '--', 'memory/topics')
    assert RuntimeStateStore(memory).git_commit('remove old topics')
    assert git(tmp_path, 'diff-tree', '--no-commit-id', '--name-status', '-r', 'HEAD') == 'D\tmemory/topics/a.md'


def test_failed_commit_keeps_unrelated_staged_content(tmp_path):
    memory = initialize(tmp_path)
    (tmp_path / 'README.md').write_text('staged\n')
    git(tmp_path, 'add', 'README.md')
    original = git(tmp_path, 'rev-parse', 'HEAD')
    (memory / 'topics/a.md').write_text('# Updated\n')
    hook = tmp_path / '.git/hooks/pre-commit'
    hook.write_text('#!/bin/sh\nexit 1\n')
    hook.chmod(0o755)
    with pytest.raises(subprocess.CalledProcessError):
        RuntimeStateStore(memory).git_commit('rejected')
    assert git(tmp_path, 'rev-parse', 'HEAD') == original
    assert git(tmp_path, 'show', ':README.md') == 'staged'


def test_non_repository_returns_no_commit(tmp_path):
    assert RuntimeStateStore(tmp_path).git_commit('no repo') is None
