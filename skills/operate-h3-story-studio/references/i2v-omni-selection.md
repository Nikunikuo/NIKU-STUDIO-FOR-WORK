# i2v, Omni, and end-frame selection

Choose the generation mode from the continuity requirement and the actual visual content of the predecessor frame. Do not make `carryOverEndFrame=true` an unconditional i2v switch.

## Decision matrix

Use **i2v with a dedicated first-frame upload** when:

- the predecessor is terminal and its final decoded frame is available;
- the frame contains the subjects and location that must continue;
- faces, clothing, and spatial relationships are visible enough to act as an identity anchor;
- the next shot benefits more from motion continuity than from a large ordered reference set.

Use **Omni with ordered reference assets and no first/last-frame slots** when:

- multiple characters must retain their registered identities;
- the predecessor frame is a hand, box, torso, back view, or other weak identity anchor;
- a new composition needs character, environment, and prop references simultaneously;
- the next shot is intentionally independent of the predecessor's camera position.

Use **t2v** only when no file-backed reference or continuity frame is required.

## End-frame suitability gate

Before carrying a frame forward, inspect a thumbnail or sampled frame and record:

- visible characters and their approximate positions;
- visible wardrobe and key props;
- location/lighting continuity;
- occlusion, blur, crop, or close-up that could destroy identity;
- the predecessor take ID, frame asset ID, dimensions, and SHA-256.

If the frame is unsuitable, turn carry-over off or switch to Omni with the normal reference set. Never silently submit a blank first frame. In H3's pinned contract, a carry-over first frame requires `i2v` and an empty `reference_asset_ids` array; Omni must not receive that first-frame slot.

## Review-driven mode changes

A completed render can still be rejected for identity drift or location drift. If that happens, change the underlying cause:

- identity drift from a weak predecessor frame: switch i2v to Omni and restore character references;
- location drift from a loose Omni prompt: anchor the same location or switch to i2v from a strong predecessor frame;
- missing prop continuity: add the registered prop reference or use a stronger predecessor frame.

Do not retry the same request unchanged. Persist the chosen mode and the reason in the take review note.
