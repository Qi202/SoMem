# Getting started

## Install from source

Use Python 3.12 or newer. In the repository root:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
somem --help
```

Windows PowerShell uses `.venv\Scripts\Activate.ps1`. Local release validation
targets macOS/Python 3.12; Windows needs separate validation. Installation includes
the SDKs and PyTorch/sentence-transformers. Keep this environment separate from
existing experiments. A PyPI publication has not been established by this work;
use the source installation above.

For the locally tested macOS arm64 / Python 3.12 environment, an observed
[dependency constraints snapshot](../constraints/macos-arm64-python312.txt) is
provided. After activating the environment, use
`python -m pip install -c constraints/macos-arm64-python312.txt -e '.[dev]'`.
It includes development packages and is not a Linux/Windows lock or an archived
paper-experiment environment.

## Offline workspace example

```bash
python examples/offline_demo.py --workspace demo-memory
somem validate --workspace demo-memory
```

Expected: `topics/preferences.md` records Alice's jasmine-tea preference;
`sources/` contains the original input; printed JSON contains BM25 hits and a
resolved source citation; validation returns `"ok": true`.

The example uses real transactions and retrieval with a manually specified Topic
patch. It does not call a model or download embedding weights. First-time token
counting may download an encoding table; after caching that table the demo can
run offline. The demo refuses a nonempty output directory. Choose another
`--workspace` rather than overwriting personal or experimental memory.

## Model-driven example

The [synthetic conversation](../examples/conversation.json) and
[live script](../examples/live_demo.py) demonstrate construction followed by
answering from saved memory. Executing the live script makes paid provider calls.

Copy `.env.example` to `.env`, fill in your settings, and load it. SoMem does not
load `.env` automatically. Bash/Zsh:

```bash
cp .env.example .env
# Edit .env, then:
set -a
source .env
set +a
python examples/live_demo.py --dry-run
python examples/live_demo.py --workspace live-demo-memory
```

Dry-run checks required settings and the conversation without model calls or
output files. The real run writes Sources, Topics, derived views,
`.somem/demo-build.json` and `.somem/demo-answer.json`. The latter records the
answer, evidence, tool trace and Reader usage. Expected answer content is Alice's
jasmine-tea preference; wording varies by model.

The script uses public-core defaults for maintenance and verification, not a
paper benchmark configuration. It has no Judge stage. See
[configuration](configuration.md) and [support limits](agent-runtime-support.md).
First semantic search may download `sentence-transformers/all-MiniLM-L6-v2`;
cache it in advance when needed. Dry-run does not verify provider availability
or actual tool-calling behavior.

## Troubleshooting

| Symptom | Check |
|---|---|
| `somem` not found | Activate the installation environment; run `python -m pip show somem-framework`. |
| Missing credentials | Export the required variables from `.env.example`. |
| HTTP 401/403 | Check the key against the exact endpoint; redact keys before sharing logs. |
| Unsupported effort/tool parameter | Use the provider's documented settings; empty effort omits the parameter. |
| Embedding download fails | Check Hugging Face access/cache; the offline demo only needs BM25. |
| Existing directory rejected | Choose a new output directory. |
| Transaction rejected | Inspect `TransactionError.code`, patch context, citations and base revision. |
| MCP prints no prompt | It is a stdio protocol server; connect from an MCP client. |
| File reads hang in a synced checkout | Check cloud placeholder/cache hydration; validate in a local copy without bytecode/test caches. |

When opening an issue include the package/commit, Python/OS, adapter, actual error
and a redacted minimal example. Do not attach credentials or private memories.
