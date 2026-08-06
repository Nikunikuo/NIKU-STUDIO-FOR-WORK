import {
  H3_ACCELERATION_PRESETS,
  H3_AUDIO_GAIN_LIMITS,
  H3_AUDIO_PRESETS,
  H3_FRAME_COUNTS,
  H3_FRAME_RATE,
  H3_JOB_STATUSES,
  H3_MODES,
  H3_MUSIC_POLICIES,
  H3_PROMPT_LIMITS,
  H3_PROMPT_PROCESSING_MODES,
  H3_REFERENCE_IMAGE_SIZES,
  H3_REFERENCE_KINDS,
  H3_REFERENCE_LIMITS,
  H3_RESOLUTION_LIMITS,
  H3_SEED_LIMITS,
  H3_STANDALONE_AUDIO_POLICIES,
  H3_STEP_LIMITS,
  H3_STYLES,
  H3_TERMINAL_JOB_STATUSES,
} from "./constants";
import type {
  H3AssetReference,
  H3ContractIssue,
  H3ContractResult,
  H3FrameCount,
  H3FrameSelection,
  H3FrameSelectionPolicy,
  H3ImageAssetReference,
  H3JobRequest,
  H3JobResponse,
  H3JobResult,
  H3JobStatus,
  H3Mode,
  H3ReferenceCounts,
  H3ReferenceKind,
  H3Resolution,
  H3ResolutionSnapPolicy,
  H3ResolutionSnapResult,
  H3TerminalJobStatus,
  NormalizedShotRenderSpec,
  NormalizeShotRenderOptions,
  ShotRenderSpec,
} from "./types";

const EPSILON = 1e-9;
const EXPLICIT_DIALOGUE =
  /「[^」\r\n]+」|『[^』\r\n]+』|“[^”\r\n]+”|"[^"\r\n]+"/;
const AUDIO_TAG = /<Audio\s+[1-9][0-9]*>/i;

function issue(code: string, path: string, message: string): H3ContractIssue {
  return { code, path, message };
}

function contains<T extends string | number>(
  values: readonly T[],
  value: unknown,
): value is T {
  return values.some((candidate) => candidate === value);
}

function success<T>(
  value: T,
  warnings: readonly H3ContractIssue[] = [],
): H3ContractResult<T> {
  return { ok: true, value, warnings };
}

function failure<T>(
  errors: readonly H3ContractIssue[],
  warnings: readonly H3ContractIssue[] = [],
): H3ContractResult<T> {
  return { ok: false, errors, warnings };
}

export function isH3FrameCount(value: unknown): value is H3FrameCount {
  return typeof value === "number" && contains(H3_FRAME_COUNTS, value);
}

export function h3DurationSeconds(frameCount: H3FrameCount): number {
  return frameCount / H3_FRAME_RATE;
}

/** Select one of H3's three legal lengths without inventing arbitrary frames. */
export function selectH3FrameCount(
  requestedSeconds: number,
  policy: H3FrameSelectionPolicy = "nearest",
): H3FrameSelection {
  if (!Number.isFinite(requestedSeconds) || requestedSeconds <= 0) {
    throw new RangeError("requestedSeconds must be a positive finite number");
  }

  const choices = H3_FRAME_COUNTS.map((frameCount) => ({
    frameCount,
    seconds: h3DurationSeconds(frameCount),
  }));
  let selected: (typeof choices)[number];

  if (policy === "ceil") {
    selected =
      choices.find((choice) => choice.seconds + EPSILON >= requestedSeconds) ??
      choices[choices.length - 1];
  } else if (policy === "floor") {
    selected =
      [...choices]
        .reverse()
        .find((choice) => choice.seconds - EPSILON <= requestedSeconds) ?? choices[0];
  } else if (policy === "nearest") {
    selected = choices.reduce((best, candidate) => {
      const bestDistance = Math.abs(best.seconds - requestedSeconds);
      const candidateDistance = Math.abs(candidate.seconds - requestedSeconds);
      if (candidateDistance + EPSILON < bestDistance) {
        return candidate;
      }
      // Prefer the longer clip on an exact tie so authored action is not cut.
      if (
        Math.abs(candidateDistance - bestDistance) <= EPSILON &&
        candidate.seconds > best.seconds
      ) {
        return candidate;
      }
      return best;
    });
  } else {
    throw new RangeError(`unsupported frame selection policy: ${String(policy)}`);
  }

  const minimum = choices[0].seconds;
  const maximum = choices[choices.length - 1].seconds;
  return {
    requestedSeconds,
    frameCount: selected.frameCount,
    actualSeconds: selected.seconds,
    deltaSeconds: selected.seconds - requestedSeconds,
    policy,
    clamped: requestedSeconds < minimum || requestedSeconds > maximum,
  };
}

