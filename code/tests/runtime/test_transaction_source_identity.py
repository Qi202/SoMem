"""Manual imports use a neutral namespace without invalidating old references."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from somem import MemoryWorkspace, inspect
from src.management import transaction
from src.management.transaction import SourceInput, source_records


def sources(drink):
    return [{'label': f'new-source-{drink}', 'role': 'user',
             'content': f'I prefer {drink}.', 'observed_at': '2026-10-31T10:00:00+08:00'}]


def patch(drink):
    return f'''--- /dev/null
+++ b/topics/{drink}.md
@@ -0,0 +1,5 @@
+# {drink.title()}
+
+The user prefers {drink}.[^new-evidence-{drink}] ^new-block-{drink}
+
+[^new-evidence-{drink}]: Time: `2026-10-31`; Sources: new-source-{drink}
'''


def test_source_identity_is_neutral_and_deterministic():
    first = source_records([SourceInput('new-source-one', 'user', 'text')])[0]
    same = source_records([SourceInput('new-source-two', 'user', 'text')])[0]
    assert first.provider == 'transaction'
    assert first.source_id == same.source_id
    assert first.source_id.startswith('transaction/')


def test_manual_update_archives_neutral_source_and_resolves_citation(tmp_path):
    workspace = MemoryWorkspace(tmp_path)
    result = workspace.update(base_revision=workspace.revision(), patch=patch('tea'),
                              sources=sources('tea'), git_commit='off')
    source_id = result.source_ids['new-source-tea']
    assert source_id.startswith('transaction/')
    archives = list((tmp_path / 'sources/transaction').glob('*.md'))
    assert len(archives) == 1
    assert f'<!-- source-id:{source_id} -->' in archives[0].read_text()
    topic = (tmp_path / 'topics/tea.md').read_text()
    assert '../sources/transaction/' in topic
    assert 'new-source-tea' not in topic
    assert inspect.search(tmp_path, 'tea', method='bm25', top_k=3)
    resumed = MemoryWorkspace(tmp_path)
    resumed._synchronize()
    assert (tmp_path / 'topics/tea.md').read_text() == topic


def test_reimport_does_not_duplicate_archived_source(tmp_path):
    workspace = MemoryWorkspace(tmp_path)
    first = workspace.update(base_revision=workspace.revision(), patch=patch('tea'),
                             sources=sources('tea'), git_commit='off')
    archive = next((tmp_path / 'sources/transaction').glob('*.md'))
    before = archive.read_bytes()
    second = workspace.update(base_revision=workspace.revision(), patch='''--- a/topics/tea.md
+++ b/topics/tea.md
@@ -1 +1 @@
-# Tea
+# Drinks
''', sources=sources('tea'), git_commit='off')
    assert first.source_ids == second.source_ids
    assert archive.read_bytes() == before


def test_existing_legacy_sources_and_links_survive_new_import(tmp_path, monkeypatch):
    workspace = MemoryWorkspace(tmp_path)
    with monkeypatch.context() as legacy:
        legacy.setattr(transaction, 'SOURCE_PROVIDER', 'claude-code')
        first = workspace.update(base_revision=workspace.revision(), patch=patch('tea'),
                                 sources=sources('tea'), git_commit='off')
    assert first.source_ids['new-source-tea'].startswith('claude-code/')
    old_archive = next((tmp_path / 'sources/claude-code').glob('*.md'))
    old_bytes = old_archive.read_bytes()
    old_topic = (tmp_path / 'topics/tea.md').read_bytes()
    second = workspace.update(base_revision=workspace.revision(), patch=patch('coffee'),
                              sources=sources('coffee'), git_commit='off')
    assert second.source_ids['new-source-coffee'].startswith('transaction/')
    assert old_archive.read_bytes() == old_bytes
    assert (tmp_path / 'topics/tea.md').read_bytes() == old_topic
    MemoryWorkspace(tmp_path)._synchronize()
