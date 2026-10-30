# LoCoMo reproduction protocol

This document records the agreed Topic-first LoCoMo configuration. The framework
APIs for building memory and answering questions are available; the [public batch entry](README.md#batch-commands) provides dataset preparation,
construction, answering and hash-locked scoring. The settings
below describe this benchmark recipe, not the defaults of the live demo.

## Dataset and scope

Use the complete LoCoMo ten-conversation dataset. Build one independent memory
workspace per conversation and evaluate categories 1–4 only:

| Category | Questions |
|---|---:|
| Single-hop (4) | 841 |
| Multi-hop (1) | 282 |
| Open-domain (3) | 96 |
| Temporal (2) | 321 |
| Total | 1,540 |

Category 5 is outside this protocol. Retain stable conversation and question IDs.
Keep original session order and timestamps when preparing construction input.
Only conversation messages go to the Writer. Only the question goes to the
Reader; reference answers, categories and annotated evidence are evaluation
metadata, not Reader inputs.

## Writer

Writer and Reader models are independently configurable. The agreed default is
GPT-6 Luna with effort `none` for both roles; use the exact model identifier
supported by the configured provider. Endpoints and credentials are supplied by
the user, rather than embedded in dataset scripts.

| Setting | Value |
|---|---|
| Batching | Complete-message token packing; session count does not control batches |
| Writer input token cap | 15,000 |
| Writer trajectory limit | 120 turns |
| Local organization interval | Every 2 completed sessions |
| Write verification | Enabled, every completed session |
| Final management | Enabled |
| Core capacity | 4,000 tokens |
| Core capacity repair target | 3,600 tokens |
| Recent view limit | 50 |
| Core capacity repair | Enabled |
| Luna Topic-routing protocol | Enabled |
| Luna Core structural and strict validation repairs | Enabled |

Record these benchmark overrides explicitly rather than inheriting live-demo
settings. An enabled management or repair setting does not mean that its
trigger fired in every build.

New workspaces use `topics/core.md`. Core holds concise, stable context suitable
for preloading. Facts may move from Core into ordinary Topics when they no longer
need to be preloaded: preserve their block IDs and evidence at the destination
before clearing them from Core. Core may be empty, but the file must remain in
place. Existing block IDs must survive in Core or Topics. These structural checks
do not independently prove semantic completeness of a rewritten paragraph.

Legacy root-level `core.md` remains readable. Historical frozen memory should
keep its original layout and provenance; moving Core under Topics may also
change which documents Topic-scoped search can retrieve.

## Reader

See the [shared Reader settings](reader-settings.md) and machine-readable presets.

| Setting | Value |
|---|---|
| Retrieval guidance | Current Topic-first prompt; a preference, not a forced tool sequence |
| Search tools | Separate BM25 and embedding search (`split`) |
| Embedding model | `all-MiniLM-L6-v2` |
| Source verification | Enabled |
| Reader trajectory limit | 20 turns |
| Visible components | Sources, Topics, Core, Timeline, Recent and Relations |
| Preloaded memory | Core and Recent |
| Source parsing | Complete messages, including multiline paragraphs and lists |
| Additional ledger/pipeline/reflective/adaptive/claim-evidence modes | Disabled |
| `force_final_at_max_turns` | `False` by default |
| Saved response | One complete `answer` field |

Do not use dual answer fields, answer compression or a shortened scoring answer.
When forced final answering is enabled explicitly, the last allowed turn stops
retrieval and requests an answer from the evidence already collected; the model
may state that evidence is insufficient. When disabled, reaching the turn limit
without an answer leaves the item incomplete. Do not convert it into a normal
answer or an incorrect Judge verdict. Record the configured switch and whether
forced answering actually occurred.

## Evaluation

Use the unchanged LightMem `strictJudge` implementation,
`experiments/locomo/llm_judge.py`, calling `evaluate_llm_judge` with its original
`ACCURACY_PROMPT`. The pinned evaluator file SHA-256 is:

```
753889df01f381980b1d96a4aab480b5ff8b0919e0e0ac7092bbf828bdfe1dc6
```

The local experiment tree stores this file under
`code/third_party/lightmem/experiments/locomo/llm_judge.py`; it has not yet been
packaged in this public repository. Do not substitute another evaluator.

Use GPT-4o-mini (`openai/gpt-4o-mini` on OpenRouter), temperature 0, and the
original JSON `CORRECT`/`WRONG` verdict protocol. Score the full saved answer.
Report LJ accuracy for each category and micro-averaged overall accuracy
(correct answers divided by evaluated questions). Full coverage requires all
1,540 unique eligible questions. Failed requests and invalid verdicts remain
pending; report incomplete coverage explicitly. BLEU-1 and token F1 are omitted.

## Reproduction workflow

1. **Prepare data.** Load the original dataset, preserve session metadata, and
   enumerate the category 1–4 questions with stable IDs.
2. **Build or resume memory.** Use `build_memory` with the Writer profile above,
   one workspace and checkpoint per conversation. Retain checkpoints on failure.
3. **Answer from saved memory.** Use `Runtime` and `collect_answer` with the
   Reader profile above. Reuse existing memory without invoking the Writer when
   running Reader-only experiments. Save each complete answer before judging.
4. **Judge and summarize.** Call the pinned evaluator on saved answers. Resume
   missing verdicts without regenerating answers or repeating completed scoring.
   Aggregate category and overall results with explicit coverage.

The available API call shapes are documented in the [Python API](../python-api.md).
The [live demo](../../examples/live_demo.py) illustrates adapter wiring, but its
budgets and forced-answer setting differ from this benchmark recipe. See the [batch commands](README.md#batch-commands) for the executable entry.

Each run should record the dataset identity, code revision, effective settings,
Writer/Reader/Judge identities, memory origin, answer files and completion state.
This recipe runs the current agreed protocol. Historical best results may use
other models and previously built memory; the default configuration is not a
claim to reproduce the README's best observed score.

## Status

- Protocol choices: confirmed.
- Protocol documentation: organized.
- Public dataset runner and pinned Judge adapter: implemented; LightMem source is supplied externally.
- New experiments or end-to-end reruns for this documentation update: not run.
