import type { EntityRef } from "./types";

export function entityRefKey(ref: EntityRef): string {
  return `${ref.kind}:${ref.id}`;
}

export function sameEntityRef(left: EntityRef, right: EntityRef): boolean {
  return left.kind === right.kind && left.id === right.id;
}

export function compareEntityRefs(left: EntityRef, right: EntityRef): number {
  return entityRefKey(left).localeCompare(entityRefKey(right));
}
