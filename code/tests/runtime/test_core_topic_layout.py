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


@pytest.mark.parametrize('legacy', [False, True])
@pytest.mark.parametrize('operation', ['remove', 'move'])
def test_core_file_cannot_be_removed_or_moved(tmp_path, legacy, operation):
    if legacy:
        (tmp_path / 'core.md').write_text('')
    workspace = MemoryWorkspace(tmp_path)
    relative = core_file(tmp_path).relative_to(tmp_path).as_posix()
    backend = WorkspaceToolBackend(workspace.stage_dir)
    with pytest.raises(WorkspaceToolError, match='Core file must remain'):
        if operation == 'remove':
            backend.remove(relative)
        else:
            backend.move(relative, 'topics/old-core.md')
    assert (workspace.stage_dir / relative).is_file()


@pytest.mark.parametrize('legacy', [False, True])
@pytest.mark.parametrize('relocate', [False, True])
def test_commit_rejects_core_deletion_even_when_bypassing_tools(tmp_path, legacy, relocate):
    from src.management.transaction import committed_baseline, install_state
    if legacy:
        (tmp_path / 'core.md').write_text('')
    workspace = MemoryWorkspace(tmp_path)
    baseline = committed_baseline(workspace)
    relative = core_file(tmp_path).relative_to(tmp_path)
    (workspace.stage_dir / relative).unlink()
    if relocate:
        other = workspace.stage_dir / ('topics/core.md' if legacy else 'core.md')
        other.parent.mkdir(parents=True, exist_ok=True)
        other.write_text('')
    with pytest.raises(ValueError, match='Core file must remain'):
        install_state(workspace, *baseline)
    assert (tmp_path / relative).is_file()


@pytest.mark.parametrize('empty', ['', '# Core\n'])
def test_core_facts_can_move_to_topic_then_core_can_be_empty(tmp_path, empty):
    from src.management.transaction import committed_baseline, install_state
    test_core_citations_commit_with_source_and_survive_reload(tmp_path)
    workspace = MemoryWorkspace(tmp_path)
    baseline = committed_baseline(workspace)
    backend = WorkspaceToolBackend(workspace.stage_dir)
    original = (workspace.stage_dir / 'topics/core.md').read_text()
    # Clearing without preserving the existing fact is rejected immediately.
    with pytest.raises(WorkspaceToolError, match='remove existing memory block ID'):
        backend.write('topics/core.md', empty)
    backend.read('topics/core.md')
    backend.write('topics/preferences.md', original.replace('# Core', '# Preferences', 1))
    backend.write('topics/core.md', empty)
    install_state(workspace, *baseline)
    assert (tmp_path / 'topics/core.md').is_file()
    assert not (tmp_path / 'topics/core.md').read_text().replace('# Core', '').strip()
    topic = (tmp_path / 'topics/preferences.md').read_text()
    assert 'prefers tea' in topic and '../sources/' in topic
    resumed = MemoryWorkspace(tmp_path)
    assert not (resumed.stage_dir / 'topics/core.md').read_text().replace('# Core', '').strip()
    assert 'prefers tea' in (resumed.stage_dir / 'topics/preferences.md').read_text()


def test_commit_rejects_lost_core_information_even_if_file_remains(tmp_path):
    from src.management.transaction import committed_baseline, install_state
    test_core_citations_commit_with_source_and_survive_reload(tmp_path)
    workspace = MemoryWorkspace(tmp_path)
    baseline = committed_baseline(workspace)
    (workspace.stage_dir / 'topics/core.md').write_text('')
    with pytest.raises(ValueError, match='block ID must not be removed'):
        install_state(workspace, *baseline)
    assert 'prefers tea' in (tmp_path / 'topics/core.md').read_text()
