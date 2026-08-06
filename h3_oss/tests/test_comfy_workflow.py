from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from webui.comfy_workflow import (
    AUDIO_VOLUME_NODE_ID,
    AUDIO_VAE_MODEL,
    CLIP_NODE_ID,
    COMFYUI_COMMIT,
    CONDITIONING_NODE_ID,
    DEFAULT_EASYCACHE_PRESET,
    EASYCACHE_NODE_ID,
    FL2VA_MODEL,
    GUIDER_NODE_ID,
    REF2VA_MODEL,
    SAVE_VIDEO_NODE_ID,
    SCHEDULER_NODE_ID,
    TEXT_ENCODER_MODEL,
    UNET_NODE_ID,
    VIDEO_VAE_MODEL,
    build_h3_workflow,
)


def build(mode: str, **overrides):
    values = {
        "mode": mode,
        "prompt": "A quiet test scene with natural stereo ambience.",
        "width": 640,
        "height": 384,
        "frames": 243,
        "steps": 20,
        "seed": 123456,
        "output_prefix": "h3/tests/result",
    }
    values.update(overrides)
    return build_h3_workflow(**values)


class ComfyWorkflowTests(unittest.TestCase):
    def assert_json_serializable(self, value):
        encoded = json.dumps(value, ensure_ascii=False)
        self.assertEqual(json.loads(encoded), value)

    def test_t2v_uses_official_fl2va_graph_and_model_names(self):
        workflow = build("t2v")
        graph = workflow["prompt"]
        self.assert_json_serializable(workflow)
        self.assertEqual(workflow["metadata"]["requested_easycache"], DEFAULT_EASYCACHE_PRESET)
        self.assertEqual(workflow["metadata"]["effective_easycache"], DEFAULT_EASYCACHE_PRESET)
        self.assertEqual(workflow["metadata"]["comfyui_commit"], COMFYUI_COMMIT)
        self.assertEqual(workflow["metadata"]["save_video_node_id"], SAVE_VIDEO_NODE_ID)
        self.assertEqual(graph[UNET_NODE_ID]["inputs"]["unet_name"], FL2VA_MODEL)
        self.assertEqual(graph[CLIP_NODE_ID]["inputs"]["clip_name"], TEXT_ENCODER_MODEL)
        self.assertEqual(graph[CLIP_NODE_ID]["inputs"]["type"], "minimax")
        self.assertEqual(graph["4"]["inputs"]["vae_name"], VIDEO_VAE_MODEL)
        self.assertEqual(graph["5"]["inputs"]["vae_name"], AUDIO_VAE_MODEL)
        self.assertEqual(graph[CONDITIONING_NODE_ID]["class_type"], "MiniMaxH3ImageToVideo")
        self.assertEqual(
            {
                key: graph[CONDITIONING_NODE_ID]["inputs"][key]
                for key in ("prompt", "width", "height", "length")
            },
            {
                "prompt": "A quiet test scene with natural stereo ambience.",
                "width": 640,
                "height": 384,
                "length": 243,
            },
        )
        self.assertNotIn("first_frame", graph[CONDITIONING_NODE_ID]["inputs"])
        self.assertNotIn("last_frame", graph[CONDITIONING_NODE_ID]["inputs"])
        self.assertEqual(graph["7"]["inputs"]["sampler_name"], "res_multistep")
        self.assertEqual(graph[SCHEDULER_NODE_ID]["inputs"]["scheduler"], "simple")
        self.assertEqual(workflow["metadata"]["requested_scheduler"], "auto")
        self.assertEqual(workflow["metadata"]["effective_scheduler"], "simple")
        self.assertEqual(graph[SCHEDULER_NODE_ID]["inputs"]["steps"], 20)
        self.assertEqual(graph["6"]["inputs"]["noise_seed"], 123456)
        self.assertEqual(graph["11"]["inputs"]["latent_image"], [CONDITIONING_NODE_ID, 1])
        self.assertEqual(graph["12"]["inputs"]["samples"], ["11", 0])
        self.assertEqual(graph["13"]["inputs"]["samples"], ["11", 0])
        self.assertEqual(graph["14"]["inputs"]["images"], ["12", 0])
        self.assertEqual(graph[SAVE_VIDEO_NODE_ID]["inputs"]["video"], ["14", 0])
        self.assertEqual(graph[SAVE_VIDEO_NODE_ID]["inputs"]["filename_prefix"], "h3/tests/result")
        self.assertEqual(graph[SAVE_VIDEO_NODE_ID]["inputs"]["format"], "auto")
        self.assertEqual(graph[SAVE_VIDEO_NODE_ID]["inputs"]["codec"], "auto")

    def test_audio_gain_is_applied_between_audio_decode_and_mux(self):
        workflow = build("t2v", audio_gain_db=6)
        graph = workflow["prompt"]
        self.assertEqual(
            graph[AUDIO_VOLUME_NODE_ID],
            {
                "class_type": "AudioAdjustVolume",
                "inputs": {"audio": ["13", 0], "volume": 6},
            },
        )
        self.assertEqual(graph["14"]["inputs"]["audio"], [AUDIO_VOLUME_NODE_ID, 0])
        self.assertEqual(workflow["metadata"]["audio_gain_db"], 6)

    def test_i2v_connects_one_input_file_as_first_frame(self):
        graph = build("i2v", input_filename="uploads/first.png")["prompt"]
        conditioning = graph[CONDITIONING_NODE_ID]["inputs"]
        self.assertEqual(graph["20"], {"class_type": "LoadImage", "inputs": {"image": "uploads/first.png"}})
        self.assertEqual(conditioning["first_frame"], ["20", 0])
        self.assertNotIn("last_frame", conditioning)

    def test_first_last_connects_both_input_files_in_order(self):
        graph = build(
            "first_last",
            input_filename="uploads/open.png",
            last_frame_filename="uploads/close.png",
        )["prompt"]
        conditioning = graph[CONDITIONING_NODE_ID]["inputs"]
        self.assertEqual(graph["20"]["inputs"]["image"], "uploads/open.png")
        self.assertEqual(graph["21"]["inputs"]["image"], "uploads/close.png")
        self.assertEqual(conditioning["first_frame"], ["20", 0])
        self.assertEqual(conditioning["last_frame"], ["21", 0])

    def test_omni_connects_autogrow_references_in_exact_source_order(self):
        workflow = build(
            "omni",
            reference_image_filenames=["p1.png", "p2.png", "p3.png"],
            reference_video_filenames=["v1.mp4", "v2.mov"],
            reference_audio_filenames=["a1.wav", "a2.flac"],
            ref_image_size="match",
            embedded_video_audio_policy="reference",
        )
        graph = workflow["prompt"]
        conditioning = graph[CONDITIONING_NODE_ID]
        inputs = conditioning["inputs"]
        self.assert_json_serializable(workflow)
        self.assertEqual(graph[UNET_NODE_ID]["inputs"]["unet_name"], REF2VA_MODEL)
        self.assertEqual(graph[SCHEDULER_NODE_ID]["inputs"]["scheduler"], "simple")
        self.assertEqual(workflow["metadata"]["requested_scheduler"], "auto")
        self.assertEqual(workflow["metadata"]["effective_scheduler"], "simple")
        self.assertEqual(conditioning["class_type"], "MiniMaxH3ReferenceToVideo")
        self.assertEqual(
            [key for key in inputs if key.startswith("ref_images.")],
            [
                "ref_images.ref_image_0",
                "ref_images.ref_image_1",
                "ref_images.ref_image_2",
            ],
        )
        self.assertEqual(inputs["ref_images.ref_image_0"], ["100", 0])
        self.assertEqual(inputs["ref_images.ref_image_2"], ["102", 0])
        self.assertEqual(graph["100"]["inputs"]["image"], "p1.png")
        self.assertEqual(graph["102"]["inputs"]["image"], "p3.png")

        self.assertEqual(graph["200"], {"class_type": "LoadVideo", "inputs": {"file": "v1.mp4"}})
        self.assertEqual(
            graph["201"],
            {"class_type": "GetVideoComponents", "inputs": {"video": ["200", 0]}},
        )
        self.assertEqual(inputs["ref_videos.ref_video_0"], ["201", 0])
        self.assertEqual(inputs["ref_video_audios.ref_video_audio_0"], ["201", 1])
        self.assertEqual(inputs["ref_videos.ref_video_1"], ["203", 0])
        self.assertEqual(inputs["ref_video_audios.ref_video_audio_1"], ["203", 1])

        self.assertEqual(graph["300"], {"class_type": "LoadAudio", "inputs": {"audio": "a1.wav"}})
        self.assertEqual(inputs["ref_audios.ref_audio_0"], ["300", 0])
        self.assertEqual(inputs["ref_audios.ref_audio_1"], ["301", 0])

    def test_conservative_cache_patches_both_scheduler_and_guider_edges(self):
        workflow = build("t2v", easycache="conservative", steps=20)
        graph = workflow["prompt"]
        cache = graph[EASYCACHE_NODE_ID]
        self.assertEqual(cache["class_type"], "EasyCache")
        self.assertEqual(cache["inputs"]["model"], [UNET_NODE_ID, 0])
        self.assertEqual(cache["inputs"]["reuse_threshold"], 0.20)
        self.assertEqual(cache["inputs"]["start_percent"], 0.20)
        self.assertEqual(cache["inputs"]["end_percent"], 0.90)
        self.assertEqual(graph[SCHEDULER_NODE_ID]["inputs"]["model"], [EASYCACHE_NODE_ID, 0])
        self.assertEqual(graph[GUIDER_NODE_ID]["inputs"]["model"], [EASYCACHE_NODE_ID, 0])
        self.assertEqual(workflow["metadata"]["effective_easycache"], "conservative")

    def test_balanced_cache_has_requested_threshold(self):
        graph = build("omni", easycache="balanced", steps=20)["prompt"]
        self.assertEqual(graph[EASYCACHE_NODE_ID]["inputs"]["reuse_threshold"], 0.30)
        self.assertEqual(graph[EASYCACHE_NODE_ID]["inputs"]["start_percent"], 0.20)
        self.assertEqual(graph[EASYCACHE_NODE_ID]["inputs"]["end_percent"], 0.90)

    def test_community_cache_matches_published_example_and_is_default(self):
        baseline = build(
            "t2v",
            workflow_profile="native_clean",
            width=864,
            height=480,
            frames=124,
            steps=20,
        )
        self.assertEqual(baseline["metadata"]["workflow_profile"], "native_clean")
        self.assertEqual(baseline["metadata"]["requested_easycache"], DEFAULT_EASYCACHE_PRESET)
        self.assertEqual(baseline["metadata"]["effective_easycache"], DEFAULT_EASYCACHE_PRESET)
        self.assertIn(EASYCACHE_NODE_ID, baseline["prompt"])
        default_cache = baseline["prompt"][EASYCACHE_NODE_ID]["inputs"]
        self.assertEqual(default_cache["reuse_threshold"], 0.20)
        self.assertEqual(default_cache["start_percent"], 0.15)
        self.assertEqual(default_cache["end_percent"], 0.95)

        community = build(
            "t2v",
            workflow_profile="native_clean",
            easycache="community",
            width=864,
            height=480,
            frames=124,
            steps=20,
        )
        cache = community["prompt"][EASYCACHE_NODE_ID]["inputs"]
        self.assertEqual(cache["reuse_threshold"], 0.20)
        self.assertEqual(cache["start_percent"], 0.15)
        self.assertEqual(cache["end_percent"], 0.95)
        self.assertEqual(community["metadata"]["requested_easycache"], "community")
        self.assertEqual(community["metadata"]["effective_easycache"], "community")

    def test_workflow_profile_is_validated_and_defaults_to_native_clean(self):
        defaulted = build("t2v")
        self.assertEqual(defaulted["metadata"]["workflow_profile"], "native_clean")
        with self.assertRaisesRegex(ValueError, "workflow profile"):
            build("t2v", workflow_profile="unknown")

    def test_cache_is_forced_off_below_twelve_steps(self):
        workflow = build("t2v", easycache="balanced", steps=11)
        graph = workflow["prompt"]
        self.assertNotIn(EASYCACHE_NODE_ID, graph)
        self.assertEqual(graph[SCHEDULER_NODE_ID]["inputs"]["model"], [UNET_NODE_ID, 0])
        self.assertEqual(graph[GUIDER_NODE_ID]["inputs"]["model"], [UNET_NODE_ID, 0])
        self.assertEqual(workflow["metadata"]["requested_easycache"], "balanced")
        self.assertEqual(workflow["metadata"]["effective_easycache"], "off")

    def test_draft_ref2va_uses_simple_scheduler_to_finish_denoising(self):
        workflow = build("omni", steps=8)
        self.assertEqual(workflow["metadata"]["requested_scheduler"], "auto")
        self.assertEqual(workflow["metadata"]["effective_scheduler"], "simple")
        self.assertEqual(
            workflow["prompt"][SCHEDULER_NODE_ID]["inputs"]["scheduler"],
            "simple",
        )

    def test_reference_limits_and_required_frames_are_enforced(self):
        with self.assertRaisesRegex(ValueError, "at most 9"):
            build("omni", reference_image_filenames=[f"{i}.png" for i in range(10)])
        with self.assertRaisesRegex(ValueError, "at most 3"):
            build("omni", reference_video_filenames=[f"{i}.mp4" for i in range(4)])
        with self.assertRaisesRegex(ValueError, "at most 3"):
            build("omni", reference_audio_filenames=[f"{i}.wav" for i in range(4)])
        with self.assertRaisesRegex(ValueError, "requires input_filename"):
            build("i2v")
        with self.assertRaisesRegex(ValueError, "requires last_frame_filename"):
            build("first_last", input_filename="first.png")

    def test_internal_scheduler_override_is_validated_and_audited(self):
        workflow = build("omni", scheduler="simple")
        self.assertEqual(workflow["prompt"][SCHEDULER_NODE_ID]["inputs"]["scheduler"], "simple")
        self.assertEqual(workflow["metadata"]["requested_scheduler"], "simple")
        self.assertEqual(workflow["metadata"]["effective_scheduler"], "simple")
        with self.assertRaisesRegex(ValueError, "unsupported scheduler"):
            build("omni", scheduler="karras")

    def test_reference_video_soundtrack_is_opt_in_and_audited(self):
        defaulted = build(
            "omni",
            reference_video_filenames=["motion.mp4"],
        )
        ignored = build(
            "omni",
            reference_video_filenames=["motion.mp4"],
            embedded_video_audio_policy="ignore",
        )
        retained = build(
            "omni",
            reference_video_filenames=["motion.mp4"],
            embedded_video_audio_policy="reference",
        )

        defaulted_inputs = defaulted["prompt"][CONDITIONING_NODE_ID]["inputs"]
        ignored_inputs = ignored["prompt"][CONDITIONING_NODE_ID]["inputs"]
        retained_inputs = retained["prompt"][CONDITIONING_NODE_ID]["inputs"]
        self.assertNotIn("ref_video_audios.ref_video_audio_0", defaulted_inputs)
        self.assertNotIn("ref_video_audios.ref_video_audio_0", ignored_inputs)
        self.assertEqual(
            retained_inputs["ref_video_audios.ref_video_audio_0"],
            ["201", 1],
        )
        self.assertEqual(defaulted["metadata"]["embedded_video_audio_policy"], "ignore")
        self.assertEqual(ignored["metadata"]["embedded_video_audio_policy"], "ignore")
        self.assertEqual(retained["metadata"]["embedded_video_audio_policy"], "reference")
        with self.assertRaisesRegex(ValueError, "embedded video audio policy"):
            build("omni", embedded_video_audio_policy="auto")

    def test_only_selected_reference_video_soundtrack_is_connected_and_audited(self):
        workflow = build(
            "omni",
            reference_video_filenames=["silent-motion.mp4", "voice-motion.mp4"],
            embedded_video_audio_policy="reference",
            embedded_video_audio_indices=[1],
        )

        inputs = workflow["prompt"][CONDITIONING_NODE_ID]["inputs"]
        self.assertEqual(inputs["ref_videos.ref_video_0"], ["201", 0])
        self.assertEqual(inputs["ref_videos.ref_video_1"], ["203", 0])
        self.assertNotIn("ref_video_audios.ref_video_audio_0", inputs)
        self.assertEqual(
            inputs["ref_video_audios.ref_video_audio_1"],
            ["203", 1],
        )
        self.assertEqual(
            workflow["metadata"]["embedded_video_audio_indices"],
            [1],
        )


if __name__ == "__main__":
    unittest.main()
