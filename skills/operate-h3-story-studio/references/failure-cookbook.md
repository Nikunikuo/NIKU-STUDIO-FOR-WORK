# H3 feedback-loop failure cookbook

Classify the failure before retrying. Preserve the failed job and report; do not overwrite it with a blind retry.

| Signal | Likely cause | Corrective action | Acceptance check |
|---|---|---|---|
| `WARDROBE_OVERRIDE_MISSING` | Japanese wardrobe description was interpreted as a wardrobe command | Add an explicit preserve-outfit/no-override constraint and remove ambiguous change verbs | Effective planner output reports no authored wardrobe override |
| `INVENTED_SPEECH_CONTROL` | Speech labels, multiline dialogue, or over-specified audio controls were invented by the planner | Simplify the Audio block, keep quote-only dialogue, keep `fields.dialogue` empty, and preserve the exact literal lines | Planner report has the expected dialogue count and no speaker label in a quote |
| `DURATION_MISMATCH` | The planner split the request into hidden shots or missed the requested frame count | State both exact seconds and frame count, and require one continuous shot | Numeric facts and rendered frame count agree |
| Completed job but wrong people | i2v predecessor frame was a weak identity anchor | Switch to Omni with ordered character references, or choose a stronger predecessor frame | Character identity and wardrobe pass visual QA |
| Completed job but wrong location | Omni prompt allowed a scene/location drift | Add a same-location anchor or switch to i2v from the preceding scene | Location and lighting pass visual QA |
| Job appears slow | Normal local H3 generation was mistaken for a stuck job | Poll the exact job, inspect `h3-state`, GPU/log liveness, and only retry after a terminal or diagnosed failure | No duplicate active jobs; final state is reconciled in Project Core |
| Unknown/ambiguous `@` mention | Authoring reference cannot be bound deterministically | Stop before render; resolve or register the asset | Effective bindings contain no unresolved mention |

For each retry record: error code, root cause, changed fields, expected effect, new job ID, and review result. A successful H3 job is only a candidate until structural, visual, audio, and continuity gates pass.
