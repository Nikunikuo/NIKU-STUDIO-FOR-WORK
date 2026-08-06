from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
import unittest

from sidecar.h3_story_core.models import ProjectManifest
from sidecar.seed_projects import demo_project_manifests


EXPECTED_PROJECTS = {
    "prj_01K1ZJ7M5X9JY4Q6C8P0V3TN2A": ("YOAKE-01", "夜明けの鋼", 184, "sh_012"),
    "prj_01K0XT4Y9G2P7Q8M3V6D1HRK5C": ("SAZANAMI-02", "さざなみ食堂", 61, "sh_112"),
    "prj_01JZQ9B2S6C4F8V1N5M7X3AD0E": ("ORBIT-03", "軌道上の手紙", 24, "sh_212"),
}


class DemoProjectManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifests = demo_project_manifests()

    def test_returns_three_fresh_immutable_manifests_with_unique_ids(self) -> None:
        self.assertIsInstance(self.manifests, tuple)
        self.assertEqual(len(self.manifests), 3)
        self.assertTrue(all(isinstance(item, ProjectManifest) for item in self.manifests))
        project_ids = [item.project_id for item in self.manifests]
        self.assertEqual(len(project_ids), len(set(project_ids)))
        self.assertEqual(set(project_ids), set(EXPECTED_PROJECTS))
        self.assertTrue(all(not Path(item.project_root).is_absolute() for item in self.manifests))

        with self.assertRaises(FrozenInstanceError):
            self.manifests[0].name = "別の作品"  # type: ignore[misc]
        with self.assertRaises(TypeError):
            self.manifests[0].metadata["code"] = "CHANGED"  # type: ignore[index]
        with self.assertRaises(TypeError):
            self.manifests[0].focus["shot_id"] = "sh_wrong"  # type: ignore[index]

        second_call = demo_project_manifests()
        self.assertEqual(second_call, self.manifests)
        self.assertIsNot(second_call, self.manifests)
        self.assertIsNot(second_call[0], self.manifests[0])

    def test_metadata_preserves_ui_code_revision_hint_and_identity(self) -> None:
        for manifest in self.manifests:
            code, title, revision_hint, _active_shot = EXPECTED_PROJECTS[manifest.project_id]
            self.assertEqual(manifest.name, title)
            self.assertEqual(manifest.metadata["code"], code)
            self.assertEqual(manifest.metadata["revision_hint"], revision_hint)
            self.assertEqual(manifest.metadata["source"], "app/page.tsx demo")
            self.assertEqual(manifest.project_root, f"projects/{code.lower()}")
            self.assertGreater(manifest.metadata["progress"], 0)

    def test_entity_ids_are_globally_unique_and_required_types_are_present(self) -> None:
        required = {"episode", "scene", "asset", "shot", "prompt"}
        all_entity_ids: list[str] = []
        for manifest in self.manifests:
            entity_types = {entity.entity_type for entity in manifest.entities}
            self.assertEqual(entity_types, required)
            self.assertEqual(sum(entity.entity_type == "asset" for entity in manifest.entities), 4)
            self.assertEqual(sum(entity.entity_type == "shot" for entity in manifest.entities), 6)
            self.assertEqual(sum(entity.entity_type == "prompt" for entity in manifest.entities), 1)
            all_entity_ids.extend(entity.entity_id for entity in manifest.entities)
        self.assertEqual(len(all_entity_ids), len(set(all_entity_ids)))

        first_asset = next(
            entity
            for entity in self.manifests[0].entities
            if entity.entity_type == "asset"
        )
        with self.assertRaises(TypeError):
            first_asset.payload["caption"] = "changed"  # type: ignore[index]

    def test_focus_resolves_to_seeded_episode_scene_active_shot_and_prompt(self) -> None:
        for manifest in self.manifests:
            _code, _title, _revision_hint, expected_active_shot = EXPECTED_PROJECTS[
                manifest.project_id
            ]
            by_key = {
                (entity.entity_type, entity.entity_id): entity
                for entity in manifest.entities
            }
            episode_id = str(manifest.focus["episode_id"])
            scene_id = str(manifest.focus["scene_id"])
            shot_id = str(manifest.focus["shot_id"])
            prompt_id = str(manifest.focus["prompt_id"])

            self.assertIn(("episode", episode_id), by_key)
            self.assertIn(("scene", scene_id), by_key)
            self.assertIn(("shot", shot_id), by_key)
            self.assertIn(("prompt", prompt_id), by_key)
            self.assertEqual(shot_id, expected_active_shot)
            self.assertEqual(by_key[("shot", shot_id)].payload["status"], "active")
            self.assertEqual(by_key[("prompt", prompt_id)].payload["shot_id"], shot_id)

    def test_ui_assets_shots_and_prompts_are_faithfully_seeded(self) -> None:
        manifests_by_code = {
            str(manifest.metadata["code"]): manifest for manifest in self.manifests
        }
        yoake = manifests_by_code["YOAKE-01"]
        assets = {
            entity.entity_id: entity.payload
            for entity in yoake.entities
            if entity.entity_type == "asset"
        }
        self.assertEqual(assets["ch_001"]["name"], "凛")
        self.assertEqual(assets["ch_001"]["kind"], "character")
        self.assertTrue(assets["ch_001"]["locked"])
        self.assertIn("right cheek", assets["ch_001"]["prompt_caption"])
        self.assertEqual(assets["env_004"]["name"], "旧軌道工場")
        self.assertEqual(assets["prop_006"]["name"], "記録端末")

        shots = {
            entity.entity_id: entity.payload
            for entity in yoake.entities
            if entity.entity_type == "shot"
        }
        self.assertEqual(shots["sh_009"]["title"], "工場へ進入")
        self.assertEqual(shots["sh_012"]["title"], "警告灯が点く")
        self.assertEqual(shots["sh_014"]["status"], "draft")

        prompt = next(entity for entity in yoake.entities if entity.entity_type == "prompt")
        self.assertIn("@凛", prompt.payload["prompt"])
        self.assertIn("@ユウ", prompt.payload["prompt"])
        self.assertIn("@記録端末", prompt.payload["prompt"])
        self.assertEqual(
            set(prompt.payload["reference_asset_ids"]),
            {"ch_001", "ch_002", "env_004", "prop_006"},
        )
        self.assertEqual(prompt.payload["revision_hint"], 184)

    def test_approval_seeds_are_unique_target_existing_entities_and_follow_ui_state(self) -> None:
        all_approval_ids: list[str] = []
        for manifest in self.manifests:
            entities = {
                (entity.entity_type, entity.entity_id): entity
                for entity in manifest.entities
            }
            self.assertEqual(len(manifest.approvals), 11)
            for approval in manifest.approvals:
                all_approval_ids.append(approval.approval_id)
                self.assertIn((approval.target_type, approval.target_id), entities)
                target = entities[(approval.target_type, approval.target_id)]
                if approval.target_type == "asset":
                    expected = "approved" if target.payload["locked"] else "pending"
                elif approval.target_type == "shot":
                    expected = "approved" if target.payload["status"] == "selected" else "pending"
                else:
                    expected = "pending"
                self.assertEqual(approval.status, expected)
                self.assertEqual(
                    approval.payload["revision_hint"],
                    manifest.metadata["revision_hint"],
                )
        self.assertEqual(len(all_approval_ids), len(set(all_approval_ids)))


if __name__ == "__main__":
    unittest.main()
