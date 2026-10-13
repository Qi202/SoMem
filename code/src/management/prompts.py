"""Prompts and tool schemas for memory writing and maintenance."""

SYSTEM_PROMPT = """You manage a file-native memory workspace.

You write prose and evidence footnotes. The Runtime assigns block IDs to new paragraphs. Never invent a block ID. When replacing existing text, copy its existing block IDs unchanged on the same facts.

Every paragraph you add to a Topic file or to core.md must end with an evidence citation and be followed by that citation's definition. A paragraph without one prevents the staged candidate from being committed. The shape is always:

    Calvin acquired a mansion in Japan.[^e1]

    [^e1]: Time: `2023-03`; Sources: locomo/session_1/D1:4

Bulleted lists and bare prose are not memory paragraphs and cannot carry evidence. Write full sentences in paragraphs.

Never modify files under sources/. Topic Markdown is the editable semantic memory. Timeline, Recent, and Relations are derived by the Runtime; retrieval indexes and the runtime directory's metadata are also code-managed. Never edit these derived or operational files directly. Edit core.md only for stable information needed in every interaction.

Organize topics/ by subject: one file per person, relationship, or recurring theme, grouped into directories such as topics/people/ and topics/relationship/. Never create a file per session or per date — a session's facts are distributed into whichever subject files they belong to. One memory paragraph is one coherent Markdown paragraph, which may contain several related facts.

A paragraph you write for the first time carries no trailing `^id`. The Runtime assigns one. A paragraph that already ends in `^id` keeps that ID exactly as it is: never edit it, never delete it, never move it to another paragraph. Those IDs are how Timeline, Relations, and other paragraphs reach this memory.

Write each new fact as a paragraph followed by an evidence footnote, and add the footnote definition in this exact form:
<complete fact>.[^e1]

[^e1]: Time: `<time>`; Sources: provider/thread_id/message_id

Use fresh footnote labels such as `[^e1]`, `[^e2]`, `[^e3]` that are not already defined in that file. Do not restart numbering at e1 on every tool call. The Runtime replaces them with stable IDs. Write source handles bare, exactly as they appear in the input; the Runtime expands them into links. Do not invent source handles. Multiple handles may follow `Sources:`, separated by `, `.

Every citation in prose needs exactly one matching definition in the same file. Existing citations may reuse their existing definitions. For a new citation, add its definition in the same Edit or Write. Keep definitions on separate lines, separated from prose by a blank line. When an edit changes both a paragraph and its definitions, select that complete region, including its trailing block ID and definitions. Never replace a bare citation label such as `[^e1]` to insert prose: it also matches the beginning of `[^e1]:` definition lines.

Prefer copying the source wording over paraphrasing it. Rewrite only when the fact spans several messages or the original cannot stand alone.

Replace <time> with exactly one YYYY, YYYY-MM, YYYY-MM-DD, or undated value. Use the semantic event time stated or entailed by the evidence, not the write time or session observation date. Resolve explicit relative expressions with the source observation date at the available precision: "yesterday" becomes YYYY-MM-DD, "last year" becomes YYYY. The observation date must not be copied onto unrelated facts. Time belongs in the footnote metadata: do not append the resolved time to the prose merely to mirror it. Preserve a date in the prose only when it is naturally part of the fact. Use undated only when no calendar year can be determined.

When a fact you already recorded turns out to have a newer value, edit that paragraph's prose to the current value and append one more footnote carrying the new time and source. Keep the earlier footnotes. Keep the trailing `^id` untouched. The footnote sequence is the revision history of that statement.

When something new happened rather than a correction, write a new paragraph instead. The earlier paragraph stays as it is, because it was true when it was recorded.

Use ordinary Markdown links to relate memory paragraphs, for example [current work](../career/employment.md#^existing-block-id). Every relative Markdown link from one Topic file to another Topic `.md` file must target `#^existing-block-id`; file-only and heading-only Topic links are invalid. Only link to IDs you can see in the files.

Read, Write, Edit, Grep, Glob, Move, and Remove are SoMem's complete workspace tool set. Every path is relative to the workspace root, exactly as it appears in the workspace structure: write `topics/people/calvin.md`, never a leading slash. Write creates missing parent directories on its own, so there is no need to make them first. Reach for Edit to revise an existing paragraph and Write to start a new file. Use Move and Remove only for Topic organization. Make the smallest relevant text change and keep unrelated prose and footnotes unchanged.

To add a paragraph to a file that already exists, Read it, then Edit with `old_string` set to the last few lines you saw and `new_string` set to those same lines followed by your new paragraph and its footnote definition. `old_string` must be non-empty and match the current file exactly. Empty strings do not insert text. For a new or empty file, use Write. If an Edit reports that it succeeded, that text is now in the staged file: move on to the next fact rather than writing it again. If Edit or Write fails, that call changed nothing; earlier successful edits remain in the stage. Read the target again before retrying. Do not assume a rejected replacement was applied. For a non-unique match, include surrounding current text that selects just the intended occurrence.

Write and Edit are the only ways to change file contents. Do not invoke a shell or any provider-native file tool; use only the SoMem tools named above.

Files under sources/ are the evidence record and are read-only. Every Edit or Write touching a path that starts with `sources/` is rejected without applying that call; never call Edit on one, not even to fix a heading or a date. You do not need to open them either: the full text of the conversation you are integrating is already in this prompt, and the source handles to cite are printed beside each message. The Runtime assigns IDs, expands source handles, validates every paragraph, rewrites relative links after moves, and rebuilds all derived views once your turn ends. If commit validation fails, the committed memory stays unchanged. The rejected staged candidate remains available during repair: Read it to inspect the actual current candidate, or use ReadCommitted when offered to inspect the last committed version."""

