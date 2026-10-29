"""Source retrieval must retain multiline turns without merging adjacent turns."""

import json
from pathlib import Path

import pytest

from src.retrieval.bm25 import MemoryBM25Index, parse_source_file
from src.retrieval.embedding import MemoryEmbeddingIndex


SOURCE = """# Conversation
<!-- source-id:somem/thread/message1 -->
(2026-10-29) user: Travel plans:

- Friday: Hangzhou.
- Saturday: Ningbo.
## Additional detail
Return on Sunday.
<!-- source-id:somem/thread/message2 -->
(2026-10-30) user: Sunday changed to Suzhou.
"""


def source_file(root: Path, newline="\n") -> Path:
    path = root / "sources/chat.md"
    path.parent.mkdir(parents=True)
    path.write_bytes(SOURCE.replace("\n", newline).encode())
    return path


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_source_turn_keeps_paragraphs_lists_headings_and_boundaries(tmp_path, newline):
    path = source_file(tmp_path, newline)
    original = path.read_bytes()
    events = parse_source_file(path, tmp_path / "sources")
    assert len(events) == 2
    assert events[0].event_id == "somem/thread/message1"
    assert events[0].refs == ["somem/thread/message1"]
    assert events[0].line == 3 and events[0].date == "2026-10-29"
    for text in ["Travel plans", "Hangzhou", "Ningbo", "Additional detail", "Return on Sunday"]:
        assert text in events[0].content
    assert "Suzhou" not in events[0].content
    assert "Suzhou" in events[1].content and "Ningbo" not in events[1].content
    assert events[1].refs == ["somem/thread/message2"]
    assert path.read_bytes() == original


def test_bm25_discards_v4_cache_and_reindexes_later_lines(tmp_path):
    path = source_file(tmp_path)
    original = path.read_bytes()
    index = MemoryBM25Index(tmp_path)
    cached = json.loads(index.cache_path.read_text())
    cached["version"] = 4
    cached["files"]["sources/chat.md"]["events"][0]["content"] = "Travel plans:"
    index.cache_path.write_text(json.dumps(cached))
    reopened = MemoryBM25Index(tmp_path)
    hits = reopened.search("Ningbo", path_prefix="sources/")
    assert hits and "Ningbo" in hits[0]["content"]
    assert hits[0]["refs"] == ["somem/thread/message1"]
    assert json.loads(reopened.cache_path.read_text())["version"] == 5
    assert path.read_bytes() == original


def test_embedding_indexes_the_full_source_turn_without_model_download(tmp_path):
    path = source_file(tmp_path)
    original = path.read_bytes()

    class Encoder:
        def encode(self, texts, **kwargs):
            return [[1.0, 0.0] if "Ningbo" in text else [0.0, 1.0] for text in texts]

    hits = MemoryEmbeddingIndex(tmp_path, encoder=Encoder()).search(
        "Ningbo", path_prefix="sources/",
    )
    assert hits and "Ningbo" in hits[0]["content"]
    assert hits[0]["refs"] == ["somem/thread/message1"]
    assert path.read_bytes() == original


def test_legacy_unmarked_source_references_remain_searchable(tmp_path):
    path = tmp_path / "legacy.md"
    path.write_text("# Old archive\n[2026-10-29] Alice visited Ningbo [D1:1]\n")
    events = parse_source_file(path, tmp_path)
    assert len(events) == 1 and events[0].refs == ["D1:1"]
    assert "Ningbo" in events[0].content
