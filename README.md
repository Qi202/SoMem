<div align="center">

# SoMem
### Can LLM Agents Organize Their Own Memory?

<p>
Zichuan Fu<sup>1*</sup>, Junjia Qi<sup>1*</sup>, Wenlin Zhang<sup>1</sup>, Yimin Deng<sup>1</sup>,<br>
Shirong Wang<sup>1</sup>, Jinyang Li<sup>1</sup>, Xian Wu<sup>2</sup>, Xiangyu Zhao<sup>1†</sup>
</p>

<p>
<sup>1</sup> City University of Hong Kong &nbsp; <sup>2</sup> Tencent Jarvis Lab<br>
<sup>*</sup> Equal contribution &nbsp; <sup>†</sup> Corresponding author
</p>

<p>
<a href="https://github.com/fzkuji2026/2027-arXiv-Zichuan-Fu-Agent-Memory">Paper source</a> &nbsp; · &nbsp;
<a href="https://github.com/Qi202/SoMem">Code</a> &nbsp; · &nbsp;
<a href="#quick-start">Quick start</a> &nbsp; · &nbsp;
<a href="#evaluation">Evaluation</a> &nbsp; · &nbsp;
<a href="#citation">Citation</a>
</p>

<p><b>Persistent memory through linked, editable documents.</b></p>

</div>

SoMem lets an LLM agent decide what to retain, how to group related information,
and when to revise its memory. Original conversations remain available as
Source evidence. At query time, the agent chooses what to search and read,
following links and checking original records when needed.

[![Overview of SoMem: writing memory, persistent documents, and evidence-based reading](docs/assets/somem-overview.png)](docs/assets/somem-overview.pdf)

*The Writer revises Topic documents, including a bounded Core in `topics/core.md`.
The runtime validates edits and derives Graph, Timeline, and Recent views.
The Reader starts with Core, Recent, and a file inventory, then retrieves further
evidence through search and document access.*

## Highlights

- **The agent organizes its own memory.** It creates and revises Markdown
  documents, groups related information, and changes that organization as new
  interactions arrive.
- **Organized memory stays linked to original evidence.** Topic statements cite
  immutable Source records, allowing the Reader to inspect details that summaries
  may omit.
- **Access views follow accepted edits.** The runtime validates document
  structure and references before rebuilding relational, temporal, and recent
  views from the accepted content.
- **Memory can be reused across models.** Writer and Reader models can be
  configured independently and share the same saved workspace.

## Main results

The manuscript's primary comparison uses **GPT-6 Luna for both Writer and
Reader**, with GPT-4o-mini as judge. LoCoMo and LongMemEval-S report overall
LLM-judge accuracy; BEAM-100K reports mean nugget score. All values are percentages;
the metrics are evaluated separately and are not averaged across benchmarks.

| Method | LoCoMo | BEAM-100K | LongMemEval-S |
|---|---:|---:|---:|
| Full Context | 84.22 | 50.92 | 65.80 |
| A-Mem | 60.06 | 50.51 | 62.80 |
| Mem0 | 80.32 | 49.00 | 72.80 |
| MemoryOS | 56.23 | 47.96 | 53.00 |
| Nemori | 72.53 | 47.96 | 66.40 |
| LightMem | 77.99 | 43.54 | 74.50 |
| E-mem | 86.30 | 43.36 | 65.20 |
| **SoMem** | **88.96** | **54.02** | **76.50** |
| **Gain over the strongest comparator (pp)** | **+2.66** | **+3.10** | **+2.00** |

The evaluations cover 1,540 LoCoMo category 1–4 questions, 400 BEAM-100K questions,
and 500 LongMemEval-S questions. See [result sources and additional configurations](docs/results.md)
for the manuscript tables and the separately reported best observed scores.
The public benchmark recipes are documented below; they have not been used to
rerun and verify these historical results.

## Quick start

### Install

Use **Python 3.12+** and an isolated environment:

```bash
git clone https://github.com/Qi202/SoMem.git
cd SoMem
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

On Windows PowerShell, activate with `.venv\Scripts\Activate.ps1`.
Installation includes the model SDKs and embedding stack. See
[installation and troubleshooting](docs/getting-started.md) for environment details.

### Conversation → memory → answer

The [live example](examples/live_demo.py) builds memory from the supplied
[conversation](examples/conversation.json) and answers a question from the saved
workspace. Configure an OpenAI-compatible model endpoint in `.env.example`:

```bash
cp .env.example .env
# Fill in SOMEM_BASE_URL, SOMEM_API_KEY, and SOMEM_WRITER_MODEL in .env.
# Optional SOMEM_READER_* settings select a separate Reader.
set -a
source .env
set +a

