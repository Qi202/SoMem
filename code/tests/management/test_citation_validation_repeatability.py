"""Missing citations must not become valid merely by repeating normalization."""
import shutil

import pytest

from src.management import MemoryConfig, MemoryWorkspace
from src.management.agent import _commit_turn, _committed_baseline
from src.management.tool_recovery import preflight_candidate
from src.management.transaction import workspace_revision


@pytest.mark.parametrize('labels', [('e1',), ('e2', 'e1')])
def test_repeated_and_fresh_checks_never_bind_missing_citations_to_nearby_definition(tmp_path, labels):
    ws = MemoryWorkspace(tmp_path / 'memory', config=MemoryConfig(
        validation_diagnostic_aggregation_enabled=True))
    ws.archive_sessions([{'observation_date': '2026-01-01',
        'turns': [('user', 'Old fact'), ('user', 'New supported fact')],
        'refs': ['D1:1', 'D1:2']}])
    topic = ws.stage_dir / 'topics/a.md'
    topic.write_text('Old fact.[^old] ^1234abcd\n\n'
                     '[^old]: Time: `2026-01-01`; Sources: D1:1\n')
    assert _commit_turn(ws, _committed_baseline(ws), []) is None
    baseline = _committed_baseline(ws)
    committed_revision = workspace_revision(ws.memory_dir)
    citations = ''.join(f'[^{label}]' for label in labels)
    original = topic.read_text()
    topic.write_text(original.replace('Old fact.[^old]', f'Old fact.[^old] New supported fact.{citations}'))
    errors = []
    for _ in range(3):
        failure = _commit_turn(ws, baseline, [])
        assert failure is not None and 'undefined footnote' in failure.message
        errors.append(failure.message)
        assert all(f'[^{label}]' in topic.read_text() for label in labels)
        before_preview = topic.read_bytes()
        preview = preflight_candidate(ws)
        assert not preview['valid'] and preview['error'] == failure.message
        assert topic.read_bytes() == before_preview
        assert workspace_revision(ws.memory_dir) == committed_revision
    assert len(set(errors)) == 1
    fresh = MemoryWorkspace(ws.memory_dir, config=ws.config)
    shutil.copy2(topic, fresh.stage_dir / 'topics/a.md')
    failure = _commit_turn(fresh, _committed_baseline(fresh), [])
    assert failure is not None and failure.message == errors[0]
    # Explicit, verified definitions (not nearby unrelated evidence) permit commit.
    topic.write_text(topic.read_text() + ''.join(
        f'\n[^{label}]: Time: `2026-01-01`; Sources: D1:2\n' for label in labels))
    assert preflight_candidate(ws)['valid']
    assert _commit_turn(ws, baseline, []) is None
    assert workspace_revision(ws.memory_dir) != committed_revision
