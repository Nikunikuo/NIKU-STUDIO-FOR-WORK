from __future__ import annotations

import copy
import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
if os.fspath(ROOT) not in sys.path:
    sys.path.insert(0, os.fspath(ROOT))

from webui.job_manager import JobManager  # noqa: E402
import webui.job_manager as job_manager_module  # noqa: E402


class IsolatedManager:
    """JobManager-shaped test double that never starts a runner thread."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.jobs_dir = self.root / "webui_data" / "jobs"
        self.outputs_dir = self.root / "outputs" / "webui"
        self.jobs_dir.mkdir(parents=True)
        self.outputs_dir.mkdir(parents=True)
        self.submitted: list[dict] = []

    def submit(self, job: dict) -> dict:
        saved = copy.deepcopy(job)
        self.submitted.append(saved)
        job_dir = self.jobs_dir / saved["id"]
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "job.json").write_text(
            json.dumps(saved, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return copy.deepcopy(saved)

    def start(self) -> None:
        raise AssertionError("the integration test must not start a JobManager thread")

    def stop(self) -> None:
        raise AssertionError("the integration test must not stop a runner")

    def list_jobs(self) -> list[dict]:
        return copy.deepcopy(self.submitted)

    def current_job_id(self) -> None:
        return None


class ComfyServerIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        cls.test_root = Path(cls.temporary.name)
        cls.manager = IsolatedManager(cls.test_root)

        sys.modules.pop("webui.server", None)
        with patch.object(job_manager_module, "JobManager", return_value=cls.manager):
            cls.server = importlib.import_module("webui.server")

        cls.original_model_root = cls.server.COMFY_MODEL_ROOT
        cls.original_model_files = cls.server.COMFY_MODEL_FILES
        cls.original_planner_status = cls.server.community_planner_status
        cls.model_root = cls.test_root / "models" / "comfy"
        model_files = {
            "fl2va": cls.model_root / "diffusion_models" / "fl2va.safetensors",
            "ref2va": cls.model_root / "diffusion_models" / "ref2va.safetensors",
            "text_encoder": cls.model_root / "text_encoders" / "qwen.safetensors",
            "video_vae": cls.model_root / "vae" / "video_vae.safetensors",
            "audio_vae": cls.model_root / "vae" / "audio_vae.safetensors",
        }
        for model_path in model_files.values():
            model_path.parent.mkdir(parents=True, exist_ok=True)
            model_path.write_bytes(b"test-model-marker")
        cls.server.COMFY_MODEL_ROOT = cls.model_root
        cls.server.COMFY_MODEL_FILES = model_files
        cls.server.community_planner_status = lambda _root: {
            "ready": True,
            "status": "ready",
            "model": "Qwen/Qwen3-4B-Instruct-2507",
            "repo_id": "Qwen/Qwen3-4B-Instruct-2507",
            "revision": "cdbee75f17c01a7cc42f958dc650907174af0554",
            "local_only": True,
            "model_inference": True,
            "total_bytes": 8_056_459_158,
            "missing_files": [],
            "invalid_files": [],
        }
        cls.client = TestClient(cls.server.app, base_url="http://127.0.0.1")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.server.COMFY_MODEL_ROOT = cls.original_model_root
        cls.server.COMFY_MODEL_FILES = cls.original_model_files
        cls.server.community_planner_status = cls.original_planner_status
        sys.modules.pop("webui.server", None)
        cls.temporary.cleanup()

    def setUp(self) -> None:
        self.manager.submitted.clear()

    @staticmethod
    def _t2v_form(
        *,
        steps: int = 20,
        acceleration: str = "balanced",
        ref_image_size: str = "max",
    ) -> dict[str, str]:
        return {
            "mode": "t2v",
            "style": "natural",
            "prompt": "A paper airplane circles through warm afternoon sunlight.",
            "width": "640",
            "height": "384",
            "num_frames": "243",
            "steps": str(steps),
            "seed": "424242",
            "acceleration": acceleration,
            "ref_image_size": ref_image_size,
            "prompt_processing_mode": "community",
            "audio_preset": "auto",
            "dialogue": "",
            "soundscape": "",
            "music_policy": "auto",
            "audio_gain_db": "-2",
        }

    def _post_multipart_t2v(self, **overrides: str):
        form = self._t2v_form()
        form.update(overrides)
        files = {
            "first_image": (
                "reference.png",
                b"\x89PNG\r\n\x1a\nfixture",
                "image/png",
            )
        }
        return self.client.post(
            "/api/jobs",
            data=form,
            files=files,
            headers={
                self.server.LOCAL_MUTATION_HEADER:
                    self.server.LOCAL_MUTATION_VALUE
            },
        )

    def _saved_request(self) -> tuple[dict, dict]:
        self.assertEqual(len(self.manager.submitted), 1)
        job = self.manager.submitted[0]
        request = json.loads(
            (self.manager.jobs_dir / job["id"] / "request.json").read_text(
                encoding="utf-8"
            )
        )
        return job, request

    def test_public_job_removes_private_engine_paths(self) -> None:
        original = {
            "id": "private-paths",
            "status": "completed",
            "technical": {
                "engine": {
                    "comfyui_commit": "abc123",
                    "variant": "ref2va",
                    "models_root": r"C:\Users\private\models",
                    "log_path": r"C:\Users\private\job.log",
                    "comfy_pid": 1234,
                }
            },
        }
        public = self.server._public_job(original)
        engine = public["technical"]["engine"]
        self.assertEqual(engine["comfyui_commit"], "abc123")
        self.assertEqual(engine["variant"], "ref2va")
        self.assertNotIn("models_root", engine)
        self.assertNotIn("log_path", engine)
        self.assertNotIn("comfy_pid", engine)
        self.assertIn("models_root", original["technical"]["engine"])

    def test_capabilities_expose_only_current_prompt_paths(self) -> None:
        capabilities = self.server.capabilities()

        self.assertEqual(capabilities["backend"], "comfy")
        self.assertTrue(capabilities["ready"])
        self.assertEqual(len(capabilities["model_files"]), 5)
        self.assertTrue(all(capabilities["model_files"].values()))
        self.assertTrue(all(capabilities["modes"].values()))
        self.assertNotIn("prompt_translator", capabilities)
        planner = capabilities["prompt_planner"]
        self.assertTrue(planner["ready"])
        self.assertEqual(planner["repo_id"], "Qwen/Qwen3-4B-Instruct-2507")
        self.assertEqual(
            planner["revision"],
            "cdbee75f17c01a7cc42f958dc650907174af0554",
        )
        self.assertEqual(
            capabilities["prompt_processing"]["options"],
            ["community", "raw_en"],
        )
        self.assertEqual(capabilities["prompt_processing"]["default"], "community")
        self.assertEqual(
            capabilities["prompt_processing"]["native_comfy_profile"],
            "native_clean",
        )
        audio_reference = capabilities["audio_controls"]["reference_audio"]
        self.assertEqual(audio_reference["no_dialogue_default"], "full_content")
        self.assertNotIn("legacy_persisted_request_fallback", audio_reference)

    def test_local_browser_boundary_blocks_rebinding_and_cross_site_posts(self) -> None:
        bad_host = self.client.get("/api/state", headers={"host": "evil.example"})
        cross_site = self.client.post(
            "/api/open-output-folder",
            headers={
                "host": "127.0.0.1",
                "sec-fetch-site": "cross-site",
                self.server.LOCAL_MUTATION_HEADER:
                    self.server.LOCAL_MUTATION_VALUE,
            },
        )
        missing_header = self.client.post(
            "/api/open-output-folder",
            headers={"host": "127.0.0.1"},
        )

        self.assertEqual(bad_host.status_code, 421)
        self.assertEqual(cross_site.status_code, 403)
        self.assertEqual(missing_header.status_code, 403)

    def test_declared_aggregate_upload_limit_is_rejected_before_parsing(self) -> None:
        response = self.client.post(
            "/api/jobs",
            content=b"not-a-multipart-body",
            headers={
                "host": "127.0.0.1",
                "content-type": "multipart/form-data; boundary=fixture",
                "content-length": str(self.server.MAX_REQUEST_BYTES + 1),
                self.server.LOCAL_MUTATION_HEADER:
                    self.server.LOCAL_MUTATION_VALUE,
            },
        )
        self.assertEqual(response.status_code, 413)

    def test_omni_reference_count_is_rejected_before_job_creation(self) -> None:
        form = self._t2v_form()
        form.update(mode="omni", prompt="Use <Picture 1> as the subject reference.")
        files = [
            ("references", (f"reference-{index}.png", b"image", "image/png"))
            for index in range(13)
        ]
        before = set(self.manager.jobs_dir.iterdir())
        response = self.client.post(
            "/api/jobs",
            data=form,
            files=files,
            headers={
                self.server.LOCAL_MUTATION_HEADER:
                    self.server.LOCAL_MUTATION_VALUE
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.manager.submitted, [])
        self.assertEqual(set(self.manager.jobs_dir.iterdir()), before)

    def test_community_request_persists_native_clean_queue_contract(self) -> None:
        prompt = "白い紙飛行機が夕日の中を大きく旋回する。"
        response = self._post_multipart_t2v(prompt=prompt)
        self.assertEqual(response.status_code, 200, response.text)
        job, request = self._saved_request()

        for saved in (job, request):
            self.assertEqual(saved["backend"], "comfy")
            self.assertEqual(saved["workflow_profile"], "native_clean")
            self.assertEqual(saved["prompt_processing_mode"], "community")
            self.assertEqual(saved["prompt"], prompt)
            self.assertEqual(saved["prompt_processing"]["mode"], "community_planner")
        self.assertEqual(request["effective_prompt"], prompt)
        self.assertEqual(request["model_path"], os.fspath(self.server.COMFY_MODEL_ROOT))
        self.assertNotIn("<d>", request["effective_prompt"])

    def test_raw_en_is_byte_preserving_and_rejects_legacy_or_untranslated_input(self) -> None:
        prompt = (
            "  Style: soft cel animation.  \r\n"
            "Scene: A girl waves.\n"
            'Audio: She says once in Japanese: "こんにちは。"\r\n\r\n'
        )
        response = self._post_multipart_t2v(
            prompt=prompt,
            prompt_processing_mode="raw_en",
        )
        self.assertEqual(response.status_code, 200, response.text)
        job, request = self._saved_request()
        self.assertEqual(job["prompt"], prompt)
        self.assertEqual(request["prompt"], prompt)
        self.assertEqual(request["effective_prompt"], prompt)
        self.assertEqual(request["prompt_processing"]["mode"], "native_raw")

        self.manager.submitted.clear()
        old_tag = self._post_multipart_t2v(
            prompt='Audio: She says <d>[Japanese] こんにちは。</d>',
            prompt_processing_mode="raw_en",
        )
        untranslated = self._post_multipart_t2v(
            prompt="少女が海辺で手を振る。",
            prompt_processing_mode="raw_en",
        )
        auxiliary_field = self._post_multipart_t2v(
            prompt="A girl waves beside the sea.",
            prompt_processing_mode="raw_en",
            soundscape="Gentle surf.",
        )
        self.assertEqual(old_tag.status_code, 400)
        self.assertEqual(untranslated.status_code, 400)
        self.assertEqual(auxiliary_field.status_code, 400)
        self.assertEqual(self.manager.submitted, [])

    def test_removed_prompt_modes_are_rejected_before_job_creation(self) -> None:
        before = set(self.manager.jobs_dir.iterdir())
        for removed_mode in ("direct", "official_en", "context_ir"):
            with self.subTest(mode=removed_mode):
                response = self._post_multipart_t2v(
                    prompt_processing_mode=removed_mode
                )
                self.assertEqual(response.status_code, 400)
        self.assertEqual(self.manager.submitted, [])
        self.assertEqual(set(self.manager.jobs_dir.iterdir()), before)

    def test_dialogue_field_rejects_removed_control_syntax(self) -> None:
        for dialogue in (
            "<d>[Japanese] こんにちは。</d>",
            "<|reserved|>",
            "overall_soundscape:\nreplacement",
        ):
            with self.subTest(dialogue=dialogue):
                response = self._post_multipart_t2v(dialogue=dialogue)
                self.assertEqual(response.status_code, 400)
        self.assertEqual(self.manager.submitted, [])

    def test_draft_steps_disable_easycache_but_keep_requested_preset(self) -> None:
        response = self._post_multipart_t2v(steps="7", acceleration="balanced")
        self.assertEqual(response.status_code, 200, response.text)
        job, request = self._saved_request()
        for saved in (job, request):
            self.assertEqual(saved["acceleration"], "balanced")
            self.assertEqual(saved["cache"]["requested"], "balanced")
            self.assertEqual(saved["cache"]["effective"], "off")
            self.assertFalse(saved["cache"]["enabled"])
            self.assertEqual(saved["cache"]["reason"], "steps_below_12")

    def test_invalid_parameters_are_rejected_before_job_creation(self) -> None:
        before = set(self.manager.jobs_dir.iterdir())
        bad_acceleration = self._post_multipart_t2v(acceleration="turbo")
        bad_reference_policy = self._post_multipart_t2v(ref_image_size="original")
        bad_prompt_processing = self._post_multipart_t2v(
            prompt_processing_mode="translate-everything"
        )
        self.assertEqual(bad_acceleration.status_code, 400)
        self.assertEqual(bad_reference_policy.status_code, 400)
        self.assertEqual(bad_prompt_processing.status_code, 400)
        self.assertEqual(self.manager.submitted, [])
        self.assertEqual(set(self.manager.jobs_dir.iterdir()), before)


class JobManagerComfyBoundaryTests(unittest.TestCase):
    def test_h3event_merges_cache_timing_and_public_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manager = JobManager(Path(temporary))
            manager.submit(
                {
                    "id": "merge-test",
                    "status": "running",
                    "cache": {
                        "requested": "balanced",
                        "effective": "balanced",
                        "reuse_threshold": 0.30,
                        "skipped_steps": 0,
                    },
                    "timings": {"queued_seconds": 1.25},
                }
            )
            manager._handle_event(  # noqa: SLF001
                "merge-test",
                json.dumps(
                    {
                        "status": "completed",
                        "backend": "comfy",
                        "acceleration": "balanced",
                        "scheduler": {"requested": "auto", "effective": "normal"},
                        "cache": {"skipped_steps": 7, "speedup": 1.35},
                        "timings": {"generation_seconds": 870.0},
                        "diagnostics": {
                            "comfyui_commit": "abc123",
                            "variant": "ref2va",
                            "models_root": r"C:\Users\private\models",
                            "log_path": r"C:\Users\private\job.log",
                            "comfy_pid": 1234,
                        },
                    }
                ),
            )
            saved = manager.get_job("merge-test")

            assert saved is not None
            self.assertEqual(saved["status"], "completed")
            self.assertEqual(saved["scheduler"], {"requested": "auto", "effective": "normal"})
            self.assertEqual(saved["cache"]["requested"], "balanced")
            self.assertEqual(saved["cache"]["reuse_threshold"], 0.30)
            self.assertEqual(saved["cache"]["skipped_steps"], 7)
            self.assertEqual(saved["cache"]["speedup"], 1.35)
            self.assertEqual(
                saved["timings"],
                {"queued_seconds": 1.25, "generation_seconds": 870.0},
            )
            engine = saved["technical"]["engine"]
            self.assertEqual(engine["comfyui_commit"], "abc123")
            self.assertEqual(engine["variant"], "ref2va")
            self.assertNotIn("models_root", engine)
            self.assertNotIn("log_path", engine)
            self.assertNotIn("comfy_pid", engine)

    def test_engine_command_uses_dedicated_comfy_venv_worker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            python = root / ".comfy-venv" / "Scripts" / "python.exe"
            python.parent.mkdir(parents=True)
            python.write_bytes(b"test executable marker")
            manager = JobManager(root)
            process = Mock()
            process.poll.return_value = None

            process_job = Mock()
            with (
                patch.object(job_manager_module.subprocess, "Popen", return_value=process) as popen,
                patch.object(job_manager_module, "ProcessJob", return_value=process_job),
            ):
                returned = manager._ensure_engine("fl2va")  # noqa: SLF001

            self.assertIs(returned, process)
            process_job.attach.assert_called_once_with(process)
            command = popen.call_args.args[0]
            self.assertEqual(
                command,
                [os.fspath(python), "-m", "webui.comfy_engine_worker", "--serve"],
            )
            self.assertEqual(popen.call_args.kwargs["cwd"], root.resolve())
            self.assertEqual(popen.call_args.kwargs["env"]["PYTHONIOENCODING"], "utf-8")
            self.assertEqual(popen.call_args.kwargs["env"]["PYTHONUNBUFFERED"], "1")

    def test_job_object_creation_failure_never_starts_uncontained_worker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            python = root / ".comfy-venv" / "Scripts" / "python.exe"
            python.parent.mkdir(parents=True)
            python.write_bytes(b"test executable marker")
            manager = JobManager(root)

            with (
                patch.object(job_manager_module, "ProcessJob", side_effect=OSError("job failed")),
                patch.object(job_manager_module.subprocess, "Popen") as popen,
            ):
                with self.assertRaisesRegex(OSError, "job failed"):
                    manager._ensure_engine("fl2va")  # noqa: SLF001

            popen.assert_not_called()
            self.assertIsNone(manager._current_process)  # noqa: SLF001
            self.assertIsNone(manager._current_process_job)  # noqa: SLF001

    def test_cancel_claims_exact_current_engine_before_releasing_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manager = JobManager(Path(temporary))
            old_process = Mock(pid=101)
            old_process.poll.return_value = None
            old_job = Mock()
            new_process = Mock(pid=202)
            new_process.poll.return_value = None
            new_job = Mock()
            manager._jobs["old"] = {"id": "old", "status": "running"}  # noqa: SLF001
            manager._current_job_id = "old"  # noqa: SLF001
            manager._current_process = old_process  # noqa: SLF001
            manager._current_process_job = old_job  # noqa: SLF001

            def cleanup(process, process_job):
                self.assertIs(process, old_process)
                self.assertIs(process_job, old_job)
                with manager._lock:  # noqa: SLF001
                    manager._current_process = new_process  # noqa: SLF001
                    manager._current_process_job = new_job  # noqa: SLF001

            with patch.object(manager, "_cleanup_owned_process", side_effect=cleanup):
                cancelled = manager.cancel("old")

            assert cancelled is not None
            self.assertEqual(cancelled["status"], "cancelled")
            self.assertIs(manager._current_process, new_process)  # noqa: SLF001
            self.assertIs(manager._current_process_job, new_job)  # noqa: SLF001


if __name__ == "__main__":
    unittest.main()
