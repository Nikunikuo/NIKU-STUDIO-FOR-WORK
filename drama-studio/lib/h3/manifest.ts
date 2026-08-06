import {
  H3_ACCELERATION_PRESETS,
  H3_CONTRACT_SCHEMA_VERSION,
  H3_DEFAULT_ORIGIN,
  H3_FRAME_COUNTS,
  H3_FRAME_RATE,
  H3_MODES,
  H3_MUTATION_HEADER,
  H3_MUTATION_HEADER_VALUE,
  H3_OUTPUT,
  H3_PROMPT_PROCESSING_MODES,
  H3_REFERENCE_IMAGE_SIZES,
  H3_REFERENCE_LIMITS,
  H3_RESOLUTION_LIMITS,
  H3_SEED_LIMITS,
  H3_STANDALONE_AUDIO_POLICIES,
  H3_STEP_LIMITS,
  H3_STYLES,
} from "./constants";
import type { CapabilityManifest } from "./types";

/**
 * Compile-time renderer contract. Runtime availability still comes from
 * H3 Studio's `GET /api/state`; this manifest must not be treated as health.
 */
export const H3_CAPABILITY_MANIFEST = Object.freeze({
  schemaVersion: H3_CONTRACT_SCHEMA_VERSION,
  renderer: {
    id: "h3-studio",
    modelFamily: "MiniMax H3",
    transport: "local-rest",
    localOnly: true,
    defaultOrigin: H3_DEFAULT_ORIGIN,
    mutationHeader: {
      name: H3_MUTATION_HEADER,
      value: H3_MUTATION_HEADER_VALUE,
    },
    routes: {
      state: "/api/state",
      jobs: "/api/jobs",
      job: "/api/jobs/{job_id}",
      cancel: "/api/jobs/{job_id}/cancel",
      result: "/api/jobs/{job_id}/result",
      preview: "/api/jobs/{job_id}/preview",
    },
  },
  modes: H3_MODES,
  styles: H3_STYLES,
  frames: {
    frameRate: H3_FRAME_RATE,
    allowed: H3_FRAME_COUNTS,
    durationsSeconds: H3_FRAME_COUNTS.map((frames) => frames / H3_FRAME_RATE),
  },
  resolution: H3_RESOLUTION_LIMITS,
  steps: H3_STEP_LIMITS,
  seed: H3_SEED_LIMITS,
  references: {
    ...H3_REFERENCE_LIMITS,
    audioRequiresVisualReference: true,
  },
  promptProcessingModes: H3_PROMPT_PROCESSING_MODES,
  accelerationPresets: H3_ACCELERATION_PRESETS,
  referenceImageSizes: H3_REFERENCE_IMAGE_SIZES,
  standaloneAudioPolicies: H3_STANDALONE_AUDIO_POLICIES,
  output: H3_OUTPUT,
} satisfies CapabilityManifest);
