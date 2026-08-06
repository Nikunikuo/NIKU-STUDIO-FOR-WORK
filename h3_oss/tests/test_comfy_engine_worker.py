from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
if os.fspath(ROOT) not in sys.path:
    sys.path.insert(0, os.fspath(ROOT))

from webui import comfy_engine_worker as worker  # noqa: E402
from webui.comfy_workflow import COMFYUI_COMMIT  # noqa: E402


class ComfyEngineWorkerTests(unittest.TestCase):
    def test_cache_schema_has_stable_shape_and_disables_short_runs(self):
        enabled = worker.cache_schema("balanced", 20)
        self.assertEqual(
            set(enabled),
            {
                "requested",
                "effective",
                "enabled",
                "reason",
                "reuse_threshold",
                "start_percent",
                "end_percent",
                "skipped_steps",
                "total_steps",
                "speedup",
            },
        )
        self.assertTrue(enabled["enabled"])
        self.assertEqual(enabled["effective"], "balanced")
        self.assertEqual(enabled["reuse_threshold"], 0.30)

        short = worker.cache_schema("conservative", 8)
        self.assertFalse(short["enabled"])
        self.assertEqual(short["effective"], "off")
        self.assertEqual(short["skipped_steps"], 0)
        self.assertEqual(short["speedup"], 1.0)
        self.assertIn("below_12", short["reason"])

    def test_progress_tracker_parses_denoise_and_easycache_summary(self):
        tracker = worker.ProgressTracker("balanced", 20, "native_clean")
        event = tracker.consume(" 50%|######## | 10/20 [01:00<01:00, 6.0s/it]")
        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event["step"], 10)
        self.assertEqual(event["total_steps"], 20)
        self.assertEqual(event["backend"], "comfy")
        self.assertEqual(event["workflow_profile"], "native_clean")
        self.assertIsNone(tracker.consume("100%|########| 13/13 [00:01, 10it/s]"))

        tracker.consume("EasyCache - skipped 7/20 steps (1.54x speedup).")
        self.assertEqual(tracker.cache["skipped_steps"], 7)
        self.assertEqual(tracker.cache["total_steps"], 20)
        self.assertEqual(tracker.cache["speedup"], 1.54)

    def test_command_is_local_pinned_and_has_all_isolation_arguments(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            paths = worker.make_runtime_paths(root, "unit")
            command = worker.build_comfy_command(root, paths, 49123)
        self.assertEqual(command[0], os.fspath(root / ".comfy-venv" / "Scripts" / "python.exe"))
        self.assertEqual(command[1], os.fspath(root / ".upstream" / "ComfyUI" / "main.py"))
        for flag in (
            "--listen",
            "--port",
            "--base-directory",
            "--models-directory",
            "--input-directory",
            "--output-directory",
            "--temp-directory",
            "--user-directory",
            "--database-url",
            "--disable-all-custom-nodes",
            "--disable-api-nodes",
            "--async-offload",
            "--log-stdout",
        ):
            self.assertIn(flag, command)
        self.assertEqual(command[command.index("--listen") + 1], "127.0.0.1")
        self.assertEqual(command[command.index("--async-offload") + 1], "2")
        self.assertNotIn("--whitelist-custom-nodes", command)
        self.assertIn("--use-sage-attention", command)
        self.assertNotIn("0.0.0.0", command)

        with mock.patch.dict(os.environ, {"H3_ATTENTION_BACKEND": "pytorch"}):
            fallback_command = worker.build_comfy_command(root, paths, 49124)
        self.assertNotIn("--use-sage-attention", fallback_command)

    def test_command_rejects_removed_workflow_profiles(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            paths = worker.make_runtime_paths(root, "native")
            command = worker.build_comfy_command(
                root,
                paths,
                49125,
                workflow_profile_name="native_clean",
            )

        self.assertIn("--disable-all-custom-nodes", command)
        self.assertNotIn("--whitelist-custom-nodes", command)
        with self.assertRaisesRegex(ValueError, "workflow_profile"):
            worker.build_comfy_command(
                root,
                paths,
                49126,
                workflow_profile_name="untrusted",
            )

    def test_verify_installation_checks_fixed_sha_size_and_download_marker(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source = root / ".upstream" / "ComfyUI"
            (source / "comfy_extras").mkdir(parents=True)
            (source / "main.py").write_text("# fixed\n", encoding="utf-8")
            (source / "comfy_extras" / "nodes_minimax_h3.py").write_text("H3\n", encoding="utf-8")
            (source / "comfy_extras" / "nodes_easycache.py").write_text("EasyCache\n", encoding="utf-8")
            python_exe = root / ".comfy-venv" / "Scripts" / "python.exe"
            python_exe.parent.mkdir(parents=True)
            python_exe.write_bytes(b"python")

            fake_specs: dict[str, tuple[str, int, str]] = {}
            required_names = (
                worker.FL2VA_MODEL,
                worker.REF2VA_MODEL,
                worker.TEXT_ENCODER_MODEL,
                worker.VIDEO_VAE_MODEL,
                worker.AUDIO_VAE_MODEL,
            )
            for index, name in enumerate(required_names):
                relative = f"kind{index}/{name}"
                lfs = f"{index:064x}"
                model = root / "models" / "comfy" / relative
                model.parent.mkdir(parents=True, exist_ok=True)
                header = b"{}"
                payload = len(header).to_bytes(8, "little") + header + b"x"
                model.write_bytes(payload)
                metadata = (
                    root
                    / "models"
                    / "comfy"
                    / ".cache"
                    / "huggingface"
                    / "download"
                    / f"{relative}.metadata"
                )
                metadata.parent.mkdir(parents=True, exist_ok=True)
                metadata.write_text(f"{worker.COMFY_MODEL_REVISION}\n{lfs}\n0\n", encoding="utf-8")
                fake_specs[name] = (relative, len(payload), lfs)

            with (
                mock.patch.object(worker, "MODEL_SPECS", fake_specs),
                mock.patch.object(worker, "_git_output", side_effect=[COMFYUI_COMMIT, ""]),
                mock.patch.dict(os.environ, {"H3_ATTENTION_BACKEND": "pytorch"}),
            ):
                report = worker.verify_installation(root, "fl2va")
            self.assertEqual(report["comfyui_commit"], COMFYUI_COMMIT)
            self.assertEqual(report["model_revision"], worker.COMFY_MODEL_REVISION)
            self.assertEqual(report["variant"], "fl2va")

    def test_private_listener_must_belong_to_spawned_process_tree(self):
        process = mock.Mock(pid=4100)
        child = mock.Mock(pid=4200)
        root = mock.Mock(pid=4100)
        root.children.return_value = [child]
        owned_listener = mock.Mock(
            status=worker.psutil.CONN_LISTEN,
            laddr=("127.0.0.1", 49123),
            pid=4200,
        )
        foreign_listener = mock.Mock(
            status=worker.psutil.CONN_LISTEN,
            laddr=("127.0.0.1", 49123),
            pid=9999,
        )

        with (
            mock.patch.object(worker.psutil, "Process", return_value=root),
            mock.patch.object(worker.psutil, "net_connections", return_value=[owned_listener]),
        ):
            worker.verify_loopback_listener_owner(process, 49123)

        with (
            mock.patch.object(worker.psutil, "Process", return_value=root),
            mock.patch.object(worker.psutil, "net_connections", return_value=[foreign_listener]),
        ):
            with self.assertRaisesRegex(RuntimeError, "not owned exclusively"):
                worker.verify_loopback_listener_owner(process, 49123)

    def test_stage_inputs_uses_safe_internal_names_and_keeps_type_order(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source_dir = root / "webui_data" / "jobs" / "abc" / "inputs"
            source_dir.mkdir(parents=True)
            first = source_dir / "first image.png"
            picture_a = source_dir / "a.webp"
            audio = source_dir / "voice.wav"
            picture_b = source_dir / "b.jpg"
            for path, data in (
                (first, b"first"),
                (picture_a, b"a"),
                (audio, b"audio"),
                (picture_b, b"b"),
            ):
                path.write_bytes(data)
            paths = worker.make_runtime_paths(root, "abc")
            request = {
                "id": "../unsafe id",
                "first_image": os.fspath(first),
                "references": [
                    {"kind": "image", "stored_path": os.fspath(picture_a)},
                    {"kind": "audio", "stored_path": os.fspath(audio)},
                    {"kind": "image", "stored_path": os.fspath(picture_b)},
                ],
            }
            staged, stage_dir = worker.stage_request_inputs(request, paths, root)
            self.assertTrue(worker._is_within(paths.input, stage_dir.resolve()))
            self.assertNotIn("..", staged["input_filename"])
            self.assertEqual(len(staged["reference_image_filenames"]), 2)
            self.assertEqual(len(staged["reference_audio_filenames"]), 1)
            self.assertEqual(staged["reference_video_filenames"], [])
            for filename in (
                [staged["input_filename"]]
                + staged["reference_image_filenames"]
                + staged["reference_audio_filenames"]
            ):
                self.assertTrue((paths.input / Path(*Path(filename).parts)).is_file())

    def test_stage_inputs_rejects_a_source_outside_project(self):
        with tempfile.TemporaryDirectory() as project, tempfile.TemporaryDirectory() as outside:
            root = Path(project).resolve()
            source = Path(outside).resolve() / "outside.png"
            source.write_bytes(b"not allowed")
            paths = worker.make_runtime_paths(root, "unit")
            request = {"id": "unit", "first_image": os.fspath(source), "references": []}
            with self.assertRaisesRegex(ValueError, "escapes"):
                worker.stage_request_inputs(request, paths, root)

    def test_stage_inputs_fail_closed_if_suppressed_audio_reaches_worker(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source = root / "webui_data" / "jobs" / "unit" / "inputs" / "voice.wav"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"audio")
            paths = worker.make_runtime_paths(root, "unit")
            request = {
                "id": "unit",
                "standalone_audio_conditioning": False,
                "references": [{"kind": "audio", "stored_path": os.fspath(source)}],
            }

            with self.assertRaisesRegex(ValueError, "conditioning was disabled"):
                worker.stage_request_inputs(request, paths, root)

            self.assertEqual(list(paths.input.iterdir()), [])

    def test_find_video_descriptor_prefers_save_video_node(self):
        history = {
            "outputs": {
                "12": {"images": [{"filename": "preview.png", "subfolder": "", "type": "temp"}]},
                worker.SAVE_VIDEO_NODE_ID: {
                    "videos": [{"filename": "final.mp4", "subfolder": "h3", "type": "output"}]
                },
            }
        }
        self.assertEqual(worker._find_video_descriptor(history)["filename"], "final.mp4")

    def test_request_translation_forwards_reference_policy_and_string_codec(self):
        staged = {
            "input_filename": None,
            "last_frame_filename": None,
            "reference_image_filenames": ["job/00_image.png"],
            "reference_video_filenames": [
                "job/01_video.mp4",
                "job/02_video.mp4",
            ],
            "reference_audio_filenames": [],
        }
        request = {
            "mode": "omni",
            "effective_prompt": "Use <Picture 1> as the subject.",
            "width": 640,
            "height": 384,
            "num_frames": 124,
            "steps": 20,
            "seed": 42,
            "audio_gain_db": -2,
            "ref_image_size": "max",
            "embedded_video_audio_policy": "reference",
            "embedded_video_audio_indices": [1],
        }
        with mock.patch.object(
            worker,
            "build_h3_workflow",
            wraps=worker.build_h3_workflow,
        ) as build_workflow:
            workflow = worker.build_request_workflow(
                request,
                staged,
                requested_acceleration="balanced",
                output_prefix="h3/unit",
            )
        self.assertEqual(
            build_workflow.call_args.kwargs["embedded_video_audio_indices"],
            [1],
        )
        conditioning = workflow["prompt"]["9"]["inputs"]
        save_video = workflow["prompt"][worker.SAVE_VIDEO_NODE_ID]["inputs"]
        self.assertEqual(conditioning["ref_image_size"], "max")
        self.assertEqual(save_video["format"], "auto")
        self.assertEqual(save_video["codec"], "auto")
        self.assertEqual(workflow["metadata"]["requested_easycache"], "balanced")
        self.assertEqual(workflow["metadata"]["requested_scheduler"], "auto")
        self.assertEqual(workflow["metadata"]["effective_scheduler"], "simple")
        self.assertEqual(workflow["metadata"]["embedded_video_audio_indices"], [1])
        self.assertEqual(workflow["prompt"]["8"]["inputs"]["scheduler"], "simple")
        self.assertNotIn(
            "ref_video_audios.ref_video_audio_0",
            conditioning,
        )
        self.assertEqual(
            conditioning["ref_video_audios.ref_video_audio_1"],
            ["203", 1],
        )

    def test_native_clean_forwards_prompt_byte_for_byte_with_public_baseline(self):
        prompt = (
            "Scene overview: a quiet room.  \r\n"
            "Audio: room tone only.\n"
            'Dialogue: She says, "日本語の台詞。"\n\n'
        )
        staged = {
            "input_filename": None,
            "last_frame_filename": None,
            "reference_image_filenames": [],
            "reference_video_filenames": [],
            "reference_audio_filenames": [],
        }
        request = {
            "mode": "t2v",
            "workflow_profile": "native_clean",
            "effective_prompt": prompt,
            "width": 864,
            "height": 480,
            "num_frames": 124,
            "steps": 20,
            "seed": 7,
        }

        workflow = worker.build_request_workflow(
            request,
            staged,
            requested_acceleration=worker._requested_acceleration(request),
            output_prefix="h3/native-clean",
        )
        conditioning = workflow["prompt"]["9"]["inputs"]

        self.assertEqual(conditioning["prompt"].encode("utf-8"), prompt.encode("utf-8"))
        self.assertEqual(conditioning["width"], 864)
        self.assertEqual(conditioning["height"], 480)
        self.assertEqual(conditioning["length"], 124)
        self.assertEqual(workflow["prompt"]["8"]["inputs"]["steps"], 20)
        self.assertEqual(workflow["prompt"]["8"]["inputs"]["scheduler"], "simple")
        self.assertEqual(workflow["prompt"]["7"]["inputs"]["sampler_name"], "res_multistep")
        self.assertEqual(workflow["metadata"]["workflow_profile"], "native_clean")
        self.assertEqual(workflow["metadata"]["effective_easycache"], "community")
        self.assertEqual(workflow["prompt"]["2"]["class_type"], "EasyCache")

    def test_effective_prompt_must_already_be_a_string(self):
        request = {
            "mode": "t2v",
            "workflow_profile": "native_clean",
            "effective_prompt": ["must", "not", "coerce"],
            "width": 864,
            "height": 480,
            "num_frames": 124,
            "steps": 20,
            "seed": 7,
        }
        staged = {
            "input_filename": None,
            "last_frame_filename": None,
            "reference_image_filenames": [],
            "reference_video_filenames": [],
            "reference_audio_filenames": [],
        }
        with self.assertRaisesRegex(TypeError, "effective_prompt"):
            worker.build_request_workflow(
                request,
                staged,
                requested_acceleration="off",
                output_prefix="h3/reject",
            )

    def test_terminate_process_tree_terminates_children_before_parent(self):
        process = mock.Mock(pid=1234)
        process.poll.return_value = None
        parent = mock.Mock()
        child = mock.Mock()
        parent.children.return_value = [child]
        with (
            mock.patch.object(worker.psutil, "Process", return_value=parent),
            mock.patch.object(worker.psutil, "wait_procs", return_value=([child, parent], [])) as wait,
        ):
            worker.terminate_process_tree(process)
        child.terminate.assert_called_once_with()
        parent.terminate.assert_called_once_with()
        wait.assert_called_once()

    def test_emit_is_ascii_line_protocol(self):
        output = io.StringIO()
        with redirect_stdout(output):
            worker.emit(status="running", message="日本語")
        line = output.getvalue().strip()
        self.assertTrue(line.startswith(worker.EVENT_PREFIX))
        self.assertTrue(line.isascii())
        payload = json.loads(line[len(worker.EVENT_PREFIX) :])
        self.assertEqual(payload["message"], "日本語")

    def test_failure_event_preserves_invalid_attention_backend_error(self):
        output = io.StringIO()
        with (
            mock.patch.dict(os.environ, {"H3_ATTENTION_BACKEND": "not-a-backend"}),
            mock.patch.object(worker.traceback, "print_exc"),
            redirect_stdout(output),
        ):
            worker._emit_failure(ValueError("original startup error"))
        payload = json.loads(output.getvalue()[len(worker.EVENT_PREFIX) :])
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["message"], "original startup error")
        self.assertEqual(payload["attention_backend"], "not-a-backend")

    def test_failure_result_records_native_clean_profile(self):
        output = io.StringIO()
        request = {
            "mode": "t2v",
            "steps": 20,
            "workflow_profile": "native_clean",
        }
        with mock.patch.object(worker.traceback, "print_exc"), redirect_stdout(output):
            worker._emit_failure(RuntimeError("baseline failed"), request)
        payload = json.loads(output.getvalue()[len(worker.EVENT_PREFIX) :])
        self.assertEqual(payload["workflow_profile"], "native_clean")
        self.assertEqual(payload["scheduler"]["effective"], "simple")

    def test_load_request_rejects_path_outside_root(self):
        with tempfile.TemporaryDirectory() as project, tempfile.TemporaryDirectory() as outside:
            request_path = Path(outside) / "request.json"
            request_path.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "escapes"):
                worker._load_request(request_path, Path(project))


if __name__ == "__main__":
    unittest.main()
