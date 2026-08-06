import type {
  ActorId,
  ApprovalId,
  AssetId,
  CharacterId,
  ContentHash,
  EpisodeId,
  EventCursor,
  IsoDateTime,
  JobId,
  ProjectId,
  PromptId,
  RevisionNumber,
  SceneId,
  ShotId,
  TakeId,
  WorkspaceId,
} from "./ids";

export type EntityRef =
  | { readonly kind: "project"; readonly id: ProjectId }
  | { readonly kind: "episode"; readonly id: EpisodeId }
  | { readonly kind: "scene"; readonly id: SceneId }
  | { readonly kind: "shot"; readonly id: ShotId }
  | { readonly kind: "asset"; readonly id: AssetId }
  | { readonly kind: "prompt"; readonly id: PromptId }
  | { readonly kind: "take"; readonly id: TakeId }
  | { readonly kind: "job"; readonly id: JobId }
  | { readonly kind: "approval"; readonly id: ApprovalId };

export type EntityKind = EntityRef["kind"];

export interface ActorRef {
  readonly id: ActorId;
  readonly kind: "human" | "agent" | "system";
  readonly displayName: string;
}

export interface RevisionStamp {
  /** Revision of this entity. */
  readonly revision: RevisionNumber;
  /** Project revision at which this entity revision became current. */
  readonly projectRevision: RevisionNumber;
  readonly contentHash: ContentHash;
  readonly updatedAt: IsoDateTime;
  readonly updatedBy: ActorRef;
}

export interface VersionedEntityBase<Id> {
  readonly id: Id;
  readonly createdAt: IsoDateTime;
  readonly version: RevisionStamp;
}

export interface ProjectScopedEntityBase<Id> extends VersionedEntityBase<Id> {
  readonly projectId: ProjectId;
}

export type ProjectStatus = "draft" | "active" | "on_hold" | "completed" | "archived";
export type ProjectPhase =
  | "development"
  | "preproduction"
  | "generation"
  | "postproduction"
  | "review"
  | "delivered";

export interface FrameRate {
  readonly numerator: number;
  readonly denominator: number;
  readonly dropFrame: boolean;
}

export interface FrameRange {
  readonly startFrame: number;
  readonly endFrameExclusive: number;
}

export interface DeliverySpec {
  readonly width: number;
  readonly height: number;
  readonly frameRate: FrameRate;
  readonly audioSampleRateHz: number;
  readonly colorSpace: "rec709" | "display-p3" | "rec2020";
  readonly container: "mp4" | "mov" | "webm";
  readonly videoCodec: "h264" | "h265" | "av1" | "prores";
}

export interface Project extends VersionedEntityBase<ProjectId> {
  readonly workspaceId: WorkspaceId;
  readonly name: string;
  readonly logline: string;
  readonly status: ProjectStatus;
  readonly phase: ProjectPhase;
  readonly format: "vertical_short" | "horizontal_short" | "episode" | "feature";
  readonly primaryLanguage: string;
  readonly targetEpisodeCount: number;
  readonly delivery: DeliverySpec;
  readonly episodeIds: readonly EpisodeId[];
  /** Digest of the complete project state represented by `version.projectRevision`. */
  readonly stateDigest: ContentHash;
}

export type EpisodeStatus = "outline" | "script" | "storyboard" | "production" | "review" | "locked";

export interface Episode extends ProjectScopedEntityBase<EpisodeId> {
  readonly sequence: number;
  readonly title: string;
  readonly synopsis: string;
  readonly status: EpisodeStatus;
  readonly targetDurationFrames: number;
  readonly sceneIds: readonly SceneId[];
}

export type SceneStatus = "outline" | "scripted" | "blocked" | "generating" | "review" | "locked";

export interface DialogueBlock {
  readonly id: string;
  readonly kind: "dialogue";
  readonly characterId: CharacterId;
  readonly characterName: string;
  readonly text: string;
  readonly parenthetical?: string;
}

export interface ActionBlock {
  readonly id: string;
  readonly kind: "action";
  readonly text: string;
}

