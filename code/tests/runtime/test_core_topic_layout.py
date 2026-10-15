from pathlib import Path
import sys
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.workspace_layout import core_file
from src.management import MemoryWorkspace
from src.management.workspace_tool_backend import WorkspaceToolBackend, WorkspaceToolError
from src.markdown import parse_topic_tree
from src.retrieval.views import memory_files
from somem.cli import ensure_workspace


def test_new_writer_commits_core_inside_topics(tmp_path):
    workspace = MemoryWorkspace(tmp_path)
    assert core_file(tmp_path) == tmp_path / 'topics/core.md'
    assert not (tmp_path / 'core.md').exists()
    backend = WorkspaceToolBackend(workspace.stage_dir)
    backend.write('topics/core.md', '# Core\n')
    workspace.shell('true')
    workspace._synchronize()
    assert (tmp_path / 'topics/core.md').read_text() == '# Core\n'
    assert not (tmp_path / 'core.md').exists()
    resumed = MemoryWorkspace(tmp_path)
    assert (resumed.stage_dir / 'topics/core.md').read_text() == '# Core\n'
    with pytest.raises(WorkspaceToolError):
        WorkspaceToolBackend(resumed.stage_dir).write('core.md', 'wrong location')


def test_nested_core_keeps_its_own_parser_and_component(tmp_path):
    topics = tmp_path / 'topics'
    topics.mkdir()
    (topics / 'core.md').write_text('# Core\n\nStable context without Topic block IDs.\n')
    assert parse_topic_tree(topics) == []
    assert memory_files(tmp_path, components=('core',)) == [topics / 'core.md']
    assert memory_files(tmp_path, components=('topics',)) == []


def test_legacy_memory_is_kept_in_place(tmp_path):
    (tmp_path / 'core.md').write_text('# Historical Core\n')
    workspace = MemoryWorkspace(tmp_path)
    assert core_file(tmp_path) == tmp_path / 'core.md'
    assert (workspace.stage_dir / 'core.md').read_text() == '# Historical Core\n'
    assert not (tmp_path / 'topics/core.md').exists()
    assert memory_files(tmp_path, components=('core',)) == [tmp_path / 'core.md']


def test_cli_initializes_same_nested_layout(tmp_path):
    root = tmp_path / 'memory'
    assert ensure_workspace(root)
    assert (root / 'topics/core.md').is_file()
    assert not (root / 'core.md').exists()
    assert not ensure_workspace(root)


def test_core_citations_commit_with_source_and_survive_reload(tmp_path):
    workspace = MemoryWorkspace(tmp_path)
    patch = """--- a/topics/core.md
+++ b/topics/core.md
@@ -1 +1,5 @@
 # Core
+
+The user prefers tea.[^new-evidence-tea]
+
+[^new-evidence-tea]: Time: `2026-10-14`; Sources: new-source-tea
"""
    workspace.update(base_revision=workspace.revision(), patch=patch, sources=[{
        'label': 'new-source-tea', 'role': 'user', 'content': 'I prefer tea.',
        'observed_at': '2026-10-14T10:00:00+08:00',
    }], git_commit='off')
    core = (tmp_path / 'topics/core.md').read_text()
    assert 'prefers tea' in core and '../sources/' in core
    assert 'new-source-tea' not in core
    assert parse_topic_tree(tmp_path / 'topics') == []
    resumed = MemoryWorkspace(tmp_path)
    resumed._synchronize()
    assert (tmp_path / 'topics/core.md').read_text() == core
    assert not (tmp_path / 'core.md').exists()
