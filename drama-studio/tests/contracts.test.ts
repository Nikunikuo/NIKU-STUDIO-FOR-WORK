import assert from "node:assert/strict";
import test from "node:test";

import {
  H3_FRAME_COUNTS,
  h3DurationSeconds,
  isH3FrameCount,
  isValidH3Resolution,
  normalizeShotRenderSpec,
  selectH3FrameCount,
  snapH3Resolution,
  toH3JobRequest,
  validateH3Resolution,
} from "../lib/h3/index";
import type {
  H3ContractIssue,
  H3ContractResult,
  H3FrameSelectionPolicy,
} from "../lib/h3/index";
import {
  contentHash,
  createHandoffCapsule,
  demoProjectContext,
  demoStoryIds,
  entityRefKey,
  isoDateTime,
  propagateStaleState,
  revisionNumber,
  summarizeProjectContext,
  validateRevisionConflicts,
} from "../lib/story/index";
import type {
  EntityRef,
  RevisionValidationInput,
  StalePropagationInput,
  StaleReason,
} from "../lib/story/index";

function expectSuccess<T>(result: H3ContractResult<T>): T {
  if (!result.ok) {
    assert.fail(
      `Expected contract success, received: ${result.errors
        .map((error) => `${error.code}@${error.path}`)
        .join(", ")}`,
    );
  }

  return result.value;
}

function expectFailure<T>(result: H3ContractResult<T>): readonly H3ContractIssue[] {
  if (result.ok) {
    assert.fail("Expected contract failure");
  }

  return result.errors;
}

function issueCodes(issues: readonly H3ContractIssue[]): readonly string[] {
  return issues.map((issue) => issue.code);
}

test("H3 frame selection only produces supported frame counts", () => {
  assert.deepEqual(H3_FRAME_COUNTS, [124, 243, 345]);
  assert.equal(h3DurationSeconds(124), 124 / 24);
  assert.equal(isH3FrameCount(243), true);
  assert.equal(isH3FrameCount(240), false);

  const nearest = selectH3FrameCount(9);
  assert.equal(nearest.frameCount, 243);
  assert.equal(nearest.actualSeconds, 10.125);
  assert.equal(nearest.clamped, false);

  assert.equal(selectH3FrameCount(5.2, "ceil").frameCount, 243);
  assert.equal(selectH3FrameCount(5.2, "floor").frameCount, 124);

  const exactTieSeconds = (124 / 24 + 243 / 24) / 2;
  assert.equal(selectH3FrameCount(exactTieSeconds, "nearest").frameCount, 243);

  const aboveMaximum = selectH3FrameCount(60, "ceil");
  assert.equal(aboveMaximum.frameCount, 345);
  assert.equal(aboveMaximum.clamped, true);

  assert.throws(
    () => selectH3FrameCount(0),
    (error: unknown) => error instanceof RangeError && /positive finite/.test(error.message),
  );
  assert.throws(
    () => selectH3FrameCount(6, "unsupported" as H3FrameSelectionPolicy),
    (error: unknown) => error instanceof RangeError && /unsupported/.test(error.message),
  );
});

test("H3 resolution validation rejects unsafe dimensions and snapping is explicit", () => {
  const validResolution = { width: 768, height: 1344 };
  assert.equal(isValidH3Resolution(validResolution), true);
  assert.deepEqual(validateH3Resolution(validResolution), []);

  const errors = validateH3Resolution({ width: 1080, height: 1400 });
  assert.deepEqual(issueCodes(errors), [
    "RESOLUTION_NOT_ALIGNED",
    "RESOLUTION_OUT_OF_RANGE",
    "RESOLUTION_NOT_ALIGNED",
  ]);
  assert.deepEqual(
    errors.map((error) => error.path),
    ["resolution.width", "resolution.height", "resolution.height"],
  );

  const snapped = snapH3Resolution({ width: 1080, height: 1900 });
  assert.deepEqual(snapped.resolution, { width: 1088, height: 1344 });
  assert.equal(snapped.changed, true);
  assert.deepEqual(snapH3Resolution({ width: 100, height: 190 }, "floor").resolution, {
    width: 192,
    height: 192,
  });

  assert.throws(
    () => snapH3Resolution({ width: Number.NaN, height: 768 }),
    (error: unknown) => error instanceof RangeError && /finite/.test(error.message),
  );
});

