export type H3Mode = (typeof import("./constants").H3_MODES)[number];
export type H3Style = (typeof import("./constants").H3_STYLES)[number];
export type H3PromptProcessingMode =
  (typeof import("./constants").H3_PROMPT_PROCESSING_MODES)[number];
export type H3AccelerationPreset =
  (typeof import("./constants").H3_ACCELERATION_PRESETS)[number];
export type H3ReferenceImageSize =
  (typeof import("./constants").H3_REFERENCE_IMAGE_SIZES)[number];
export type H3StandaloneAudioPolicy =
  (typeof import("./constants").H3_STANDALONE_AUDIO_POLICIES)[number];
export type H3AudioPreset =
  (typeof import("./constants").H3_AUDIO_PRESETS)[number];
export type H3MusicPolicy =
  (typeof import("./constants").H3_MUSIC_POLICIES)[number];
export type H3FrameCount =
  (typeof import("./constants").H3_FRAME_COUNTS)[number];
export type H3ReferenceKind =
  (typeof import("./constants").H3_REFERENCE_KINDS)[number];
export type H3JobStatus =
  (typeof import("./constants").H3_JOB_STATUSES)[number];
export type H3TerminalJobStatus =
  (typeof import("./constants").H3_TERMINAL_JOB_STATUSES)[number];

export type H3FrameSelectionPolicy = "nearest" | "ceil" | "floor";
export type H3ResolutionSnapPolicy = "nearest" | "ceil" | "floor";

export interface H3Resolution {
  readonly width: number;
  readonly height: number;
}

/**
 * A browser/sidecar-neutral asset reference. `assetId` resolves to a Blob in
 * the browser or to a trusted local file in the sidecar; filesystem paths and
 * Blob objects deliberately do not cross this shared contract.
 */
export interface H3AssetReference {
  readonly assetId: string;
  readonly kind: H3ReferenceKind;
  readonly name?: string;
  readonly mediaType?: string;
  readonly sizeBytes?: number;
}

export interface H3ImageAssetReference extends H3AssetReference {
  readonly kind: "image";
}

export interface H3ReferenceCounts {
  readonly total: number;
  readonly image: number;
  readonly video: number;
  readonly audio: number;
  /** Sum of sizes that were known at validation time. */
  readonly knownBytes: number;
  readonly unknownSizeCount: number;
}

export interface H3ContractIssue {
  readonly code: string;
  readonly path: string;
  readonly message: string;
}

export type H3ContractResult<T> =
  | {
      readonly ok: true;
      readonly value: T;
      readonly warnings: readonly H3ContractIssue[];
    }
  | {
      readonly ok: false;
      readonly errors: readonly H3ContractIssue[];
      readonly warnings: readonly H3ContractIssue[];
    };

export interface H3FrameSelection {
  readonly requestedSeconds: number;
  readonly frameCount: H3FrameCount;
  readonly actualSeconds: number;
  readonly deltaSeconds: number;
  readonly policy: H3FrameSelectionPolicy;
  readonly clamped: boolean;
}

export interface H3ResolutionSnapResult {
  readonly requested: H3Resolution;
  readonly resolution: H3Resolution;
  readonly changed: boolean;
  readonly policy: H3ResolutionSnapPolicy;
}

/** Authoring-level input shared by the story UI and local sidecar. */
export interface ShotRenderSpec {
  readonly shotId: string;
  readonly prompt: string;
  readonly mode?: H3Mode | "auto";
  readonly resolution: H3Resolution;
  /** Explicit renderer frame count. Takes precedence over target duration. */
  readonly frameCount?: number;
  /** Human-facing requested duration, snapped to an H3 frame count. */
  readonly targetDurationSeconds?: number;
  readonly frameSelectionPolicy?: H3FrameSelectionPolicy;
  readonly style?: H3Style;
  readonly steps?: number;
  readonly seed?: number;
  readonly acceleration?: H3AccelerationPreset;
  readonly referenceImageSize?: H3ReferenceImageSize;
  readonly promptProcessingMode?: H3PromptProcessingMode;
  readonly standaloneAudioPolicy?: H3StandaloneAudioPolicy;
  readonly audioPreset?: H3AudioPreset;
  readonly dialogue?: string;
  readonly soundscape?: string;
  readonly musicPolicy?: H3MusicPolicy;
  readonly audioGainDb?: number;
  readonly firstFrame?: H3ImageAssetReference | null;
  readonly lastFrame?: H3ImageAssetReference | null;
  readonly references?: readonly H3AssetReference[];
}

