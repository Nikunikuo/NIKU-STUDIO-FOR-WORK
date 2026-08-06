import type { IsoDateTime } from "./ids";
import { entityRefKey } from "./refs";
import type {
  ApprovalGate,
  EntityRef,
  HandoffCapsule,
  Job,
  JobStatus,
  ProjectContextEnvelope,
  ProjectContextSummary,
  StatusCount,
} from "./types";

const ACTIVE_JOB_STATUSES: ReadonlySet<JobStatus> = new Set([
  "queued",
  "running",
  "waiting_for_input",
]);

const APPROVAL_GATE_ORDER: readonly ApprovalGate[] = [
  "story",
  "script",
  "character_bible",
  "storyboard",
  "take",
  "timeline",
  "export",
];

const JOB_STATUS_ORDER: readonly JobStatus[] = [
  "queued",
  "running",
  "waiting_for_input",
  "succeeded",
  "failed",
  "cancelled",
];

function countByKnownStatus<Status extends string>(
  values: readonly Status[],
  order: readonly Status[],
): readonly StatusCount<Status>[] {
  const counts = new Map<Status, number>();

  for (const value of values) {
    counts.set(value, (counts.get(value) ?? 0) + 1);
  }

  return order
    .map((status) => ({ status, count: counts.get(status) ?? 0 }))
    .filter(({ count }) => count > 0);
}

function isActiveJob(job: Job): boolean {
  return ACTIVE_JOB_STATUSES.has(job.status);
}

function collectBlockers(envelope: ProjectContextEnvelope): readonly string[] {
  const blockers: string[] = [];

  for (const take of envelope.entities.takes) {
    if (take.freshness.state === "stale" && take.freshness.blocking) {
      blockers.push(`${take.label}: ${take.freshness.reasons[0]?.message ?? "生成結果が古くなっています"}`);
    }
  }

  for (const approval of envelope.approvals) {
    if (approval.freshness.state === "stale" && approval.freshness.blocking) {
      blockers.push(`承認 ${approval.gate}: 承認対象が更新されています`);
    }

    if (approval.status === "changes_requested") {
      blockers.push(`承認 ${approval.gate}: 修正依頼があります${approval.note ? `（${approval.note}）` : ""}`);
    }
  }

  for (const job of envelope.entities.jobs) {
    if (job.status === "failed") {
      blockers.push(`${job.kind}: ${job.error?.message ?? "ジョブが失敗しました"}`);
    }
  }

  for (const warning of envelope.warnings) {
    if (warning.severity === "error") {
      blockers.push(warning.message);
    }
  }

  return [...new Set(blockers)].sort((left, right) => left.localeCompare(right, "ja"));
}

function recommendNextAction(input: {
  readonly blockingStaleEntityCount: number;
  readonly failedJobCount: number;
  readonly pendingApprovalCount: number;
  readonly activeJobCount: number;
  readonly shotsWithoutSelectedTake: number;
  readonly projectPhase: ProjectContextEnvelope["project"]["phase"];
}): string {
  if (input.blockingStaleEntityCount > 0) {
    return "上流変更で古くなった生成結果を再生成し、承認を取り直す";
  }

  if (input.failedJobCount > 0) {
    return "失敗したジョブの原因を確認し、修正後に再実行する";
  }

  if (input.pendingApprovalCount > 0) {
    return "保留中のレビューを開き、承認または修正依頼を行う";
  }

  if (input.activeJobCount > 0) {
    return "実行中の生成ジョブを監視し、完了したテイクをレビューする";
  }

  if (input.shotsWithoutSelectedTake > 0) {
    return "未選択ショットのテイクを生成または比較し、採用テイクを選ぶ";
  }

  if (input.projectPhase === "review") {
    return "タイムラインを最終確認し、書き出し承認へ進む";
  }

  if (input.projectPhase === "delivered") {
    return "納品物と再現性メタデータをアーカイブする";
  }

  return "次のシーンの制作を開始する";
}

