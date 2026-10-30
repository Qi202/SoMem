"""Universal-newline comparisons must not weaken Source content integrity."""
from pathlib import Path
import sys

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.management import MemoryWorkspace
from src.management.source_archive import assert_session_sources, SourceIntegrityError


def session(ref, content):
    return [{'observation_date': '2026-10-31',
             'turns': [('user', content)], 'refs': [ref]}]


@pytest.mark.parametrize('ref', ['D1:1', 'test/thread/message'])
@pytest.mark.parametrize('newline', ['\n', '\r\n', '\r'])
def test_archive_and_recheck_accept_equivalent_newlines_without_rewriting(tmp_path, ref, newline):
    workspace = MemoryWorkspace(tmp_path)
    data = session(ref, f'First paragraph.{newline}{newline}Second paragraph.{newline}- item')
    workspace.archive_sessions(data)
    paths = list((tmp_path / 'sources').rglob('*.md'))
    assert paths
    before = {p: p.read_bytes() for p in paths}
    assert_session_sources(tmp_path, data)
    # A caller with LF-decoded input can recheck the same stored record.
    assert_session_sources(tmp_path, session(ref, data[0]['turns'][0][1].replace(
        '\r\n', '\n').replace('\r', '\n')))
    assert {p: p.read_bytes() for p in paths} == before


@pytest.mark.parametrize('ref', ['D1:1', 'test/thread/message'])
@pytest.mark.parametrize('modified', [
    'Changed paragraph.\r\n\r\nSecond paragraph.\r\n- item',
    'First paragraph.\r\n- item',
    'First paragraph.\r\n\r\nSecond paragraph.\r\n- item\r\nExtra fact.',
    'First  paragraph.\r\n\r\nSecond paragraph.\r\n- item',
])
def test_newline_compatibility_still_rejects_content_loss_changes_and_additions(tmp_path, ref, modified):
    workspace = MemoryWorkspace(tmp_path)
    original = session(ref, 'First paragraph.\r\n\r\nSecond paragraph.\r\n- item')
    workspace.archive_sessions(original)
    with pytest.raises(SourceIntegrityError, match='missing or changed source records'):
        assert_session_sources(tmp_path, session(ref, modified))
