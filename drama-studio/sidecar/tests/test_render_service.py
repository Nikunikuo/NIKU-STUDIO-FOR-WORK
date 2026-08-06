from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from sidecar.h3_adapter import H3Job, H3JobStatus, H3Result
from sidecar.h3_story_core.models import EntityState
from sidecar.render_service import RenderService, RenderServiceError


def render_request(
    shot_id: str = "sh_012",
    *,
    mode: str = "t2v",
    prompt: str = "blue pre-dawn orbital workshop, slow cinematic push-in",
    reference_asset_ids: tuple[str, ...] = (),
    prompt_processing_mode: str = "community",
) -> dict[str, object]:
    return {
        "shotId": shot_id,
        "fields": {
            "mode": mode,
            "style": "natural",
            "prompt": prompt,
            "width": 864,
            "height": 480,
            "num_frames": 124,
            "steps": 2,
            "seed": 42,
            "acceleration": "off",
            "ref_image_size": "match",
            "prompt_processing_mode": prompt_processing_mode,
            "standalone_audio_policy": "full_content",
            "audio_preset": "auto",
            "dialogue": "",
            "soundscape": "",
            "music_policy": "auto",
            "audio_gain_db": 0,
        },
        "uploads": {
            "first_image_asset_id": None,
            "last_image_asset_id": None,
            "reference_asset_ids": list(reference_asset_ids),
        },
    }


def make_project_directory(local_root: str | Path) -> Path:
    project_directory = Path(local_root, "projects", "demo")
    project_directory.mkdir(parents=True, exist_ok=True)
    return project_directory


def register_asset(
    repository: "FakeRepository",
    project_directory: Path,
    *,
    asset_id: str,
    name: str,
    caption: str,
    prompt_caption: str,
    file_name: str | None = None,
    content: bytes | None = None,
    mime_type: str | None = None,
) -> Path | None:
    payload: dict[str, object] = {
        "name": name,
        "caption": caption,
        "prompt_caption": prompt_caption,
    }
    path: Path | None = None
    if file_name is not None:
        if content is None or mime_type is None:
            raise AssertionError("file-backed fixtures require content and MIME")
        path = project_directory / "assets" / file_name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        payload.update(
            relative_path=path.relative_to(project_directory).as_posix(),
            mime_type=mime_type,
            byte_size=len(content),
            sha256=sha256(content).hexdigest(),
        )
    repository.entities[("asset", asset_id)] = EntityState(
        entity_type="asset",
        entity_id=asset_id,
        revision=repository.revision,
        payload=payload,
    )
    return path


def h3_job(status: H3JobStatus, *, progress: float = 0.0) -> H3Job:
    return H3Job(
        id="a" * 12,
        status=status,
        phase="render",
        message=status.value,
        progress=progress,
        result_url="/api/jobs/aaaaaaaaaaaa/result" if status.terminal else None,
        preview_url=None,
        can_cancel=not status.terminal,
        media=None,
        raw={},
    )


class FakeRepository:
    def __init__(self) -> None:
        self.revision = 1
        self.entities: dict[tuple[str, str], EntityState] = {}
        self.patches = []

    def get_context(self, session_id: str, project_id: str | None = None):
        if session_id != "ses_001" or project_id != "prj_001":
            raise AssertionError("unexpected binding")
        return SimpleNamespace(
            revision=self.revision,
            project_root="projects/demo",
            entities=tuple(self.entities.values()),
        )

    def commit_patch(self, session_id, project_id, expected_revision, patch):
        if session_id != "ses_001" or project_id != "prj_001":
            raise AssertionError("unexpected binding")
        if expected_revision != self.revision:
            raise RuntimeError("revision conflict")
        self.revision += 1
        self.patches.append(patch)
        for mutation in patch.entities:
            key = (mutation.entity_type, mutation.entity_id)
            if mutation.operation == "delete":
                self.entities.pop(key, None)
            else:
                self.entities[key] = EntityState(
                    entity_type=mutation.entity_type,
                    entity_id=mutation.entity_id,
                    revision=self.revision,
                    payload=dict(mutation.payload or {}),
                )
        return SimpleNamespace(revision=self.revision)


