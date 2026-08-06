from __future__ import annotations

import unittest
from pathlib import Path

from webui.resolutions import (
    CANVAS_MULTIPLE,
    MAX_CANVAS_EDGE,
    MIN_CANVAS_EDGE,
    QUALITY_LABELS,
    RESOLUTION_PRESETS,
    resolution_catalog,
)


class ResolutionPresetTests(unittest.TestCase):
    def test_all_presets_are_h3_aligned_and_within_the_supported_canvas(self) -> None:
        for aspect_ratio, tiers in RESOLUTION_PRESETS.items():
            with self.subTest(aspect_ratio=aspect_ratio):
                self.assertEqual(set(tiers), set(QUALITY_LABELS))
            for tier, preset in tiers.items():
                with self.subTest(aspect_ratio=aspect_ratio, tier=tier):
                    self.assertEqual(preset.width % CANVAS_MULTIPLE, 0)
                    self.assertEqual(preset.height % CANVAS_MULTIPLE, 0)
                    self.assertGreaterEqual(preset.width, MIN_CANVAS_EDGE)
                    self.assertGreaterEqual(preset.height, MIN_CANVAS_EDGE)
                    self.assertLessEqual(preset.width, MAX_CANVAS_EDGE)
                    self.assertLessEqual(preset.height, MAX_CANVAS_EDGE)

    def test_landscape_and_portrait_presets_are_exact_transposes(self) -> None:
        for landscape, portrait in (("16:9", "9:16"), ("4:3", "3:4")):
            for tier in QUALITY_LABELS:
                wide = RESOLUTION_PRESETS[landscape][tier]
                tall = RESOLUTION_PRESETS[portrait][tier]
                self.assertEqual((wide.width, wide.height), (tall.height, tall.width))

    def test_public_catalog_explains_that_actual_pixels_are_h3_aligned(self) -> None:
        catalog = resolution_catalog()

        self.assertEqual(catalog["canvas_multiple"], 32)
        self.assertEqual(catalog["default_aspect_ratio"], "16:9")
        self.assertEqual(catalog["default_quality"], "sd")
        hd = next(item for item in catalog["qualities"] if item["id"] == "hd")
        self.assertEqual(hd["label"], "HD 720p相当")
        wide = next(item for item in catalog["aspect_ratios"] if item["id"] == "16:9")
        self.assertEqual(wide["presets"]["hd"]["width"], 1312)
        self.assertEqual(wide["presets"]["hd"]["height"], 736)

    def test_quick_preview_preserves_the_selected_aspect_ratio(self) -> None:
        source = (
            Path(__file__).resolve().parents[1] / "webui" / "static" / "app.js"
        ).read_text(encoding="utf-8")
        handler_start = source.index('ui.quickPreview.addEventListener("click"')
        handler_end = source.index("\n  });", handler_start)
        handler = source[handler_start:handler_end]

        self.assertIn("selectedResolutionAspect()?.id", handler)
        self.assertIn('setResolutionSelection(aspectId, "preview")', handler)
        self.assertNotIn('setResolutionSelection("16:9", "preview")', handler)


if __name__ == "__main__":
    unittest.main()