export function summarizeProjectContext(envelope: ProjectContextEnvelope): ProjectContextSummary {
  const takes = envelope.entities.takes;
  const selectedTakes = takes.filter((take) => take.selection === "selected");
  const selectedFreshTakes = selectedTakes.filter((take) => take.freshness.state === "fresh");
  const staleTakes = takes.filter((take) => take.freshness.state === "stale");
  const staleApprovals = envelope.approvals.filter(
    (approval) => approval.freshness.state === "stale",
  );
  const blockingStaleEntityCount = [...staleTakes, ...staleApprovals].filter(
    (entity) => entity.freshness.state === "stale" && entity.freshness.blocking,
  ).length;
  const pendingApprovals = envelope.approvals.filter((approval) => approval.status === "pending");
  const activeJobs = envelope.entities.jobs.filter(isActiveJob);
  const failedJobs = envelope.entities.jobs.filter((job) => job.status === "failed");
  const shotsWithoutSelectedTake = envelope.entities.shots.filter(
    (shot) => shot.selectedTakeId === undefined,
  ).length;
  const blockers = collectBlockers(envelope);
  const recommendedNextAction = recommendNextAction({
    blockingStaleEntityCount,
    failedJobCount: failedJobs.length,
    pendingApprovalCount: pendingApprovals.length,
    activeJobCount: activeJobs.length,
    shotsWithoutSelectedTake,
    projectPhase: envelope.project.phase,
  });

  return {
    projectId: envelope.project.id,
    projectRevision: envelope.snapshotProjectRevision,
    title: envelope.project.name,
    phase: envelope.project.phase,
    episodeCount: envelope.entities.episodes.length,
    sceneCount: envelope.entities.scenes.length,
    shotCount: envelope.entities.shots.length,
    takeCount: takes.length,
    selectedTakeCount: selectedTakes.length,
    selectedFreshTakeCount: selectedFreshTakes.length,
    shotsWithoutSelectedTake,
    staleEntityCount: staleTakes.length + staleApprovals.length,
    blockingStaleEntityCount,
    pendingApprovalCount: pendingApprovals.length,
    activeJobCount: activeJobs.length,
    failedJobCount: failedJobs.length,
    approvalsByGate: countByKnownStatus(
      envelope.approvals.map((approval) => approval.gate),
      APPROVAL_GATE_ORDER,
    ),
    jobsByStatus: countByKnownStatus(
      envelope.entities.jobs.map((job) => job.status),
      JOB_STATUS_ORDER,
    ),
    blockers,
    recommendedNextAction,
    conciseText: `${envelope.project.name}（r${envelope.snapshotProjectRevision}）: ${envelope.entities.shots.length}ショット中${selectedFreshTakes.length}件の採用テイクが最新。保留承認${pendingApprovals.length}件、稼働ジョブ${activeJobs.length}件、ブロッカー${blockers.length}件。次: ${recommendedNextAction}`,
  };
}

function uniqueRefs(refs: readonly EntityRef[]): readonly EntityRef[] {
  const byKey = new Map<string, EntityRef>();

  for (const ref of refs) {
    byKey.set(entityRefKey(ref), ref);
  }

  return [...byKey.values()].sort((left, right) =>
    entityRefKey(left).localeCompare(entityRefKey(right)),
  );
}

export function createHandoffCapsule(
  envelope: ProjectContextEnvelope,
  createdAt: IsoDateTime = envelope.capturedAt,
): HandoffCapsule {
  const summary = summarizeProjectContext(envelope);
  const pendingApprovals = envelope.approvals.filter((approval) => approval.status === "pending");
  const activeJobs = envelope.entities.jobs.filter(isActiveJob);
  const focusRefs: EntityRef[] = [...envelope.focus.selectedRefs];

  if (envelope.focus.activeEpisodeId !== undefined) {
    focusRefs.push({ kind: "episode", id: envelope.focus.activeEpisodeId });
  }
  if (envelope.focus.activeSceneId !== undefined) {
    focusRefs.push({ kind: "scene", id: envelope.focus.activeSceneId });
  }
  if (envelope.focus.activeShotId !== undefined) {
    focusRefs.push({ kind: "shot", id: envelope.focus.activeShotId });
  }

  return {
    protocol: "h3-story-studio/handoff",
    schemaVersion: 1,
    createdAt,
    projectId: envelope.project.id,
    projectRevision: envelope.snapshotProjectRevision,
    stateDigest: envelope.stateDigest,
    eventCursor: envelope.eventCursor,
    focus: envelope.focus,
    summary,
    keyRefs: uniqueRefs([
      ...focusRefs,
      ...pendingApprovals.map((approval) => approval.target),
      ...activeJobs.map((job) => job.target),
    ]),
    pendingApprovalIds: pendingApprovals.map((approval) => approval.id),
    activeJobIds: activeJobs.map((job) => job.id),
    blockers: summary.blockers,
    warnings: envelope.warnings,
  };
}