python examples/live_demo.py --dry-run
python examples/live_demo.py --workspace live-demo-memory
```

The sample asks **“What drink does Alice prefer?”** The expected answer identifies
**jasmine tea**; wording depends on the model. The run saves original Sources,
organized Topics, derived views, and the answer with its tool trace. The real run
makes provider calls; `--dry-run` checks configuration without calling a model.
Use a new workspace for each run. See the [complete walkthrough](docs/getting-started.md#model-driven-example)
for output locations and environment setup.

<details>
<summary><b>Try document storage and retrieval without a model API key</b></summary>

```bash
python examples/offline_demo.py --workspace demo-memory
somem validate --workspace demo-memory
```

This example commits a manually supplied, cited preference and retrieves it with
BM25. It exercises storage, validation, and retrieval; it does not use an LLM
Writer or Reader. First-time tokenizer initialization may download its encoding
table. See [expected output](docs/getting-started.md#offline-workspace-example).

</details>

## Evaluation

The public runner supports **data preparation → memory construction → question
answering → judging** for LoCoMo, BEAM-100K, BEAM-1M, and LongMemEval-S.
Start with the [benchmark guide](docs/benchmarks/README.md) for dataset input
formats, model settings, and the fixed scoring protocols.

For example, after configuring `SOMEM_BASE_URL` and `SOMEM_API_KEY`:

```bash
python -m pip install -e '.[benchmarks]'
mkdir -p work

somem-benchmark prepare --benchmark locomo --input /path/to/locomo10.json --output work/locomo.json
somem-benchmark build --input work/locomo.json --output work/locomo-memory --workers 1
somem-benchmark qa --input work/locomo.json --memory-root work/locomo-memory --output work/locomo-qa --workers 1

# Judging uses GPT-4o-mini; configure OPENROUTER_API_KEY separately.
somem-benchmark judge --benchmark locomo --answers work/locomo-qa --output work/locomo-judged
```

Download the benchmark data separately and supply its local path. The runner
saves per-question answers and scores; it does not aggregate final tables or
automatically resume runs. Use new output directories. Public recipe settings
and historical manuscript configurations are distinguished in
[result provenance](docs/results.md#public-recipes-and-historical-results).

## Using SoMem in an agent

Use the [Python API](docs/python-api.md) to build memory, query a saved workspace,
or commit validated Topic edits. The [MCP integration](docs/mcp.md) exposes six
tools for an existing agent: `memory_status`, `memory_list`, `memory_read`,
`memory_grep`, `memory_search`, and `memory_update`.

```text
memory/
  sources/             original conversation records
  topics/              editable semantic documents
    core.md            bounded, preloaded context
  recent_events.jsonl  derived recent-memory view
  timeline/            derived temporal views
  relations.json       derived links and backlinks
  .somem/              runtime state
```

See the [documentation index](docs/README.md) for configuration, integrations,
benchmark protocols, and development guides.

## Acknowledgements

We thank the benchmark authors whose work supports the evaluation of SoMem.
Third-party evaluation code retains its attribution and license notices in
[the scoring-source documentation](code/somem/evaluation_sources/NOTICE.md).

## Citation

The manuscript source is available [here](https://github.com/fzkuji2026/2027-arXiv-Zichuan-Fu-Agent-Memory).
Until an archival identifier is available, it can be cited as a manuscript:

```bibtex
@unpublished{fu_somem,
  title  = {Can LLM Agents Organize Their Own Memory?},
  author = {Fu, Zichuan and Qi, Junjia and Zhang, Wenlin and Deng, Yimin and
            Wang, Shirong and Li, Jinyang and Wu, Xian and Zhao, Xiangyu},
  note   = {Manuscript},
  url    = {https://github.com/fzkuji2026/2027-arXiv-Zichuan-Fu-Agent-Memory}
}
```

For software experiments, also record the SoMem code revision and effective
model and benchmark settings. SoMem is released under the [MIT License](LICENSE).