export function validateH3Resolution(
  resolution: H3Resolution,
  path = "resolution",
): readonly H3ContractIssue[] {
  const errors: H3ContractIssue[] = [];
  for (const dimension of ["width", "height"] as const) {
    const value = resolution[dimension];
    const dimensionPath = `${path}.${dimension}`;
    if (!Number.isInteger(value)) {
      errors.push(
        issue(
          "RESOLUTION_NOT_INTEGER",
          dimensionPath,
          `${dimension} must be an integer`,
        ),
      );
      continue;
    }
    if (
      value < H3_RESOLUTION_LIMITS.minimum ||
      value > H3_RESOLUTION_LIMITS.maximum
    ) {
      errors.push(
        issue(
          "RESOLUTION_OUT_OF_RANGE",
          dimensionPath,
          `${dimension} must be from ${H3_RESOLUTION_LIMITS.minimum} to ${H3_RESOLUTION_LIMITS.maximum}`,
        ),
      );
    }
    if (value % H3_RESOLUTION_LIMITS.multiple !== 0) {
      errors.push(
        issue(
          "RESOLUTION_NOT_ALIGNED",
          dimensionPath,
          `${dimension} must be a multiple of ${H3_RESOLUTION_LIMITS.multiple}`,
        ),
      );
    }
  }
  return errors;
}

export function isValidH3Resolution(resolution: H3Resolution): boolean {
  return validateH3Resolution(resolution).length === 0;
}

function snapDimension(value: number, policy: H3ResolutionSnapPolicy): number {
  if (!Number.isFinite(value)) {
    throw new RangeError("resolution dimensions must be finite numbers");
  }
  const bounded = Math.min(
    H3_RESOLUTION_LIMITS.maximum,
    Math.max(H3_RESOLUTION_LIMITS.minimum, value),
  );
  const ratio = bounded / H3_RESOLUTION_LIMITS.multiple;
  const alignedRatio =
    policy === "ceil"
      ? Math.ceil(ratio)
      : policy === "floor"
        ? Math.floor(ratio)
        : policy === "nearest"
          ? Math.round(ratio)
          : (() => {
              throw new RangeError(`unsupported resolution snap policy: ${String(policy)}`);
            })();
  return Math.min(
    H3_RESOLUTION_LIMITS.maximum,
    Math.max(
      H3_RESOLUTION_LIMITS.minimum,
      alignedRatio * H3_RESOLUTION_LIMITS.multiple,
    ),
  );
}

/** Explicit opt-in helper for UI suggestions; normalization itself never snaps. */
export function snapH3Resolution(
  requested: H3Resolution,
  policy: H3ResolutionSnapPolicy = "nearest",
): H3ResolutionSnapResult {
  const resolution = {
    width: snapDimension(requested.width, policy),
    height: snapDimension(requested.height, policy),
  };
  return {
    requested: { ...requested },
    resolution,
    changed:
      resolution.width !== requested.width || resolution.height !== requested.height,
    policy,
  };
}

function normalizeAsset<T extends H3AssetReference>(asset: T): T {
  return {
    ...asset,
    assetId: asset.assetId.trim(),
    name: asset.name?.trim() || undefined,
    mediaType: asset.mediaType?.trim() || undefined,
  };
}