test("H3 normalization trims authoring input, applies defaults, and snaps duration", () => {
  const result = normalizeShotRenderSpec({
    shotId: "  shot-platform-01  ",
    prompt: "  Empty station platform after the last train.  ",
    resolution: { width: 768, height: 1344 },
    targetDurationSeconds: 10,
  });
  const normalized = expectSuccess(result);

  assert.equal(normalized.shotId, "shot-platform-01");
  assert.equal(normalized.prompt, "Empty station platform after the last train.");
  assert.equal(normalized.mode, "t2v");
  assert.equal(normalized.frameCount, 243);
  assert.equal(normalized.durationSeconds, 10.125);
  assert.equal(normalized.style, "natural");
  assert.equal(normalized.steps, 20);
  assert.equal(normalized.seed, 0);
  assert.equal(normalized.acceleration, "community");
  assert.equal(normalized.promptProcessingMode, "community");
  assert.equal(normalized.standaloneAudioPolicy, "dialogue_priority");
  assert.deepEqual(normalized.references, []);
  assert.deepEqual(issueCodes(result.warnings), ["DURATION_SNAPPED_TO_H3_FRAMES"]);
});

test("H3 normalization infers omni and builds exact job fields and upload order", () => {
  const result = normalizeShotRenderSpec({
    shotId: "shot-omni-01",
    prompt: "Mio turns toward the apartment door when the bell rings once.",
    mode: "auto",
    resolution: { width: 768, height: 1344 },
    frameCount: 345,
    style: "cinematic",
    steps: 32,
    seed: 114_203,
    acceleration: "balanced",
    referenceImageSize: "max",
    promptProcessingMode: "community",
    standaloneAudioPolicy: "full_content",
    audioPreset: "dialogue",
    dialogue: "  Who is it?  ",
    soundscape: "  Quiet room tone and one doorbell.  ",
    musicPolicy: "subtle",
    audioGainDb: -2,
    references: [
      {
        assetId: "  asset:mio-reference  ",
        kind: "image",
        name: "  Mio reference  ",
        sizeBytes: 4_000_000,
      },
      {
        assetId: "asset:camera-motion",
        kind: "video",
        sizeBytes: 12_000_000,
      },
      {
        assetId: "asset:doorbell",
        kind: "audio",
        sizeBytes: 500_000,
      },
    ],
  });
  const normalized = expectSuccess(result);
  const request = toH3JobRequest(normalized);

  assert.equal(normalized.mode, "omni");
  assert.equal(normalized.dialogue, "Who is it?");
  assert.equal(normalized.soundscape, "Quiet room tone and one doorbell.");
  assert.equal(normalized.references[0]?.assetId, "asset:mio-reference");
  assert.equal(normalized.references[0]?.name, "Mio reference");
  assert.deepEqual(result.warnings, []);

  assert.deepEqual(request, {
    shotId: "shot-omni-01",
    fields: {
      mode: "omni",
      style: "cinematic",
      prompt: "Mio turns toward the apartment door when the bell rings once.",
      width: 768,
      height: 1344,
      num_frames: 345,
      steps: 32,
      seed: 114_203,
      acceleration: "balanced",
      ref_image_size: "max",
      prompt_processing_mode: "community",
      standalone_audio_policy: "full_content",
      audio_preset: "dialogue",
      dialogue: "Who is it?",
      soundscape: "Quiet room tone and one doorbell.",
      music_policy: "subtle",
      audio_gain_db: -2,
    },
    uploads: {
      first_image_asset_id: null,
      last_image_asset_id: null,
      reference_asset_ids: [
        "asset:mio-reference",
        "asset:camera-motion",
        "asset:doorbell",
      ],
    },
  });
});

