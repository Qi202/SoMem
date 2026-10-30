"""Versioned public benchmark recipes, without credentials or dataset access."""
from dataclasses import dataclass
from importlib.resources import files
import json
from typing import Any


@dataclass(frozen=True)
class BenchmarkProtocol:
    name: str
    version: str
    build_config: Any
    query_config: Any
    writer_options: dict[str, Any]
    reader_options: dict[str, Any]
    specification: dict[str, Any]


def load_benchmark_protocol(name: str) -> BenchmarkProtocol:
    """Load pinned settings as validated framework config objects; no API calls."""
    from src.build import BuildConfig
    from src.management.config import MemoryConfig
    from src.retrieval.config import QueryConfig

    document = json.loads(
        files('somem').joinpath('protocol_data/benchmarks-v1.json').read_text(encoding='utf-8')
    )
    if name not in document['benchmarks']:
        raise ValueError(f"unknown benchmark {name!r}; choose from "
                         + ', '.join(document['benchmarks']))
    spec = document['benchmarks'][name]
    build = dict(spec['build'])
    build['memory_config'] = MemoryConfig(**build['memory_config'])
    return BenchmarkProtocol(
        name=name, version=document['version'],
        build_config=BuildConfig(**build), query_config=QueryConfig(**spec['query']),
        writer_options=dict(spec['writer']), reader_options=dict(spec['reader']),
        specification=spec,
    )