export interface NormalizedShotRenderSpec {
  readonly shotId: string;
  readonly prompt: string;
  readonly mode: H3Mode;
  readonly resolution: H3Resolution;
  readonly frameCount: H3FrameCount;
  readonly durationSeconds: number;
  readonly style: H3Style;
  readonly steps: number;
  readonly seed: number;
  readonly acceleration: H3AccelerationPreset;
  readonly referenceImageSize: H3ReferenceImageSize;
  readonly promptProcessingMode: H3PromptProcessingMode;
  readonly standaloneAudioPolicy: H3StandaloneAudioPolicy;
  readonly audioPreset: H3AudioPreset;
  readonly dialogue: string;
  readonly soundscape: string;
  readonly musicPolicy: H3MusicPolicy;
  readonly audioGainDb: number;
  readonly firstFrame: H3ImageAssetReference | null;
  readonly lastFrame: H3ImageAssetReference | null;
  readonly references: readonly H3AssetReference[];
}

export interface NormalizeShotRenderOptions {
  readonly defaultDurationSeconds?: number;
  readonly defaultSteps?: number;
  readonly defaultSeed?: number;
}

/** Exact scalar names expected by H3 Studio's multipart `POST /api/jobs`. */
export interface H3JobRequestFields {
  readonly mode: H3Mode;
  readonly style: H3Style;
  readonly prompt: string;
  readonly width: number;
  readonly height: number;
  readonly num_frames: H3FrameCount;
  readonly steps: number;
  readonly seed: number;
  readonly acceleration: H3AccelerationPreset;
  readonly ref_image_size: H3ReferenceImageSize;
  readonly prompt_processing_mode: H3PromptProcessingMode;
  readonly standalone_audio_policy: H3StandaloneAudioPolicy;
  readonly audio_preset: H3AudioPreset;
  readonly dialogue: string;
  readonly soundscape: string;
  readonly music_policy: H3MusicPolicy;
  readonly audio_gain_db: number;
}

/**
 * Logical upload slots for the sidecar. The HTTP adapter is responsible for
 * resolving each ID and appending it to FormData under the named H3 field.
 */
export interface H3JobUploadBindings {
  readonly first_image_asset_id: string | null;
  readonly last_image_asset_id: string | null;
  readonly reference_asset_ids: readonly string[];
}

export interface H3JobRequest {
  readonly shotId: string;
  readonly fields: H3JobRequestFields;
  readonly uploads: H3JobUploadBindings;
}

export interface H3JobAttachment {
  readonly index: number;
  readonly name: string;
  readonly kind: H3ReferenceKind;
  readonly size: number;
  readonly url: string;
}

export interface H3MediaMetadata {
  readonly bytes: number;
  readonly duration_seconds: number | null;
  readonly video_codec: string;
  readonly audio_codec: string;
  readonly width: number;
  readonly height: number;
  readonly video_frames: number;
  readonly audio_frames: number;
  readonly audio_samples: number;
  readonly audio_samples_per_channel: number;
  readonly audio_sample_rate: number;
  readonly audio_channels: number;
}