export interface TransitionBlock {
  readonly id: string;
  readonly kind: "transition";
  readonly text: string;
}

export type ScriptBlock = DialogueBlock | ActionBlock | TransitionBlock;

export interface Scene extends ProjectScopedEntityBase<SceneId> {
  readonly episodeId: EpisodeId;
  readonly sequence: number;
  readonly heading: string;
  readonly synopsis: string;
  readonly storyBeat: string;
  readonly status: SceneStatus;
  readonly timeOfDay: "dawn" | "day" | "dusk" | "night" | "unspecified";
  readonly characterIds: readonly CharacterId[];
  readonly script: readonly ScriptBlock[];
  readonly shotIds: readonly ShotId[];
}

export type ShotStatus = "planned" | "prompted" | "generating" | "review" | "selected" | "locked";
export type ShotSize = "extreme_wide" | "wide" | "medium" | "close_up" | "extreme_close_up";
export type CameraMovement =
  | "locked"
  | "pan"
  | "tilt"
  | "dolly"
  | "tracking"
  | "handheld"
  | "crane"
  | "orbit";

export interface Shot extends ProjectScopedEntityBase<ShotId> {
  readonly episodeId: EpisodeId;
  readonly sceneId: SceneId;
  readonly sequence: number;
  readonly title: string;
  readonly description: string;
  readonly intent: string;
  readonly status: ShotStatus;
  readonly range: FrameRange;
  readonly shotSize: ShotSize;
  readonly cameraMovement: CameraMovement;
  readonly characterIds: readonly CharacterId[];
  readonly sourceAssetIds: readonly AssetId[];
  readonly promptIds: readonly PromptId[];
  readonly takeIds: readonly TakeId[];
  readonly selectedTakeId?: TakeId;
}

export type AssetKind = "image" | "video" | "audio" | "document";
export type AssetRole =
  | "character_reference"
  | "location_reference"
  | "style_reference"
  | "storyboard"
  | "generated_take"
  | "dialogue"
  | "music"
  | "sound_effect"
  | "script"
  | "export";
export type AssetAvailability = "uploading" | "available" | "missing" | "quarantined" | "deleted";

export interface ImageAssetMetadata {
  readonly kind: "image";
  readonly width: number;
  readonly height: number;
  readonly colorSpace?: string;
}

export interface VideoAssetMetadata {
  readonly kind: "video";
  readonly width: number;
  readonly height: number;
  readonly durationFrames: number;
  readonly frameRate: FrameRate;
  readonly codec?: string;
  readonly hasAudio: boolean;
}

export interface AudioAssetMetadata {
  readonly kind: "audio";
  readonly durationSamples: number;
  readonly sampleRateHz: number;
  readonly channels: number;
  readonly codec?: string;
}

export interface DocumentAssetMetadata {
  readonly kind: "document";
  readonly pageCount?: number;
  readonly format: "plain_text" | "markdown" | "pdf" | "json" | "other";
}

export type AssetMetadata =
  | ImageAssetMetadata
  | VideoAssetMetadata
  | AudioAssetMetadata
  | DocumentAssetMetadata;

export type AssetProvenance =
  | {
      readonly kind: "user_upload";
      readonly importedAt: IsoDateTime;
      readonly importedBy: ActorRef;
      readonly originalFileName: string;
    }
  | {
      readonly kind: "generated";
      readonly jobId: JobId;
      readonly promptId?: PromptId;
      readonly promptRevision?: RevisionNumber;
    }
  | {
      readonly kind: "derived";
      readonly sourceAssetIds: readonly AssetId[];
      readonly operation: string;
    };

export interface Asset extends ProjectScopedEntityBase<AssetId> {
  readonly name: string;
  readonly kind: AssetKind;
  readonly role: AssetRole;
  readonly availability: AssetAvailability;
  readonly uri: string;
  readonly mimeType: string;
  readonly byteSize: number;
  readonly checksum: ContentHash;
  readonly metadata: AssetMetadata;
  readonly provenance: AssetProvenance;
  readonly tags: readonly string[];
}