function validateAsset(
  asset: H3AssetReference,
  path: string,
): readonly H3ContractIssue[] {
  const errors: H3ContractIssue[] = [];
  if (!asset.assetId.trim()) {
    errors.push(issue("ASSET_ID_REQUIRED", `${path}.assetId`, "assetId is required"));
  }
  if (!contains(H3_REFERENCE_KINDS, asset.kind)) {
    errors.push(
      issue("REFERENCE_KIND_INVALID", `${path}.kind`, "unsupported reference kind"),
    );
  }
  if (asset.sizeBytes !== undefined) {
    if (!Number.isSafeInteger(asset.sizeBytes) || asset.sizeBytes < 0) {
      errors.push(
        issue(
          "ASSET_SIZE_INVALID",
          `${path}.sizeBytes`,
          "sizeBytes must be a non-negative safe integer",
        ),
      );
    } else if (asset.sizeBytes > H3_REFERENCE_LIMITS.maximumFileBytes) {
      errors.push(
        issue(
          "ASSET_TOO_LARGE",
          `${path}.sizeBytes`,
          `each H3 upload is limited to ${H3_REFERENCE_LIMITS.maximumFileBytes} bytes`,
        ),
      );
    }
  }
  return errors;
}

export function countH3References(
  references: readonly H3AssetReference[],
): H3ReferenceCounts {
  const counts: Record<H3ReferenceKind, number> = {
    image: 0,
    video: 0,
    audio: 0,
  };
  let knownBytes = 0;
  let unknownSizeCount = 0;
  for (const reference of references) {
    if (contains(H3_REFERENCE_KINDS, reference.kind)) {
      counts[reference.kind] += 1;
    }
    if (reference.sizeBytes === undefined) {
      unknownSizeCount += 1;
    } else if (Number.isSafeInteger(reference.sizeBytes) && reference.sizeBytes >= 0) {
      knownBytes += reference.sizeBytes;
    }
  }
  return {
    total: references.length,
    image: counts.image,
    video: counts.video,
    audio: counts.audio,
    knownBytes,
    unknownSizeCount,
  };
}

export function validateH3References(
  references: readonly H3AssetReference[],
  path = "references",
): H3ContractResult<H3ReferenceCounts> {
  const errors: H3ContractIssue[] = [];
  const warnings: H3ContractIssue[] = [];
  const ids = new Set<string>();

  references.forEach((reference, index) => {
    const itemPath = `${path}[${index}]`;
    errors.push(...validateAsset(reference, itemPath));
    const id = reference.assetId.trim();
    if (id && ids.has(id)) {
      errors.push(
        issue(
          "REFERENCE_ASSET_DUPLICATE",
          `${itemPath}.assetId`,
          `assetId ${JSON.stringify(id)} appears more than once`,
        ),
      );
    }
    ids.add(id);
  });

  const counts = countH3References(references);
  for (const kind of H3_REFERENCE_KINDS) {
    if (counts[kind] > H3_REFERENCE_LIMITS[kind]) {
      errors.push(
        issue(
          "REFERENCE_KIND_LIMIT_EXCEEDED",
          path,
          `${kind} references are limited to ${H3_REFERENCE_LIMITS[kind]}`,
        ),
      );
    }
  }
  if (counts.total > H3_REFERENCE_LIMITS.total) {
    errors.push(
      issue(
        "REFERENCE_TOTAL_LIMIT_EXCEEDED",
        path,
        `H3 Omni accepts at most ${H3_REFERENCE_LIMITS.total} references`,
      ),
    );
  }
  if (counts.audio > 0 && counts.image + counts.video === 0) {
    errors.push(
      issue(
        "AUDIO_REFERENCE_REQUIRES_VISUAL",
        path,
        "audio references must be combined with at least one image or video reference",
      ),
    );
  }
  if (counts.knownBytes > H3_REFERENCE_LIMITS.maximumRequestBytes) {
    errors.push(
      issue(
        "REQUEST_UPLOAD_LIMIT_EXCEEDED",
        path,
        `known reference bytes exceed the ${H3_REFERENCE_LIMITS.maximumRequestBytes}-byte request limit`,
      ),
    );
  }
  if (counts.unknownSizeCount > 0) {
    warnings.push(
      issue(
        "REFERENCE_SIZE_UNKNOWN",
        path,
        "the sidecar must enforce per-file and aggregate byte limits while resolving assets",
      ),
    );
  }
  return errors.length ? failure(errors, warnings) : success(counts, warnings);
}