class FakeH3:
    def __init__(self, *, fail_submit: bool = False) -> None:
        self.fail_submit = fail_submit
        self.submissions = []
        self.asset_resolvers = []
        self.received_assets = []
        self.job = h3_job(H3JobStatus.QUEUED)

    def submit_job(self, request, *, asset_resolver=None):
        self.submissions.append(request)
        self.asset_resolvers.append(asset_resolver)
        asset_ids = tuple(
            asset_id
            for asset_id in (
                request.uploads.first_image_asset_id,
                request.uploads.last_image_asset_id,
                *request.uploads.reference_asset_ids,
            )
            if asset_id is not None
        )
        received: dict[str, dict[str, object]] = {}
        for asset_id in asset_ids:
            if asset_resolver is None:
                raise AssertionError("file-backed request did not receive an asset resolver")
            descriptor = asset_resolver.resolve_descriptor(asset_id)
            path = Path(descriptor.path)
            received[asset_id] = {
                "path": path,
                "source_path": descriptor.source_path,
                "mime_type": descriptor.mime_type,
                "byte_size": descriptor.byte_size,
                "sha256": descriptor.sha256,
                "content_sha256": sha256(path.read_bytes()).hexdigest(),
            }
        self.received_assets.append(received)
        if self.fail_submit:
            raise RuntimeError("H3 offline")
        return self.job

    def get_job(self, job_id: str):
        if job_id != "a" * 12:
            raise AssertionError("wrong job")
        return self.job

    def get_result(self, job_id: str):
        if job_id != "a" * 12:
            raise AssertionError("wrong job")
        return H3Result(job_id=job_id, content=b"demo-mp4", content_type="video/mp4")