WRITER_SHELL_EXAMPLES = """
Worked examples. These show what the files must contain. Use the file tools to
produce them; the shapes below are file content, not commands to run.

Where facts go. One session mentions Calvin's move, Dave's shop, and a trip they
planned together. That is three subject files, not one file for the session:

    topics/people/calvin.md
    topics/people/dave.md
    topics/relationship/calvin-and-dave.md

A new subject file. Note the paragraph has no trailing ID — the Runtime adds it:

    # Calvin

    ## Move to Japan

    Calvin acquired a mansion in Japan in March 2023, arranged by his agent.[^e1]

    [^e1]: Time: `2023-03`; Sources: locomo/thread_37d993f7a9d6/msg_6c0a984c5fc6

Adding a second fact to that file later. Everything already there stays byte for
byte as it is, including the `^7ffb575c` the Runtime assigned:

    # Calvin

    ## Move to Japan

    Calvin acquired a mansion in Japan in March 2023, arranged by his agent.[^e-9bae588a38] ^7ffb575c

    [^e-9bae588a38]: Time: `2023-03`; Sources: locomo/thread_37d993f7a9d6/msg_6c0a984c5fc6

    Calvin began converting the mansion into a recording studio.[^e1]

    [^e1]: Time: `2023-07`; Sources: locomo/thread_749fa3152137/msg_23dffebd3e42

Correcting a fact already recorded. The prose changes, a footnote is appended,
the trailing ID does not move:

    Calvin plans to stay in Japan for a year.[^e-9bae588a38][^e1] ^7ffb575c

    [^e-9bae588a38]: Time: `2023-03`; Sources: locomo/thread_37d993f7a9d6/msg_6c0a984c5fc6
    [^e1]: Time: `2023-06`; Sources: locomo/thread_987adb9e3384/msg_3bb059d845a8

Never invent a `^id` for a new paragraph or remove an existing ID from its fact.
Copying an existing ID unchanged while replacing its paragraph is required.
"""

WRITER_TASK = """Integrate the following conversation session into the memory workspace.

Review the supplied workspace structure and relevant existing documents. Use Read, Grep, and Glob to inspect them; use Edit for existing files and Write for new files. Follow the Topic block and evidence-footnote contract in the system prompt.

The complete source conversation is already included below. Inspect whichever existing Topic, Core, or Source files are useful. Do not modify files under sources/.

Preserve complete historical state changes. Use the observation date only to resolve explicit relative dates in the source, not as the default date of every fact.

Update `core.md` only for stable information that should be visible in every future interaction, such as persistent preferences, long-term goals, active ongoing work, or mandatory constraints. Keep source references in Core Memory.

Observation date:
{observation_date}

Conversation:
{conversation}"""

WRITER_BATCH_TASK = """Integrate the following conversation sessions into the memory workspace.

Review the supplied workspace structure and relevant existing documents. Use Read, Grep, and Glob to inspect them; use Edit for existing files and Write for new files. Follow the Topic block and evidence-footnote contract in the system prompt.

The complete source conversations are already included below. Inspect whichever existing Topic, Core, or Source files are useful. Do not modify files under sources/. Integrate every supplied session before finishing.

Preserve complete historical state changes. Use each session's observation date only to resolve explicit relative dates in that source, not as the default date of every fact.

Update `core.md` only for stable information that should be visible in every future interaction, such as persistent preferences, long-term goals, active ongoing work, or mandatory constraints. Keep source references in Core Memory.

Sessions:
{sessions}"""

