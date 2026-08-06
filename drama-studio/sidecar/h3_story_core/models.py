"""Dependency-free data contracts for the local Project Core repository."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Mapping


JsonObject = Mapping[str, Any]
EntityOperation = Literal["upsert", "delete"]
ApprovalStatus = Literal["pending", "approved", "changes_requested", "revoked"]


@dataclass(frozen=True, slots=True)
class EntitySeed:
    entity_type: str
    entity_id: str
    payload: JsonObject


@dataclass(frozen=True, slots=True)
class ApprovalSeed:
    approval_id: str
    target_type: str
    target_id: str
    status: ApprovalStatus = "pending"
    payload: JsonObject = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProjectManifest:
    project_id: str
    name: str
    # Interpreted relative to the repository's local root. The repository
    # rejects absolute paths and traversal components.
    project_root: str | Path
    focus: JsonObject = field(default_factory=dict)
    metadata: JsonObject = field(default_factory=dict)
    entities: tuple[EntitySeed, ...] = ()
    approvals: tuple[ApprovalSeed, ...] = ()


@dataclass(frozen=True, slots=True)
class ProjectCreateSpec:
    title: str
    code: str
    subtitle: str
    episode_title: str
    scene_title: str
    shot_title: str
    logline: str
    summary: str


@dataclass(frozen=True, slots=True)
class ProjectCreateResult:
    project_id: str
    project_root: str
    code: str
    title: str
    revision: int
    digest: str
    focus: JsonObject
    metadata: JsonObject

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "project_root": self.project_root,
            "code": self.code,
            "title": self.title,
            "revision": self.revision,
            "digest": self.digest,
            "focus": dict(self.focus),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class EntityMutation:
    entity_type: str
    entity_id: str
    payload: JsonObject | None = None
    operation: EntityOperation = "upsert"


@dataclass(frozen=True, slots=True)
class ApprovalMutation:
    approval_id: str
    target_type: str
    target_id: str
    status: ApprovalStatus
    payload: JsonObject = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProjectPatch:
    entities: tuple[EntityMutation, ...] = ()
    approvals: tuple[ApprovalMutation, ...] = ()
    focus: JsonObject | None = None
    message: str = "Project patch"


@dataclass(frozen=True, slots=True)
class EntityState:
    entity_type: str
    entity_id: str
    revision: int
    payload: JsonObject

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "revision": self.revision,
            "payload": dict(self.payload),
        }


@dataclass(frozen=True, slots=True)
class ApprovalState:
    approval_id: str
    target_type: str
    target_id: str
    status: ApprovalStatus
    revision: int
    payload: JsonObject

    def to_dict(self) -> dict[str, Any]:
        return {
            "approval_id": self.approval_id,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "status": self.status,
            "revision": self.revision,
            "payload": dict(self.payload),
        }


@dataclass(frozen=True, slots=True)
class SessionBinding:
    session_id: str
    project_id: str
    agent_id: str
    revision: int
    digest: str
    focus: JsonObject
    activated_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "project_id": self.project_id,
            "agent_id": self.agent_id,
            "revision": self.revision,
            "digest": self.digest,
            "focus": dict(self.focus),
            "activated_at": self.activated_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True, slots=True)
class ProjectSummary:
    project_id: str
    name: str
    project_root: str
    revision: int
    digest: str
    focus: JsonObject
    created_at: str
    updated_at: str
    metadata: JsonObject = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "name": self.name,
            "project_root": self.project_root,
            "revision": self.revision,
            "digest": self.digest,
            "focus": dict(self.focus),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class ProjectContext:
    project_id: str
    project_name: str
    project_root: str
    revision: int
    digest: str
    focus: JsonObject
    entities: tuple[EntityState, ...]
    approvals: tuple[ApprovalState, ...]
    current_project_revision: int
    is_current: bool
    session_id: str | None = None
    agent_id: str | None = None
    metadata: JsonObject = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "project_name": self.project_name,
            "project_root": self.project_root,
            "revision": self.revision,
            "digest": self.digest,
            "focus": dict(self.focus),
            "entities": [entity.to_dict() for entity in self.entities],
            "approvals": [approval.to_dict() for approval in self.approvals],
            "current_project_revision": self.current_project_revision,
            "is_current": self.is_current,
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class CommitResult:
    project_id: str
    session_id: str
    previous_revision: int
    revision: int
    digest: str
    focus: JsonObject
    entity_change_count: int
    approval_change_count: int
    committed_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "session_id": self.session_id,
            "previous_revision": self.previous_revision,
            "revision": self.revision,
            "digest": self.digest,
            "focus": dict(self.focus),
            "entity_change_count": self.entity_change_count,
            "approval_change_count": self.approval_change_count,
            "committed_at": self.committed_at,
        }


@dataclass(frozen=True, slots=True)
class HandoffCapsule:
    handoff_id: str
    project_id: str
    project_name: str
    project_root: str
    session_id: str
    agent_id: str
    revision: int
    digest: str
    focus: JsonObject
    entities: tuple[EntityState, ...]
    approvals: tuple[ApprovalState, ...]
    created_at: str
    relative_path: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol": "h3-story-studio/handoff",
            "schema_version": 1,
            "handoff_id": self.handoff_id,
            "project_id": self.project_id,
            "project_name": self.project_name,
            "project_root": self.project_root,
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "revision": self.revision,
            "digest": self.digest,
            "focus": dict(self.focus),
            "entities": [entity.to_dict() for entity in self.entities],
            "approvals": [approval.to_dict() for approval in self.approvals],
            "created_at": self.created_at,
            "relative_path": self.relative_path,
        }


@dataclass(frozen=True, slots=True)
class AuditEvent:
    event_id: int
    project_id: str
    session_id: str | None
    actor_id: str
    event_type: str
    revision: int
    payload: JsonObject
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "project_id": self.project_id,
            "session_id": self.session_id,
            "actor_id": self.actor_id,
            "event_type": self.event_type,
            "revision": self.revision,
            "payload": dict(self.payload),
            "created_at": self.created_at,
        }
