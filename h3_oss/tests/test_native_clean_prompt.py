from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from webui.job_manager import (
    COMMUNITY_CACHE_SCHEMA_VERSION,
    COMMUNITY_COMPILER_REVISION,
    COMMUNITY_PLANNER_CONTRACT,
    COMMUNITY_PLANNER_MODEL_ID,
    COMMUNITY_PLANNER_REVISION,
    JobManager,
    _validate_native_clean_prompt,
    _validated_community_cache_response,
)


class NativeCleanPromptTests(unittest.TestCase):
    @staticmethod
    def _cache_envelope(prompt: str) -> dict:
        import hashlib

        response = {
            "ok": True,
            "compiled_prompt": prompt,
            "plan": {"schema_version": COMMUNITY_PLANNER_CONTRACT},
            "planner_metadata": {
                "contract": COMMUNITY_PLANNER_CONTRACT,
                "model_id": COMMUNITY_PLANNER_MODEL_ID,
                "model_revision": COMMUNITY_PLANNER_REVISION,
            },
            "diagnostics": [],
        }
        return {
            "schema_version": COMMUNITY_CACHE_SCHEMA_VERSION,
            "cache_key": "abc123",
            "compiler_revision": COMMUNITY_COMPILER_REVISION,
            "planner_contract": COMMUNITY_PLANNER_CONTRACT,
            "planner_model": COMMUNITY_PLANNER_MODEL_ID,
            "planner_revision": COMMUNITY_PLANNER_REVISION,
            "compiled_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "response": response,
        }

    def test_public_success_contract_allows_only_quoted_japanese_dialogue(self) -> None:
        prompt = (
            "Scene overview: A cheerful girl turns toward the camera.\n\n"
            "Storyboard:\n"
            "[0s-3s] She approaches in a medium shot.\n"
            "[3s-5s] She speaks exactly once with precise lip sync.\n\n"
            'Audio: A young female voice says exactly once in Japanese: '
            '"ミニマックス エイチスリーで遊んでみない？". '
            'A bright "ping!" follows.'
        )

        self.assertEqual(
            _validate_native_clean_prompt(prompt, mode="t2v", references=[]),
            [],
        )

    def test_japanese_control_prose_is_blocked_even_when_dialogue_is_valid(self) -> None:
        prompt = (
            "Scene overview: A woman exercises.\n"
            "最後にプロテインを飲んで終了。\n"
            'Audio: She says once: "うおお！"'
        )

        diagnostics = _validate_native_clean_prompt(
            prompt,
            mode="t2v",
            references=[],
        )
        self.assertEqual(diagnostics[0]["code"], "NATIVE_CLEAN_CJK_CONTROL_PROSE")

    def test_old_native_dialogue_tag_and_reserved_tokens_are_blocked(self) -> None:
        prompt = "Audio: <d>[Japanese] こんにちは。</d> <|caption_start|>"

        codes = {
            item["code"]
            for item in _validate_native_clean_prompt(
                prompt,
                mode="t2v",
                references=[],
            )
        }
        self.assertIn("NATIVE_CLEAN_DIALOGUE_TAG", codes)
        self.assertIn("NATIVE_CLEAN_RESERVED_TOKEN", codes)

    def test_omni_reference_tags_must_match_actual_inventory(self) -> None:
        prompt = "Subject: <Picture 2> is the visible woman."
        references = [{"kind": "image", "stored_path": "unused"}]

        diagnostics = _validate_native_clean_prompt(
            prompt,
            mode="omni",
            references=references,
        )
        self.assertEqual(diagnostics[0]["code"], "PROMPT_REFERENCE_OUT_OF_RANGE")

    def test_raw_en_job_manager_keeps_validated_prompt_byte_for_byte(self) -> None:
        prompt = (
            "  Style: soft cel animation.  \r\n"
            "Scene: A girl waves.\n"
            'Audio: She says once in Japanese: "こんにちは。"\r\n\r\n'
        )
        with tempfile.TemporaryDirectory() as temporary:
            manager = JobManager(Path(temporary))
            job_id = "raw-exact"
            job_dir = manager.jobs_dir / job_id
            job_dir.mkdir(parents=True)
            request_path = job_dir / "request.json"
            request_path.write_text(
                json.dumps(
                    {
                        "id": job_id,
                        "mode": "t2v",
                        "prompt": prompt,
                        "effective_prompt": prompt,
                        "prompt_processing_mode": "raw_en",
                        "prompt_processing": {"mode": "native_raw"},
                        "references": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            manager.submit(
                {
                    "id": job_id,
                    "status": "running",
                    "progress": 1,
                    "prompt": prompt,
                }
            )

            execution_path = manager._prepare_effective_prompt(  # noqa: SLF001
                job_id, request_path
            )

            self.assertIsNotNone(execution_path)
            execution = json.loads(Path(execution_path).read_text(encoding="utf-8"))
            self.assertEqual(execution["effective_prompt"], prompt)
            self.assertEqual(manager.get_job(job_id)["effective_prompt"], prompt)

    def test_removed_prompt_mode_fails_closed_without_execution_request(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manager = JobManager(Path(temporary))
            job_id = "removed-mode"
            job_dir = manager.jobs_dir / job_id
            job_dir.mkdir(parents=True)
            request_path = job_dir / "request.json"
            request_path.write_text(
                json.dumps(
                    {
                        "id": job_id,
                        "mode": "t2v",
                        "prompt": "A girl waves.",
                        "prompt_processing_mode": "direct",
                        "prompt_processing": {"mode": "raw_guarded"},
                        "references": [],
                    }
                ),
                encoding="utf-8",
            )
            manager.submit(
                {
                    "id": job_id,
                    "status": "running",
                    "progress": 1,
                    "prompt": "A girl waves.",
                }
            )

            execution_path = manager._prepare_effective_prompt(  # noqa: SLF001
                job_id, request_path
            )

            self.assertIsNone(execution_path)
            self.assertFalse((job_dir / "execution_request.json").exists())
            job = manager.get_job(job_id)
            self.assertEqual(job["status"], "failed")
            self.assertEqual(
                job["prompt_processing"]["error_code"],
                "LEGACY_PROMPT_MODE_REMOVED",
            )

    def test_community_cache_accepts_only_current_fully_validated_envelopes(self) -> None:
        prompt = (
            "Style: soft cel animation.\n"
            "Scene: A girl waves.\n"
            'Audio: She says once in Japanese: "こんにちは。"'
        )
        envelope = self._cache_envelope(prompt)

        accepted = _validated_community_cache_response(
            envelope,
            cache_key="abc123",
            mode="t2v",
            references=[],
        )

        self.assertIs(accepted, envelope["response"])

    def test_community_cache_treats_old_rejected_or_stale_entries_as_misses(self) -> None:
        prompt = "Style: soft cel animation.\nScene: A girl waves.\nAudio: room tone."
        valid = self._cache_envelope(prompt)
        cases = {
            "old_unwrapped_response": valid["response"],
            "stale_compiler": {**valid, "compiler_revision": "older"},
            "wrong_key": {**valid, "cache_key": "different"},
            "bad_checksum": {**valid, "compiled_sha256": "0" * 64},
            "fatal_diagnostic": {
                **valid,
                "response": {
                    **valid["response"],
                    "diagnostics": [
                        {"severity": "error", "code": "BAD", "fatal": True}
                    ],
                },
            },
        }
        for name, envelope in cases.items():
            with self.subTest(name=name):
                self.assertIsNone(
                    _validated_community_cache_response(
                        envelope,
                        cache_key="abc123",
                        mode="t2v",
                        references=[],
                    )
                )


if __name__ == "__main__":
    unittest.main()
