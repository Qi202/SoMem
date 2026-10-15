import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import ModuleType

import pytest

import src.retrieval.embedding as embedding
from src.retrieval.embedding import MemoryEmbeddingIndex
from src.management import MemoryWorkspace


def _write_events(memory_dir: Path, events: list[dict]) -> None:
    workspace = MemoryWorkspace(memory_dir)
    workspace.archive_sessions([{
        "observation_date": event["when"],
        "turns": [("user", event["summary"])],
        "refs": event["dia_ids"],
    } for event in events])
    workspace.save_memory([{
        "when": event["when"],
        "content": event["summary"],
        "refs": event["dia_ids"],
        "topic_path": f"{event['topic']}.md",
        "headings": event["topic"].split("/"),
    } for event in events])


class FakeEncoder:
    def encode(self, texts, **_kwargs):
        vectors = []
        for text in texts:
            if text == "visual art":
                vectors.append([1.0, 0.0])
            elif "sunrise" in text:
                vectors.append([1.0, 0.0])
            else:
                vectors.append([0.0, 1.0])
        return vectors


def test_embedding_search_ranks_topic_events_without_writing_files(tmp_path: Path):
    memory_dir = tmp_path / "memory"
    _write_events(memory_dir, [
        {
            "when": "2023-05-07",
            "summary": "Caroline visited an LGBTQ support group",
            "dia_ids": ["D1:3"],
            "topic": "Caroline/support",
        },
        {
            "when": "2023-05-08",
            "summary": "Melanie painted a sunrise",
            "dia_ids": ["D1:4"],
            "topic": "Melanie/painting",
        },
    ])
    before = {
        path.relative_to(memory_dir).as_posix(): path.read_bytes()
        for path in memory_dir.rglob("*")
        if path.is_file()
    }

    results = MemoryEmbeddingIndex(memory_dir, encoder=FakeEncoder()).search(
        "visual art"
    )

    assert results[0]["refs"] == ["D1:4"]
    assert results[0]["similarity"] == pytest.approx(1.0)
    assert set(results[0]) == {
        "event",
        "path",
        "line",
        "date",
        "content",
        "refs",
        "similarity",
    }
    after = {
        path.relative_to(memory_dir).as_posix(): path.read_bytes()
        for path in memory_dir.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_embedding_search_hard_caps_top_k_at_ten(tmp_path: Path):
    memory_dir = tmp_path / "memory"
    topic = memory_dir / "topics/events.md"
    topic.parent.mkdir(parents=True)
    topic.write_text(
        "# Events\n\n"
        + "\n".join(
            f"[2023-05-{day:02d}] Event {day} [D1:{day}]"
            for day in range(1, 13)
        )
        + "\n",
        encoding="utf-8",
    )

    results = MemoryEmbeddingIndex(memory_dir, encoder=FakeEncoder()).search(
        "events", top_k=999
    )

    assert len(results) == 10


def test_embedding_search_filters_by_partial_temporal_window(tmp_path: Path):
    memory_dir = tmp_path / "memory"
    _write_events(memory_dir, [
        {
            "when": "2023-05-08",
            "summary": "Melanie painted a sunrise in spring",
            "dia_ids": ["D1:4"],
            "topic": "Melanie/painting",
        },
        {
            "when": "2024-01-12",
            "summary": "Melanie painted another sunrise in winter",
            "dia_ids": ["D2:4"],
            "topic": "Melanie/painting",
        },
        {
            "when": "2023-09-20",
            "summary": "Melanie painted a sunrise in autumn",
            "dia_ids": ["D3:4"],
            "topic": "Melanie/painting",
        },
    ])

    results = MemoryEmbeddingIndex(memory_dir, encoder=FakeEncoder()).search(
        "visual art", date_from="2023", date_to="2023"
    )

    assert {tuple(row["refs"]) for row in results} == {("D1:4",), ("D3:4",)}


def test_embedding_index_reuses_document_vectors_for_query_rewrites(tmp_path: Path):
    memory_dir = tmp_path / "memory"
    topic = memory_dir / "topics/art.md"
    topic.parent.mkdir(parents=True)
    topic.write_text(
        "# Art\n\n[2023-05-08] Melanie painted a sunrise [D1:4]\n",
        encoding="utf-8",
    )

    class CountingEncoder(FakeEncoder):
        def __init__(self):
            self.batch_sizes = []

        def encode(self, texts, **kwargs):
            self.batch_sizes.append(len(texts))
            return super().encode(texts, **kwargs)

    encoder = CountingEncoder()
    index = MemoryEmbeddingIndex(memory_dir, encoder=encoder)

    index.search("visual art")
    index.search("painting")

    assert encoder.batch_sizes == [1, 1, 1]


def test_embedding_index_builds_document_vectors_once_across_threads(
    tmp_path: Path,
):
    memory_dir = tmp_path / "memory"
    topic = memory_dir / "topics/art.md"
    topic.parent.mkdir(parents=True)
    topic.write_text(
        "# Art\n\n[2023-05-08] Melanie painted a sunrise [D1:4]\n",
        encoding="utf-8",
    )

    class SlowCountingEncoder(FakeEncoder):
        def __init__(self):
            self.document_batches = 0

        def encode(self, texts, **kwargs):
            if any("Melanie painted a sunrise" in text for text in texts):
                self.document_batches += 1
                time.sleep(0.05)
            return super().encode(texts, **kwargs)

    encoder = SlowCountingEncoder()
    index = MemoryEmbeddingIndex(memory_dir, encoder=encoder)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(index.search, ["visual art", "painting"]))

    assert all(result for result in results)
    assert encoder.document_batches == 1