export type PromptPurpose =
  | "shot_generation"
  | "image_generation"
  | "dialogue_generation"
  | "music_generation"
  | "continuity_analysis"
  | "quality_review";
export type PromptStatus = "draft" | "ready" | "superseded" | "archived";
export type AudioStrategy = "h3_joint" | "tts_lipsync" | "voiceover" | "silent";

export interface PromptCompilation {
  readonly model: string;
  readonly modelRevision: string;
  readonly compiledText: string;
  readonly negativeText?: string;
  readonly seed?: number;
  readonly parameters: Readonly<Record<string, string | number | boolean>>;
  readonly compiledAt: IsoDateTime;
}

export interface Prompt extends ProjectScopedEntityBase<PromptId> {
  readonly purpose: PromptPurpose;
  readonly status: PromptStatus;
  readonly target: EntityRef;
  readonly title: string;
  readonly authoringText: string;
  readonly lockedDialogue?: string;
  readonly negativeText?: string;
  readonly audioStrategy: AudioStrategy;
  readonly referenceAssetIds: readonly AssetId[];
  readonly compilation: PromptCompilation;
}

export type StaleSeverity = "info" | "warning" | "blocking";
export type KnownStaleReasonCode =
  | "upstream_changed"
  | "script_changed"
  | "prompt_changed"
  | "asset_changed"
  | "model_changed"
  | "selection_changed"
  | "approval_revoked";
export type StaleReasonCode = KnownStaleReasonCode | (string & {});

export interface StaleReason {
  readonly code: StaleReasonCode;
  /** Original changed entity that initiated propagation. */
  readonly root: EntityRef;
  /** Immediate upstream entity for this dependency hop. */
  readonly source: EntityRef;
  /** Traversal from the root change to the stale entity, inclusive. */
  readonly path: readonly EntityRef[];
  readonly sourceRevision: RevisionNumber;
  readonly detectedAtProjectRevision: RevisionNumber;
  readonly severity: StaleSeverity;
  readonly message: string;
}

export type Freshness =
  | { readonly state: "fresh" }
  | {
      readonly state: "stale";
      readonly staleSinceProjectRevision: RevisionNumber;
      readonly blocking: boolean;
      readonly reasons: readonly StaleReason[];
    };

export type TakeStatus = "queued" | "generating" | "ready" | "failed" | "rejected" | "archived";
export type TakeSelection = "unselected" | "candidate" | "selected";

export interface TakeQuality {
  readonly overall?: number;
  readonly identityConsistency?: number;
  readonly motionCoherence?: number;
  readonly dialogueAlignment?: number;
  readonly reviewerNotes?: string;
}

export interface Take extends ProjectScopedEntityBase<TakeId> {
  readonly episodeId: EpisodeId;
  readonly sceneId: SceneId;
  readonly shotId: ShotId;
  readonly label: string;
  readonly status: TakeStatus;
  readonly selection: TakeSelection;
  readonly promptId: PromptId;
  readonly promptRevision: RevisionNumber;
  readonly generationSpecHash: ContentHash;
  readonly jobId: JobId;
  readonly videoAssetId?: AssetId;
  readonly audioAssetId?: AssetId;
  readonly thumbnailAssetId?: AssetId;
  readonly durationFrames?: number;
  readonly quality: TakeQuality;
  readonly freshness: Freshness;
}

export type JobKind =
  | "h3_video_generation"
  | "image_generation"
  | "audio_generation"
  | "continuity_analysis"
  | "quality_analysis"
  | "render"
  | "export";
export type JobStatus =
  | "queued"
  | "running"
  | "waiting_for_input"
  | "succeeded"
  | "failed"
  | "cancelled";

export interface JobInputRevision {
  readonly ref: EntityRef;
  readonly revision: RevisionNumber;
  readonly contentHash: ContentHash;
}

export interface JobProgress {
  readonly completedUnits: number;
  readonly totalUnits: number;
  readonly phase: string;
  readonly message?: string;
  readonly updatedAt: IsoDateTime;
}