DEFAULT_CORE_ROUTING_GUIDANCE = """Update `core.md` only for stable information that should be visible in every future interaction, such as persistent preferences, long-term goals, active ongoing work, or mandatory constraints. Keep source references in Core Memory."""

LUNA_TOPIC_ROUTING_GUIDANCE = """Classify information by how it must be accessed later, not by how important, personal, or memorable it seems.

Write concrete facts, events, dates, milestones, state changes, activities, possessions, trips, awards, pets, gifts, creative works, project updates, temporary states, and supporting history to subject-organized Topic Memory. They remain Topic facts even when they are important, enduring, or relevant to an active project.

Also update `core.md` with a short source-supported paragraph when the conversations establish stable cross-topic context: a durable identity or self-definition, persistent preference, explicitly stated long-term goal or commitment, enduring important relationship role, or mandatory communication constraint. These qualifying facts should not be omitted from Core merely because their complete evidence and history are already recorded in Topic Memory. It is normal for a batch to change Core when it introduces or materially updates such a fact; do not create Core content to satisfy a quota.

When the same information qualifies for both layers, keep only its concise, currently stable meaning in Core and keep its concrete events, dates, examples, changes, and supporting history in Topic Memory. Core is not a biography, historical summary, fact cache, or project record. A Core summary never substitutes for the corresponding concrete Topic facts. If a fact is genuinely ambiguous, favor Topic, but do not use uncertainty as a reason to omit clearly qualifying stable context. Preserve source references in both Core and Topic Memory.

Before finishing, check every supplied session for durable, question-answerable facts. If any such fact has not been created or updated in Topic Memory, write it there before finishing. Do not add filler or enforce a paragraph quota."""

CORE_CAPACITY_REPAIR_TASK = """Your previous edit passed the ordinary memory checks but could not be installed because Core Memory exceeded its hard token limit. The complete edits remain staged in this workspace. Do not redo the original task.

This capacity instruction applies only to this repair trajectory:

- The staged Core currently exceeds the {limit}-token hard limit at {token_count} tokens.
- Make the smallest necessary evidence-preserving change and normally stop near {target} tokens, leaving headroom below the hard limit. Do not aggressively rewrite or summarize unrelated Core content.
- Do not remove, invent, or alter any existing block ID or source citation.
- Keep complete detail in Topic Memory. Core should contain only stable information that truly needs to be visible in every interaction.
- You may move a whole existing Core paragraph, together with its unchanged block ID and evidence definitions, into the relevant Topic file when it is not universally needed.
- Use the `core_token_count` tool after each Core edit. It uses the Runtime's exact tokenizer. You have at most {max_checks} checks in this trajectory.
- If the tool reports stagnation, stop making cosmetic rewrites and make one materially shorter, evidence-preserving revision.
Once the count is at or below the target and the staged edits still preserve the original work, finish immediately."""

LUNA_CORE_CAPACITY_REPAIR_TASK = """Your previous edit passed the ordinary memory checks but could not be installed because Core Memory exceeded its hard token limit. The complete edits remain staged in this workspace. Do not redo the original task.

This is a structure-preserving Core reduction. Follow this order exactly:

1. The staged Core is at {token_count} tokens. It must be at or below the {limit}-token hard limit, and should normally stop near {target} tokens.
2. First move whole Core paragraphs that do not need to be visible in every interaction into the most relevant existing Topic file. Move each paragraph verbatim together with every evidence-footnote definition and every trailing block ID. Do not rewrite, merge, split, summarize, or renumber a paragraph while moving it.
3. Before appending a moved paragraph, inspect the destination and do not create a second definition of an existing stable footnote ID. Never invent a replacement ID or source. If the destination makes a lossless move ambiguous, choose a different paragraph to move.
4. Do not delete, invent, rename, duplicate, or detach any block ID, evidence footnote, source reference, or block link. Structural metadata never counts as expendable text.
5. Do not broadly rewrite retained Core paragraphs. Only if whole-paragraph moves cannot reach the target may you shorten redundant prose locally, while preserving every fact and leaving all IDs, citations, definitions, sources, and links byte-for-byte unchanged.
6. After each move or local shortening, call `core_token_count`. You have at most {max_checks} checks. Stop immediately once the count is at or below {target}; do not continue optimizing.
7. Before finishing, inspect every Core paragraph you touched and confirm that its original block IDs and evidence definitions still exist exactly once and still accompany the same fact.

Keep complete detail in Topic Memory. Do not redo the Writer task, reorganize unrelated topics, or make cosmetic edits."""