test("H3 normalization accumulates independent authoring contract failures", () => {
  const result = normalizeShotRenderSpec({
    shotId: " ",
    prompt: " ",
    mode: "i2v",
    resolution: { width: 191, height: 1350.5 },
    frameCount: 200,
    steps: 1,
    seed: Number.MAX_SAFE_INTEGER + 1,
    audioGainDb: 1.5,
  });
  const codes = new Set(issueCodes(expectFailure(result)));

  assert.deepEqual(
    [...codes].sort(),
    [
      "AUDIO_GAIN_INVALID",
      "FRAME_COUNT_INVALID",
      "I2V_FIRST_FRAME_REQUIRED",
      "PROMPT_LENGTH_INVALID",
      "RESOLUTION_NOT_INTEGER",
      "RESOLUTION_NOT_ALIGNED",
      "RESOLUTION_OUT_OF_RANGE",
      "SEED_INVALID",
      "SHOT_ID_REQUIRED",
      "STEPS_INVALID",
    ].sort(),
  );
});

test("Project summary and handoff expose an atomic, deterministic production snapshot", () => {
  const before = JSON.stringify(demoProjectContext);
  const summary = summarizeProjectContext(demoProjectContext);
  const handoff = createHandoffCapsule(
    demoProjectContext,
    isoDateTime("2026-08-05T08:43:00+09:00"),
  );

  assert.deepEqual(
    {
      episodes: summary.episodeCount,
      scenes: summary.sceneCount,
      shots: summary.shotCount,
      takes: summary.takeCount,
      selected: summary.selectedTakeCount,
      selectedFresh: summary.selectedFreshTakeCount,
      shotsWithoutSelection: summary.shotsWithoutSelectedTake,
      stale: summary.staleEntityCount,
      blockingStale: summary.blockingStaleEntityCount,
      approvals: summary.pendingApprovalCount,
      activeJobs: summary.activeJobCount,
      failedJobs: summary.failedJobCount,
    },
    {
      episodes: 1,
      scenes: 2,
      shots: 3,
      takes: 3,
      selected: 2,
      selectedFresh: 1,
      shotsWithoutSelection: 1,
      stale: 1,
      blockingStale: 1,
      approvals: 1,
      activeJobs: 1,
      failedJobs: 0,
    },
  );
  assert.match(summary.recommendedNextAction, /再生成/);
  assert.equal(summary.blockers.length, 1);
  assert.deepEqual(summary.jobsByStatus, [
    { status: "running", count: 1 },
    { status: "succeeded", count: 2 },
  ]);

  assert.equal(handoff.protocol, "h3-story-studio/handoff");
  assert.equal(handoff.projectRevision, demoProjectContext.snapshotProjectRevision);
  assert.equal(handoff.stateDigest, demoProjectContext.stateDigest);
  assert.equal(handoff.createdAt, "2026-08-05T08:43:00+09:00");
  assert.deepEqual(handoff.pendingApprovalIds, [demoStoryIds.approvalFirstTake]);
  assert.deepEqual(handoff.activeJobIds, [demoStoryIds.jobDoorbell]);

  const keyRefKeys = handoff.keyRefs.map(entityRefKey);
  assert.equal(new Set(keyRefKeys).size, keyRefKeys.length);
  assert.deepEqual(keyRefKeys, [...keyRefKeys].sort((left, right) => left.localeCompare(right)));
  assert.ok(keyRefKeys.includes(`shot:${demoStoryIds.shotDoorbell}`));
  assert.ok(keyRefKeys.includes(`take:${demoStoryIds.takeEmptyPlatform}`));
  assert.equal(JSON.stringify(demoProjectContext), before, "summary helpers must not mutate context");
});

