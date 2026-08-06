import type { RevisionNumber } from "./ids";
import { compareEntityRefs, entityRefKey } from "./refs";
import type {
  DependencyEdge,
  EntityFreshnessRecord,
  EntityRef,
  Freshness,
  StalePropagationInput,
  StalePropagationResult,
  StaleReason,
} from "./types";

interface TraversalState {
  readonly ref: EntityRef;
  readonly path: readonly EntityRef[];
}

function reasonKey(reason: StaleReason): string {
  return [
    entityRefKey(reason.root),
    entityRefKey(reason.source),
    reason.code,
    reason.path.map(entityRefKey).join(">"),
  ].join("|");
}

function mergeFreshness(
  current: Freshness,
  additions: readonly StaleReason[],
): Freshness {
  if (additions.length === 0) {
    return current;
  }

  const reasonsByKey = new Map<string, StaleReason>();
  if (current.state === "stale") {
    for (const reason of current.reasons) {
      reasonsByKey.set(reasonKey(reason), reason);
    }
  }
  for (const reason of additions) {
    reasonsByKey.set(reasonKey(reason), reason);
  }

  const reasons = [...reasonsByKey.values()].sort((left, right) =>
    reasonKey(left).localeCompare(reasonKey(right)),
  );
  const earliestNewRevision = additions.reduce<RevisionNumber>(
    (earliest, reason) =>
      reason.detectedAtProjectRevision < earliest ? reason.detectedAtProjectRevision : earliest,
    additions[0]!.detectedAtProjectRevision,
  );
  const staleSinceProjectRevision =
    current.state === "stale" && current.staleSinceProjectRevision < earliestNewRevision
      ? current.staleSinceProjectRevision
      : earliestNewRevision;

  return {
    state: "stale",
    staleSinceProjectRevision,
    blocking:
      (current.state === "stale" && current.blocking) ||
      additions.some((reason) => reason.severity === "blocking"),
    reasons,
  };
}

function sortEdges(edges: readonly DependencyEdge[]): readonly DependencyEdge[] {
  return [...edges].sort((left, right) => {
    const byTarget = entityRefKey(left.target).localeCompare(entityRefKey(right.target));
    if (byTarget !== 0) return byTarget;
    const byCode = left.reasonCode.localeCompare(right.reasonCode);
    if (byCode !== 0) return byCode;
    return left.severity.localeCompare(right.severity);
  });
}

/**
 * Computes transitive invalidation without mutating the dependency graph or the
 * supplied freshness records. A non-propagating edge still marks its direct
 * target stale; it only stops traversal beyond that target.
 */
export function propagateStaleState(input: StalePropagationInput): StalePropagationResult {
  const warnings: string[] = [];
  const adjacency = new Map<string, DependencyEdge[]>();

  for (const edge of input.dependencies) {
    const key = entityRefKey(edge.source);
    const list = adjacency.get(key) ?? [];
    list.push(edge);
    adjacency.set(key, list);
  }
  for (const [key, edges] of adjacency) {
    adjacency.set(key, [...sortEdges(edges)]);
  }

  const currentByKey = new Map<string, EntityFreshnessRecord>();
  for (const record of input.current) {
    const key = entityRefKey(record.ref);
    if (currentByKey.has(key)) {
      warnings.push(`Duplicate current freshness record ignored: ${key}`);
      continue;
    }
    currentByKey.set(key, record);
  }

  const reasonsByTarget = new Map<string, { ref: EntityRef; reasons: StaleReason[] }>();
  const sortedChanges = [...input.changes].sort((left, right) =>
    entityRefKey(left.ref).localeCompare(entityRefKey(right.ref)),
  );

  for (const change of sortedChanges) {
    const rootKey = entityRefKey(change.ref);
    const expanded = new Set<string>([rootKey]);
    const queue: TraversalState[] = [{ ref: change.ref, path: [change.ref] }];

    for (let cursor = 0; cursor < queue.length; cursor += 1) {
      const state = queue[cursor]!;
      const edges = adjacency.get(entityRefKey(state.ref)) ?? [];

      for (const edge of edges) {
        const targetKey = entityRefKey(edge.target);
        if (targetKey === rootKey) {
          warnings.push(`Dependency cycle returned to changed root and was stopped: ${rootKey}`);
          continue;
        }

        const path = [...state.path, edge.target];
        const target = reasonsByTarget.get(targetKey) ?? { ref: edge.target, reasons: [] };
        target.reasons.push({
          code: edge.reasonCode,
          root: change.ref,
          source: edge.source,
          path,
          sourceRevision: change.revision,
          detectedAtProjectRevision: change.projectRevision,
          severity: edge.severity,
          message:
            edge.message ??
            `${entityRefKey(edge.source)} changed (${change.changeKind}); ${targetKey} requires review.`,
        });
        reasonsByTarget.set(targetKey, target);

        if (edge.propagate && !expanded.has(targetKey)) {
          expanded.add(targetKey);
          queue.push({ ref: edge.target, path });
        }
      }
    }
  }

  const affected: EntityFreshnessRecord[] = [];
  for (const [key, target] of reasonsByTarget) {
    const current = currentByKey.get(key);
    if (current === undefined) {
      warnings.push(`No current freshness record; assumed fresh: ${key}`);
    }

    affected.push({
      ref: target.ref,
      freshness: mergeFreshness(current?.freshness ?? { state: "fresh" }, target.reasons),
    });
  }
  affected.sort((left, right) => compareEntityRefs(left.ref, right.ref));

  const affectedKeys = new Set(affected.map((record) => entityRefKey(record.ref)));
  const unchanged = [...currentByKey.values()]
    .filter((record) => !affectedKeys.has(entityRefKey(record.ref)))
    .sort((left, right) => compareEntityRefs(left.ref, right.ref));

  return {
    affected,
    unchanged,
    warnings: [...new Set(warnings)].sort((left, right) => left.localeCompare(right)),
  };
}
