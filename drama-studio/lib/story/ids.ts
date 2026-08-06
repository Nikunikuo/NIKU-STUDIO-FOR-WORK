export type Brand<Value, Name extends string> = Value & {
  readonly __brand: Name;
};

export type WorkspaceId = Brand<string, "WorkspaceId">;
export type ProjectId = Brand<string, "ProjectId">;
export type EpisodeId = Brand<string, "EpisodeId">;
export type SceneId = Brand<string, "SceneId">;
export type ShotId = Brand<string, "ShotId">;
export type AssetId = Brand<string, "AssetId">;
export type PromptId = Brand<string, "PromptId">;
export type TakeId = Brand<string, "TakeId">;
export type JobId = Brand<string, "JobId">;
export type ApprovalId = Brand<string, "ApprovalId">;
export type ActorId = Brand<string, "ActorId">;
export type CharacterId = Brand<string, "CharacterId">;
export type EventCursor = Brand<string, "EventCursor">;
export type ContentHash = Brand<string, "ContentHash">;
export type IsoDateTime = Brand<string, "IsoDateTime">;
export type RevisionNumber = Brand<number, "RevisionNumber">;

function nonEmptyId<Value extends string>(value: string, label: string): Value {
  if (value.trim().length === 0) {
    throw new Error(`${label} must not be empty.`);
  }

  return value as Value;
}

export const workspaceId = (value: string): WorkspaceId =>
  nonEmptyId<WorkspaceId>(value, "WorkspaceId");
export const projectId = (value: string): ProjectId =>
  nonEmptyId<ProjectId>(value, "ProjectId");
export const episodeId = (value: string): EpisodeId =>
  nonEmptyId<EpisodeId>(value, "EpisodeId");
export const sceneId = (value: string): SceneId =>
  nonEmptyId<SceneId>(value, "SceneId");
export const shotId = (value: string): ShotId =>
  nonEmptyId<ShotId>(value, "ShotId");
export const assetId = (value: string): AssetId =>
  nonEmptyId<AssetId>(value, "AssetId");
export const promptId = (value: string): PromptId =>
  nonEmptyId<PromptId>(value, "PromptId");
export const takeId = (value: string): TakeId =>
  nonEmptyId<TakeId>(value, "TakeId");
export const jobId = (value: string): JobId =>
  nonEmptyId<JobId>(value, "JobId");
export const approvalId = (value: string): ApprovalId =>
  nonEmptyId<ApprovalId>(value, "ApprovalId");
export const actorId = (value: string): ActorId =>
  nonEmptyId<ActorId>(value, "ActorId");
export const characterId = (value: string): CharacterId =>
  nonEmptyId<CharacterId>(value, "CharacterId");
export const eventCursor = (value: string): EventCursor =>
  nonEmptyId<EventCursor>(value, "EventCursor");
export const contentHash = (value: string): ContentHash =>
  nonEmptyId<ContentHash>(value, "ContentHash");

export function isoDateTime(value: string): IsoDateTime {
  if (Number.isNaN(Date.parse(value))) {
    throw new Error(`Invalid ISO date-time: ${value}`);
  }

  return value as IsoDateTime;
}

export function revisionNumber(value: number): RevisionNumber {
  if (!Number.isSafeInteger(value) || value < 0) {
    throw new Error(`RevisionNumber must be a non-negative safe integer: ${value}`);
  }

  return value as RevisionNumber;
}
