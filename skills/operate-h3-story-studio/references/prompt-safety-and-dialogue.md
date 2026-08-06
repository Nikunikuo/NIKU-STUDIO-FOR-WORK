# Prompt safety and dialogue

Use this checklist whenever a Japanese script contains dialogue or `@` asset mentions.

## Keep three layers separate

1. **Authoring text**: the human-readable Story Studio prompt with exact `@素材名` mentions.
2. **Project metadata**: speaker IDs, character names, timing, captions, and review notes.
3. **H3 wire/effective prompt**: the result after Story Studio and the existing H3 `community` planner compile the prompt.

Do not add a second translation or prompt-planner layer in Codex. Verify the compiled result instead.

## Speaker-name guard

When dialogue is authored inside the prompt:

- Keep the speaker name in metadata or a separate event field.
- Put only the literal spoken sentence inside the H3 dialogue quotation.
- Never send `ミオ「いいね！」`, `ミオ: いいね！`, or a multiline speaker-labelled block as one spoken literal.
- Keep `fields.dialogue` empty when the authored prompt already contains the dialogue events, unless the pinned H3 contract explicitly requires a standalone dialogue field.
- Keep BGM/music disabled when the project requests dialogue and SE only.

Before submission, inspect the planner output and fail the render if any quoted literal contains a known speaker label (`名前「`, `名前:`, or `名前：`). Also verify that the expected number of dialogue events survived compilation and that the effective prompt has the intended quote text verbatim.

## `@` reference guard

Treat `@素材名` as Story Studio authoring syntax. Before H3 submission:

- Every mention resolves to exactly one registered Project asset or metadata description.
- File-backed references appear in the effective prompt as ordered `<Picture N>`, `<Video N>`, or `<Audio N>` bindings.
- Unknown, ambiguous, prefix-only, or unresolved mentions stop the render before a revision is reserved.
- The reference order in the render request matches the order recorded in the job intent.

Record the authoring prompt, resolved prompt, effective prompt, dialogue count, and ordered bindings in the job/review report. A render is not accepted merely because the job reached `completed`.