test("Stale propagation is transitive, severity-aware, cycle-safe, and non-mutating", () => {
  const changedPrompt: EntityRef = {
    kind: "prompt",
    id: demoStoryIds.promptMessageReflection,
  };
  const generatedTake: EntityRef = {
    kind: "take",
    id: demoStoryIds.takeMessageReflection,
  };
  const takeApproval: EntityRef = {
    kind: "approval",
    id: demoStoryIds.approvalFirstTake,
  };
  const downstreamJob: EntityRef = {
    kind: "job",
    id: demoStoryIds.jobDoorbell,
  };
  const oldReason: StaleReason = {
    code: "script_changed",
    root: { kind: "scene", id: demoStoryIds.scenePlatform },
    source: { kind: "scene", id: demoStoryIds.scenePlatform },
    path: [
      { kind: "scene", id: demoStoryIds.scenePlatform },
      generatedTake,
    ],
    sourceRevision: revisionNumber(4),
    detectedAtProjectRevision: revisionNumber(5),
    severity: "warning",
    message: "Earlier script edit requires review.",
  };
  const input: StalePropagationInput = {
    changes: [
      {
        ref: changedPrompt,
        revision: revisionNumber(5),
        projectRevision: revisionNumber(13),
        changeKind: "locked_dialogue_changed",
      },
    ],
    dependencies: [
      {
        source: changedPrompt,
        target: generatedTake,
        reasonCode: "prompt_changed",
        severity: "blocking",
        propagate: true,
      },
      {
        source: generatedTake,
        target: takeApproval,
        reasonCode: "upstream_changed",
        severity: "warning",
        propagate: false,
      },
      {
        source: generatedTake,
        target: changedPrompt,
        reasonCode: "upstream_changed",
        severity: "warning",
        propagate: true,
      },
      {
        source: takeApproval,
        target: downstreamJob,
        reasonCode: "approval_revoked",
        severity: "blocking",
        propagate: true,
      },
    ],
    current: [
      {
        ref: generatedTake,
        freshness: {
          state: "stale",
          staleSinceProjectRevision: revisionNumber(5),
          blocking: false,
          reasons: [oldReason],
        },
      },
      { ref: takeApproval, freshness: { state: "fresh" } },
      { ref: downstreamJob, freshness: { state: "fresh" } },
    ],
  };
  const before = JSON.stringify(input);
  const result = propagateStaleState(input);

  assert.deepEqual(result.affected.map((record) => entityRefKey(record.ref)), [
    entityRefKey(takeApproval),
    entityRefKey(generatedTake),
  ]);
  assert.deepEqual(result.unchanged.map((record) => entityRefKey(record.ref)), [
    entityRefKey(downstreamJob),
  ]);

  const takeRecord = result.affected.find(
    (record) => entityRefKey(record.ref) === entityRefKey(generatedTake),
  );
  assert.ok(takeRecord);
  assert.equal(takeRecord.freshness.state, "stale");
  if (takeRecord.freshness.state === "stale") {
    assert.equal(takeRecord.freshness.staleSinceProjectRevision, revisionNumber(5));
    assert.equal(takeRecord.freshness.blocking, true);
    assert.equal(takeRecord.freshness.reasons.length, 2);
  }

  const approvalRecord = result.affected.find(
    (record) => entityRefKey(record.ref) === entityRefKey(takeApproval),
  );
  assert.ok(approvalRecord);
  assert.equal(approvalRecord.freshness.state, "stale");
  if (approvalRecord.freshness.state === "stale") {
    assert.equal(approvalRecord.freshness.blocking, false);
    assert.deepEqual(approvalRecord.freshness.reasons[0]?.path, [
      changedPrompt,
      generatedTake,
      takeApproval,
    ]);
  }

  assert.ok(result.warnings.some((warning) => /cycle/.test(warning)));
  assert.equal(JSON.stringify(input), before, "stale propagation must not mutate its inputs");
});