export interface JobError {
  readonly code: string;
  readonly message: string;
  readonly retryable: boolean;
  readonly details?: Readonly<Record<string, string | number | boolean>>;
}

export interface JobResourceUsage {
  readonly gpuModel?: string;
  readonly peakVramBytes?: number;
  readonly elapsedMilliseconds?: number;
  readonly energyWattHours?: number;
}

export interface Job extends ProjectScopedEntityBase<JobId> {
  readonly kind: JobKind;
  readonly status: JobStatus;
  readonly target: EntityRef;
  readonly dependsOnJobIds: readonly JobId[];
  readonly inputs: readonly JobInputRevision[];
  readonly outputAssetIds: readonly AssetId[];
  readonly idempotencyKey: string;
  readonly priority: number;
  readonly progress: JobProgress;
  readonly submittedAt: IsoDateTime;
  readonly startedAt?: IsoDateTime;
  readonly finishedAt?: IsoDateTime;
  readonly attempt: number;
  readonly maxAttempts: number;
  readonly error?: JobError;
  readonly resourceUsage?: JobResourceUsage;
}

export type ApprovalGate =
  | "story"
  | "script"
  | "character_bible"
  | "storyboard"
  | "take"
  | "timeline"
  | "export";

interface ApprovalBase extends ProjectScopedEntityBase<ApprovalId> {
  readonly gate: ApprovalGate;
  readonly target: EntityRef;
  readonly targetRevision: RevisionNumber;
  readonly requestedAt: IsoDateTime;
  readonly requestedBy: ActorRef;
  readonly summary: string;
  readonly freshness: Freshness;
}

export interface PendingApproval extends ApprovalBase {
  readonly status: "pending";
}

export interface DecidedApproval extends ApprovalBase {
  readonly status: "approved" | "changes_requested" | "revoked";
  readonly decidedAt: IsoDateTime;
  readonly decidedBy: ActorRef;
  readonly note?: string;
}

export type ApprovalRecord = PendingApproval | DecidedApproval;

export interface ProjectEntityCollections {
  readonly episodes: readonly Episode[];
  readonly scenes: readonly Scene[];
  readonly shots: readonly Shot[];
  readonly assets: readonly Asset[];
  readonly prompts: readonly Prompt[];
  readonly takes: readonly Take[];
  readonly jobs: readonly Job[];
}

export interface ProjectContextFocus {
  readonly activeEpisodeId?: EpisodeId;
  readonly activeSceneId?: SceneId;
  readonly activeShotId?: ShotId;
  readonly selectedRefs: readonly EntityRef[];
}

export type ProjectCapability =
  | "edit_story"
  | "edit_prompts"
  | "generate_h3"
  | "review_takes"
  | "approve"
  | "render"
  | "export";

export interface ContextWarning {
  readonly code: string;
  readonly severity: "info" | "warning" | "error";
  readonly message: string;
  readonly ref?: EntityRef;
}

/**
 * Atomic read model handed to an agent. Every included entity is from exactly
 * `snapshotProjectRevision`; callers must reject partial mixes of revisions.
 */
export interface ProjectContextEnvelope {
  readonly protocol: "h3-story-studio/project-context";
  readonly schemaVersion: 1;
  readonly capturedAt: IsoDateTime;
  readonly workspaceId: WorkspaceId;
  readonly snapshotProjectRevision: RevisionNumber;
  readonly stateDigest: ContentHash;
  readonly eventCursor: EventCursor;
  readonly project: Project;
  readonly focus: ProjectContextFocus;
  readonly entities: ProjectEntityCollections;
  readonly approvals: readonly ApprovalRecord[];
  readonly capabilities: readonly ProjectCapability[];
  readonly warnings: readonly ContextWarning[];
}

export interface StatusCount<Status extends string> {
  readonly status: Status;
  readonly count: number;
}