export interface H3JobResponse {
  readonly id: string;
  readonly backend: string;
  readonly workflow_profile: string;
  readonly attention_backend: string;
  readonly status: H3JobStatus;
  readonly phase: string;
  readonly message: string;
  readonly progress: number;
  readonly created_at: string;
  readonly progress_updated_at: string;
  readonly started_at: string | null;
  readonly finished_at: string | null;
  readonly mode: H3Mode;
  readonly style: H3Style;
  readonly prompt: string;
  readonly width: number;
  readonly height: number;
  readonly num_frames: H3FrameCount;
  readonly steps: number;
  readonly seed: number;
  readonly attachments: readonly H3JobAttachment[];
  readonly result: string | null;
  readonly preview: string | null;
  readonly media?: H3MediaMetadata;
  readonly error?: string | null;
  readonly can_cancel: boolean;
  /** H3 Studio adds diagnostics over time; consumers must ignore unknown keys. */
  readonly [key: string]: unknown;
}

/** Descriptor of the binary `GET /api/jobs/{id}/result` response. */
export interface H3JobResult {
  readonly jobId: string;
  readonly status: "completed";
  readonly resultUrl: string;
  readonly previewUrl: string | null;
  readonly media: H3MediaMetadata | null;
  readonly mediaType: "video/mp4";
}

export interface H3RuntimeCapabilities {
  readonly backend: string;
  readonly ready: boolean;
  readonly local_only: boolean;
  readonly modes: Readonly<Record<H3Mode, boolean>>;
  readonly model_files: Readonly<Record<string, boolean>>;
  readonly [key: string]: unknown;
}

export interface H3StateResponse {
  readonly capabilities: H3RuntimeCapabilities;
  readonly system: Readonly<Record<string, unknown>>;
  readonly active_job_id: string | null;
  readonly jobs: readonly H3JobResponse[];
}

/** Static contract consumed by both the story UI and its local sidecar. */
export interface CapabilityManifest {
  readonly schemaVersion: string;
  readonly renderer: {
    readonly id: "h3-studio";
    readonly modelFamily: "MiniMax H3";
    readonly transport: "local-rest";
    readonly localOnly: true;
    readonly defaultOrigin: string;
    readonly mutationHeader: {
      readonly name: string;
      readonly value: string;
    };
    readonly routes: {
      readonly state: "/api/state";
      readonly jobs: "/api/jobs";
      readonly job: "/api/jobs/{job_id}";
      readonly cancel: "/api/jobs/{job_id}/cancel";
      readonly result: "/api/jobs/{job_id}/result";
      readonly preview: "/api/jobs/{job_id}/preview";
    };
  };
  readonly modes: readonly H3Mode[];
  readonly styles: readonly H3Style[];
  readonly frames: {
    readonly frameRate: number;
    readonly allowed: readonly H3FrameCount[];
    readonly durationsSeconds: readonly number[];
  };
  readonly resolution: {
    readonly minimum: number;
    readonly maximum: number;
    readonly multiple: number;
  };
  readonly steps: {
    readonly minimum: number;
    readonly maximum: number;
    readonly default: number;
  };
  readonly seed: {
    readonly minimum: number;
    readonly maximumSafeInteger: number;
    readonly rendererMaximumDecimal: string;
    readonly default: number;
  };
  readonly references: {
    readonly total: number;
    readonly image: number;
    readonly video: number;
    readonly audio: number;
    readonly maximumFileBytes: number;
    readonly maximumRequestBytes: number;
    readonly audioRequiresVisualReference: true;
  };
  readonly promptProcessingModes: readonly H3PromptProcessingMode[];
  readonly accelerationPresets: readonly H3AccelerationPreset[];
  readonly referenceImageSizes: readonly H3ReferenceImageSize[];
  readonly standaloneAudioPolicies: readonly H3StandaloneAudioPolicy[];
  readonly output: {
    readonly mediaType: "video/mp4";
    readonly frameRate: number;
    readonly audioSampleRate: number;
    readonly audioChannels: number;
    readonly maximumActiveJobs: 1;
  };
}
