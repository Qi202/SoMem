from dataclasses import asdict
import json
from pathlib import Path
import sys

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from somem import load_benchmark_protocol
from somem import OpenAIChatConfig, Runtime


@pytest.mark.parametrize('name,cap,turns', [
    ('locomo', 15000, 20), ('beam-100k', 32768, 120),
    ('beam-1m', 32768, 120), ('longmemeval-s', None, 120),
])
def test_recipe_wires_validated_configs_into_runtime(name, cap, turns):
    protocol = load_benchmark_protocol(name)
    build = protocol.build_config
    assert build.writer_input_token_cap == cap
    assert build.memory_config.core_max_tokens == 4000
    assert build.memory_config.core_repair_target_tokens == 3600
    assert build.memory_config.max_turns == 120
    assert build.local_reorg_every_sessions == 2
    assert build.verify_writes and build.verify_every_sessions == 1
    assert build.memory_config.luna_topic_routing_protocol_enabled
    assert build.memory_config.luna_core_structural_repair_enabled
    assert build.memory_config.luna_strict_validation_repair_enabled
    reader = OpenAIChatConfig(base_url='https://example.test/v1', api_key='test',
                              **protocol.reader_options)
    assert reader.reasoning_effort == 'none'
    assert not reader.force_final_at_max_turns
    runtime = Runtime(object(), protocol.writer_options['model'],
                      build_config=build, query_config=protocol.query_config)
    assert runtime.query_config.max_turns == turns
    assert runtime.query_config.search_tools == 'split'
    assert set(runtime.query_config.memory_components) == {
        'sources', 'topics', 'core', 'timeline', 'recent', 'relations'}
    json.dumps(asdict(build))


def test_protocol_loads_are_independent_and_unknown_names_fail():
    first = load_benchmark_protocol('locomo')
    first.reader_options['model'] = 'alternate'
    assert load_benchmark_protocol('locomo').reader_options['model'] == 'gpt-6-luna'
    with pytest.raises(ValueError, match='unknown benchmark'):
        load_benchmark_protocol('unrecognized')