test("Stale propagation reports duplicate or missing freshness records", () => {
  const promptRef: EntityRef = {
    kind: "prompt",
    id: demoStoryIds.promptEmptyPlatform,
  };
  const takeRef: EntityRef = {
    kind: "take",
    id: demoStoryIds.takeEmptyPlatform,
  };
  const input: StalePropagationInput = {
    changes: [
      {
        ref: promptRef,
        revision: revisionNumber(3),
        projectRevision: revisionNumber(14),
        changeKind: "prompt_changed",
      },
    ],
    dependencies: [
      {
        source: promptRef,
        target: takeRef,
        reasonCode: "prompt_changed",
        severity: "blocking",
        propagate: false,
      },
    ],
    current: [],
  };

  const missing = propagateStaleState(input);
  assert.equal(missing.affected.length, 1);
  assert.ok(missing.warnings.some((warning) => /assumed fresh/.test(warning)));

  const duplicate = propagateStaleState({
    ...input,
    current: [
      { ref: takeRef, freshness: { state: "fresh" } },
      { ref: takeRef, freshness: { state: "fresh" } },
    ],
  });
  assert.ok(duplicate.warnings.some((warning) => /Duplicate current/.test(warning)));
});

test("Optimistic revision validation accepts an unchanged atomic snapshot", () => {
  const prompt = demoProjectContext.entities.prompts.find(
    (candidate) => candidate.id === demoStoryIds.promptMessageReflection,
  );
  const take = demoProjectContext.entities.takes.find(
    (candidate) => candidate.id === demoStoryIds.takeMessageReflection,
  );
  assert.ok(prompt);
  assert.ok(take);

  const input: RevisionValidationInput = {
    expectedProjectRevision: revisionNumber(12),
    actualProjectRevision: revisionNumber(12),
    preconditions: [
      {
        ref: { kind: "prompt", id: prompt.id },
        expectedRevision: prompt.version.revision,
        expectedContentHash: prompt.version.contentHash,
      },
      {
        ref: { kind: "take", id: take.id },
        expectedRevision: take.version.revision,
      },
    ],
    current: [
      {
        ref: { kind: "prompt", id: prompt.id },
        revision: prompt.version.revision,
        contentHash: prompt.version.contentHash,
      },
      {
        ref: { kind: "take", id: take.id },
        revision: take.version.revision,
        contentHash: take.version.contentHash,
      },
    ],
  };

  assert.deepEqual(validateRevisionConflicts(input), {
    ok: true,
    validatedProjectRevision: revisionNumber(12),
    validatedEntityCount: 2,
  });
});

test("Optimistic revision validation returns all project, entity, hash, missing, and duplicate conflicts", () => {
  const promptRef: EntityRef = {
    kind: "prompt",
    id: demoStoryIds.promptMessageReflection,
  };
  const takeRef: EntityRef = {
    kind: "take",
    id: demoStoryIds.takeDoorbell,
  };
  const currentPromptHash = contentHash("sha256:prompt-current");
  const input: RevisionValidationInput = {
    expectedProjectRevision: revisionNumber(11),
    actualProjectRevision: revisionNumber(12),
    preconditions: [
      {
        ref: promptRef,
        expectedRevision: revisionNumber(3),
        expectedContentHash: contentHash("sha256:prompt-expected"),
      },
      {
        ref: promptRef,
        expectedRevision: revisionNumber(4),
      },
      {
        ref: takeRef,
        expectedRevision: revisionNumber(1),
      },
    ],
    current: [
      {
        ref: promptRef,
        revision: revisionNumber(4),
        contentHash: currentPromptHash,
      },
      {
        ref: promptRef,
        revision: revisionNumber(4),
        contentHash: currentPromptHash,
      },
    ],
  };
  const before = JSON.stringify(input);
  const result = validateRevisionConflicts(input);

  assert.equal(result.ok, false);
  if (result.ok) {
    assert.fail("Expected revision conflicts");
  }

  assert.deepEqual(
    result.conflicts.map((conflict) => conflict.kind).sort(),
    [
      "project_revision_mismatch",
      "duplicate_precondition",
      "duplicate_current_entity",
      "entity_revision_mismatch",
      "entity_content_hash_mismatch",
      "entity_missing",
    ].sort(),
  );
  assert.equal(JSON.stringify(input), before, "revision validation must not mutate its inputs");
});
