"""Human-facing aspect ratio and quality presets for MiniMax H3.

H3's video latent is spatially patchified after a 16x VAE reduction, so both
pixel axes must be divisible by 32.  Familiar labels such as "720p" therefore
map to the nearest useful H3 canvas instead of pretending that 1280x720 is a
valid native shape.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


CANVAS_MULTIPLE: Final = 32
MIN_CANVAS_EDGE: Final = 192
MAX_CANVAS_EDGE: Final = 1344


@dataclass(frozen=True, slots=True)
class ResolutionPreset:
    width: int
    height: int
    label: str

    def to_public_dict(self) -> dict[str, int | str]:
        return {"width": self.width, "height": self.height, "label": self.label}


ASPECT_RATIO_LABELS: Final[dict[str, str]] = {
    "16:9": "横 16:9",
    "9:16": "縦 9:16",
    "1:1": "正方形 1:1",
    "4:3": "横 4:3",
    "3:4": "縦 3:4",
}

QUALITY_LABELS: Final[dict[str, str]] = {
    "preview": "Preview",
    "sd": "SD 480p相当",
    "hd": "HD 720p相当",
    "native": "Native 768p",
}


RESOLUTION_PRESETS: Final[dict[str, dict[str, ResolutionPreset]]] = {
    "16:9": {
        "preview": ResolutionPreset(672, 384, "672×384"),
        "sd": ResolutionPreset(864, 480, "864×480"),
        "hd": ResolutionPreset(1312, 736, "1312×736"),
        "native": ResolutionPreset(1344, 768, "1344×768"),
    },
    "9:16": {
        "preview": ResolutionPreset(384, 672, "384×672"),
        "sd": ResolutionPreset(480, 864, "480×864"),
        "hd": ResolutionPreset(736, 1312, "736×1312"),
        "native": ResolutionPreset(768, 1344, "768×1344"),
    },
    "1:1": {
        "preview": ResolutionPreset(384, 384, "384×384"),
        "sd": ResolutionPreset(480, 480, "480×480"),
        "hd": ResolutionPreset(736, 736, "736×736"),
        "native": ResolutionPreset(768, 768, "768×768"),
    },
    "4:3": {
        "preview": ResolutionPreset(512, 384, "512×384"),
        "sd": ResolutionPreset(640, 480, "640×480"),
        "hd": ResolutionPreset(896, 672, "896×672"),
        "native": ResolutionPreset(1024, 768, "1024×768"),
    },
    "3:4": {
        "preview": ResolutionPreset(384, 512, "384×512"),
        "sd": ResolutionPreset(480, 640, "480×640"),
        "hd": ResolutionPreset(672, 896, "672×896"),
        "native": ResolutionPreset(768, 1024, "768×1024"),
    },
}


def validate_resolution_catalog() -> None:
    expected_tiers = set(QUALITY_LABELS)
    for aspect_ratio, presets in RESOLUTION_PRESETS.items():
        if aspect_ratio not in ASPECT_RATIO_LABELS:
            raise ValueError(f"missing aspect-ratio label: {aspect_ratio}")
        if set(presets) != expected_tiers:
            raise ValueError(f"incomplete quality tiers for {aspect_ratio}")
        for tier, preset in presets.items():
            if preset.width % CANVAS_MULTIPLE or preset.height % CANVAS_MULTIPLE:
                raise ValueError(f"{aspect_ratio}/{tier} is not 32px aligned")
            if not (
                MIN_CANVAS_EDGE <= preset.width <= MAX_CANVAS_EDGE
                and MIN_CANVAS_EDGE <= preset.height <= MAX_CANVAS_EDGE
            ):
                raise ValueError(f"{aspect_ratio}/{tier} is outside the NIKU H STUDIO canvas")


def resolution_catalog() -> dict[str, object]:
    validate_resolution_catalog()
    return {
        "canvas_multiple": CANVAS_MULTIPLE,
        "default_aspect_ratio": "16:9",
        "default_quality": "sd",
        "aspect_ratios": [
            {
                "id": aspect_ratio,
                "label": label,
                "presets": {
                    tier: RESOLUTION_PRESETS[aspect_ratio][tier].to_public_dict()
                    for tier in QUALITY_LABELS
                },
            }
            for aspect_ratio, label in ASPECT_RATIO_LABELS.items()
        ],
        "qualities": [
            {"id": tier, "label": label}
            for tier, label in QUALITY_LABELS.items()
        ],
    }


validate_resolution_catalog()


__all__ = [
    "ASPECT_RATIO_LABELS",
    "CANVAS_MULTIPLE",
    "MAX_CANVAS_EDGE",
    "MIN_CANVAS_EDGE",
    "QUALITY_LABELS",
    "RESOLUTION_PRESETS",
    "ResolutionPreset",
    "resolution_catalog",
    "validate_resolution_catalog",
]
