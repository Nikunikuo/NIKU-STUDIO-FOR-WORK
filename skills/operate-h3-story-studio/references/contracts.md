# H3 Story Studio mutation and render contracts

## Contents

1. Wire versus canonical keys
2. Runtime Project and asset creation
3. Patch envelope and operation forms
4. Canonical entity payloads
5. Take selection
6. H3 render request
7. Render lifecycle

## 1. Wire versus canonical keys

The Sidecar HTTP envelope and response use `camelCase`. Project Core stores entity and focus payload schemas with `snake_case` keys.

Do not paste a context response payload verbatim into `upsert_entity`. Rebuild the complete canonical payload using the templates below, preserving any additional fields after converting their schema keys deliberately.

Use camelCase for these HTTP/operation fields:

- `sessionId`, `expectedRevision`
- `entityType`, `entityId`
- approval fields `approvalId`, `targetType`, `targetId`
- render request top-level `shotId`

Use snake_case inside entity payloads, focus payloads, H3 `fields`, and H3 `uploads`.

## 2. Runtime Project and asset creation

Create only on explicit user request. The server owns `projectId`, `projectRoot`, Revision 0, digest, and initial focus.

```json
{
  "title": "物語タイトル",
  "code": "STORY-01",
  "subtitle": "短編",
  "episodeTitle": "第1話",
  "sceneTitle": "Scene 01",
  "shotTitle": "最初のShot",
  "logline": "一文の物語核",
  "summary": "概要"
}
```

```powershell
python scripts\storyctl.py create-project --spec-file <PROJECT_SPEC_JSON>
```

After creation, activate the returned `projectId`; do not fabricate a session. The scaffold contains one complete Episode→Scene→Shot→Prompt chain.

Asset spec:

```json
{
  "name": "凛",
  "category": "character",
  "role": "主人公 / 整備士",
  "caption": "黒いショートヘアの整備士。",
  "promptCaption": "short black hair, mechanic workwear",
  "locked": true,
  "source": "codex_gpt_image_2"
}
```

`category` is `character`, `environment`, `prop`, or `reference`. `source` is `human` or `codex_gpt_image_2`. Never claim the latter unless the uploaded bytes came from GPT-image-2. Omit `--file` for metadata-only assets; include it for a real image/video/audio upload. The response advances Revision and returns a server-generated `assetId`, byte size, MIME, SHA-256, and project-relative path. Re-fetch context before the next mutation.

```powershell
python scripts\storyctl.py add-asset `
  --project-id <PROJECT_ID> `
  --session-id <SESSION_ID> `
  --expected-revision <REVISION> `
  --spec-file <ASSET_SPEC_JSON> `
  --file <LOCAL_MEDIA_PATH>