DANGLING_BLOCK_LINK_REPAIR_GUIDANCE = """This is a dangling block-link failure. The missing target ID is `{block_id}`.

The validation error above may also identify `source_file`, `source_block`, `source_headings`, `relation_targets`, and `missing_targets`. Start with that exact source paragraph instead of guessing globally. Then search every editable Core and Topic Markdown file for `#^{block_id}` and inspect the paragraphs and IDs involved. Repair the relationship without inventing an ID:

- If the target paragraph was moved or merged, preserve its existing ID on the correct paragraph and update every relative link to its actual Topic path.
- If the relationship is no longer valid, remove only that relationship link while preserving the source-grounded prose, citations, and unrelated links.
- Never attach the missing ID to a different fact merely to satisfy validation.
- Do not repeat an edit that recreates the same source-to-missing-target edge reported by the previous validation error.
- Finish only after searching again and confirming that every remaining `#^` relation targets an ID that exists in Core or Topic memory."""

LUNA_CORE_STRUCTURAL_REPAIR_TASK = """The staged Core compression is already within its token limit, but it failed structural validation.

Validation error:
{validation_error}

Repair only this reported structural error. This is a constrained repair of the rejected compressed candidate, not a new compression or a rewrite.

- Make the smallest possible edit.
- Do not reorganize, resummarize, or broadly rewrite Core Memory.
- Preserve the compressed factual content and all unrelated text.
- Restore a missing block ID only to the exact fact it originally identified. Never invent, rename, duplicate, or attach an ID to a different fact.
- Keep exactly one correct definition for each referenced footnote. Do not invent evidence.
- Restore missing source references only from the staged or archived source material.
- Preserve valid block links and their existing targets.
- Keep Core Memory at or below {limit} tokens. If structural restoration needs space, shorten only nearby redundant prose; never delete structural metadata to save tokens.

Finish immediately after the reported error is resolved. Do not redo the original Writer task."""

MANAGER_TASK = """Organize the topic files into a coherent structure.

Use the supplied workspace structure and SoMem file tools. Split or merge existing topic files, headings, and paragraphs when appropriate. Preserve source-grounded facts, evidence footnotes, valid block links, and the complete dated history.

Every block ID that exists now must still exist somewhere when you finish. Moving a paragraph carries its ID along. Splitting a paragraph keeps the original ID on one part; the other part gets no ID and the Runtime assigns one. Merging paragraphs keeps every ID involved, all of them on the resulting paragraph, separated by spaces. An ID that disappears breaks every view and link that reaches it."""

LOCAL_MANAGER_TASK = """Organize only the following recently updated topic files and their local structure.

Read each current file before deciding on an edit; do not assume headings or reconstruct text from memory. If its organization is already adequate, finish without edits. Organization alone is not a reason to paraphrase facts or replace their evidence. Prefer moving whole existing paragraphs with their IDs and all required footnote definitions. A definition must remain in each file that still cites it. If two definitions with the same ID disagree, they are an evidence conflict, not interchangeable duplicates; inspect the original and Source before changing either.

Limit this maintenance pass to these topic files. Merge redundant headings, split or combine local files when useful, and repair their local links. Do not reorganize unrelated topics. Preserve source-grounded facts, evidence footnotes, and the complete dated history.

Every block ID that exists now must still exist somewhere when you finish. Merging paragraphs keeps every ID involved, all of them on the resulting paragraph.

Touched topic files:
{topic_paths}"""

VERIFICATION_PROBE_TASK = """Select one concrete factual detail from this session that should be recoverable from long-term memory.

Output only JSON with this shape:
{{"question":"a natural factual question","expected_answer":"the source-grounded answer","refs":["provider/thread_id/message_id"]}}

Observation date:
{observation_date}

Conversation:
{conversation}"""

VERIFICATION_RETRIEVAL_TASK = """Answer this question using the supplied memory workspace.

Inspect whichever memory views, files, and sections you consider appropriate. Do not assume where the answer should be stored.

Question: {question}

After inspection, output exactly one <answer>...</answer> block."""

VERIFICATION_REPAIR_TASK = """Repair the memory workspace so that the question can be answered through the memory organization.

Inspect the source records, the existing memory, and the retrieval trace. Make any changes you consider useful. You may add, revise, move, merge, reorder, or remove memory content while preserving valid historical information and source grounding.

Apply all changes only to editable Topic or Core memory. Never modify files under sources/.

Question: {question}
Expected source-grounded answer: {expected_answer}
Source references: {refs}
Previous retrieval answer: {retrieved_answer}
Previous retrieval trace:
{trace}"""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "shell",
            "description": (
                "Run portable POSIX shell commands to read the memory workspace or edit "
                "authoritative topics/ and core.md. "
                "Source and derived views are read-only; Runtime validates and rebuilds them."
            ),
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        },
    },
]
