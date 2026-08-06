# Candidate QA, selected timeline, and final assembly

## Candidate acceptance gates

Inspect every completed candidate before adoption.

### Structural

- artifact exists under the focused Project's `outputs/<shot>/` directory;
- registered byte size and SHA-256 match the local file;
- video codec, dimensions, frame count, and duration match the render request;
- the expected audio track exists and has the intended sample rate/channels;
- the job is terminal and synced; no duplicate pending job remains.

### Visual

Sample the first, middle, and last frames (or inspect the full clip) for character count, identity, wardrobe, location, key props, motion continuity, and obvious face/hand/object breakage. Reject a completed candidate if it drifts in identity or location even when the file is technically valid.

### Audio

Check that the media is decodable, the track is not silent or clipped unexpectedly, BGM follows the project policy, and dialogue is not replaced by speaker labels or planner prose. Keep the effective prompt report with the take review.

## Candidate rail and selected timeline UI contract

The video production screen should expose the same Project Core state in two places:

- **Right candidate rail**: one card per generated take, showing a real first-frame thumbnail when an artifact exists, take ID/state, and a one-click download action. Rendering/failed candidates must remain visibly distinct from ready candidates.
- **Bottom selected timeline**: one card per shot, showing the currently selected take's thumbnail and a clear `採用` state. Clicking the card focuses that shot; the download action downloads the selected artifact for that shot.
- A missing artifact may use a neutral placeholder, but it must never be presented as generated media.
- Download buttons must use the registered Project asset URL and a deterministic filename; the browser should save the media through the normal download mechanism rather than exposing a temporary or unrelated path.

The rail and timeline are views of the same take IDs, not separate fixture collections. Adoption updates Project Core first, then refreshes both views from the new revision.

## Final assembly

Before concatenating:

1. Assert one selected ready take per shot and sort by persisted shot order.
2. Re-run structural checks for every selected artifact.
3. Concatenate with the local ffmpeg runtime into a derived output under the same Project's `outputs/` directory.
4. Probe/hash the final output and report nominal versus container-measured duration.
5. Keep the selected takes and their end-frame assets as the canonical Project Core entities. Do not mislabel a derived concat as a human/reference asset when the current asset contract has no derived-video category.

The bundled `scripts/audit_selected_takes.py` performs the selected-take audit and can optionally assemble a concat. It accepts a `storyctl.py context` JSON response on disk or stdin so it cannot accidentally combine takes from another Project.