```

## 3. Patch envelope and operation forms

`storyctl.py patch` accepts an operations JSON array. The CLI creates the outer envelope.

```json
[
  {
    "op": "upsert_entity",
    "entityType": "asset",
    "entityId": "ch_001",
    "payload": {}
  },
  {
    "op": "delete_entity",
    "entityType": "asset",
    "entityId": "unused_asset"
  },
  {
    "op": "set_focus",
    "payload": {
      "episode_id": "ep_001",
      "scene_id": "sc_003",
      "shot_id": "sh_012",
      "prompt_id": "prompt_012"
    }
  },
  {
    "op": "set_approval",
    "payload": {
      "approvalId": "apr_yoake-01_prompt_prompt_012",
      "targetType": "prompt",
      "targetId": "prompt_012",
      "status": "approved",
      "payload": {
        "review_note": "Reviewed against selected take"
      }
    }
  }
]
```

Send the smallest atomic set. Do not send a delete without explicit user intent. Valid approval states are defined by the current Project Core; preserve the existing approval ID and target tuple.

## 4. Canonical entity payloads

Asset caption or lock update replaces the full asset payload:

```json
{
  "kind": "character",
  "name": "凛",
  "role": "主人公 / 整備士",
  "caption": "黒いショートヘア。右頬の細い傷と、使い込まれた作業服。",
  "prompt_caption": "young Japanese mechanic, short black hair, thin scar on right cheek, worn charcoal workwear",
  "locked": true,
  "tone": "ember",
  "revision_hint": 8
}
```

Prompt update replaces the full prompt payload:

```json
{
  "code": "PROMPT-SH012",
  "prompt": "Persisted H3 prompt text",
  "mode": "t2v",
  "episode_id": "ep_001",
  "scene_id": "sc_003",
  "shot_id": "sh_012",
  "reference_asset_ids": ["ch_001", "ch_002", "env_004", "prop_006"],
  "revision_hint": 8
}
```

Preserve `revision_hint` as provenance; concurrency is enforced by outer `expectedRevision`, not this hint.

Web UI Codex inbox request:

```json
{
  "request": "User's project-bound request",
  "status": "pending",
  "requested_by": "web_ui",
  "project_id": "prj_01K1ZJ7M5X9JY4Q6C8P0V3TN2A",
  "episode_id": "ep_001",
  "scene_id": "sc_003",
  "shot_id": "sh_012",
  "prompt_id": "prompt_012",
  "created_at": "2026-08-05T07:00:00.000Z"
}
```

Preserve every field when changing `status` to `completed` or `failed`. Add `completed_at` and a concise `result_summary` only after the corresponding work has reached that terminal state.

## 5. Take selection

Select one registered take and demote any other selected take in the same shot in one atomic patch. Preserve every artifact field exactly.

```json
[
  {
    "op": "upsert_entity",
    "entityType": "take",
    "entityId": "take_430aac73e28d",
    "payload": {
      "shot_id": "sh_012",
      "job_id": "job_430aac73e28d",
      "h3_job_id": "85280b103019",
      "status": "ready",
      "selection": "selected",
      "artifact_id": "asset_take_430aac73e28d",
      "artifact_relative_path": "outputs/sh_012/take_430aac73e28d.mp4",
      "artifact_sha256": "2a278b4122815ff19141915f81f1613ca108b21a015129ac9b118d73f9db9417"
    }
  }
]
```

Never select a UI fixture take that has no matching Project Core `take` entity.

## 6. H3 render request

Pass only this inner object to `storyctl.py render --request-file`:

```json
{
  "shotId": "sh_012",
  "fields": {
    "mode": "t2v",
    "style": "natural",
    "prompt": "The persisted prompt for the focused shot",
    "width": 864,
    "height": 480,
    "num_frames": 124,
    "steps": 2,
    "seed": 133625,
    "acceleration": "off",
    "ref_image_size": "match",
    "prompt_processing_mode": "community",
    "standalone_audio_policy": "full_content",
    "audio_preset": "auto",
    "dialogue": "",
    "soundscape": "",
    "music_policy": "auto",
    "audio_gain_db": 0
  },
  "uploads": {
    "first_image_asset_id": null,
    "last_image_asset_id": null,
    "reference_asset_ids": []
  }
}
```

Constraints:

- `mode`: `t2v`, `i2v`, `first_last`, or `omni`
- `style`: `natural`, `cinematic`, `photoreal`, `anime`, `illustration`, `product`, or `moody`
- width/height: multiples of 32 from 192 through 1344
- `num_frames`: 124, 243, or 345
- steps: 2 through 40
- prompt: 1 through 4000 characters
- reference asset IDs: at most 12 unique registered assets
- `raw_en` requires natural style and empty/auto auxiliary audio fields

Story Studio authoring uses exact `@素材名` mentions. Before H3 submission, the Sidecar deterministically binds uploaded references to per-media formal tags in request order: `<Picture N>`, `<Video N>`, and `<Audio N>`. Metadata-only mentions expand to their stored descriptions. Unknown, ambiguous, prefix-only, or post-expansion unresolved mentions fail before Project Revision reservation. Story Studio does not translate Japanese or duplicate prompt planning; the effective H3 request remains `prompt_processing_mode: community` and the existing H3 Studio flow owns that transformation.

Use 864×480, 124 frames, and 2 steps only as a cheap vertical-slice test. Choose intentional production settings for a final render.

## 7. Render lifecycle

Render submission advances the project revision when it reserves the job/take and may advance it again when H3 accepts the job. Use the exact `revision` returned by submission for later sync.

```powershell
python scripts\storyctl.py job --job-id <H3_JOB_ID>

python scripts\storyctl.py sync `
  --project-id <PROJECT_ID> `
  --local-job-id <LOCAL_JOB_ID> `
  --session-id <SESSION_ID> `
  --expected-revision <SUBMISSION_REVISION>
```

Successful sync registers both a `video` asset and a `take`, writes under `outputs/<shot>/`, hashes the media, and advances the revision. A terminal failed/cancelled/interrupted job must be synced for reconciliation but must not be reported as usable media.
