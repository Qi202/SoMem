# Audit of optional Writer routing and repair guidance

Audit date: 2026-10-30. Scope: the three existing Luna-named switches in the
public framework. No new benchmark or live model calls were run. Nine existing
focused offline tests passed against a source-matched isolated copy; these use
simulated model responses and do not demonstrate semantic quality or model
transfer. After reviewing this audit, the user explicitly selected all three options
as defaults for all four public benchmark recipes. The recipe presets now
enable them. The evidence and limitations below are unchanged.

| Option | Verified behavior | Generalization boundary | Initial audit recommendation |
|---|---|---|---|
| `luna_topic_routing_protocol_enabled` | Replaces the default Writer Core guidance, changes protocol hash, and prioritizes Core source validation before capacity validation | Narrows Core guidance compared with the ordinary active-work rule; asks concrete project updates and history to remain in Topics. It can change storage and retrieval outcomes. No matched cross-model quality experiment establishes benefit | Keep opt-in; preserve its inclusion in the separately agreed LoCoMo recipe, but do not automatically extend to other recipes |
| `luna_core_structural_repair_enabled` | Adds bounded targeted repair after a Core capacity-repair attempt fails with structural keywords | `_is_core_structural_error` matches keywords such as footnote, memory_id and dangling block link without proving the error is located in Core. The repair prompt assumes a post-compression Core error, so a mixed Topic/Core failure can receive mismatched scope guidance | Keep opt-in; do not activate across recipes on the current evidence |
| `luna_strict_validation_repair_enabled` | Substitutes local evidence-preserving guidance for recognized citation/Source failures within the existing general repair budget; validators are unchanged | Explicitly constrains the repair to local changes. Useful intent and mock branch tests do not establish that every provider can complete those repairs or that the constrained prompt improves real outcomes | Keep opt-in; no claimed all-model non-regression |

The options contain no model-name gate, so they can technically be enabled for
other adapters. That is not evidence of model-independent quality. Prompt
constraints may be desirable but are still behavioral changes. No candidate
currently meets both conditions of demonstrated quality benefit and demonstrated
absence of new generalization restrictions.

Relevant implementation: `code/src/management/api.py` (routing substitution),
`code/src/management/block_views.py` (validation order),
`code/src/management/agent.py` (repair selection and budgets),
`code/src/management/prompts.py` (repair scope), and
`code/tests/management/test_memory.py` (mocked branch tests).

To justify broader activation, compare matched frozen inputs with one switch
changed at a time. Inspect build completion, preserved facts and evidence,
retrieval/answer outcomes, repair attempts and token usage. Include Topic-side
citation failures after Core compression and at least one alternate Writer
model before asserting model transfer. Existing historical benchmark scores
with different memories and settings cannot isolate these switches' effects.

## Selected public recipe defaults

Following explicit user direction, all four public recipe presets now enable
the three audited switches. The initial recommendations above document the
evidence assessment, not the current preset state. No live experiment was run
as part of that configuration update.
