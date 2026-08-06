from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from sidecar.h3_story_core import (
    ApprovalMutation,
    ApprovalSeed,
    EntityMutation,
    EntitySeed,
    ProjectBoundaryError,
    ProjectCodeAlreadyExistsError,
    ProjectCreateSpec,
    ProjectManifest,
    ProjectPatch,
    ProjectRepository,
    RevisionConflictError,
    UnsafePathError,
    ValidationError,
)


class ProjectRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.local_root = Path(self.temporary_directory.name)
        self.repository = ProjectRepository(self.local_root)
        self.repository.seed_project(self._manifest())

    def tearDown(self) -> None:
        self.repository.close()
        self.temporary_directory.cleanup()

    @staticmethod
    def _manifest(
        project_id: str = "project-alpha",
        name: str = "Midnight Signal",
        project_root: str = "projects/alpha",
    ) -> ProjectManifest:
        return ProjectManifest(
            project_id=project_id,
            name=name,
            project_root=project_root,
            focus={"episode_id": "episode-01", "scene_id": "scene-platform"},
            metadata={"format": "vertical_short", "language": "ja-JP"},
            entities=(
                EntitySeed(
                    "episode",
                    "episode-01",
                    {
                        "project_id": project_id,
                        "title": "The Missing Timecode",
                    },
                ),
                EntitySeed(
                    "scene",
                    "scene-platform",
                    {
                        "project_id": project_id,
                        "episode_id": "episode-01",
                        "heading": "EXT. PLATFORM - NIGHT",
                    },
                ),
                EntitySeed(
                    "shot",
                    "shot-platform-wide",
                    {
                        "project_id": project_id,
                        "scene_id": "scene-platform",
                        "status": "planned",
                    },
                ),
            ),
            approvals=(
                ApprovalSeed(
                    approval_id="approval-script",
                    target_type="episode",
                    target_id="episode-01",
                    status="approved",
                    payload={"note": "Dialogue is locked."},
                ),
            ),
        )

    @staticmethod
    def _create_spec(code: str = "NEW-STORY") -> ProjectCreateSpec:
        return ProjectCreateSpec(
            title="New Story",
            code=code,
            subtitle="A runtime project",
            episode_title="Episode One",
            scene_title="Opening Room",
            shot_title="First Signal",
            logline="A signal wakes an empty room.",
            summary="An intentionally empty production scaffold.",
        )

    def _revision_one_patch(self) -> ProjectPatch:
        return ProjectPatch(
            entities=(
                EntityMutation(
                    "shot",
                    "shot-platform-wide",
                    {
                        "project_id": "project-alpha",
                        "scene_id": "scene-platform",
                        "status": "selected",
                        "selected_take_id": "take-platform-a",
                    },
                ),
                EntityMutation(
                    "take",
                    "take-platform-a",
                    {
                        "project_id": "project-alpha",
                        "shot_id": "shot-platform-wide",
                        "status": "ready",
                        "freshness": "fresh",
                    },
                ),
            ),
            approvals=(
                ApprovalMutation(
                    approval_id="approval-take-a",
                    target_type="take",
                    target_id="take-platform-a",
                    status="pending",
                    payload={"requested_by": "agent:director"},
                ),
            ),
            focus={
                "episode_id": "episode-01",
                "scene_id": "scene-platform",
                "shot_id": "shot-platform-wide",
            },
            message="Select first H3 take",
        )

    def test_manifest_seed_session_binding_schema_and_project_listing(self) -> None:
        settings = self.repository.storage_settings()
        self.assertEqual(settings["journal_mode"], "wal")
        self.assertIs(settings["foreign_keys"], True)
        self.assertEqual(settings["user_version"], 1)
        self.assertTrue(Path(str(settings["database_path"])).is_relative_to(self.local_root))

        connection = sqlite3.connect(self.repository.database_path)
        try:
            table_names = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        finally:
            connection.close()
        self.assertTrue(
            {
                "projects",
                "revisions",
                "entities",
                "sessions",
                "handoffs",
                "approvals",
                "audit_events",
            }.issubset(table_names)
        )

        self.repository.seed_project(
            self._manifest(
                project_id="project-beta",
                name="Beta Project",
                project_root="projects/beta",
            )
        )
        projects = self.repository.list_projects()
        self.assertEqual(
            [project.project_id for project in projects],
            ["project-alpha", "project-beta"],
        )
        self.assertEqual(projects[0].revision, 0)
        self.assertEqual(projects[0].project_root, "projects/alpha")
        self.assertEqual(
            projects[0].focus,
            {"episode_id": "episode-01", "scene_id": "scene-platform"},
        )

        session = self.repository.activate_session("project-alpha", "agent:director")
        context = self.repository.get_context(session.session_id, "project-alpha")
        self.assertEqual(session.project_id, "project-alpha")
        self.assertEqual(session.revision, 0)
        self.assertEqual(session.digest, context.digest)
        self.assertEqual(session.focus, context.focus)
        self.assertTrue(context.is_current)
        self.assertEqual(context.current_project_revision, 0)
        self.assertEqual(len(context.entities), 3)
        self.assertEqual(len(context.approvals), 1)
        self.assertTrue((self.local_root / "projects" / "alpha").is_dir())

        audit_types = [
            event.event_type
            for event in self.repository.list_audit_events("project-alpha")
        ]
        self.assertEqual(audit_types, ["project_seeded", "session_activated"])

    def test_atomic_patch_commit_and_handoff_json_export(self) -> None:
        session = self.repository.activate_session("project-alpha", "agent:director")
        previous_digest = session.digest
        result = self.repository.commit_patch(
            session.session_id,
            "project-alpha",
            expected_revision=0,
            patch=self._revision_one_patch(),
        )

        self.assertEqual(result.previous_revision, 0)
        self.assertEqual(result.revision, 1)
        self.assertNotEqual(result.digest, previous_digest)
        self.assertEqual(result.entity_change_count, 2)
        self.assertEqual(result.approval_change_count, 1)

        context = self.repository.get_context(session.session_id)
        self.assertEqual(context.revision, 1)
        self.assertEqual(context.digest, result.digest)
        self.assertTrue(context.is_current)
        self.assertEqual(context.focus["shot_id"], "shot-platform-wide")
        entities = {
            (entity.entity_type, entity.entity_id): entity for entity in context.entities
        }
        self.assertEqual(
            entities[("shot", "shot-platform-wide")].payload["status"],
            "selected",
        )
        self.assertEqual(entities[("take", "take-platform-a")].revision, 1)
        approvals = {
            approval.approval_id: approval for approval in context.approvals
        }
        self.assertEqual(approvals["approval-take-a"].status, "pending")
        self.assertEqual(approvals["approval-take-a"].revision, 1)

        capsule = self.repository.create_handoff_capsule(
            session.session_id,
            "project-alpha",
            "handoffs/review-r1.json",
        )
        handoff_path = self.local_root / "projects" / "alpha" / capsule.relative_path
        self.assertTrue(handoff_path.is_file())
        exported = json.loads(handoff_path.read_text(encoding="utf-8"))
        self.assertEqual(exported["protocol"], "h3-story-studio/handoff")
        self.assertEqual(exported["project_id"], "project-alpha")
        self.assertEqual(exported["session_id"], session.session_id)
        self.assertEqual(exported["revision"], 1)
        self.assertEqual(exported["digest"], result.digest)
        self.assertEqual(exported["relative_path"], "handoffs/review-r1.json")
        self.assertEqual(len(exported["entities"]), 4)
        self.assertEqual(len(exported["approvals"]), 2)

        with self.assertRaises(FileExistsError):
            self.repository.create_handoff_capsule(
                session.session_id,
                output_relative_path="handoffs/review-r1.json",
            )

        audit_types = [
            event.event_type
            for event in self.repository.list_audit_events("project-alpha")
        ]
        self.assertEqual(
            audit_types,
            [
                "project_seeded",
                "session_activated",
                "patch_committed",
                "handoff_created",
            ],
        )

    def test_invalid_compound_patch_rolls_back_every_change(self) -> None:
        session = self.repository.activate_session("project-alpha", "agent:director")
        before = self.repository.get_context(session.session_id)
        invalid_patch = ProjectPatch(
            entities=(
                EntityMutation(
                    "shot",
                    "shot-platform-wide",
                    {
                        "project_id": "project-alpha",
                        "status": "should-not-persist",
                    },
                ),
            ),
            approvals=(
                ApprovalMutation(
                    approval_id="approval-missing-target",
                    target_type="take",
                    target_id="take-does-not-exist",
                    status="pending",
                ),
            ),
            message="This transaction must roll back",
        )

        with self.assertRaises(ValidationError):
            self.repository.commit_patch(
                session.session_id,
                "project-alpha",
                expected_revision=0,
                patch=invalid_patch,
            )

        after = self.repository.get_context(session.session_id)
        self.assertEqual(after.revision, 0)
        self.assertEqual(after.digest, before.digest)
        self.assertEqual(after.to_dict(), before.to_dict())
        self.assertEqual(self.repository.list_projects()[0].revision, 0)
        self.assertNotIn(
            "patch_committed",
            [
                event.event_type
                for event in self.repository.list_audit_events("project-alpha")
            ],
        )

    def test_revision_conflict_preserves_stale_snapshot_and_is_audited(self) -> None:
        first = self.repository.activate_session("project-alpha", "agent:first")
        stale = self.repository.activate_session("project-alpha", "agent:stale")
        self.repository.commit_patch(
            first.session_id,
            "project-alpha",
            expected_revision=0,
            patch=self._revision_one_patch(),
        )

        competing_patch = ProjectPatch(
            focus={"scene_id": "different-scene"},
            message="Stale competing edit",
        )
        with ProjectRepository(self.local_root) as concurrent_repository:
            with self.assertRaises(RevisionConflictError) as raised:
                concurrent_repository.commit_patch(
                    stale.session_id,
                    "project-alpha",
                    expected_revision=0,
                    patch=competing_patch,
                )
            conflict = raised.exception
            self.assertEqual(conflict.expected_revision, 0)
            self.assertEqual(conflict.actual_revision, 1)
            self.assertEqual(conflict.session_revision, 0)

        stale_context = self.repository.get_context(stale.session_id)
        self.assertEqual(stale_context.revision, 0)
        self.assertEqual(stale_context.current_project_revision, 1)
        self.assertFalse(stale_context.is_current)
        stale_shot = next(
            entity
            for entity in stale_context.entities
            if entity.entity_id == "shot-platform-wide"
        )
        self.assertEqual(stale_shot.payload["status"], "planned")

        current_context = self.repository.get_context(first.session_id)
        self.assertEqual(current_context.revision, 1)
        self.assertEqual(current_context.focus["shot_id"], "shot-platform-wide")
        rejection_events = [
            event
            for event in self.repository.list_audit_events("project-alpha")
            if event.event_type == "mutation_rejected"
        ]
        self.assertEqual(len(rejection_events), 1)
        self.assertEqual(
            rejection_events[0].payload["reason"], "RevisionConflictError"
        )
        self.assertEqual(rejection_events[0].payload["session_revision"], 0)

    def test_wrong_project_session_payload_and_unsafe_paths_are_rejected(self) -> None:
        self.repository.seed_project(
            self._manifest(
                project_id="project-beta",
                name="Beta Project",
                project_root="projects/beta",
            )
        )
        alpha_session = self.repository.activate_session(
            "project-alpha", "agent:alpha"
        )
        beta_session = self.repository.activate_session("project-beta", "agent:beta")

        with self.assertRaises(ProjectBoundaryError):
            self.repository.commit_patch(
                alpha_session.session_id,
                "project-beta",
                expected_revision=0,
                patch=ProjectPatch(focus={"scene_id": "wrong-project"}),
            )
        with self.assertRaises(ProjectBoundaryError):
            self.repository.commit_patch(
                beta_session.session_id,
                "project-alpha",
                expected_revision=0,
                patch=ProjectPatch(focus={"scene_id": "wrong-session"}),
            )
        with self.assertRaises(ProjectBoundaryError):
            self.repository.get_context(alpha_session.session_id, "project-beta")
        with self.assertRaises(ProjectBoundaryError):
            self.repository.create_handoff_capsule(
                alpha_session.session_id, "project-beta"
            )

        with self.assertRaises(ValidationError):
            self.repository.commit_patch(
                alpha_session.session_id,
                "project-alpha",
                expected_revision=0,
                patch=ProjectPatch(
                    entities=(
                        EntityMutation(
                            "shot",
                            "shot-platform-wide",
                            {"project_id": "project-beta", "status": "selected"},
                        ),
                    )
                ),
            )

        with self.assertRaises(UnsafePathError):
            self.repository.create_handoff_capsule(
                alpha_session.session_id,
                output_relative_path="../escaped.json",
            )
        with self.assertRaises(UnsafePathError):
            self.repository.create_handoff_capsule(
                alpha_session.session_id,
                output_relative_path=self.local_root / "absolute.json",
            )
        with self.assertRaises(UnsafePathError):
            ProjectRepository(self.local_root, "../outside.sqlite3")

        self.assertFalse((self.local_root / "escaped.json").exists())
        self.assertEqual(
            {project.project_id: project.revision for project in self.repository.list_projects()},
            {"project-alpha": 0, "project-beta": 0},
        )
        rejection_reasons = [
            event.payload["reason"]
            for project_id in ("project-alpha", "project-beta")
            for event in self.repository.list_audit_events(project_id)
            if event.event_type == "mutation_rejected"
        ]
        self.assertEqual(
            rejection_reasons.count("ProjectBoundaryError"),
            2,
        )

    def test_restart_persists_sessions_revisions_handoffs_audit_and_listing(self) -> None:
        session = self.repository.activate_session("project-alpha", "agent:director")
        committed = self.repository.commit_patch(
            session.session_id,
            "project-alpha",
            expected_revision=0,
            patch=self._revision_one_patch(),
        )
        handoff = self.repository.create_handoff_capsule(session.session_id)
        handoff_path = (
            self.local_root / "projects" / "alpha" / handoff.relative_path
        )
        self.assertTrue(handoff_path.is_file())

        self.repository.close()
        self.repository = ProjectRepository(self.local_root)

        projects = self.repository.list_projects()
        self.assertEqual(len(projects), 1)
        self.assertEqual(projects[0].revision, 1)
        self.assertEqual(projects[0].digest, committed.digest)
        restored = self.repository.get_context(session.session_id, "project-alpha")
        self.assertEqual(restored.revision, 1)
        self.assertEqual(restored.digest, committed.digest)
        self.assertEqual(restored.focus["shot_id"], "shot-platform-wide")
        self.assertTrue(restored.is_current)
        self.assertTrue(handoff_path.is_file())
        self.assertIn(
            "handoff_created",
            [
                event.event_type
                for event in self.repository.list_audit_events("project-alpha")
            ],
        )

        resumed = self.repository.activate_session("project-alpha", "agent:resumed")
        self.assertEqual(resumed.revision, 1)
        second_commit = self.repository.commit_patch(
            resumed.session_id,
            "project-alpha",
            expected_revision=1,
            patch=ProjectPatch(
                approvals=(
                    ApprovalMutation(
                        approval_id="approval-take-a",
                        target_type="take",
                        target_id="take-platform-a",
                        status="approved",
                        payload={"reviewer": "human:director"},
                    ),
                ),
                message="Approve selected take after restart",
            ),
        )
        self.assertEqual(second_commit.revision, 2)
        final_context = self.repository.get_context(resumed.session_id)
        self.assertEqual(final_context.revision, 2)
        approval = next(
            item
            for item in final_context.approvals
            if item.approval_id == "approval-take-a"
        )
        self.assertEqual(approval.status, "approved")

    def test_runtime_create_project_generates_safe_scaffold_and_metadata(self) -> None:
        created = self.repository.create_project(self._create_spec())

        self.assertRegex(created.project_id, r"^prj_[a-f0-9]{32}$")
        self.assertRegex(
            created.project_root,
            r"^projects/new-story-[a-f0-9]{10}$",
        )
        self.assertEqual(created.code, "NEW-STORY")
        self.assertEqual(created.title, "New Story")
        self.assertEqual(created.revision, 0)
        self.assertTrue(created.digest.startswith("sha256:"))
        self.assertTrue((self.local_root / created.project_root).is_dir())
        self.assertEqual(
            set(created.focus),
            {"episode_id", "scene_id", "shot_id", "prompt_id"},
        )

        session = self.repository.activate_session(created.project_id, "codex:test")
        context = self.repository.get_context(session.session_id, created.project_id)
        self.assertEqual(
            {entity.entity_type for entity in context.entities},
            {"episode", "scene", "shot", "prompt"},
        )
        shot = next(entity for entity in context.entities if entity.entity_type == "shot")
        self.assertEqual(shot.entity_id, created.focus["shot_id"])
        self.assertEqual(shot.payload["description"], "")
        self.assertEqual(context.metadata["code"], "NEW-STORY")
        summary = next(
            item
            for item in self.repository.list_projects()
            if item.project_id == created.project_id
        )
        self.assertEqual(summary.metadata["episode_title"], "Episode One")
        self.assertIn(
            "project_created",
            [
                event.event_type
                for event in self.repository.list_audit_events(created.project_id)
            ],
        )

    def test_runtime_create_rejects_duplicate_code_without_extra_directory(self) -> None:
        self.repository.create_project(self._create_spec("UNIQUE-CODE"))
        before = {
            path.resolve()
            for path in (self.local_root / "projects").iterdir()
            if path.is_dir()
        }
        with self.assertRaises(ProjectCodeAlreadyExistsError):
            self.repository.create_project(self._create_spec("unique-code"))
        after = {
            path.resolve()
            for path in (self.local_root / "projects").iterdir()
            if path.is_dir()
        }
        self.assertEqual(after, before)

    def test_runtime_create_rolls_back_database_and_new_directory_on_failure(self) -> None:
        before_projects = {
            item.project_id for item in self.repository.list_projects()
        }
        before_directories = {
            path.resolve()
            for path in (self.local_root / "projects").iterdir()
            if path.is_dir()
        }
        with mock.patch.object(
            self.repository,
            "_insert_audit",
            side_effect=RuntimeError("injected audit failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "injected audit failure"):
                self.repository.create_project(self._create_spec("ROLLBACK-CODE"))
        self.assertEqual(
            {item.project_id for item in self.repository.list_projects()},
            before_projects,
        )
        self.assertEqual(
            {
                path.resolve()
                for path in (self.local_root / "projects").iterdir()
                if path.is_dir()
            },
            before_directories,
        )


if __name__ == "__main__":
    unittest.main()