export function inferH3Mode(
  spec: Pick<ShotRenderSpec, "mode" | "firstFrame" | "lastFrame" | "references">,
): H3Mode {
  if (spec.mode && spec.mode !== "auto") {
    return spec.mode;
  }
  if ((spec.references?.length ?? 0) > 0) {
    return "omni";
  }
  if (spec.lastFrame) {
    return "first_last";
  }
  if (spec.firstFrame) {
    return "i2v";
  }
  return "t2v";
}

function validateModeAssets(
  mode: H3Mode,
  firstFrame: H3ImageAssetReference | null,
  lastFrame: H3ImageAssetReference | null,
  references: readonly H3AssetReference[],
): readonly H3ContractIssue[] {
  const errors: H3ContractIssue[] = [];
  if (mode === "t2v") {
    if (firstFrame || lastFrame || references.length) {
      errors.push(
        issue("T2V_UPLOADS_NOT_ALLOWED", "mode", "t2v cannot include uploaded assets"),
      );
    }
  } else if (mode === "i2v") {
    if (!firstFrame) {
      errors.push(
        issue("I2V_FIRST_FRAME_REQUIRED", "firstFrame", "i2v requires a first image"),
      );
    }
    if (lastFrame) {
      errors.push(
        issue("I2V_LAST_FRAME_NOT_ALLOWED", "lastFrame", "i2v does not use a last image"),
      );
    }
    if (references.length) {
      errors.push(
        issue(
          "REFERENCES_REQUIRE_OMNI",
          "references",
          "ordered references are only accepted in omni mode",
        ),
      );
    }
  } else if (mode === "first_last") {
    if (!firstFrame || !lastFrame) {
      errors.push(
        issue(
          "FIRST_LAST_IMAGES_REQUIRED",
          "firstFrame",
          "first_last requires both first and last images",
        ),
      );
    }
    if (references.length) {
      errors.push(
        issue(
          "REFERENCES_REQUIRE_OMNI",
          "references",
          "ordered references are only accepted in omni mode",
        ),
      );
    }
  } else if (mode === "omni") {
    if (firstFrame || lastFrame) {
      errors.push(
        issue(
          "OMNI_FRAME_SLOTS_NOT_ALLOWED",
          "mode",
          "omni assets must all be supplied through ordered references",
        ),
      );
    }
    if (references.length === 0) {
      errors.push(
        issue(
          "OMNI_REFERENCE_REQUIRED",
          "references",
          "omni requires at least one reference",
        ),
      );
    }
  }
  return errors;
}

function validateEnum<T extends string>(
  value: unknown,
  allowed: readonly T[],
  path: string,
): readonly H3ContractIssue[] {
  return contains(allowed, value)
    ? []
    : [issue("ENUM_VALUE_INVALID", path, `unsupported ${path}: ${String(value)}`)];
}

