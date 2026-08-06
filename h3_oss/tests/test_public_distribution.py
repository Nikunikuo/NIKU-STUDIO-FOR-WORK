from __future__ import annotations

import hashlib
import json
import re
import subprocess
import unittest
from pathlib import Path

from webui.job_manager import (
    COMMUNITY_PLANNER_LOCK,
    COMMUNITY_PLANNER_MODEL_ID,
    COMMUNITY_PLANNER_PROVENANCE_FILE,
    COMMUNITY_PLANNER_PROVENANCE_SCHEMA_VERSION,
    COMMUNITY_PLANNER_REQUIRED_FILES,
    COMMUNITY_PLANNER_REVISION,
)


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_TEXT_SUFFIXES = {
    ".cmd",
    ".css",
    ".html",
    ".js",
    ".json",
    ".md",
    ".ps1",
    ".py",
    ".txt",
}
PUBLIC_TEXT_NAMES = {".gitattributes", ".gitignore", "LICENSE"}


def distributable_files() -> list[Path]:
    """Return tracked and not-ignored files without descending into local assets."""
    result = subprocess.run(
        [
            "git",
            "-C",
            str(ROOT),
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "-z",
        ],
        check=True,
        capture_output=True,
    )
    relative_paths = result.stdout.decode("utf-8").split("\0")
    return [ROOT / relative for relative in relative_paths if relative]


