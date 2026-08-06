"""SQLite-backed, local-only Project Core repository.

The repository deliberately owns its transaction boundary. Every mutation is
scoped to an activated session and uses an expected project revision; callers
cannot accidentally mix entities from two project snapshots.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Mapping, Sequence

from .models import (
    ApprovalMutation,
    ApprovalSeed,
    ApprovalState,
    AuditEvent,
    CommitResult,
    EntityMutation,
    EntitySeed,
    EntityState,
    HandoffCapsule,
    ProjectContext,
    ProjectCreateResult,
    ProjectCreateSpec,
    ProjectManifest,
    ProjectPatch,
    ProjectSummary,
    SessionBinding,
)


class ProjectCoreError(RuntimeError):
    """Base class for repository contract failures."""


class ValidationError(ProjectCoreError):
    """Raised when a manifest or patch violates a Project Core invariant."""


class UnsafePathError(ValidationError):
    """Raised when a path could escape its local project boundary."""


class ProjectNotFoundError(ProjectCoreError):
    def __init__(self, project_id: str) -> None:
        self.project_id = project_id
        super().__init__(f"Project not found: {project_id}")


class ProjectAlreadyExistsError(ProjectCoreError):
    def __init__(self, project_id: str) -> None:
        self.project_id = project_id
        super().__init__(f"Project already exists: {project_id}")


class ProjectCodeAlreadyExistsError(ProjectCoreError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(f"Project code already exists: {code}")


class SessionNotFoundError(ProjectCoreError):
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        super().__init__(f"Session not found: {session_id}")


class ProjectBoundaryError(ProjectCoreError):
    def __init__(
        self,
        *,
        session_id: str,
        bound_project_id: str,
        requested_project_id: str,
    ) -> None:
        self.session_id = session_id
        self.bound_project_id = bound_project_id
        self.requested_project_id = requested_project_id
        super().__init__(
            f"Session {session_id} is bound to project {bound_project_id}, "
            f"not {requested_project_id}"
        )


class RevisionConflictError(ProjectCoreError):
    """Dedicated optimistic-concurrency conflict."""

    def __init__(
        self,
        *,
        project_id: str,
        session_id: str,
        expected_revision: int,
        actual_revision: int,
        session_revision: int,
    ) -> None:
        self.project_id = project_id
        self.session_id = session_id
        self.expected_revision = expected_revision
        self.actual_revision = actual_revision
        self.session_revision = session_revision
        super().__init__(
            f"Revision conflict for {project_id}: expected {expected_revision}, "
            f"project is {actual_revision}, session is bound to {session_revision}"
        )


class StorageIntegrityError(ProjectCoreError):
    """Raised when a persisted snapshot no longer matches its digest/binding."""


_APPROVAL_STATUSES = frozenset(
    ("pending", "approved", "changes_requested", "revoked")
)
_DEFAULT_DATABASE_PATH = Path(".h3_story_core") / "project_core.sqlite3"
_SCHEMA_PATH = Path(__file__).with_name("schema.sql")
_PROJECT_CODE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise ValidationError(f"Value is not canonical JSON: {error}") from error


def _json_object(value: Mapping[str, Any], label: str) -> dict[str, Any]:
    serialized = _canonical_json(value)
    parsed = json.loads(serialized)
    if not isinstance(parsed, dict):
        raise ValidationError(f"{label} must be a JSON object")
    return parsed


def _required_text(value: str, label: str) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{label} must be a string")
    normalized = value.strip()
    if not normalized or "\x00" in normalized:
        raise ValidationError(f"{label} must be a non-empty string without NUL")
    return normalized


def _bounded_text(
    value: str,
    label: str,
    *,
    maximum: int,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str) or "\x00" in value:
        raise ValidationError(f"{label} must be a string without NUL")
    normalized = value.strip()
    if not allow_empty and not normalized:
        raise ValidationError(f"{label} must be non-empty")
    if len(normalized) > maximum:
        raise ValidationError(f"{label} exceeds {maximum} characters")
    return normalized


def _snapshot_digest(snapshot: Mapping[str, Any]) -> str:
    encoded = _canonical_json(snapshot).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _snapshot_states(
    snapshot: Mapping[str, Any],
) -> tuple[tuple[EntityState, ...], tuple[ApprovalState, ...], dict[str, Any]]:
    raw_focus = snapshot.get("focus")
    raw_entities = snapshot.get("entities")
    raw_approvals = snapshot.get("approvals")
    if not isinstance(raw_focus, dict):
        raise StorageIntegrityError("Snapshot focus is not a JSON object")
    if not isinstance(raw_entities, list) or not isinstance(raw_approvals, list):
        raise StorageIntegrityError("Snapshot entity/approval collections are invalid")

    entities: list[EntityState] = []
    for item in raw_entities:
        if not isinstance(item, dict) or not isinstance(item.get("payload"), dict):
            raise StorageIntegrityError("Snapshot contains an invalid entity")
        try:
            entities.append(
                EntityState(
                    entity_type=str(item["entity_type"]),
                    entity_id=str(item["entity_id"]),
                    revision=int(item["revision"]),
                    payload=dict(item["payload"]),
                )
            )
        except (KeyError, TypeError, ValueError) as error:
            raise StorageIntegrityError("Snapshot contains an invalid entity") from error

    approvals: list[ApprovalState] = []
    for item in raw_approvals:
        if not isinstance(item, dict) or not isinstance(item.get("payload"), dict):
            raise StorageIntegrityError("Snapshot contains an invalid approval")
        try:
            status = str(item["status"])
            if status not in _APPROVAL_STATUSES:
                raise ValueError("unknown approval status")
            approvals.append(
                ApprovalState(
                    approval_id=str(item["approval_id"]),
                    target_type=str(item["target_type"]),
                    target_id=str(item["target_id"]),
                    status=status,  # type: ignore[arg-type]
                    revision=int(item["revision"]),
                    payload=dict(item["payload"]),
                )
            )
        except (KeyError, TypeError, ValueError) as error:
            raise StorageIntegrityError("Snapshot contains an invalid approval") from error

    return tuple(entities), tuple(approvals), dict(raw_focus)


class ProjectRepository:
    def __init__(
        self,
        local_root: str | Path,
        database_relative_path: str | Path = _DEFAULT_DATABASE_PATH,
    ) -> None:
        root = Path(local_root).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        self.local_root = root
        _, database_path = self._resolve_safe_relative(
            root,
            database_relative_path,
            label="database_relative_path",
        )
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self.database_path = database_path
        self._connection = sqlite3.connect(
            database_path,
            timeout=5.0,
            isolation_level=None,
        )
        self._connection.row_factory = sqlite3.Row
        self._closed = False
        self._connection.execute("PRAGMA busy_timeout = 5000")
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA synchronous = NORMAL")
        journal_mode = str(
            self._connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
        ).lower()
        if journal_mode != "wal":
            self._connection.close()
            self._closed = True
            raise ProjectCoreError(f"SQLite refused WAL mode: {journal_mode}")
        self._connection.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
        if self._connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            self.close()
            raise ProjectCoreError("SQLite foreign key enforcement is unavailable")

    def __enter__(self) -> ProjectRepository:
        self._ensure_open()
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.close()

    def close(self) -> None:
        if not self._closed:
            if self._connection.in_transaction:
                self._connection.rollback()
            self._connection.close()
            self._closed = True

    def _ensure_open(self) -> None:
        if self._closed:
            raise ProjectCoreError("Repository is closed")

    @staticmethod
    def _resolve_safe_relative(
        base: Path,
        relative_path: str | Path,
        *,
        label: str,
    ) -> tuple[Path, Path]:
        path = Path(relative_path)
        if (
            not str(path).strip()
            or "\x00" in str(path)
            or path.is_absolute()
            or bool(path.drive)
            or bool(path.anchor)
            or any(part == ".." for part in path.parts)
        ):
            raise UnsafePathError(f"{label} must be a safe relative path: {relative_path}")
        normalized_parts = tuple(part for part in path.parts if part not in ("", "."))
        if not normalized_parts:
            raise UnsafePathError(f"{label} must not resolve to the local root")
        normalized = Path(*normalized_parts)
        resolved_base = base.resolve()
        resolved = (resolved_base / normalized).resolve()
        try:
            resolved.relative_to(resolved_base)
        except ValueError as error:
            raise UnsafePathError(
                f"{label} escapes its local root: {relative_path}"
            ) from error
        return normalized, resolved

    def _rollback(self) -> None:
        if self._connection.in_transaction:
            self._connection.rollback()

    def _now(self) -> str:
        return str(
            self._connection.execute(
                "SELECT strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"
            ).fetchone()[0]
        )

    def _new_id(self, prefix: str) -> str:
        token = str(
            self._connection.execute("SELECT lower(hex(randomblob(16)))").fetchone()[0]
        )
        return f"{prefix}_{token}"

    def storage_settings(self) -> dict[str, object]:
        self._ensure_open()
        return {
            "journal_mode": str(
                self._connection.execute("PRAGMA journal_mode").fetchone()[0]
            ).lower(),
            "foreign_keys": bool(
                self._connection.execute("PRAGMA foreign_keys").fetchone()[0]
            ),
            "user_version": int(
                self._connection.execute("PRAGMA user_version").fetchone()[0]
            ),
            "database_path": str(self.database_path),
        }

    def create_project(self, spec: ProjectCreateSpec) -> ProjectCreateResult:
        """Create a runtime project with a safe server-owned identity and root.

        The initial revision contains one empty episode/scene/shot/prompt chain
        so both the human UI and a fresh Codex session have an exact focus from
        the first activation.  ``seed_project`` owns the SQLite transaction and
        removes a newly-created empty project directory on any failure.
        """

        self._ensure_open()
        if not isinstance(spec, ProjectCreateSpec):
            raise ValidationError("spec must be a ProjectCreateSpec")
        title = _bounded_text(spec.title, "spec.title", maximum=200)
        code = _bounded_text(spec.code, "spec.code", maximum=32)
        if _PROJECT_CODE.fullmatch(code) is None:
            raise ValidationError(
                "spec.code must start with an alphanumeric character and contain "
                "only letters, numbers, underscores, or hyphens"
            )
        subtitle = _bounded_text(
            spec.subtitle, "spec.subtitle", maximum=500, allow_empty=True
        )
        episode_title = _bounded_text(
            spec.episode_title, "spec.episode_title", maximum=200
        )
        scene_title = _bounded_text(
            spec.scene_title, "spec.scene_title", maximum=200
        )
        shot_title = _bounded_text(
            spec.shot_title, "spec.shot_title", maximum=200
        )
        logline = _bounded_text(
            spec.logline, "spec.logline", maximum=2_000, allow_empty=True
        )
        summary = _bounded_text(
            spec.summary, "spec.summary", maximum=20_000, allow_empty=True
        )

        existing = self._connection.execute(
            """
            SELECT project_id FROM projects
            WHERE lower(json_extract(manifest_json, '$.metadata.code')) = lower(?)
            """,
            (code,),
        ).fetchone()
        if existing is not None:
            raise ProjectCodeAlreadyExistsError(code)

        project_id = self._new_id("prj")
        identity_token = project_id.rsplit("_", 1)[-1]
        root_slug = code.casefold().replace("_", "-")
        project_root = f"projects/{root_slug}-{identity_token[:10]}"
        episode_id = f"ep_{identity_token[:16]}"
        scene_id = f"sc_{identity_token[8:24]}"
        shot_id = f"sh_{identity_token[16:32]}"
        prompt_id = f"prompt_{identity_token[:24]}"
        focus = {
            "episode_id": episode_id,
            "scene_id": scene_id,
            "shot_id": shot_id,
            "prompt_id": prompt_id,
        }
        metadata = {
            "code": code,
            "title": title,
            "subtitle": subtitle,
            "episode_title": episode_title,
            "scene_title": scene_title,
            "shot_title": shot_title,
            "logline": logline,
            "summary": summary,
            "phase": "development",
            "progress": 0,
            "source": "runtime_create",
        }
        entities = (
            EntitySeed(
                "episode",
                episode_id,
                {
                    "code": "EP01",
                    "number": 1,
                    "title": episode_title,
                    "subtitle": subtitle,
                    "logline": logline,
                    "summary": summary,
                    "status": "outline",
                    "scene_ids": [scene_id],
                },
            ),
            EntitySeed(
                "scene",
                scene_id,
                {
                    "code": "SC01",
                    "number": 1,
                    "episode_id": episode_id,
                    "title": scene_title,
                    "summary": summary,
                    "status": "outline",
                    "script": [],
                    "shot_ids": [shot_id],
                },
            ),
            EntitySeed(
                "shot",
                shot_id,
                {
                    "code": "SH001",
                    "label": "Shot 01",
                    "title": shot_title,
                    "status": "draft",
                    "order": 1,
                    "episode_id": episode_id,
                    "scene_id": scene_id,
                    "description": "",
                    "intent": "",
                    "acting": "",
                    "duration_frames": 124,
                    "camera": "locked",
                },
            ),
            EntitySeed(
                "prompt",
                prompt_id,
                {
                    "code": "PROMPT-SH001",
                    "prompt": "",
                    "mode": "t2v",
                    "status": "draft",
                    "episode_id": episode_id,
                    "scene_id": scene_id,
                    "shot_id": shot_id,
                    "reference_asset_ids": [],
                },
            ),
        )
        manifest = ProjectManifest(
            project_id=project_id,
            name=title,
            project_root=project_root,
            focus=focus,
            metadata=metadata,
            entities=entities,
        )
        try:
            context = self.seed_project(
                manifest,
                actor_id="human:web-ui",
                event_type="project_created",
                message="Runtime project creation",
                patch_kind="runtime_create",
            )
        except sqlite3.IntegrityError as error:
            if "idx_projects_metadata_code_unique" in str(error) or (
                "UNIQUE constraint failed: index" in str(error)
                and "metadata_code" in str(error)
            ):
                raise ProjectCodeAlreadyExistsError(code) from error
            raise
        return ProjectCreateResult(
            project_id=context.project_id,
            project_root=context.project_root,
            code=code,
            title=title,
            revision=context.revision,
            digest=context.digest,
            focus=context.focus,
            metadata=context.metadata,
        )

    def seed_project(
        self,
        manifest: ProjectManifest,
        *,
        actor_id: str = "system:manifest-seed",
        event_type: str = "project_seeded",
        message: str = "Project manifest seed",
        patch_kind: str = "manifest_seed",
    ) -> ProjectContext:
        self._ensure_open()
        actor_id = _required_text(actor_id, "actor_id")
        event_type = _required_text(event_type, "event_type")
        message = _required_text(message, "message")
        patch_kind = _required_text(patch_kind, "patch_kind")
        project_id = _required_text(manifest.project_id, "manifest.project_id")
        name = _required_text(manifest.name, "manifest.name")
        relative_root, project_directory = self._resolve_safe_relative(
            self.local_root,
            manifest.project_root,
            label="manifest.project_root",
        )
        project_root = relative_root.as_posix()
        focus = _json_object(manifest.focus, "manifest.focus")
        metadata = _json_object(manifest.metadata, "manifest.metadata")
        entities = self._seed_entities(manifest.entities)
        approvals = self._seed_approvals(project_id, manifest.approvals, entities)
        snapshot = {
            "project_id": project_id,
            "focus": focus,
            "entities": list(entities.values()),
            "approvals": list(approvals.values()),
        }
        snapshot["entities"].sort(
            key=lambda item: (item["entity_type"], item["entity_id"])
        )
        snapshot["approvals"].sort(key=lambda item: item["approval_id"])
        digest = _snapshot_digest(snapshot)
        manifest_document = {
            "project_id": project_id,
            "name": name,
            "project_root": project_root,
            "focus": focus,
            "metadata": metadata,
            "entities": [
                {
                    "entity_type": item["entity_type"],
                    "entity_id": item["entity_id"],
                    "payload": item["payload"],
                }
                for item in snapshot["entities"]
            ],
            "approvals": [
                {
                    "approval_id": item["approval_id"],
                    "target_type": item["target_type"],
                    "target_id": item["target_id"],
                    "status": item["status"],
                    "payload": item["payload"],
                }
                for item in snapshot["approvals"]
            ],
        }

        project_directory_created = False
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            if self._connection.execute(
                "SELECT 1 FROM projects WHERE project_id = ?", (project_id,)
            ).fetchone():
                raise ProjectAlreadyExistsError(project_id)
            if not project_directory.exists():
                project_directory.mkdir(parents=True, exist_ok=False)
                project_directory_created = True
            elif not project_directory.is_dir():
                raise UnsafePathError(
                    f"manifest.project_root is not a directory: {project_root}"
                )
            now = self._now()
            self._connection.execute(
                """
                INSERT INTO projects (
                    project_id, name, project_root, current_revision,
                    current_digest, focus_json, manifest_json, created_at, updated_at
                ) VALUES (?, ?, ?, 0, ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    name,
                    project_root,
                    digest,
                    _canonical_json(focus),
                    _canonical_json(manifest_document),
                    now,
                    now,
                ),
            )
            self._connection.execute(
                """
                INSERT INTO revisions (
                    project_id, revision, parent_revision, digest, focus_json,
                    snapshot_json, patch_json, session_id, actor_id, message, created_at
                ) VALUES (?, 0, NULL, ?, ?, ?, ?, NULL, ?, ?, ?)
                """,
                (
                    project_id,
                    digest,
                    _canonical_json(focus),
                    _canonical_json(snapshot),
                    _canonical_json({"kind": patch_kind}),
                    actor_id,
                    message,
                    now,
                ),
            )
            for item in snapshot["entities"]:
                self._connection.execute(
                    """
                    INSERT INTO entities (
                        project_id, entity_type, entity_id, revision, payload_json, updated_at
                    ) VALUES (?, ?, ?, 0, ?, ?)
                    """,
                    (
                        project_id,
                        item["entity_type"],
                        item["entity_id"],
                        _canonical_json(item["payload"]),
                        now,
                    ),
                )
            for item in snapshot["approvals"]:
                self._connection.execute(
                    """
                    INSERT INTO approvals (
                        project_id, approval_id, target_type, target_id,
                        status, revision, payload_json, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 0, ?, ?)
                    """,
                    (
                        project_id,
                        item["approval_id"],
                        item["target_type"],
                        item["target_id"],
                        item["status"],
                        _canonical_json(item["payload"]),
                        now,
                    ),
                )
            self._insert_audit(
                project_id=project_id,
                session_id=None,
                actor_id=actor_id,
                event_type=event_type,
                revision=0,
                payload={"digest": digest, "project_root": project_root},
                created_at=now,
            )
            self._connection.commit()
        except Exception:
            self._rollback()
            if project_directory_created:
                try:
                    project_directory.rmdir()
                except OSError:
                    pass
            raise

        entity_states, approval_states, snapshot_focus = _snapshot_states(snapshot)
        return ProjectContext(
            project_id=project_id,
            project_name=name,
            project_root=project_root,
            revision=0,
            digest=digest,
            focus=snapshot_focus,
            entities=entity_states,
            approvals=approval_states,
            current_project_revision=0,
            is_current=True,
            metadata=metadata,
        )

    def _seed_entities(
        self, seeds: Sequence[EntitySeed]
    ) -> dict[tuple[str, str], dict[str, Any]]:
        entities: dict[tuple[str, str], dict[str, Any]] = {}
        for index, seed in enumerate(seeds):
            entity_type = _required_text(
                seed.entity_type, f"manifest.entities[{index}].entity_type"
            )
            entity_id = _required_text(
                seed.entity_id, f"manifest.entities[{index}].entity_id"
            )
            key = (entity_type, entity_id)
            if key in entities:
                raise ValidationError(f"Duplicate manifest entity: {entity_type}:{entity_id}")
            entities[key] = {
                "entity_type": entity_type,
                "entity_id": entity_id,
                "revision": 0,
                "payload": _json_object(
                    seed.payload, f"manifest.entities[{index}].payload"
                ),
            }
        return entities

    def _seed_approvals(
        self,
        project_id: str,
        seeds: Sequence[ApprovalSeed],
        entities: Mapping[tuple[str, str], Mapping[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        approvals: dict[str, dict[str, Any]] = {}
        for index, seed in enumerate(seeds):
            approval_id = _required_text(
                seed.approval_id, f"manifest.approvals[{index}].approval_id"
            )
            target_type = _required_text(
                seed.target_type, f"manifest.approvals[{index}].target_type"
            )
            target_id = _required_text(
                seed.target_id, f"manifest.approvals[{index}].target_id"
            )
            if approval_id in approvals:
                raise ValidationError(f"Duplicate manifest approval: {approval_id}")
            self._validate_approval_target(project_id, target_type, target_id, entities)
            if seed.status not in _APPROVAL_STATUSES:
                raise ValidationError(f"Unknown approval status: {seed.status}")
            approvals[approval_id] = {
                "approval_id": approval_id,
                "target_type": target_type,
                "target_id": target_id,
                "status": seed.status,
                "revision": 0,
                "payload": _json_object(
                    seed.payload, f"manifest.approvals[{index}].payload"
                ),
            }
        return approvals

    @staticmethod
    def _validate_approval_target(
        project_id: str,
        target_type: str,
        target_id: str,
        entities: Mapping[tuple[str, str], Mapping[str, Any]],
    ) -> None:
        if target_type == "project":
            if target_id != project_id:
                raise ValidationError(
                    f"Project approval target must be {project_id}, not {target_id}"
                )
        elif (target_type, target_id) not in entities:
            raise ValidationError(
                f"Approval target does not exist in this project: {target_type}:{target_id}"
            )

    def list_projects(self) -> tuple[ProjectSummary, ...]:
        self._ensure_open()
        rows = self._connection.execute(
            """
            SELECT project_id, name, project_root, current_revision,
                   current_digest, focus_json, manifest_json, created_at, updated_at
            FROM projects
            ORDER BY project_id
            """
        ).fetchall()
        return tuple(
            ProjectSummary(
                project_id=str(row["project_id"]),
                name=str(row["name"]),
                project_root=str(row["project_root"]),
                revision=int(row["current_revision"]),
                digest=str(row["current_digest"]),
                focus=json.loads(str(row["focus_json"])),
                created_at=str(row["created_at"]),
                updated_at=str(row["updated_at"]),
                metadata=dict(
                    json.loads(str(row["manifest_json"])).get("metadata", {})
                ),
            )
            for row in rows
        )

    def activate_session(self, project_id: str, agent_id: str) -> SessionBinding:
        self._ensure_open()
        project_id = _required_text(project_id, "project_id")
        agent_id = _required_text(agent_id, "agent_id")
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            project = self._connection.execute(
                """
                SELECT current_revision, current_digest, focus_json
                FROM projects WHERE project_id = ?
                """,
                (project_id,),
            ).fetchone()
            if project is None:
                raise ProjectNotFoundError(project_id)
            session_id = self._new_id("session")
            now = self._now()
            focus = json.loads(str(project["focus_json"]))
            self._connection.execute(
                """
                INSERT INTO sessions (
                    session_id, project_id, agent_id, bound_revision,
                    bound_digest, focus_json, active, activated_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    session_id,
                    project_id,
                    agent_id,
                    int(project["current_revision"]),
                    str(project["current_digest"]),
                    _canonical_json(focus),
                    now,
                    now,
                ),
            )
            self._insert_audit(
                project_id=project_id,
                session_id=session_id,
                actor_id=agent_id,
                event_type="session_activated",
                revision=int(project["current_revision"]),
                payload={"digest": str(project["current_digest"])},
                created_at=now,
            )
            self._connection.commit()
        except Exception:
            self._rollback()
            raise
        return SessionBinding(
            session_id=session_id,
            project_id=project_id,
            agent_id=agent_id,
            revision=int(project["current_revision"]),
            digest=str(project["current_digest"]),
            focus=focus,
            activated_at=now,
            updated_at=now,
        )

    def get_context(
        self, session_id: str, project_id: str | None = None
    ) -> ProjectContext:
        self._ensure_open()
        session_id = _required_text(session_id, "session_id")
        requested_project = (
            _required_text(project_id, "project_id") if project_id is not None else None
        )
        try:
            self._connection.execute("BEGIN")
            context = self._load_context(session_id, requested_project)
            self._connection.commit()
            return context
        except Exception:
            self._rollback()
            raise

    def _load_context(
        self, session_id: str, requested_project_id: str | None
    ) -> ProjectContext:
        row = self._connection.execute(
            """
            SELECT s.session_id, s.project_id, s.agent_id, s.bound_revision,
                   s.bound_digest, s.focus_json AS session_focus_json, s.active,
                   p.name, p.project_root, p.current_revision, p.manifest_json,
                   r.digest AS revision_digest, r.snapshot_json
            FROM sessions AS s
            JOIN projects AS p ON p.project_id = s.project_id
            JOIN revisions AS r
              ON r.project_id = s.project_id AND r.revision = s.bound_revision
            WHERE s.session_id = ?
            """,
            (session_id,),
        ).fetchone()
        if row is None or not bool(row["active"]):
            raise SessionNotFoundError(session_id)
        bound_project_id = str(row["project_id"])
        if requested_project_id is not None and requested_project_id != bound_project_id:
            raise ProjectBoundaryError(
                session_id=session_id,
                bound_project_id=bound_project_id,
                requested_project_id=requested_project_id,
            )
        snapshot = json.loads(str(row["snapshot_json"]))
        if not isinstance(snapshot, dict):
            raise StorageIntegrityError("Revision snapshot is not a JSON object")
        calculated_digest = _snapshot_digest(snapshot)
        revision_digest = str(row["revision_digest"])
        bound_digest = str(row["bound_digest"])
        if calculated_digest != revision_digest or revision_digest != bound_digest:
            raise StorageIntegrityError(
                f"Session {session_id} digest does not match revision snapshot"
            )
        entities, approvals, focus = _snapshot_states(snapshot)
        session_focus = json.loads(str(row["session_focus_json"]))
        if _canonical_json(session_focus) != _canonical_json(focus):
            raise StorageIntegrityError(
                f"Session {session_id} focus does not match revision snapshot"
            )
        bound_revision = int(row["bound_revision"])
        current_revision = int(row["current_revision"])
        manifest_document = json.loads(str(row["manifest_json"]))
        if not isinstance(manifest_document, dict) or not isinstance(
            manifest_document.get("metadata", {}), dict
        ):
            raise StorageIntegrityError("Project manifest metadata is invalid")
        return ProjectContext(
            project_id=bound_project_id,
            project_name=str(row["name"]),
            project_root=str(row["project_root"]),
            revision=bound_revision,
            digest=bound_digest,
            focus=focus,
            entities=entities,
            approvals=approvals,
            current_project_revision=current_revision,
            is_current=bound_revision == current_revision,
            session_id=session_id,
            agent_id=str(row["agent_id"]),
            metadata=dict(manifest_document.get("metadata", {})),
        )

    def commit_patch(
        self,
        session_id: str,
        project_id: str,
        expected_revision: int,
        patch: ProjectPatch,
    ) -> CommitResult:
        self._ensure_open()
        session_id = _required_text(session_id, "session_id")
        project_id = _required_text(project_id, "project_id")
        if (
            isinstance(expected_revision, bool)
            or not isinstance(expected_revision, int)
            or expected_revision < 0
        ):
            raise ValidationError("expected_revision must be a non-negative integer")
        if not isinstance(patch, ProjectPatch):
            raise ValidationError("patch must be a ProjectPatch")

        try:
            return self._commit_patch(
                session_id=session_id,
                project_id=project_id,
                expected_revision=expected_revision,
                patch=patch,
            )
        except (ProjectBoundaryError, RevisionConflictError) as error:
            self._record_mutation_rejection(
                session_id=session_id,
                requested_project_id=project_id,
                error=error,
            )
            raise

    def _commit_patch(
        self,
        *,
        session_id: str,
        project_id: str,
        expected_revision: int,
        patch: ProjectPatch,
    ) -> CommitResult:
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            session = self._connection.execute(
                """
                SELECT s.project_id, s.agent_id, s.bound_revision, s.bound_digest,
                       s.active, p.current_revision, p.current_digest
                FROM sessions AS s
                JOIN projects AS p ON p.project_id = s.project_id
                WHERE s.session_id = ?
                """,
                (session_id,),
            ).fetchone()
            if session is None or not bool(session["active"]):
                raise SessionNotFoundError(session_id)
            bound_project_id = str(session["project_id"])
            if bound_project_id != project_id:
                raise ProjectBoundaryError(
                    session_id=session_id,
                    bound_project_id=bound_project_id,
                    requested_project_id=project_id,
                )

            actual_revision = int(session["current_revision"])
            session_revision = int(session["bound_revision"])
            if (
                expected_revision != actual_revision
                or expected_revision != session_revision
                or str(session["bound_digest"]) != str(session["current_digest"])
            ):
                raise RevisionConflictError(
                    project_id=project_id,
                    session_id=session_id,
                    expected_revision=expected_revision,
                    actual_revision=actual_revision,
                    session_revision=session_revision,
                )

            revision_row = self._connection.execute(
                """
                SELECT snapshot_json, digest
                FROM revisions
                WHERE project_id = ? AND revision = ?
                """,
                (project_id, expected_revision),
            ).fetchone()
            if revision_row is None:
                raise StorageIntegrityError(
                    f"Missing revision {project_id}@{expected_revision}"
                )
            snapshot = json.loads(str(revision_row["snapshot_json"]))
            if not isinstance(snapshot, dict):
                raise StorageIntegrityError("Revision snapshot is not a JSON object")
            if _snapshot_digest(snapshot) != str(revision_row["digest"]):
                raise StorageIntegrityError("Revision snapshot digest mismatch")

            entity_states, approval_states, existing_focus = _snapshot_states(snapshot)
            entity_map: dict[tuple[str, str], dict[str, Any]] = {
                (entity.entity_type, entity.entity_id): entity.to_dict()
                for entity in entity_states
            }
            approval_map: dict[str, dict[str, Any]] = {
                approval.approval_id: approval.to_dict()
                for approval in approval_states
            }
            new_revision = expected_revision + 1
            entity_operations = self._apply_entity_mutations(
                project_id=project_id,
                session_id=session_id,
                revision=new_revision,
                mutations=patch.entities,
                entity_map=entity_map,
            )
            approval_operations = self._apply_approval_mutations(
                project_id=project_id,
                revision=new_revision,
                mutations=patch.approvals,
                entity_map=entity_map,
                approval_map=approval_map,
            )
            if not entity_operations and not approval_operations and patch.focus is None:
                raise ValidationError("A ProjectPatch must contain at least one change")
            focus = (
                _json_object(patch.focus, "patch.focus")
                if patch.focus is not None
                else existing_focus
            )
            message = _required_text(patch.message, "patch.message")
            next_snapshot = {
                "project_id": project_id,
                "focus": focus,
                "entities": sorted(
                    entity_map.values(),
                    key=lambda item: (item["entity_type"], item["entity_id"]),
                ),
                "approvals": sorted(
                    approval_map.values(), key=lambda item: item["approval_id"]
                ),
            }
            digest = _snapshot_digest(next_snapshot)
            patch_document = {
                "message": message,
                "entities": entity_operations,
                "approvals": approval_operations,
                "focus": focus if patch.focus is not None else None,
            }
            now = self._now()

            self._connection.execute(
                """
                INSERT INTO revisions (
                    project_id, revision, parent_revision, digest, focus_json,
                    snapshot_json, patch_json, session_id, actor_id, message, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    new_revision,
                    expected_revision,
                    digest,
                    _canonical_json(focus),
                    _canonical_json(next_snapshot),
                    _canonical_json(patch_document),
                    session_id,
                    str(session["agent_id"]),
                    message,
                    now,
                ),
            )
            self._persist_entity_mutations(
                project_id, new_revision, patch.entities, now
            )
            self._persist_approval_mutations(
                project_id, new_revision, patch.approvals, now
            )
            updated = self._connection.execute(
                """
                UPDATE projects
                SET current_revision = ?, current_digest = ?, focus_json = ?, updated_at = ?
                WHERE project_id = ? AND current_revision = ? AND current_digest = ?
                """,
                (
                    new_revision,
                    digest,
                    _canonical_json(focus),
                    now,
                    project_id,
                    expected_revision,
                    str(session["current_digest"]),
                ),
            )
            if updated.rowcount != 1:
                raise RevisionConflictError(
                    project_id=project_id,
                    session_id=session_id,
                    expected_revision=expected_revision,
                    actual_revision=actual_revision,
                    session_revision=session_revision,
                )
            self._connection.execute(
                """
                UPDATE sessions
                SET bound_revision = ?, bound_digest = ?, focus_json = ?, updated_at = ?
                WHERE session_id = ? AND project_id = ?
                """,
                (
                    new_revision,
                    digest,
                    _canonical_json(focus),
                    now,
                    session_id,
                    project_id,
                ),
            )
            self._insert_audit(
                project_id=project_id,
                session_id=session_id,
                actor_id=str(session["agent_id"]),
                event_type="patch_committed",
                revision=new_revision,
                payload={
                    "previous_revision": expected_revision,
                    "digest": digest,
                    "entity_change_count": len(entity_operations),
                    "approval_change_count": len(approval_operations),
                    "message": message,
                },
                created_at=now,
            )
            self._connection.commit()
            return CommitResult(
                project_id=project_id,
                session_id=session_id,
                previous_revision=expected_revision,
                revision=new_revision,
                digest=digest,
                focus=focus,
                entity_change_count=len(entity_operations),
                approval_change_count=len(approval_operations),
                committed_at=now,
            )
        except Exception:
            self._rollback()
            raise

    def _apply_entity_mutations(
        self,
        *,
        project_id: str,
        session_id: str,
        revision: int,
        mutations: Sequence[EntityMutation],
        entity_map: dict[tuple[str, str], dict[str, Any]],
    ) -> list[dict[str, Any]]:
        operations: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for index, mutation in enumerate(mutations):
            entity_type = _required_text(
                mutation.entity_type, f"patch.entities[{index}].entity_type"
            )
            entity_id = _required_text(
                mutation.entity_id, f"patch.entities[{index}].entity_id"
            )
            key = (entity_type, entity_id)
            if key in seen:
                raise ValidationError(
                    f"Duplicate entity mutation: {entity_type}:{entity_id}"
                )
            seen.add(key)
            if mutation.operation == "delete":
                if mutation.payload is not None:
                    raise ValidationError(
                        f"Delete mutation must not contain payload: {entity_type}:{entity_id}"
                    )
                if key not in entity_map:
                    raise ValidationError(
                        f"Cannot delete missing entity: {entity_type}:{entity_id}"
                    )
                del entity_map[key]
                operations.append(
                    {
                        "operation": "delete",
                        "entity_type": entity_type,
                        "entity_id": entity_id,
                    }
                )
            elif mutation.operation == "upsert":
                if mutation.payload is None:
                    raise ValidationError(
                        f"Upsert mutation requires payload: {entity_type}:{entity_id}"
                    )
                payload = _json_object(
                    mutation.payload, f"patch.entities[{index}].payload"
                )
                self._validate_payload_boundary(
                    payload,
                    project_id=project_id,
                    session_id=session_id,
                )
                entity_map[key] = {
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                    "revision": revision,
                    "payload": payload,
                }
                operations.append(
                    {
                        "operation": "upsert",
                        "entity_type": entity_type,
                        "entity_id": entity_id,
                        "payload": payload,
                    }
                )
            else:
                raise ValidationError(
                    f"Unsupported entity operation: {mutation.operation}"
                )
        return operations

    @staticmethod
    def _validate_payload_boundary(
        payload: Mapping[str, Any], *, project_id: str, session_id: str
    ) -> None:
        for key in ("project_id", "projectId"):
            if key in payload and payload[key] != project_id:
                raise ValidationError(
                    f"Entity payload {key} does not match mutation project {project_id}"
                )
        for key in ("session_id", "sessionId"):
            if key in payload and payload[key] != session_id:
                raise ValidationError(
                    f"Entity payload {key} does not match mutation session {session_id}"
                )

    def _apply_approval_mutations(
        self,
        *,
        project_id: str,
        revision: int,
        mutations: Sequence[ApprovalMutation],
        entity_map: Mapping[tuple[str, str], Mapping[str, Any]],
        approval_map: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        operations: list[dict[str, Any]] = []
        seen: set[str] = set()
        for index, mutation in enumerate(mutations):
            approval_id = _required_text(
                mutation.approval_id, f"patch.approvals[{index}].approval_id"
            )
            target_type = _required_text(
                mutation.target_type, f"patch.approvals[{index}].target_type"
            )
            target_id = _required_text(
                mutation.target_id, f"patch.approvals[{index}].target_id"
            )
            if approval_id in seen:
                raise ValidationError(f"Duplicate approval mutation: {approval_id}")
            seen.add(approval_id)
            if mutation.status not in _APPROVAL_STATUSES:
                raise ValidationError(f"Unknown approval status: {mutation.status}")
            self._validate_approval_target(
                project_id, target_type, target_id, entity_map
            )
            payload = _json_object(
                mutation.payload, f"patch.approvals[{index}].payload"
            )
            item = {
                "approval_id": approval_id,
                "target_type": target_type,
                "target_id": target_id,
                "status": mutation.status,
                "revision": revision,
                "payload": payload,
            }
            approval_map[approval_id] = item
            operations.append(dict(item))
        return operations

    def _persist_entity_mutations(
        self,
        project_id: str,
        revision: int,
        mutations: Sequence[EntityMutation],
        updated_at: str,
    ) -> None:
        for mutation in mutations:
            entity_type = mutation.entity_type.strip()
            entity_id = mutation.entity_id.strip()
            if mutation.operation == "delete":
                self._connection.execute(
                    """
                    DELETE FROM entities
                    WHERE project_id = ? AND entity_type = ? AND entity_id = ?
                    """,
                    (project_id, entity_type, entity_id),
                )
            else:
                self._connection.execute(
                    """
                    INSERT INTO entities (
                        project_id, entity_type, entity_id, revision,
                        payload_json, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(project_id, entity_type, entity_id) DO UPDATE SET
                        revision = excluded.revision,
                        payload_json = excluded.payload_json,
                        updated_at = excluded.updated_at
                    """,
                    (
                        project_id,
                        entity_type,
                        entity_id,
                        revision,
                        _canonical_json(mutation.payload),
                        updated_at,
                    ),
                )

    def _persist_approval_mutations(
        self,
        project_id: str,
        revision: int,
        mutations: Sequence[ApprovalMutation],
        updated_at: str,
    ) -> None:
        for mutation in mutations:
            self._connection.execute(
                """
                INSERT INTO approvals (
                    project_id, approval_id, target_type, target_id,
                    status, revision, payload_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id, approval_id) DO UPDATE SET
                    target_type = excluded.target_type,
                    target_id = excluded.target_id,
                    status = excluded.status,
                    revision = excluded.revision,
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at
                """,
                (
                    project_id,
                    mutation.approval_id.strip(),
                    mutation.target_type.strip(),
                    mutation.target_id.strip(),
                    mutation.status,
                    revision,
                    _canonical_json(mutation.payload),
                    updated_at,
                ),
            )

    def create_handoff_capsule(
        self,
        session_id: str,
        project_id: str | None = None,
        output_relative_path: str | Path | None = None,
    ) -> HandoffCapsule:
        self._ensure_open()
        session_id = _required_text(session_id, "session_id")
        requested_project = (
            _required_text(project_id, "project_id") if project_id is not None else None
        )
        created_file: Path | None = None
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            context = self._load_context(session_id, requested_project)
            handoff_id = self._new_id("handoff")
            created_at = self._now()
            _, project_directory = self._resolve_safe_relative(
                self.local_root,
                context.project_root,
                label="project.project_root",
            )
            relative_output = (
                Path(".h3story") / "handoffs" / f"{handoff_id}.json"
                if output_relative_path is None
                else Path(output_relative_path)
            )
            normalized_output, output_path = self._resolve_safe_relative(
                project_directory,
                relative_output,
                label="output_relative_path",
            )
            if output_path.suffix.lower() != ".json":
                raise UnsafePathError("Handoff output must use a .json extension")
            capsule = HandoffCapsule(
                handoff_id=handoff_id,
                project_id=context.project_id,
                project_name=context.project_name,
                project_root=context.project_root,
                session_id=session_id,
                agent_id=context.agent_id or "unknown-agent",
                revision=context.revision,
                digest=context.digest,
                focus=context.focus,
                entities=context.entities,
                approvals=context.approvals,
                created_at=created_at,
                relative_path=normalized_output.as_posix(),
            )
            capsule_json = json.dumps(
                capsule.to_dict(),
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
                allow_nan=False,
            ) + "\n"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with output_path.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(capsule_json)
            created_file = output_path
            self._connection.execute(
                """
                INSERT INTO handoffs (
                    handoff_id, project_id, session_id, revision, digest,
                    capsule_json, relative_path, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    handoff_id,
                    context.project_id,
                    session_id,
                    context.revision,
                    context.digest,
                    _canonical_json(capsule.to_dict()),
                    normalized_output.as_posix(),
                    created_at,
                ),
            )
            self._insert_audit(
                project_id=context.project_id,
                session_id=session_id,
                actor_id=context.agent_id or "unknown-agent",
                event_type="handoff_created",
                revision=context.revision,
                payload={
                    "handoff_id": handoff_id,
                    "digest": context.digest,
                    "relative_path": normalized_output.as_posix(),
                },
                created_at=created_at,
            )
            self._connection.commit()
            return capsule
        except Exception:
            self._rollback()
            if created_file is not None:
                created_file.unlink(missing_ok=True)
            raise

    def list_audit_events(self, project_id: str) -> tuple[AuditEvent, ...]:
        self._ensure_open()
        project_id = _required_text(project_id, "project_id")
        if self._connection.execute(
            "SELECT 1 FROM projects WHERE project_id = ?", (project_id,)
        ).fetchone() is None:
            raise ProjectNotFoundError(project_id)
        rows = self._connection.execute(
            """
            SELECT event_id, project_id, session_id, actor_id, event_type,
                   revision, payload_json, created_at
            FROM audit_events
            WHERE project_id = ?
            ORDER BY event_id
            """,
            (project_id,),
        ).fetchall()
        return tuple(
            AuditEvent(
                event_id=int(row["event_id"]),
                project_id=str(row["project_id"]),
                session_id=(
                    str(row["session_id"])
                    if row["session_id"] is not None
                    else None
                ),
                actor_id=str(row["actor_id"]),
                event_type=str(row["event_type"]),
                revision=int(row["revision"]),
                payload=json.loads(str(row["payload_json"])),
                created_at=str(row["created_at"]),
            )
            for row in rows
        )

    def _insert_audit(
        self,
        *,
        project_id: str,
        session_id: str | None,
        actor_id: str,
        event_type: str,
        revision: int,
        payload: Mapping[str, Any],
        created_at: str,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO audit_events (
                project_id, session_id, actor_id, event_type,
                revision, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                session_id,
                actor_id,
                event_type,
                revision,
                _canonical_json(payload),
                created_at,
            ),
        )

    def _record_mutation_rejection(
        self,
        *,
        session_id: str,
        requested_project_id: str,
        error: ProjectCoreError,
    ) -> None:
        """Best-effort security audit after the failed mutation was rolled back."""
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            session = self._connection.execute(
                """
                SELECT s.project_id, s.agent_id, p.current_revision
                FROM sessions AS s
                JOIN projects AS p ON p.project_id = s.project_id
                WHERE s.session_id = ?
                """,
                (session_id,),
            ).fetchone()
            if session is None:
                self._connection.rollback()
                return
            payload: dict[str, Any] = {
                "reason": type(error).__name__,
                "message": str(error),
                "requested_project_id": requested_project_id,
            }
            if isinstance(error, RevisionConflictError):
                payload.update(
                    {
                        "expected_revision": error.expected_revision,
                        "actual_revision": error.actual_revision,
                        "session_revision": error.session_revision,
                    }
                )
            now = self._now()
            self._insert_audit(
                project_id=str(session["project_id"]),
                session_id=session_id,
                actor_id=str(session["agent_id"]),
                event_type="mutation_rejected",
                revision=int(session["current_revision"]),
                payload=payload,
                created_at=now,
            )
            self._connection.commit()
        except sqlite3.Error:
            self._rollback()