export function normalizeShotRenderSpec(
  spec: ShotRenderSpec,
  options: NormalizeShotRenderOptions = {},
): H3ContractResult<NormalizedShotRenderSpec> {
  const errors: H3ContractIssue[] = [];
  const warnings: H3ContractIssue[] = [];
  const shotId = spec.shotId.trim();
  const prompt = spec.prompt.trim();
  const dialogue = spec.dialogue?.trim() ?? "";
  const soundscape = spec.soundscape?.trim() ?? "";
  const firstFrame = spec.firstFrame ? normalizeAsset(spec.firstFrame) : null;
  const lastFrame = spec.lastFrame ? normalizeAsset(spec.lastFrame) : null;
  const references = (spec.references ?? []).map(normalizeAsset);
  const mode = inferH3Mode(spec);

  if (!shotId) {
    errors.push(issue("SHOT_ID_REQUIRED", "shotId", "shotId is required"));
  }
  if (!prompt || prompt.length > H3_PROMPT_LIMITS.promptCharacters) {
    errors.push(
      issue(
        "PROMPT_LENGTH_INVALID",
        "prompt",
        `prompt must contain 1 to ${H3_PROMPT_LIMITS.promptCharacters} characters`,
      ),
    );
  }
  if (dialogue.length > H3_PROMPT_LIMITS.dialogueCharacters) {
    errors.push(
      issue(
        "DIALOGUE_TOO_LONG",
        "dialogue",
        `dialogue is limited to ${H3_PROMPT_LIMITS.dialogueCharacters} characters`,
      ),
    );
  }
  if (soundscape.length > H3_PROMPT_LIMITS.soundscapeCharacters) {
    errors.push(
      issue(
        "SOUNDSCAPE_TOO_LONG",
        "soundscape",
        `soundscape is limited to ${H3_PROMPT_LIMITS.soundscapeCharacters} characters`,
      ),
    );
  }
  errors.push(...validateH3Resolution(spec.resolution));
  errors.push(...validateModeAssets(mode, firstFrame, lastFrame, references));
  if (firstFrame) {
    errors.push(...validateAsset(firstFrame, "firstFrame"));
  }
  if (lastFrame) {
    errors.push(...validateAsset(lastFrame, "lastFrame"));
  }
  const referenceValidation = validateH3References(references);
  warnings.push(...referenceValidation.warnings);
  if (!referenceValidation.ok) {
    errors.push(...referenceValidation.errors);
  }

  const allAssets = [
    ...(firstFrame ? [firstFrame] : []),
    ...(lastFrame ? [lastFrame] : []),
    ...references,
  ];
  const aggregateKnownBytes = allAssets.reduce(
    (total, asset) => total + (asset.sizeBytes ?? 0),
    0,
  );
  if (aggregateKnownBytes > H3_REFERENCE_LIMITS.maximumRequestBytes) {
    errors.push(
      issue(
        "REQUEST_UPLOAD_LIMIT_EXCEEDED",
        "assets",
        `known upload bytes exceed the ${H3_REFERENCE_LIMITS.maximumRequestBytes}-byte request limit`,
      ),
    );
  }

  let frameCount: H3FrameCount = H3_FRAME_COUNTS[0];
  if (spec.frameCount !== undefined) {
    if (!isH3FrameCount(spec.frameCount)) {
      errors.push(
        issue(
          "FRAME_COUNT_INVALID",
          "frameCount",
          `frameCount must be one of ${H3_FRAME_COUNTS.join(", ")}`,
        ),
      );
    } else {
      frameCount = spec.frameCount;
      if (spec.targetDurationSeconds !== undefined) {
        const actual = h3DurationSeconds(frameCount);
        if (Math.abs(actual - spec.targetDurationSeconds) > EPSILON) {
          warnings.push(
            issue(
              "FRAME_COUNT_OVERRIDES_DURATION",
              "targetDurationSeconds",
              `explicit ${frameCount} frames produces ${actual.toFixed(3)} seconds`,
            ),
          );
        }
      }
    }
  } else {
    const requestedDuration =
      spec.targetDurationSeconds ??
      options.defaultDurationSeconds ??
      h3DurationSeconds(H3_FRAME_COUNTS[0]);
    try {
      const selected = selectH3FrameCount(
        requestedDuration,
        spec.frameSelectionPolicy ?? "nearest",
      );
      frameCount = selected.frameCount;
      if (Math.abs(selected.deltaSeconds) > EPSILON) {
        warnings.push(
          issue(
            "DURATION_SNAPPED_TO_H3_FRAMES",
            "targetDurationSeconds",
            `${requestedDuration} seconds maps to ${selected.frameCount} frames (${selected.actualSeconds.toFixed(3)} seconds)`,
          ),
        );
      }
    } catch (error) {
      errors.push(
        issue(
          "DURATION_INVALID",
          "targetDurationSeconds",
          error instanceof Error ? error.message : "invalid target duration",
        ),
      );
    }
  }

  const style = spec.style ?? "natural";
  const acceleration = spec.acceleration ?? "community";
  const referenceImageSize = spec.referenceImageSize ?? "match";
  const promptProcessingMode = spec.promptProcessingMode ?? "community";
  const audioPreset = spec.audioPreset ?? "auto";
  const musicPolicy = spec.musicPolicy ?? "auto";
  errors.push(...validateEnum(mode, H3_MODES, "mode"));
  errors.push(...validateEnum(style, H3_STYLES, "style"));
  errors.push(...validateEnum(acceleration, H3_ACCELERATION_PRESETS, "acceleration"));
  errors.push(
    ...validateEnum(referenceImageSize, H3_REFERENCE_IMAGE_SIZES, "referenceImageSize"),
  );
  errors.push(
    ...validateEnum(
      promptProcessingMode,
      H3_PROMPT_PROCESSING_MODES,
      "promptProcessingMode",
    ),
  );
  errors.push(...validateEnum(audioPreset, H3_AUDIO_PRESETS, "audioPreset"));
  errors.push(...validateEnum(musicPolicy, H3_MUSIC_POLICIES, "musicPolicy"));

  const steps = spec.steps ?? options.defaultSteps ?? H3_STEP_LIMITS.default;
  if (
    !Number.isInteger(steps) ||
    steps < H3_STEP_LIMITS.minimum ||
    steps > H3_STEP_LIMITS.maximum
  ) {
    errors.push(
      issue(
        "STEPS_INVALID",
        "steps",
        `steps must be an integer from ${H3_STEP_LIMITS.minimum} to ${H3_STEP_LIMITS.maximum}`,
      ),
    );
  }

  const seed = spec.seed ?? options.defaultSeed ?? H3_SEED_LIMITS.default;
  if (
    !Number.isSafeInteger(seed) ||
    seed < H3_SEED_LIMITS.minimum ||
    seed > H3_SEED_LIMITS.maximumSafeInteger
  ) {
    errors.push(
      issue(
        "SEED_INVALID",
        "seed",
        `seed must be a safe integer from ${H3_SEED_LIMITS.minimum} to ${H3_SEED_LIMITS.maximumSafeInteger}`,
      ),
    );
  }

  const audioGainDb = spec.audioGainDb ?? H3_AUDIO_GAIN_LIMITS.defaultDb;
  if (
    !Number.isInteger(audioGainDb) ||
    audioGainDb < H3_AUDIO_GAIN_LIMITS.minimumDb ||
    audioGainDb > H3_AUDIO_GAIN_LIMITS.maximumDb
  ) {
    errors.push(
      issue(
        "AUDIO_GAIN_INVALID",
        "audioGainDb",
        `audio gain must be an integer from ${H3_AUDIO_GAIN_LIMITS.minimumDb} to ${H3_AUDIO_GAIN_LIMITS.maximumDb} dB`,
      ),
    );
  }

  const hasAudioReference = references.some((reference) => reference.kind === "audio");
  const hasExplicitDialogue = Boolean(dialogue || EXPLICIT_DIALOGUE.test(prompt));
  let standaloneAudioPolicy = spec.standaloneAudioPolicy;
  if (standaloneAudioPolicy !== undefined) {
    errors.push(
      ...validateEnum(
        standaloneAudioPolicy,
        H3_STANDALONE_AUDIO_POLICIES,
        "standaloneAudioPolicy",
      ),
    );
  }
  if (hasAudioReference && hasExplicitDialogue && standaloneAudioPolicy === undefined) {
    errors.push(
      issue(
        "AUDIO_POLICY_REQUIRED",
        "standaloneAudioPolicy",
        "explicit dialogue plus an audio reference requires dialogue_priority or full_content",
      ),
    );
  }
  standaloneAudioPolicy ??= hasAudioReference ? "full_content" : "dialogue_priority";
  if (
    hasAudioReference &&
    hasExplicitDialogue &&
    standaloneAudioPolicy === "dialogue_priority" &&
    AUDIO_TAG.test(`${prompt}\n${dialogue}`)
  ) {
    errors.push(
      issue(
        "AUDIO_TAG_CONFLICTS_WITH_DIALOGUE_PRIORITY",
        "prompt",
        "dialogue_priority removes standalone audio conditioning, so <Audio N> tags must also be removed",
      ),
    );
  }

  if (promptProcessingMode === "raw_en") {
    if (
      style !== "natural" ||
      audioPreset !== "auto" ||
      dialogue !== "" ||
      soundscape !== "" ||
      musicPolicy !== "auto"
    ) {
      errors.push(
        issue(
          "RAW_EN_AUXILIARY_FIELDS_NOT_ALLOWED",
          "promptProcessingMode",
          "raw_en requires natural style and all dialogue/audio auxiliary fields to remain empty or auto",
        ),
      );
    }
  }

  if (errors.length) {
    return failure(errors, warnings);
  }

  return success(
    {
      shotId,
      prompt,
      mode,
      resolution: { ...spec.resolution },
      frameCount,
      durationSeconds: h3DurationSeconds(frameCount),
      style,
      steps,
      seed,
      acceleration,
      referenceImageSize,
      promptProcessingMode,
      standaloneAudioPolicy,
      audioPreset,
      dialogue,
      soundscape,
      musicPolicy,
      audioGainDb,
      firstFrame,
      lastFrame,
      references,
    },
    warnings,
  );
}

