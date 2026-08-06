from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import webui.job_manager as job_manager


class PromptPlannerProvenanceTests(unittest.TestCase):
    def _fixture(self, temporary: str) -> tuple[Path, Path, dict[str, int]]:
        root = Path(temporary) / "日本語プロジェクト"
        model_root = root / job_manager.COMMUNITY_PLANNER_MODEL_DIR
        model_root.mkdir(parents=True)
        required = {"config.json": 3, "tokenizer.json": 4}
        (model_root / "config.json").write_bytes(b"cfg")
        (model_root / "tokenizer.json").write_bytes(b"tokn")
        (root / job_manager.COMMUNITY_PLANNER_LOCK).write_bytes(
            b'{"schema_version":1,"fixture":"provenance"}\n'
        )
        return root, model_root, required

    @staticmethod
    def _expected_marker(root: Path, required: dict[str, int]) -> dict[str, object]:
        lock_path = root / job_manager.COMMUNITY_PLANNER_LOCK
        return {
            "schema_version": job_manager.COMMUNITY_PLANNER_PROVENANCE_SCHEMA_VERSION,
            "model_id": job_manager.COMMUNITY_PLANNER_MODEL_ID,
            "revision": job_manager.COMMUNITY_PLANNER_REVISION,
            "lock_sha256": hashlib.sha256(lock_path.read_bytes()).hexdigest(),
            "file_count": len(required),
            "total_bytes": sum(required.values()),
        }

    def test_status_requires_valid_h3_studio_marker_and_lock_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, model_root, required = self._fixture(temporary)
            marker_path = model_root / job_manager.COMMUNITY_PLANNER_PROVENANCE_FILE
            with patch.object(job_manager, "COMMUNITY_PLANNER_REQUIRED_FILES", required):
                missing = job_manager.community_planner_status(root)
                self.assertFalse(missing["ready"])
                self.assertEqual("provenance_missing", missing["status"])

                marker_path.write_text("not-json", encoding="utf-8")
                malformed = job_manager.community_planner_status(root)
                self.assertFalse(malformed["ready"])
                self.assertEqual("provenance_invalid", malformed["status"])

                marker_path.write_text(
                    json.dumps(self._expected_marker(root, required)),
                    encoding="utf-8",
                )
                ready = job_manager.community_planner_status(root)
                self.assertTrue(ready["ready"])
                self.assertEqual("ready", ready["status"])

                (root / job_manager.COMMUNITY_PLANNER_LOCK).write_bytes(b"changed-lock\n")
                changed_lock = job_manager.community_planner_status(root)
                self.assertFalse(changed_lock["ready"])
                self.assertEqual("provenance_invalid", changed_lock["status"])

    def test_status_rejects_wrong_marker_field_types_and_model_sizes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, model_root, required = self._fixture(temporary)
            marker_path = model_root / job_manager.COMMUNITY_PLANNER_PROVENANCE_FILE
            marker = self._expected_marker(root, required)
            marker["schema_version"] = "1"
            marker_path.write_text(json.dumps(marker), encoding="utf-8")
            with patch.object(job_manager, "COMMUNITY_PLANNER_REQUIRED_FILES", required):
                wrong_type = job_manager.community_planner_status(root)
                self.assertFalse(wrong_type["ready"])
                self.assertEqual("provenance_invalid", wrong_type["status"])

                (model_root / "config.json").write_bytes(b"wrong-size")
                wrong_model = job_manager.community_planner_status(root)
                self.assertFalse(wrong_model["ready"])
                self.assertEqual("model_invalid", wrong_model["status"])

    def test_status_distinguishes_missing_repository_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, _model_root, required = self._fixture(temporary)
            (root / job_manager.COMMUNITY_PLANNER_LOCK).unlink()
            with patch.object(job_manager, "COMMUNITY_PLANNER_REQUIRED_FILES", required):
                status = job_manager.community_planner_status(root)
            self.assertFalse(status["ready"])
            self.assertEqual("lock_missing", status["status"])


if __name__ == "__main__":
    unittest.main()