class RenderServiceTests(unittest.TestCase):
    def test_submission_records_intent_before_h3_and_acceptance_after(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            make_project_directory(directory)
            repository = FakeRepository()
            h3 = FakeH3()
            service = RenderService(repository, h3, directory)
            result = service.submit_render(
                session_id="ses_001",
                project_id="prj_001",
                expected_revision=1,
                shot_id="sh_012",
                request=render_request(),
            )

            self.assertEqual(result.revision, 3)
            self.assertEqual(result.h3_job_id, "a" * 12)
            self.assertEqual(len(repository.patches), 2)
            self.assertEqual(len(h3.submissions), 1)
            job = repository.entities[("job", result.local_job_id)]
            self.assertEqual(job.payload["h3_job_id"], "a" * 12)
            self.assertEqual(job.payload["input_project_revision"], 1)
            take = repository.entities[("take", result.take_id)]
            self.assertEqual(take.payload["status"], "generating")

    def test_h3_dispatch_failure_is_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            make_project_directory(directory)
            repository = FakeRepository()
            service = RenderService(repository, FakeH3(fail_submit=True), directory)

            with self.assertRaisesRegex(RuntimeError, "H3 offline"):
                service.submit_render(
                    session_id="ses_001",
                    project_id="prj_001",
                    expected_revision=1,
                    shot_id="sh_012",
                    request=render_request(),
                )

            self.assertEqual(repository.revision, 3)
            failed_jobs = [
                item for (kind, _), item in repository.entities.items() if kind == "job"
            ]
            self.assertEqual(len(failed_jobs), 1)
            self.assertEqual(failed_jobs[0].payload["status"], "failed")

    def test_mismatched_shot_is_rejected_before_repository_or_h3(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            make_project_directory(directory)
            repository = FakeRepository()
            h3 = FakeH3()
            service = RenderService(repository, h3, directory)

            with self.assertRaisesRegex(RenderServiceError, "must match"):
                service.submit_render(
                    session_id="ses_001",
                    project_id="prj_001",
                    expected_revision=1,
                    shot_id="sh_999",
                    request=render_request("sh_012"),
                )
            self.assertEqual(repository.patches, [])
            self.assertEqual(h3.submissions, [])

    def test_project_assets_compile_mentions_and_pass_scoped_resolver(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project_directory = make_project_directory(directory)
            repository = FakeRepository()
            image_one = register_asset(
                repository,
                project_directory,
                asset_id="asset_rin",
                name="凛",
                caption="黒いショートヘアの整備士。",
                prompt_caption="young mechanic with short black hair",
                file_name="rin.png",
                content=b"RIN_IMAGE",
                mime_type="image/png",
            )
            video = register_asset(
                repository,
                project_directory,
                asset_id="asset_motion",
                name="動作見本",
                caption="ゆっくり振り返る動き。",
                prompt_caption="slow turn motion reference",
                file_name="motion.mp4",
                content=b"MOTION_VIDEO",
                mime_type="video/mp4",
            )
            image_two = register_asset(
                repository,
                project_directory,
                asset_id="asset_yu",
                name="ユウ",
                caption="金属義手の帰還兵。",
                prompt_caption="veteran with a brass mechanical arm",
                file_name="yu.webp",
                content=b"YU_IMAGE",
                mime_type="image/webp",
            )
            audio = register_asset(
                repository,
                project_directory,
                asset_id="asset_voice",
                name="声見本",
                caption="低く落ち着いた声。",
                prompt_caption="calm low voice reference",
                file_name="voice.wav",
                content=b"VOICE_AUDIO",
                mime_type="audio/wav",
            )
            register_asset(
                repository,
                project_directory,
                asset_id="asset_factory",
                name="旧軌道工場",
                caption="夜明け前の工場",
                prompt_caption="abandoned orbital workshop",
            )
            h3 = FakeH3()
            service = RenderService(repository, h3, directory)
            authoring = "@凛と@動作見本を見て、@ユウが@声見本を聞く。@旧軌道工場"
            story_request = render_request(
                mode="omni",
                prompt=authoring,
                reference_asset_ids=(
                    "asset_rin",
                    "asset_motion",
                    "asset_yu",
                    "asset_voice",
                ),
                # Even a legacy raw_en request is normalized before H3's
                # raw-only style/audio restrictions are evaluated.
                prompt_processing_mode="raw_en",
            )
            story_fields = story_request["fields"]
            assert isinstance(story_fields, dict)
            story_fields["style"] = "cinematic"
            story_fields["soundscape"] = "distant machinery"
            result = service.submit_render(
                session_id="ses_001",
                project_id="prj_001",
                expected_revision=1,
                shot_id="sh_012",
                request=story_request,
            )

            self.assertEqual(result.revision, 3)
            self.assertEqual(len(h3.submissions), 1)
            sent = h3.submissions[0]
            self.assertEqual(sent.fields.prompt_processing_mode, "community")
            expected_prompt = (
                "<Picture 1>と<Video 1>を見て、<Picture 2>が<Audio 1>を聞く。"
                "素材[名前: 旧軌道工場]（説明: 夜明け前の工場。"
                "H3向け特徴: abandoned orbital workshop）"
            )
            self.assertEqual(sent.fields.prompt, expected_prompt)

            self.assertEqual(
                h3.received_assets[0]["asset_rin"],
                {
                    "path": h3.received_assets[0]["asset_rin"]["path"],
                    "source_path": image_one,
                    "mime_type": "image/png",
                    "byte_size": len(b"RIN_IMAGE"),
                    "sha256": sha256(b"RIN_IMAGE").hexdigest(),
                    "content_sha256": sha256(b"RIN_IMAGE").hexdigest(),
                },
            )
            snapshot_path = h3.received_assets[0]["asset_rin"]["path"]
            self.assertIsInstance(snapshot_path, Path)
            self.assertNotEqual(snapshot_path, image_one)
            self.assertFalse(snapshot_path.exists())
            self.assertEqual(
                h3.received_assets[0]["asset_motion"]["source_path"], video
            )
            self.assertEqual(
                h3.received_assets[0]["asset_yu"]["source_path"], image_two
            )
            self.assertEqual(
                h3.received_assets[0]["asset_voice"]["source_path"], audio
            )

            job = repository.entities[("job", result.local_job_id)]
            self.assertEqual(job.payload["authoring_prompt"], authoring)
            self.assertEqual(job.payload["resolved_prompt"], expected_prompt)
            self.assertEqual(job.payload["prompt_processing_mode_requested"], "raw_en")
            self.assertEqual(job.payload["prompt_processing_mode_effective"], "community")
            self.assertEqual(
                [binding["tag"] for binding in job.payload["bindings"][:4]],
                ["<Picture 1>", "<Video 1>", "<Picture 2>", "<Audio 1>"],
            )
            first_binding = job.payload["bindings"][0]
            self.assertEqual(first_binding["relative_path"], "assets/rin.png")
            self.assertEqual(first_binding["byte_size"], len(b"RIN_IMAGE"))
            self.assertEqual(first_binding["sha256"], sha256(b"RIN_IMAGE").hexdigest())
            metadata_binding = job.payload["bindings"][-1]
            self.assertFalse(metadata_binding["attached"])
            self.assertEqual(metadata_binding["asset_id"], "asset_factory")
            self.assertEqual(
                metadata_binding["replacement"],
                "素材[名前: 旧軌道工場]（説明: 夜明け前の工場。"
                "H3向け特徴: abandoned orbital workshop）",
            )
            self.assertEqual(job.payload["request"]["fields"]["prompt"], expected_prompt)
            self.assertEqual(
                job.payload["request"]["fields"]["prompt_processing_mode"],
                "community",
            )

    def test_metadata_asset_prose_cannot_be_misread_as_h3_dialogue(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project_directory = make_project_directory(directory)
            repository = FakeRepository()
            register_asset(
                repository,
                project_directory,
                asset_id="asset_voice",
                name="少女の声",
                caption="決め台詞は「雨を返して」。",
                prompt_caption='soft "distant" memory voice',
            )
            h3 = FakeH3()
            service = RenderService(repository, h3, directory)

            service.submit_render(
                session_id="ses_001",
                project_id="prj_001",
                expected_revision=1,
                shot_id="sh_012",
                request=render_request(prompt="@少女の声 を思い出す。"),
            )

            sent_prompt = h3.submissions[0].fields.prompt
            self.assertEqual(
                sent_prompt,
                "素材[名前: 少女の声]（説明: 決め台詞は［雨を返して］。"
                "H3向け特徴: soft ＂distant＂ memory voice） を思い出す。",
            )
            for ordinary_quote in ('"', "「", "」", "『", "』", "“", "”"):
                self.assertNotIn(ordinary_quote, sent_prompt)

    def test_unresolved_mention_is_rejected_without_revision_or_h3_submission(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            make_project_directory(directory)
            repository = FakeRepository()
            h3 = FakeH3()
            service = RenderService(repository, h3, directory)

            with self.assertRaisesRegex(RenderServiceError, "unresolved asset mention"):
                service.submit_render(
                    session_id="ses_001",
                    project_id="prj_001",
                    expected_revision=1,
                    shot_id="sh_012",
                    request=render_request(prompt="@存在しない素材が歩く。"),
                )

            self.assertEqual(repository.revision, 1)
            self.assertEqual(repository.patches, [])
            self.assertEqual(h3.submissions, [])

    def test_known_name_prefix_does_not_consume_a_longer_unknown_mention(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project_directory = make_project_directory(directory)
            repository = FakeRepository()
            register_asset(
                repository,
                project_directory,
                asset_id="asset_rin",
                name="凛",
                caption="整備士",
                prompt_caption="mechanic",
            )
            h3 = FakeH3()
            service = RenderService(repository, h3, directory)

            with self.assertRaisesRegex(RenderServiceError, "@凛子"):
                service.submit_render(
                    session_id="ses_001",
                    project_id="prj_001",
                    expected_revision=1,
                    shot_id="sh_012",
                    request=render_request(prompt="@凛子が歩く。"),
                )

            self.assertEqual(repository.revision, 1)
            self.assertEqual(repository.patches, [])
            self.assertEqual(h3.submissions, [])

            # Braces are the explicit syntax when literal text must directly
            # follow a mention without a normal boundary.
            service.submit_render(
                session_id="ses_001",
                project_id="prj_001",
                expected_revision=1,
                shot_id="sh_012",
                request=render_request(prompt="@{凛}子が歩く。"),
            )
            self.assertEqual(
                h3.submissions[0].fields.prompt,
                "素材[名前: 凛]（説明: 整備士。H3向け特徴: mechanic）子が歩く。",
            )

    def test_metadata_expansion_cannot_introduce_an_unresolved_mention(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project_directory = make_project_directory(directory)
            repository = FakeRepository()
            register_asset(
                repository,
                project_directory,
                asset_id="asset_factory",
                name="工場",
                caption="@未登録素材の隣にある工場",
                prompt_caption="abandoned factory",
            )
            h3 = FakeH3()
            service = RenderService(repository, h3, directory)

            with self.assertRaisesRegex(
                RenderServiceError, "resolved prompt contains unresolved asset mention"
            ):
                service.submit_render(
                    session_id="ses_001",
                    project_id="prj_001",
                    expected_revision=1,
                    shot_id="sh_012",
                    request=render_request(prompt="@工場で待つ。"),
                )

            self.assertEqual(repository.revision, 1)
            self.assertEqual(repository.patches, [])
            self.assertEqual(h3.submissions, [])

    def test_asset_hash_mismatch_is_rejected_before_revision_reservation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project_directory = make_project_directory(directory)
            repository = FakeRepository()
            register_asset(
                repository,
                project_directory,
                asset_id="asset_rin",
                name="凛",
                caption="整備士",
                prompt_caption="mechanic",
                file_name="rin.png",
                content=b"ORIGINAL_IMAGE",
                mime_type="image/png",
            )
            entity = repository.entities[("asset", "asset_rin")]
            repository.entities[("asset", "asset_rin")] = EntityState(
                entity_type=entity.entity_type,
                entity_id=entity.entity_id,
                revision=entity.revision,
                payload={**dict(entity.payload), "sha256": "0" * 64},
            )
            h3 = FakeH3()
            service = RenderService(repository, h3, directory)

            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                service.submit_render(
                    session_id="ses_001",
                    project_id="prj_001",
                    expected_revision=1,
                    shot_id="sh_012",
                    request=render_request(
                        mode="omni",
                        prompt="@凛が歩く。",
                        reference_asset_ids=("asset_rin",),
                    ),
                )

            self.assertEqual(repository.revision, 1)
            self.assertEqual(repository.patches, [])
            self.assertEqual(h3.submissions, [])

    def test_completed_sync_writes_artifact_then_commits_asset(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            make_project_directory(directory)
            repository = FakeRepository()
            h3 = FakeH3()
            service = RenderService(repository, h3, directory)
            submission = service.submit_render(
                session_id="ses_001",
                project_id="prj_001",
                expected_revision=1,
                shot_id="sh_012",
                request=render_request(),
            )
            h3.job = h3_job(H3JobStatus.COMPLETED, progress=1.0)

            synced = service.sync_render(
                session_id="ses_001",
                project_id="prj_001",
                expected_revision=submission.revision,
                local_job_id=submission.local_job_id,
            )

            self.assertEqual(synced.status, "completed")
            self.assertEqual(synced.progress, 1.0)
            self.assertEqual(synced.artifact_id, f"asset_{submission.take_id}")
            self.assertIsNotNone(synced.artifact_sha256)
            artifact = Path(directory, "projects", "demo", synced.artifact_relative_path)
            self.assertEqual(artifact.read_bytes(), b"demo-mp4")
            asset = repository.entities[("asset", f"asset_{submission.take_id}")]
            self.assertEqual(asset.payload["relative_path"], synced.artifact_relative_path)

    def test_completed_sync_can_register_end_frame_for_continuity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            make_project_directory(directory)
            repository = FakeRepository()
            h3 = FakeH3()
            service = RenderService(repository, h3, directory)
            submission = service.submit_render(
                session_id="ses_001",
                project_id="prj_001",
                expected_revision=1,
                shot_id="sh_012",
                request=render_request(),
            )
            h3.job = h3_job(H3JobStatus.COMPLETED, progress=1.0)
            with patch.object(
                service,
                "_extract_end_frame",
                return_value=("outputs/sh_012/take-end-frame.png", "frame-hash", 1234),
            ) as extract:
                synced = service.sync_render(
                    session_id="ses_001",
                    project_id="prj_001",
                    expected_revision=submission.revision,
                    local_job_id=submission.local_job_id,
                    extract_end_frame=True,
                )

            end_frame_id = f"asset_{submission.take_id}_end_frame"
            self.assertEqual(synced.end_frame_asset_id, end_frame_id)
            self.assertEqual(repository.entities[("take", submission.take_id)].payload["end_frame_asset_id"], end_frame_id)
            self.assertEqual(repository.entities[("asset", end_frame_id)].payload["sha256"], "frame-hash")
            extract.assert_called_once()

    def test_project_root_cannot_escape_local_story_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            make_project_directory(directory)
            repository = FakeRepository()
            h3 = FakeH3()
            service = RenderService(repository, h3, directory)
            submission = service.submit_render(
                session_id="ses_001",
                project_id="prj_001",
                expected_revision=1,
                shot_id="sh_012",
                request=render_request(),
            )
            repository.get_context = lambda *_args, **_kwargs: SimpleNamespace(
                revision=submission.revision,
                project_root="../escape",
                entities=tuple(repository.entities.values()),
            )
            h3.job = h3_job(H3JobStatus.COMPLETED, progress=1.0)

            with self.assertRaisesRegex(RenderServiceError, "escaped"):
                service.sync_render(
                    session_id="ses_001",
                    project_id="prj_001",
                    expected_revision=submission.revision,
                    local_job_id=submission.local_job_id,
                )


if __name__ == "__main__":
    unittest.main()
