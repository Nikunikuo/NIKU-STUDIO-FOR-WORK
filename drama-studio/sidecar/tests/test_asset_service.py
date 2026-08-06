from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import tempfile
import unittest

from sidecar.asset_service import (
    AssetCreateSpec,
    AssetMediaTypeError,
    AssetPathError,
    AssetService,
    AssetTooLargeError,
    AssetUpload,
    AssetValidationError,
)
from sidecar.h3_story_core import (
    ProjectCreateSpec,
    ProjectRepository,
    RevisionConflictError,
)


PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"test-png-payload"
MP4_BYTES = b"\x00\x00\x00\x18ftypisom" + b"test-mp4-payload"
WAV_BYTES = b"RIFF\x10\x00\x00\x00WAVE" + b"test-wav-payload"


class AssetServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.local_root = Path(self.temporary_directory.name)
        self.repository = ProjectRepository(self.local_root)
        self.project = self.repository.create_project(
            ProjectCreateSpec(
                title="Asset Test",
                code="ASSET-TEST",
                subtitle="",
                episode_title="Episode",
                scene_title="Scene",
                shot_title="Shot",
                logline="",
                summary="",
            )
        )
        self.session = self.repository.activate_session(
            self.project.project_id, "human:test"
        )
        self.service = AssetService(self.repository, self.local_root)

    def tearDown(self) -> None:
        self.repository.close()
        self.temporary_directory.cleanup()

    def spec(self, *, expected_revision: int = 0, **changes: object) -> AssetCreateSpec:
        values: dict[str, object] = {
            "session_id": self.session.session_id,
            "expected_revision": expected_revision,
            "name": "Hero reference",
            "category": "character",
            "role": "Main character",
            "caption": "Human-facing caption",
            "prompt_caption": "consistent short black hair",
            "locked": True,
            "source": "human",
        }
        values.update(changes)
        return AssetCreateSpec(**values)  # type: ignore[arg-type]

    def test_metadata_only_asset_uses_server_id_and_project_revision(self) -> None:
        result = self.service.create_asset(
            project_id=self.project.project_id,
            spec=self.spec(),
        )

        self.assertRegex(result.asset_id, r"^asset_[a-f0-9]{32}$")
        self.assertEqual(result.revision, 1)
        self.assertTrue(result.digest.startswith("sha256:"))
        self.assertEqual(result.payload["kind"], "character")
        self.assertEqual(result.payload["prompt_caption"], "consistent short black hair")
        self.assertNotIn("relative_path", result.payload)
        self.assertFalse((self.local_root / self.project.project_root / "assets").exists())
        context = self.repository.get_context(
            self.session.session_id, self.project.project_id
        )
        entity = next(
            item
            for item in context.entities
            if item.entity_type == "asset" and item.entity_id == result.asset_id
        )
        self.assertEqual(dict(entity.payload), dict(result.payload))

    def test_uploaded_image_is_hashed_and_stored_under_project_assets(self) -> None:
        result = self.service.create_asset(
            project_id=self.project.project_id,
            spec=self.spec(source="codex_gpt_image_2"),
            upload=AssetUpload("hero.png", "image/png", PNG_BYTES),
        )

        self.assertEqual(result.payload["media_kind"], "image")
        self.assertEqual(result.payload["mime_type"], "image/png")
        self.assertEqual(result.payload["byte_size"], len(PNG_BYTES))
        self.assertEqual(result.payload["sha256"], sha256(PNG_BYTES).hexdigest())
        relative_path = Path(str(result.payload["relative_path"]))
        self.assertEqual(relative_path.parts[0], "assets")
        self.assertEqual(relative_path.suffix, ".png")
        stored = self.local_root / self.project.project_root / relative_path
        self.assertEqual(stored.read_bytes(), PNG_BYTES)
        self.assertEqual(stored.name, f"{result.asset_id}.png")

    def test_upload_accepts_video_and_audio_browser_preview_types(self) -> None:
        video = self.service.create_asset(
            project_id=self.project.project_id,
            spec=self.spec(),
            upload=AssetUpload("reference.mp4", "video/mp4", MP4_BYTES),
        )
        self.assertEqual(video.payload["media_kind"], "video")

        audio = self.service.create_asset(
            project_id=self.project.project_id,
            spec=self.spec(expected_revision=1, name="Voice reference"),
            upload=AssetUpload("voice.wav", "audio/wav", WAV_BYTES),
        )
        self.assertEqual(audio.revision, 2)
        self.assertEqual(audio.payload["media_kind"], "audio")

    def test_validation_rejects_categories_sources_mime_signature_and_size(self) -> None:
        with self.assertRaises(AssetValidationError):
            self.service.create_asset(
                project_id=self.project.project_id,
                spec=self.spec(category="executable"),
            )
        with self.assertRaises(AssetValidationError):
            self.service.create_asset(
                project_id=self.project.project_id,
                spec=self.spec(source="remote_url"),
            )
        with self.assertRaises(AssetMediaTypeError):
            self.service.create_asset(
                project_id=self.project.project_id,
                spec=self.spec(),
                upload=AssetUpload("hero.png", "image/jpeg", PNG_BYTES),
            )
        with self.assertRaises(AssetMediaTypeError):
            self.service.create_asset(
                project_id=self.project.project_id,
                spec=self.spec(),
                upload=AssetUpload("hero.png", "image/png", b"not-a-png"),
            )
        limited = AssetService(self.repository, self.local_root, max_file_bytes=8)
        with self.assertRaises(AssetTooLargeError):
            limited.create_asset(
                project_id=self.project.project_id,
                spec=self.spec(),
                upload=AssetUpload("hero.png", "image/png", PNG_BYTES),
            )
        self.assertEqual(
            self.repository.get_context(
                self.session.session_id, self.project.project_id
            ).revision,
            0,
        )

    def test_revision_conflict_after_file_write_cleans_file_and_new_directory(self) -> None:
        real_repository = self.repository
        session = self.session
        project = self.project

        class FailingRepository:
            def get_context(self, session_id: str, project_id: str | None = None):
                return real_repository.get_context(session_id, project_id)

            def commit_patch(self, *args, **kwargs):
                raise RevisionConflictError(
                    project_id=project.project_id,
                    session_id=session.session_id,
                    expected_revision=0,
                    actual_revision=1,
                    session_revision=0,
                )

        failing = AssetService(FailingRepository(), self.local_root)
        with self.assertRaises(RevisionConflictError):
            failing.create_asset(
                project_id=self.project.project_id,
                spec=self.spec(),
                upload=AssetUpload("hero.png", "image/png", PNG_BYTES),
            )
        self.assertFalse((self.local_root / self.project.project_root / "assets").exists())

    def test_unsafe_project_root_is_rejected_before_file_write(self) -> None:
        class UnsafeRepository:
            def get_context(self, _session_id: str, _project_id: str | None = None):
                return {
                    "project_id": self_outer.project.project_id,
                    "project_root": "../outside",
                    "revision": 0,
                    "current_project_revision": 0,
                    "is_current": True,
                }

            def commit_patch(self, *args, **kwargs):
                raise AssertionError("commit must not be reached")

        self_outer = self
        unsafe = AssetService(UnsafeRepository(), self.local_root)
        with self.assertRaises(AssetPathError):
            unsafe.create_asset(
                project_id=self.project.project_id,
                spec=self.spec(),
                upload=AssetUpload("hero.png", "image/png", PNG_BYTES),
            )


if __name__ == "__main__":
    unittest.main()
