/**
 * Stable limits of the local H3 Studio REST renderer.
 *
 * Keep this module dependency-free: it is shared by browser code and the
 * local sidecar, and must not import server-only APIs such as `fs` or `path`.
 */

export const H3_CONTRACT_SCHEMA_VERSION =
  "h3-story-studio/h3-renderer-contract/v1" as const;

export const H3_DEFAULT_ORIGIN = "http://127.0.0.1:7863" as const;
export const H3_MUTATION_HEADER = "X-H3-Studio-Request" as const;
export const H3_MUTATION_HEADER_VALUE = "1" as const;

export const H3_MODES = ["t2v", "i2v", "first_last", "omni"] as const;
export const H3_STYLES = [
  "natural",
  "cinematic",
  "photoreal",
  "anime",
  "illustration",
  "product",
  "moody",
] as const;
export const H3_PROMPT_PROCESSING_MODES = ["community", "raw_en"] as const;
export const H3_ACCELERATION_PRESETS = [
  "off",
  "community",
  "conservative",
  "balanced",
] as const;
export const H3_REFERENCE_IMAGE_SIZES = ["match", "max"] as const;
export const H3_STANDALONE_AUDIO_POLICIES = [
  "dialogue_priority",
  "full_content",
] as const;
export const H3_AUDIO_PRESETS = [
  "auto",
  "dialogue",
  "ambience",
  "effects",
  "music",
  "quiet",
] as const;
export const H3_MUSIC_POLICIES = ["auto", "none", "subtle", "prominent"] as const;

export const H3_FRAME_RATE = 24 as const;
export const H3_FRAME_COUNTS = [124, 243, 345] as const;

export const H3_RESOLUTION_LIMITS = {
  minimum: 192,
  maximum: 1344,
  multiple: 32,
} as const;

export const H3_STEP_LIMITS = {
  minimum: 2,
  maximum: 40,
  default: 20,
} as const;

/**
 * H3 Studio accepts signed 63-bit seeds. JavaScript cannot exactly represent
 * that entire range, so this shared JSON contract intentionally stops at the
 * largest safe integer. The sidecar must never coerce a larger decimal seed to
 * a JS number.
 */
export const H3_SEED_LIMITS = {
  minimum: 0,
  maximumSafeInteger: Number.MAX_SAFE_INTEGER,
  rendererMaximumDecimal: "9223372036854775807",
  default: 0,
} as const;

export const H3_PROMPT_LIMITS = {
  promptCharacters: 4_000,
  dialogueCharacters: 800,
  soundscapeCharacters: 800,
} as const;

export const H3_AUDIO_GAIN_LIMITS = {
  minimumDb: -24,
  maximumDb: 12,
  defaultDb: 0,
} as const;

export const H3_REFERENCE_KINDS = ["image", "video", "audio"] as const;
export const H3_REFERENCE_LIMITS = {
  total: 12,
  image: 9,
  video: 3,
  audio: 3,
  maximumFileBytes: 2 * 1024 ** 3,
  maximumRequestBytes: 8 * 1024 ** 3,
} as const;

export const H3_JOB_STATUSES = [
  "queued",
  "running",
  "completed",
  "failed",
  "cancelled",
  "interrupted",
] as const;

export const H3_TERMINAL_JOB_STATUSES = [
  "completed",
  "failed",
  "cancelled",
  "interrupted",
] as const;

export const H3_OUTPUT = {
  mediaType: "video/mp4",
  frameRate: H3_FRAME_RATE,
  audioSampleRate: 32_000,
  audioChannels: 2,
  maximumActiveJobs: 1,
} as const;
