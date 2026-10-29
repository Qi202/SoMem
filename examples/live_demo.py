"""Build memory and answer a question using OpenAI-compatible tool-call models."""

import argparse
import json
import os
from pathlib import Path

from somem import (
    BuildConfig, MemoryConfig, OpenAIChatAgent, OpenAIChatConfig,
    QueryConfig, Runtime, build_memory, collect_answer,
)


def settings():
    def required(name):
        value = os.environ.get(name, "").strip()
        if not value:
            raise ValueError(f"set {name}; see .env.example")
        return value

    writer = {
        "base_url": required("SOMEM_BASE_URL"),
        "api_key": required("SOMEM_API_KEY"),
        "model": required("SOMEM_WRITER_MODEL"),
        "reasoning_effort": os.environ.get("SOMEM_WRITER_EFFORT", "").strip() or None,
    }
    reader = {
        key: os.environ.get(f"SOMEM_READER_{suffix}", "").strip() or writer[key]
        for key, suffix in [("base_url", "BASE_URL"), ("api_key", "API_KEY"), ("model", "MODEL")]
    }
    reader["reasoning_effort"] = (
        os.environ.get("SOMEM_READER_EFFORT", "").strip() or None
    )
    return writer, reader


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--conversation", type=Path, default=Path(__file__).with_name("conversation.json"))
    parser.add_argument("--workspace", type=Path, default=Path("live-demo-memory"))
    parser.add_argument("--question", default="What drink does Alice prefer?")
    parser.add_argument("--dry-run", action="store_true", help="check inputs without model calls or output files")
    args = parser.parse_args()
    try:
        writer, reader = settings()
        conversation = json.loads(args.conversation.read_text(encoding="utf-8"))
        if not isinstance(conversation, dict) or not conversation.get("session_1"):
            raise ValueError("conversation needs a nonempty session_1; see the supplied JSON")
        root = args.workspace.expanduser().resolve()
        if root.exists() and any(root.iterdir()):
            raise ValueError("use a new or empty workspace; existing memory is never overwritten")
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    if args.dry_run:
        print(json.dumps({"mode": "dry-run", "writer_model": writer["model"],
                          "reader_model": reader["model"], "workspace": str(root),
                          "network_calls": 0, "output_created": False}, indent=2))
        return
    agents = [OpenAIChatAgent(OpenAIChatConfig(
        **values, max_output_tokens=8192, force_final_at_max_turns=True,
        preserve_reasoning_content=True, record_response_diagnostics=True,
    )) for values in (writer, reader)]
    build_config = BuildConfig(memory_config=MemoryConfig(max_turns=20))
    query_config = QueryConfig(max_turns=20, search_tools="split")
    build_memory(conversation, root, agent=agents[0], model=writer["model"],
                 config=build_config, checkpoint_path=root / ".somem/demo-build.json")
    runtime = Runtime(agents[0], writer["model"], query_agent=agents[1],
                      query_model=reader["model"], query_config=query_config)
    evidence, steps, answer, trace = collect_answer(
        runtime, {"question": args.question}, root, {}, config=query_config,
    )
    result = {"question": args.question, "answer": answer, "retrieval_steps": steps,
              "evidence": evidence, "tool_trace": trace, "reader_calls": runtime.call_log}
    (root / ".somem/demo-answer.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print(answer)


if __name__ == "__main__":
    main()