def test_shared_encoder_calls_are_serialized_across_indexes(tmp_path: Path):
    memory_dir = tmp_path / "memory"
    topic = memory_dir / "topics/art.md"
    topic.parent.mkdir(parents=True)
    topic.write_text(
        "# Art\n\n[2023-05-08] Melanie painted a sunrise [D1:4]\n",
        encoding="utf-8",
    )

    class ConcurrentCallDetector(FakeEncoder):
        def __init__(self):
            self.active = 0
            self.max_active = 0
            self.lock = threading.Lock()

        def encode(self, texts, **kwargs):
            with self.lock:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
            try:
                time.sleep(0.02)
                return super().encode(texts, **kwargs)
            finally:
                with self.lock:
                    self.active -= 1

    encoder = ConcurrentCallDetector()
    indexes = [
        MemoryEmbeddingIndex(memory_dir, encoder=encoder),
        MemoryEmbeddingIndex(memory_dir, encoder=encoder),
        MemoryEmbeddingIndex(memory_dir, encoder=encoder),
    ]

    with ThreadPoolExecutor(max_workers=3) as executor:
        results = list(executor.map(lambda index: index.search("visual art"), indexes))

    assert all(result for result in results)
    assert encoder.max_active == 1


def test_default_encoder_is_loaded_only_when_search_needs_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    memory_dir = tmp_path / "memory"
    topic = memory_dir / "topics/art.md"
    topic.parent.mkdir(parents=True)
    topic.write_text(
        "# Art\n\n[2023-05-08] Melanie painted a sunrise [D1:4]\n",
        encoding="utf-8",
    )
    loaded = []

    class StubSentenceTransformer(FakeEncoder):
        def __init__(self, model_name):
            loaded.append(model_name)

    module = ModuleType("sentence_transformers")
    module.SentenceTransformer = StubSentenceTransformer
    monkeypatch.setitem(sys.modules, "sentence_transformers", module)

    index = MemoryEmbeddingIndex(memory_dir)
    assert loaded == []

    index.search("visual art")

    assert loaded == ["sentence-transformers/all-MiniLM-L6-v2"]


def test_render_search_results_keeps_location_score_and_references():
    rendered = embedding.render_search_results([
        {
            "event": "event-1",
            "path": "topics/art.md",
            "line": 7,
            "date": "2023-05-08",
            "content": "Melanie painted a sunrise.",
            "refs": ["D1:4"],
            "similarity": 0.875,
        }
    ])

    assert "topics/art.md:7" in rendered
    assert "similarity=0.8750" in rendered
    assert "Melanie painted a sunrise." in rendered
    assert "refs: D1:4" in rendered