class PublicDistributionTests(unittest.TestCase):
    def test_distributable_text_has_no_developer_specific_absolute_paths(self) -> None:
        # Keep the forbidden values assembled so this test does not flag itself.
        forbidden = (
            "c:" + "\\users\\" + "niku_",
            "c:" + "/users/" + "niku_",
            "c:" + "\\project_hub",
            "c:" + "/project_hub",
        )
        violations: list[str] = []

        for path in distributable_files():
            if path.suffix.lower() not in PUBLIC_TEXT_SUFFIXES and path.name not in PUBLIC_TEXT_NAMES:
                continue
            text = path.read_text(encoding="utf-8").casefold()
            if any(value in text for value in forbidden):
                violations.append(path.relative_to(ROOT).as_posix())

        self.assertEqual([], violations, f"developer-specific paths found in: {violations}")

    def test_setup_scripts_do_not_default_to_a_specific_python_executable(self) -> None:
        declaration = re.compile(
            r"\[\s*string\s*\]\s*\$PythonExe(?:\s*=\s*(?P<default>[^,\r\n\)]*))?",
            re.IGNORECASE,
        )
        for relative in ("scripts/setup.ps1", "scripts/setup_comfy.ps1"):
            with self.subTest(script=relative):
                text = (ROOT / relative).read_text(encoding="utf-8")
                match = declaration.search(text)
                self.assertIsNotNone(match, f"{relative} must expose a PythonExe parameter")
                default = (match.group("default") or "").strip().casefold()
                self.assertIn(default, {"", '""', "''", "$null"})

    def test_prompt_planner_runtime_dependency_is_pinned(self) -> None:
        requirements = (ROOT / "requirements.comfy.txt").read_text(encoding="utf-8")
        self.assertRegex(requirements, r"(?m)^accelerate==1\.14\.0$")

    def test_webui_runtime_includes_video_probe_dependency(self) -> None:
        requirements = (ROOT / "requirements.webui.txt").read_text(encoding="utf-8")
        setup = (ROOT / "scripts/setup.ps1").read_text(encoding="utf-8")
        self.assertRegex(requirements, r"(?m)^av==18\.0\.0$")
        self.assertIn("import av, fastapi", setup)

    def test_normal_webui_launch_skips_qwen_full_weight_probe(self) -> None:
        setup = (ROOT / "scripts/setup_comfy.ps1").read_text(encoding="utf-8")
        start = (ROOT / "scripts/start_webui.ps1").read_text(encoding="utf-8")
        self.assertIn("Test-PromptPlannerRuntime -LoadWeights:(-not $VerifyOnly)", setup)
        self.assertIn("-VerifyOnly -SkipModelHash", start)

    def test_first_time_setup_has_no_removed_lfm_path(self) -> None:
        bootstrap = (ROOT / "scripts/bootstrap.ps1").read_text(encoding="utf-8")
        setup = (ROOT / "scripts/setup_comfy.ps1").read_text(encoding="utf-8")
        combined = bootstrap + setup
        self.assertNotIn("PromptTranslator", combined)
        self.assertNotIn("prompt_translator", combined)
        self.assertNotIn("ACCEPT-LFM", combined)
        self.assertIn("prompt_planner.lock.json", setup)

    def test_job_details_show_both_original_and_effective_prompts(self) -> None:
        html = (ROOT / "webui/static/index.html").read_text(encoding="utf-8")
        app = (ROOT / "webui/static/app.js").read_text(encoding="utf-8")
        self.assertIn('id="detail-original-group"', html)
        self.assertIn('id="detail-effective-group"', html)
        self.assertIn("入力した原文", html)
        self.assertIn("const originalPrompt = firstDetailValue(job.original_prompt, job.prompt);", app)
        self.assertIn("setDetailGroup(ui.detailOriginalGroup", app)

    def test_acceleration_controls_explain_usage_and_default_to_restrained_cache(self) -> None:
        html = (ROOT / "webui/static/index.html").read_text(encoding="utf-8")
        app = (ROOT / "webui/static/app.js").read_text(encoding="utf-8")
        expected_labels = (
            "高速化なし（比較用）",
            "おすすめ：控えめ（通常）",
            "より慎重（品質優先）",
            "速度優先（品質を確認）",
        )
        for label in expected_labels:
            with self.subTest(label=label):
                self.assertIn(label, html)
                self.assertIn(label, app)
        self.assertIn('<option value="community" selected>', html)
        self.assertIn('ui.acceleration.value = "community"', app)
        self.assertIn("Draft（12 steps未満）では自動的に高速化なし", html)
        self.assertIn("Draft（12 steps未満）では自動的に高速化なし", app)
        self.assertNotIn("EasyCache 公開例", app)
        self.assertNotIn("EasyCache 高速", app)

    def test_product_brand_is_niku_h_studio(self) -> None:
        html = (ROOT / "webui/static/index.html").read_text(encoding="utf-8")
        server = (ROOT / "webui/server.py").read_text(encoding="utf-8")
        self.assertIn("<title>NIKU H STUDIO — Local Video Generation</title>", html)
        self.assertIn("<strong>NIKU H STUDIO</strong>", html)
        self.assertIn('FastAPI(title="NIKU H STUDIO"', server)
        self.assertNotIn("<title>H3 Studio", html)

    def test_windows_launchers_are_location_independent(self) -> None:
        for relative in ("Setup-H3-Studio.cmd", "Start-H3-WebUI.cmd"):
            with self.subTest(launcher=relative):
                launcher = ROOT / relative
                self.assertTrue(launcher.is_file(), f"missing launcher: {relative}")
                self.assertIn("%~dp0", launcher.read_text(encoding="utf-8").casefold())

    def test_gitignore_excludes_local_runtime_and_large_artifacts(self) -> None:
        patterns = {
            line.strip().replace("\\", "/").casefold()
            for line in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        required = {
            ".venv/",
            ".comfy-venv/",
            "models/",
            "outputs/",
            "webui_data/",
            "*.safetensors",
            "*.ckpt",
            "*.pt",
            "*.pth",
        }
        self.assertFalse(required - patterns, f"missing ignore rules: {sorted(required - patterns)}")

    def test_comfy_model_lock_is_complete_and_uses_an_immutable_license_url(self) -> None:
        lock = json.loads((ROOT / "comfy_models.lock.json").read_text(encoding="utf-8"))
        files = lock["files"]

        self.assertEqual(5, len(files))
        self.assertEqual(63_440_965_087, lock["verification"]["total_bytes"])
        self.assertEqual(lock["verification"]["total_bytes"], sum(item["size"] for item in files))
        self.assertEqual(5, len({item["path"] for item in files}))
        for item in files:
            with self.subTest(model=item["path"]):
                self.assertTrue(item["path"].startswith("models/comfy/"))
                self.assertTrue(item["path"].endswith(".safetensors"))
                self.assertRegex(item["sha256"], r"^[0-9a-f]{64}$")

        source = lock["source"]
        self.assertEqual("minimax-h3-community-license-agreement", source["license"])
        self.assertRegex(source["revision"], r"^[0-9a-f]{40}$")
        self.assertRegex(
            source["license_url"],
            r"^https://huggingface\.co/MiniMaxAI/MiniMax-H3/blob/[0-9a-f]{40}/LICENSE$",
        )

    def test_prompt_planner_lock_matches_the_minimal_runtime_snapshot(self) -> None:
        lock = json.loads((ROOT / "prompt_planner.lock.json").read_text(encoding="utf-8"))
        files = lock["files"]
        expected_names = {
            "config.json",
            "generation_config.json",
            "LICENSE",
            "model-00001-of-00003.safetensors",
            "model-00002-of-00003.safetensors",
            "model-00003-of-00003.safetensors",
            "model.safetensors.index.json",
            "tokenizer.json",
            "tokenizer_config.json",
        }

        self.assertEqual(9, len(files))
        self.assertEqual(8_056_459_158, lock["verification"]["total_bytes"])
        self.assertEqual(lock["verification"]["total_bytes"], sum(item["size"] for item in files))
        self.assertEqual(
            expected_names,
            {Path(item["path"]).name for item in files},
        )
        self.assertEqual(
            COMMUNITY_PLANNER_REQUIRED_FILES,
            {Path(item["path"]).name: item["size"] for item in files},
        )
        for item in files:
            with self.subTest(model=item["path"]):
                self.assertTrue(
                    item["path"].startswith(
                        "models/prompt_planner/Qwen3-4B-Instruct-2507/"
                    )
                )
                self.assertRegex(item["sha256"], r"^[0-9a-f]{64}$")

        source = lock["source"]
        self.assertEqual(COMMUNITY_PLANNER_MODEL_ID, source["repo_id"])
        self.assertEqual("apache-2.0", source["license"].casefold())
        self.assertEqual(COMMUNITY_PLANNER_REVISION, source["revision"])

    def test_prompt_planner_provenance_is_bound_to_the_complete_lock(self) -> None:
        lock_path = ROOT / COMMUNITY_PLANNER_LOCK
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        expected = {
            "schema_version": COMMUNITY_PLANNER_PROVENANCE_SCHEMA_VERSION,
            "model_id": COMMUNITY_PLANNER_MODEL_ID,
            "revision": COMMUNITY_PLANNER_REVISION,
            "lock_sha256": hashlib.sha256(lock_path.read_bytes()).hexdigest(),
            "file_count": len(COMMUNITY_PLANNER_REQUIRED_FILES),
            "total_bytes": sum(COMMUNITY_PLANNER_REQUIRED_FILES.values()),
        }
        self.assertEqual(9, expected["file_count"])
        self.assertEqual(8_056_459_158, expected["total_bytes"])
        self.assertEqual(lock["verification"]["total_bytes"], expected["total_bytes"])

        setup = (ROOT / "scripts/setup_comfy.ps1").read_text(encoding="utf-8")
        self.assertIn(COMMUNITY_PLANNER_PROVENANCE_FILE, setup)
        self.assertIn("Test-PromptPlannerFiles -Lock $promptPlannerLock -FullHash", setup)
        self.assertIn("Write-PromptPlannerProvenance -Lock $promptPlannerLock", setup)
        self.assertGreaterEqual(
            setup.count("Test-PromptPlannerProvenance -Lock $promptPlannerLock"),
            2,
        )
        self.assertNotIn(
            ROOT / "models" / "prompt_planner" / "Qwen3-4B-Instruct-2507" /
            COMMUNITY_PLANNER_PROVENANCE_FILE,
            distributable_files(),
        )

    def test_repository_line_ending_policy_is_explicit(self) -> None:
        attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
        self.assertRegex(attributes, r"(?m)^\* text=auto eol=lf$")
        self.assertRegex(attributes, r"(?m)^\*\.cmd text eol=crlf$")


if __name__ == "__main__":
    unittest.main()
