import { entityRefKey } from "./refs";
import type {
  CurrentEntityRevision,
  EntityRevisionPrecondition,
  RevisionConflict,
  RevisionValidationInput,
  RevisionValidationResult,
} from "./types";

function collectUnique<T extends { readonly ref: EntityRevisionPrecondition["ref"] }>(
  values: readonly T[],
  duplicateKind: "duplicate_precondition" | "duplicate_current_entity",
): {
  readonly unique: ReadonlyMap<string, T>;
  readonly conflicts: readonly RevisionConflict[];
} {
  const unique = new Map<string, T>();
  const duplicateKeys = new Set<string>();

  for (const value of values) {
    const key = entityRefKey(value.ref);
    if (unique.has(key)) {
      duplicateKeys.add(key);
    } else {
      unique.set(key, value);
    }
  }

  const conflicts: RevisionConflict[] = [...duplicateKeys]
    .sort((left, right) => left.localeCompare(right))
    .map((key) => ({ kind: duplicateKind, ref: unique.get(key)!.ref }));

  return { unique, conflicts };
}

export function validateRevisionConflicts(
  input: RevisionValidationInput,
): RevisionValidationResult {
  const conflicts: RevisionConflict[] = [];

  if (input.expectedProjectRevision !== input.actualProjectRevision) {
    conflicts.push({
      kind: "project_revision_mismatch",
      expected: input.expectedProjectRevision,
      actual: input.actualProjectRevision,
    });
  }

  const preconditions = collectUnique(input.preconditions, "duplicate_precondition");
  const current = collectUnique<CurrentEntityRevision>(input.current, "duplicate_current_entity");
  conflicts.push(...preconditions.conflicts, ...current.conflicts);

  for (const [key, precondition] of [...preconditions.unique.entries()].sort(([left], [right]) =>
    left.localeCompare(right),
  )) {
    const actual = current.unique.get(key);

    if (actual === undefined) {
      conflicts.push({
        kind: "entity_missing",
        ref: precondition.ref,
        expectedRevision: precondition.expectedRevision,
      });
      continue;
    }

    if (precondition.expectedRevision !== actual.revision) {
      conflicts.push({
        kind: "entity_revision_mismatch",
        ref: precondition.ref,
        expected: precondition.expectedRevision,
        actual: actual.revision,
      });
    }

    if (
      precondition.expectedContentHash !== undefined &&
      precondition.expectedContentHash !== actual.contentHash
    ) {
      conflicts.push({
        kind: "entity_content_hash_mismatch",
        ref: precondition.ref,
        expected: precondition.expectedContentHash,
        actual: actual.contentHash,
      });
    }
  }

  if (conflicts.length > 0) {
    return { ok: false, conflicts };
  }

  return {
    ok: true,
    validatedProjectRevision: input.actualProjectRevision,
    validatedEntityCount: preconditions.unique.size,
  };
}