/** Convert a validated shot to scalar multipart fields and logical upload IDs. */
export function toH3JobRequest(spec: NormalizedShotRenderSpec): H3JobRequest {
  return {
    shotId: spec.shotId,
    fields: {
      mode: spec.mode,
      style: spec.style,
      prompt: spec.prompt,
      width: spec.resolution.width,
      height: spec.resolution.height,
      num_frames: spec.frameCount,
      steps: spec.steps,
      seed: spec.seed,
      acceleration: spec.acceleration,
      ref_image_size: spec.referenceImageSize,
      prompt_processing_mode: spec.promptProcessingMode,
      standalone_audio_policy: spec.standaloneAudioPolicy,
      audio_preset: spec.audioPreset,
      dialogue: spec.dialogue,
      soundscape: spec.soundscape,
      music_policy: spec.musicPolicy,
      audio_gain_db: spec.audioGainDb,
    },
    uploads: {
      first_image_asset_id: spec.firstFrame?.assetId ?? null,
      last_image_asset_id: spec.lastFrame?.assetId ?? null,
      reference_asset_ids: spec.references.map((reference) => reference.assetId),
    },
  };
}

export function isH3JobStatus(value: unknown): value is H3JobStatus {
  return typeof value === "string" && contains(H3_JOB_STATUSES, value);
}

export function isH3TerminalJobStatus(value: unknown): value is H3TerminalJobStatus {
  return typeof value === "string" && contains(H3_TERMINAL_JOB_STATUSES, value);
}

export function toH3JobResult(job: H3JobResponse): H3ContractResult<H3JobResult> {
  if (job.status !== "completed") {
    return failure([
      issue(
        "JOB_NOT_COMPLETED",
        "status",
        `job ${job.id} is ${job.status}, not completed`,
      ),
    ]);
  }
  if (!job.result) {
    return failure([
      issue("JOB_RESULT_URL_MISSING", "result", `completed job ${job.id} has no result URL`),
    ]);
  }
  return success({
    jobId: job.id,
    status: "completed",
    resultUrl: job.result,
    previewUrl: job.preview,
    media: job.media ?? null,
    mediaType: "video/mp4",
  });
}