def test_embedding_searches_source_turns_missing_from_topics(tmp_path: Path):
    memory_dir = tmp_path / "memory"
    source = memory_dir / "sources/locomo/thread_1.md"
    source.parent.mkdir(parents=True)
    source.write_text(
        "# thread_1\n\n"
        '<a id="source-abc"></a>\n'
        "<!-- source-id:locomo/thread_1/msg_1 -->\n"
        "[2023-10-04] Calvin: I painted a sunrise.\n",
        encoding="utf-8",
    )

    results = MemoryEmbeddingIndex(
        memory_dir, encoder=FakeEncoder()
    ).search("visual art")

    assert results[0]["path"] == "sources/locomo/thread_1.md"
    assert results[0]["refs"] == ["locomo/thread_1/msg_1"]


@pytest.fixture
def scoped_index(tmp_path):
    from src.retrieval.bm25 import MemoryEvent
    index = MemoryEmbeddingIndex(tmp_path, encoder=FakeEncoder())
    index._events_cache = [
        MemoryEvent(str(i), path, 1, [], date, [date], content, [])
        for i, (path, date, content) in enumerate([
            ("sources/session.md", "2024-01-01", "sunrise source"),
            ("topics/art/old.md", "2023-01-01", "old art"),
            ("topics/art/new.md", "2024-01-01", "new art"),
            ("topics/travel.md", "2024-01-01", "sunrise travel"),
        ])
    ]
    return index


def test_path_filter_applies_before_top_k(scoped_index):
    assert scoped_index.search("visual art", top_k=1)[0]["path"].startswith("sources/")
    rows = scoped_index.search("visual art", path_prefix="topics/art/", top_k=1)
    assert len(rows) == 1
    assert rows[0]["path"].startswith("topics/art/")
    assert rows[0]["similarity"] == 0


def test_path_and_date_filters_intersect(scoped_index):
    rows = scoped_index.search("visual art", path_prefix="art", date_from="2024")
    assert [row["path"] for row in rows] == ["topics/art/new.md"]
    assert scoped_index.search("visual art", path_prefix="missing") == []
    assert scoped_index.search("visual art", path_prefix="sources/")[0]["path"] == "sources/session.md"
    assert scoped_index.search("visual art", path_prefix=None) == scoped_index.search("visual art")
    assert scoped_index.search_broad("visual art", path_prefix="art", date_from="2024") == rows


def test_embedding_tool_dispatches_path_filter(scoped_index, tmp_path):
    from src.retrieval.tools import execute_tool_call
    from src.retrieval.schemas import TOOL_DEFINITIONS
    schema = next(t["function"] for t in TOOL_DEFINITIONS if t["function"]["name"] == "embedding_search")
    assert "path_prefix" in schema["parameters"]["properties"]
    rendered, ok, _ = execute_tool_call(
        None, "embedding_search", {"query": "visual art", "path_prefix": "art", "date_from": "2024"},
        memory_dir=tmp_path, files=[], condition="topic_source", include_recent=False,
        indexes={"embedding": scoped_index},
    )
    assert ok and "topics/art/new.md" in rendered
    assert "sources/session.md" not in rendered
    assert "topics/art/old.md" not in rendered


def test_fusion_filters_embedding_before_top_k(scoped_index):
    from src.retrieval.fusion import fused_search
    class EmptyBM25:
        def search(self, *args, **kwargs):
            return []
    # More high-scoring sources than the embedding hard cap must not crowd out topics.
    from dataclasses import replace
    source = scoped_index._events_cache[0]
    scoped_index._events_cache.extend(replace(source, event_id=f"source-{i}", path=f"sources/{i}.md") for i in range(20))
    rows = fused_search(EmptyBM25(), scoped_index, "visual art", path_prefix="art", top_k=1)
    assert len(rows) == 1
    assert rows[0]["path"].startswith("topics/art/")