export interface ProjectContextSummary {
  readonly projectId: ProjectId;
  readonly projectRevision: RevisionNumber;
  readonly title: string;
  readonly phase: ProjectPhase;
  readonly episodeCount: number;
  readonly sceneCount: number;
  readonly shotCount: number;
  readonly takeCount: number;
  readonly selectedTakeCount: number;
  readonly selectedFreshTakeCount: number;
  readonly shotsWithoutSelectedTake: number;
  readonly staleEntityCount: number;
  readonly blockingStaleEntityCount: number;
  readonly pendingApprovalCount: number;
  readonly activeJobCount: number;
  readonly failedJobCount: number;
  readonly approvalsByGate: readonly StatusCount<ApprovalGate>[];
  readonly jobsByStatus: readonly StatusCount<JobStatus>[];
  readonly blockers: readonly string[];
  readonly recommendedNextAction: string;
  readonly conciseText: string;
}

/** Compact, serializable state transfer between cooperating agents. */
export interface HandoffCapsule {
  readonly protocol: "h3-story-studio/handoff";
  readonly schemaVersion: 1;
  readonly createdAt: IsoDateTime;
  readonly projectId: ProjectId;
  readonly projectRevision: RevisionNumber;
  readonly stateDigest: ContentHash;
  readonly eventCursor: EventCursor;
  readonly focus: ProjectContextFocus;
  readonly summary: ProjectContextSummary;
  readonly keyRefs: readonly EntityRef[];
  readonly pendingApprovalIds: readonly ApprovalId[];
  readonly activeJobIds: readonly JobId[];
  readonly blockers: readonly string[];
  readonly warnings: readonly ContextWarning[];
}

export interface DependencyEdge {
  /** If this entity changes, `target` may become stale. */
  readonly source: EntityRef;
  readonly target: EntityRef;
  readonly reasonCode: StaleReasonCode;
  readonly severity: StaleSeverity;
  readonly propagate: boolean;
  readonly message?: string;
}

export interface ChangedEntity {
  readonly ref: EntityRef;
  readonly revision: RevisionNumber;
  readonly projectRevision: RevisionNumber;
  readonly changeKind: string;
}

export interface EntityFreshnessRecord {
  readonly ref: EntityRef;
  readonly freshness: Freshness;
}

export interface StalePropagationInput {
  readonly changes: readonly ChangedEntity[];
  readonly dependencies: readonly DependencyEdge[];
  readonly current: readonly EntityFreshnessRecord[];
}

export interface StalePropagationResult {
  readonly affected: readonly EntityFreshnessRecord[];
  readonly unchanged: readonly EntityFreshnessRecord[];
  readonly warnings: readonly string[];
}

export interface EntityRevisionPrecondition {
  readonly ref: EntityRef;
  readonly expectedRevision: RevisionNumber;
  readonly expectedContentHash?: ContentHash;
}

export interface CurrentEntityRevision {
  readonly ref: EntityRef;
  readonly revision: RevisionNumber;
  readonly contentHash: ContentHash;
}

export interface RevisionValidationInput {
  readonly expectedProjectRevision: RevisionNumber;
  readonly actualProjectRevision: RevisionNumber;
  readonly preconditions: readonly EntityRevisionPrecondition[];
  readonly current: readonly CurrentEntityRevision[];
}

export type RevisionConflict =
  | {
      readonly kind: "project_revision_mismatch";
      readonly expected: RevisionNumber;
      readonly actual: RevisionNumber;
    }
  | {
      readonly kind: "entity_missing";
      readonly ref: EntityRef;
      readonly expectedRevision: RevisionNumber;
    }
  | {
      readonly kind: "entity_revision_mismatch";
      readonly ref: EntityRef;
      readonly expected: RevisionNumber;
      readonly actual: RevisionNumber;
    }
  | {
      readonly kind: "entity_content_hash_mismatch";
      readonly ref: EntityRef;
      readonly expected: ContentHash;
      readonly actual: ContentHash;
    }
  | {
      readonly kind: "duplicate_precondition" | "duplicate_current_entity";
      readonly ref: EntityRef;
    };

export type RevisionValidationResult =
  | {
      readonly ok: true;
      readonly validatedProjectRevision: RevisionNumber;
      readonly validatedEntityCount: number;
    }
  | {
      readonly ok: false;
      readonly conflicts: readonly RevisionConflict[];
    };
