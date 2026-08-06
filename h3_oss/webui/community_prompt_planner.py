"""Strict Japanese-to-English prompt planning for the community H3 workflow.

The public MiniMax H3 examples that reliably speak Japanese use English for
all scene control and keep the literal Japanese utterance inside one ordinary
pair of double quotes.  This module enforces that contract at a trust boundary:

* literal dialogue is removed before the language model is called;
* the language model may return only a small JSON document;
* reference tags and literal dialogue are rendered by Python, never by the
  model; and
* the finished prompt is rejected if control prose is non-English, references
  were invented, a number/unit disappeared, or dialogue occurs more than once.

Model loading deliberately lives in :mod:`webui.community_prompt_worker` so
the parser, validator, and renderer remain deterministic and unit-testable.
"""

from __future__ import annotations

import json
import hashlib
import math
import re
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


MODEL_ID = "Qwen/Qwen3-4B-Instruct-2507"
MODEL_REVISION = "cdbee75f17c01a7cc42f958dc650907174af0554"
MODEL_RELATIVE_PATH = Path("models/prompt_planner/Qwen3-4B-Instruct-2507")
MODEL_PROVENANCE_FILENAME = "h3-studio-provenance.json"
MODEL_LOCK_FILENAME = "prompt_planner.lock.json"
MODEL_RUNTIME_FILE_COUNT = 9
MODEL_RUNTIME_TOTAL_BYTES = 8_056_459_158
PLAN_SCHEMA_VERSION = "h3-community-plan-v1"


_NON_ENGLISH_RE = re.compile(
    r"[\u3040-\u30ff\u31f0-\u31ff\u3400-\u9fff\uf900-\ufaff"
    r"\uac00-\ud7af\u0600-\u06ff\u0400-\u04ff]"
)
_JAPANESE_RE = re.compile(r"[\u3040-\u30ff\u31f0-\u31ff\u3400-\u9fff]")
_REFERENCE_TAG_RE = re.compile(
    r"<(Picture|Video|Audio)\s+([1-9][0-9]*)>", re.IGNORECASE
)
_ANY_REFERENCE_LIKE_RE = re.compile(
    r"<(?:Picture|Video|Audio|Subject)\s+[1-9][0-9]*>", re.IGNORECASE
)
_D_TAG_RE = re.compile(r"</?d(?:\s+[^>]*)?>", re.IGNORECASE)
_QUOTE_RE = re.compile(
    r"「(?P<corner>[^」\r\n]+)」|『(?P<double_corner>[^』\r\n]+)』|"
    r"“(?P<curly>[^”\r\n]+)”|\"(?P<ascii>[^\"\r\n]+)\""
)
_SHOT_HEADER_RE = re.compile(
    r"(?im)^\s*(?:\[(?:Cut|Shot)\s*(?P<bracket>[1-9][0-9]*)\]"
    r"|(?:Cut|Shot|カット|ショット)\s*#?\s*(?P<plain>[1-9][0-9]*))"
    r"(?=\s|$|[:：-])"
)
_VISUAL_TEXT_CUE_RE = re.compile(
    r"(?:字幕|テロップ|看板|標識|画面|文字|題名|タイトル|表示|書か|"
    r"on[- ]screen|caption|subtitle|sign|label|written|displayed)",
    re.IGNORECASE,
)
_EXPLICIT_SPEECH_CUE_RE = re.compile(
    r"(?:セリフ|台詞|発話|言(?:う|って|い)|話(?:す|して)|喋|しゃべ|"
    r"つぶや|囁|ささや|叫(?:ぶ|ん)|口にする|"
    r"\b(?:dialogue|spoken line|says?|speaks?|whispers?|shouts?)\b)",
    re.IGNORECASE,
)
_SPEECH_CONTROL_RE = re.compile(
    r"\b(?:says?|speaks?|talks?|dialogue|spoken|narrat(?:e|es|ed|ion)|"
    r"voice[- ]?over)\b",
    re.IGNORECASE,
)
_FORBIDDEN_AUDIO_SPEECH_RE = re.compile(
    r"\b(?:speech|spoken|dialogue|narration|voice[- ]?over|says?|speaks?)\b",
    re.IGNORECASE,
)

# Camera direction is validated as geometry, not merely as a bag of glossary
# words.  Keep viewpoint height (low/high) separate from viewing direction
# (up/down), then reject opposing evidence inside one shot.
_CAMERA_LOW_VIEWPOINT_RE = re.compile(
    r"\b(?:low[- ]angle(?:d)?|worm(?:'s|s)?[- ]eye|from below|"
    r"positioned(?:\s+slightly)?\s+below|below\s+(?:the\s+)?(?:subject|character))\b",
    re.IGNORECASE,
)
_CAMERA_HIGH_VIEWPOINT_RE = re.compile(
    r"\b(?:high[- ]angle(?:d)?|bird(?:'s|s)?[- ]eye|top[- ]down|overhead|"
    r"from above|positioned(?:\s+slightly)?\s+above|"
    r"above\s+(?:the\s+)?(?:subject|character))\b",
    re.IGNORECASE,
)
_CAMERA_UPWARD_VIEW_RE = re.compile(
    r"\b(?:upward(?:[- ]looking|\s+view|\s+angle|\s+tilt)?|"
    r"look(?:s|ing)?\s+up(?:ward)?|points?\s+up(?:ward)?|viewed\s+upward)\b",
    re.IGNORECASE,
)
_CAMERA_DOWNWARD_VIEW_RE = re.compile(
    r"\b(?:downward(?:[- ]looking|\s+view|\s+angle|\s+tilt)?|"
    r"look(?:s|ing)?\s+down(?:ward)?|points?\s+down(?:ward)?|viewed\s+downward)\b",
    re.IGNORECASE,
)


class CommunityPromptPlannerError(ValueError):
    """A planner input, model result, or rendered prompt violated the contract."""

    def __init__(self, message: str, *, code: str = "INVALID_COMMUNITY_PLAN") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class DialogueLiteral:
    dialogue_id: int
    text: str
    voice_direction: str = ""

    @property
    def placeholder(self) -> str:
        return f"[DIALOGUE_{self.dialogue_id}]"


@dataclass(frozen=True, slots=True)
class NonverbalCue:
    cue_id: int
    source_text: str
    english_sound: str

    @property
    def placeholder(self) -> str:
        return f"[NONVERBAL_SOUND_{self.cue_id}: {self.english_sound}]"


@dataclass(frozen=True, slots=True)
class ReferenceItem:
    kind: str
    index: int
    role: str = "auto"

    @property
    def label(self) -> str:
        label = {"image": "Picture", "video": "Video", "audio": "Audio"}[self.kind]
        return f"<{label} {self.index}>"


@dataclass(frozen=True, slots=True)
class NumericFact:
    value: str
    unit: str
    source_text: str

    @property
    def key(self) -> tuple[str, str]:
        return self.value, self.unit


@dataclass(frozen=True, slots=True)
class PreparedPlannerInput:
    source_prompt: str
    redacted_prompt: str
    dialogues: tuple[DialogueLiteral, ...]
    nonverbal_cues: tuple[NonverbalCue, ...]
    references: tuple[ReferenceItem, ...]
    source_shot_numbers: tuple[int, ...]
    numeric_facts: tuple[NumericFact, ...]
    duration_seconds: float | None = None
    style_direction: str = ""
    soundscape: str = ""
    audio_preset: str = "auto"
    music_policy: str = "auto"
    wardrobe_override: bool = False
    wardrobe_direction: str = ""
    wardrobe_required_terms: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ShotPlan:
    number: int
    start_seconds: float
    end_seconds: float
    framing: str
    camera: str
    action: str


@dataclass(frozen=True, slots=True)
class DialogueDelivery:
    dialogue_id: int
    shot: int
    start_seconds: float
    speaker: str
    delivery: str


@dataclass(frozen=True, slots=True)
class CommunityPromptPlan:
    schema_version: str
    style: str
    scene: str
    shots: tuple[ShotPlan, ...]
    ambient: tuple[str, ...]
    foley: tuple[str, ...]
    music: str
    dialogue_delivery: tuple[DialogueDelivery, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class CompiledCommunityPrompt:
    prompt: str
    plan: CommunityPromptPlan
    prepared: PreparedPlannerInput

    def metadata(self) -> dict[str, Any]:
        return {
            "contract": PLAN_SCHEMA_VERSION,
            "model_id": MODEL_ID,
            "model_revision": MODEL_REVISION,
            "dialogue_count": len(self.prepared.dialogues),
            "dialogue_voice_directions": [
                {
                    "dialogue_id": item.dialogue_id,
                    "effective": item.voice_direction or None,
                    "deterministic_override": bool(item.voice_direction),
                }
                for item in self.prepared.dialogues
            ],
            "reference_tags": [item.label for item in self.prepared.references],
            "source_shot_numbers": list(self.prepared.source_shot_numbers),
            "numeric_facts": [
                {"value": fact.value, "unit": fact.unit}
                for fact in self.prepared.numeric_facts
            ],
            "audio_preset": self.prepared.audio_preset,
            "music_policy": self.prepared.music_policy,
            "wardrobe_override": self.prepared.wardrobe_override,
            "wardrobe_direction": self.prepared.wardrobe_direction or None,
        }


@dataclass(frozen=True, slots=True)
class ModelCheckoutMetadata:
    model_id: str
    expected_revision: str
    detected_revision: str | None
    verified: bool
    path: str
    provenance_path: str | None = None
    lock_path: str | None = None
    lock_sha256: str | None = None
    file_count: int | None = None
    total_bytes: int | None = None
    verification_method: str = "h3-studio-provenance-v1"


_TOP_LEVEL_KEYS = {
    "schema_version",
    "style",
    "scene",
    "shots",
    "ambient",
    "foley",
    "music",
    "dialogue_delivery",
}
_SHOT_KEYS = {
    "number",
    "start_seconds",
    "end_seconds",
    "framing",
    "camera",
    "action",
}
_DELIVERY_KEYS = {
    "dialogue_id",
    "shot",
    "start_seconds",
    "speaker",
    "delivery",
}

_AUDIO_PRESET_DIRECTIONS = {
    "auto": "Use a balanced, physically coherent natural mix.",
    "dialogue": "Keep the exact dialogue clear and naturally integrated with the visible space.",
    "ambience": "Prioritize detailed spatial ambience and convincing room tone.",
    "effects": "Prioritize precisely synchronized physical foley and clear natural transients.",
    "music": "Use a music-led mix while retaining physically matched diegetic sound.",
    "quiet": "Use a restrained, quiet mix with subtle room tone and only necessary diegetic sounds.",
}
_MUSIC_POLICY_DIRECTIONS = {
    "none": "N/A",
    "subtle": "A subtle instrumental score supports the ambience and important physical sounds.",
    "prominent": "A prominent instrumental score follows the rhythm and emotional arc of the edit.",
}


SYSTEM_PROMPT = r"""You are a compiler for a local MiniMax H3 video workflow.
Return exactly one raw JSON object. Do not use Markdown or commentary.

Translate the Japanese production request into concise, concrete English H3
control prose. Dialogue words have already been hidden from you. Never invent,
quote, paraphrase, or repeat any spoken words. Never create narration,
voice-over, subtitles, captions, labels, reference tags, XML tags, or <d> tags.
Source reference tags have been rewritten as ordinary descriptive phrases.
Never turn those phrases back into tags. Python will insert all reference tags
and exact dialogue later.

Use this exact JSON shape and no additional keys:
{
  "schema_version": "h3-community-plan-v1",
  "style": "English visual style and continuity instructions",
  "scene": "English scene overview",
  "shots": [
    {
      "number": 1,
      "start_seconds": 0.0,
      "end_seconds": 5.0,
      "framing": "English framing and composition",
      "camera": "English camera placement and movement",
      "action": "English visible action"
    }
  ],
  "ambient": ["English environmental sound"],
  "foley": ["English physical sound effect"],
  "music": "English music direction or N/A",
  "dialogue_delivery": [
    {
      "dialogue_id": 1,
      "shot": 1,
      "start_seconds": 1.5,
      "speaker": "the woman",
      "delivery": "warm, clear, and conversational"
    }
  ]
}

Every string value must be English control prose with no quotation marks.
Preserve every supplied number and unit exactly. Preserve the supplied Cut or
Shot numbers and order. Use one simple dominant action per shot. Make camera
instructions explicit. A Japanese 仰角 or 煽り instruction means a low-angle upward view;
俯瞰 means a high-angle downward view. Keep the full camera meaning in the same
numbered Shot where the source requested it. Within one Shot, never combine a
low-angle or upward view with an above, high-angle, or downward view, and never
combine a high-angle or downward view with a below, low-angle, or upward view.
Treat breathing, panting, grunts, and
exertion cries such as はぁはぁ or うおお as nonverbal physical foley unless
the source explicitly labels the quoted literal as dialogue. Include each
supplied NONVERBAL_SOUND phrase verbatim
in foley. dialogue_delivery must contain exactly one entry for every supplied
DIALOGUE id, but must never contain its words. Preserve every supplied English
DIALOGUE_VOICE_CONSTRAINT exactly in the corresponding delivery; never replace
an explicit voice with a generic conversational voice. Preserve every supplied
WARDROBE_OVERRIDE phrase exactly in scene or visible shot action; it replaces
the clothing from an image reference while identity, face, body shape, and hair
stay referenced. If no dialogue IDs are supplied, dialogue_delivery must be an
empty list."""


def _error(message: str, code: str) -> CommunityPromptPlannerError:
    return CommunityPromptPlannerError(message, code=code)


def _quote_text(match: re.Match[str]) -> str:
    return next(value for value in match.groupdict().values() if value is not None).strip()


def _contains_japanese(text: str) -> bool:
    return bool(_JAPANESE_RE.search(text))


def _normal_decimal(value: str | int | float | Decimal) -> str:
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise _error(f"Invalid numeric value: {value!r}", "INVALID_NUMBER") from exc
    if not number.is_finite():
        raise _error(f"Non-finite numeric value: {value!r}", "INVALID_NUMBER")
    normalized = format(number.normalize(), "f")
    return "0" if normalized in {"-0", ""} else normalized


def _nonverbal_meaning(text: str) -> str | None:
    compact = re.sub(r"[\s、。,.!！?？…・ー〜～~\-]", "", text).casefold()
    if not compact:
        return None
    if re.fullmatch(
        r"(?:はぁ|はあ|ハァ|ハア|ぜぇ|ぜえ|ゼェ|ゼエ|ふぅ|ふう|haa?|hah?)+",
        compact,
    ):
        return "strained breathing and panting"
    if re.fullmatch(r"(?:うぉ|うお|ウォ|ウオ|おぉ|おお){1,}[ぁあぉおォオ]*", compact):
        return "a nonverbal exertion roar"
    if re.fullmatch(r"(?:ぐっ|んっ|ぬっ|くっ|ふん|grunt)+", compact):
        return "a brief nonverbal exertion grunt"
    return None


def _voice_direction_for(context: str, literal: str) -> str:
    """Compile explicit Japanese voice controls without model interpretation."""

    traits: list[str] = []
    if re.search(r"(?:高い|高め|ハイトーン|high[- ]pitched)", context, re.IGNORECASE):
        traits.append("high-pitched")
    if re.search(r"(?:低い|低め|low[- ]pitched|deep voice)", context, re.IGNORECASE):
        traits.append("low-pitched")
    if re.search(r"(?:かわいい|可愛い|キュート|cute|sweet voice)", context, re.IGNORECASE):
        traits.append("cute")
    if re.search(r"(?:ロリ声|幼い声|幼め|youthful anime voice)", context, re.IGNORECASE):
        traits.append("youthful anime voice")
    if re.search(r"(?:くぐもった|こもった|muffled)", context, re.IGNORECASE):
        traits.append("muffled")
    if re.search(r"(?:落ち着いた|穏やか|calm)", context, re.IGNORECASE):
        traits.append("calm")
    if re.search(r"(?:明るい声|元気な声|bright voice|cheerful)", context, re.IGNORECASE):
        traits.append("bright and cheerful")
    if re.search(r"(?:かすれた|ハスキー|hoarse|husky)", context, re.IGNORECASE):
        traits.append("husky")
    if re.search(r"(?:囁|ささや|whisper)", context, re.IGNORECASE):
        traits.append("softly whispered")
    if re.search(r"(?:力強|強い口調|forceful)", context, re.IGNORECASE):
        traits.append("forceful")

    # An exertion cry is normally foley, but once the user explicitly marks it
    # as dialogue its performance must still match its literal shape.
    performance = ""
    meaning = _nonverbal_meaning(literal)
    if meaning == "a nonverbal exertion roar":
        performance = "delivered as a forceful exertion shout"
    elif meaning == "strained breathing and panting":
        performance = "delivered as breathless panting"

    traits = list(dict.fromkeys(traits))
    if traits:
        direction = "a " + ", ".join(traits) + " voice"
        if "youthful anime voice" in traits:
            # Avoid the awkward duplicated phrase "voice voice".
            direction = "a " + ", ".join(traits)
        return f"{direction}, {performance}" if performance else direction
    return performance


_WARDROBE_TERM_RE = re.compile(
    r"(?:衣装|服装|ウェア|ショートパンツ|タンクトップ|ランニング|ビキニ|水着|"
    r"制服|ジャージ|スカート|シャツ|ブラウス|ジーンズ|ズボン|ドレス|ワンピース)",
    re.IGNORECASE,
)
_WARDROBE_CHANGE_RE = re.compile(
    r"(?:着(?:て|る|用)|変更|替え|換え)", re.IGNORECASE
)
_WARDROBE_ENGLISH_OVERRIDE_RE = re.compile(
    r"(?:wears?|wearing|dressed in|change(?:s|d)? (?:the )?(?:outfit|clothes)|"
    r"wardrobe must)",
    re.IGNORECASE,
)


def _wardrobe_override_for(prompt: str) -> tuple[bool, str, tuple[str, ...]]:
    """Detect an authored replacement outfit and compile common garments."""

    lines = [
        line.strip()
        for line in prompt.splitlines()
        if (
            (_WARDROBE_TERM_RE.search(line) and _WARDROBE_CHANGE_RE.search(line))
            or _WARDROBE_ENGLISH_OVERRIDE_RE.search(line)
        )
    ]
    if not lines:
        return False, "", ()
    text = " ".join(lines)
    # Explicit preservation is not an override; the default image-reference
    # contract already preserves the original appearance and clothing.
    if re.search(r"(?:維持|保つ|そのまま|変えない|preserve|keep unchanged)", text, re.I):
        return False, "", ()

    terms: list[str] = []
    if re.search(r"(?:タンクトップ|ランニング)", text, re.I):
        terms.append("tank top")
    if re.search(r"ショートパンツ", text, re.I):
        terms.append("shorts")
    for pattern, english in (
        (r"マイクロビキニ", "micro bikini"),
        (r"(?<!マイクロ)ビキニ", "bikini"),
        (r"水着", "swimsuit"),
        (r"制服", "uniform"),
        (r"ジャージ", "tracksuit"),
        (r"スカート", "skirt"),
        (r"(?:Tシャツ|Ｔシャツ)", "T-shirt"),
        (r"ブラウス", "blouse"),
        (r"ジーンズ", "jeans"),
        (r"ズボン", "trousers"),
        (r"ドレス", "dress"),
        (r"ワンピース", "one-piece dress"),
    ):
        if re.search(pattern, text, re.I):
            terms.append(english)
    terms = list(dict.fromkeys(terms))

    if "トレーニングウェア" in text and {"tank top", "shorts"}.issubset(terms):
        direction = "workout wear consisting of a tank top and shorts"
    elif "トレーニングウェア" in text and terms:
        direction = "workout wear consisting of " + " and ".join(terms)
    elif "トレーニングウェア" in text:
        direction = "workout wear"
    elif terms:
        direction = " and ".join(terms)
    else:
        # Unknown clothing remains model-translated, but the reference contract
        # still switches from outfit preservation to authored wardrobe.
        direction = ""
    return True, direction, tuple(terms)


_UNQUOTED_NONVERBAL_RE = re.compile(
    r"(?<![\w\u3040-\u30ff\u31f0-\u31ff\u3400-\u9fff])"
    r"(?P<sound>(?:(?:(?:は[ぁあ]|ハ[ァア]|ぜ[ぇえ]|ゼ[ェエ])"
    r"(?:[、, ]*)){2,}[!！…]*|"
    r"う[ぉおォオー〜～]{3,}[!！…]*|ウ[ォオぉおー〜～]{3,}[!！…]*|"
    r"(?:ぐっ|んっ|ぬっ|くっ)(?:[、, ]*(?:ぐっ|んっ|ぬっ|くっ))*)[!！…]*)"
    r"(?=$|[\s、。,.!！?？…]|息|声|と|し)"
)


def _normalize_dialogue_text(text: str) -> str:
    value = re.sub(r"\s+", " ", text).strip()
    if not value:
        raise _error("Dialogue cannot be empty.", "EMPTY_DIALOGUE")
    if "\"" in value or "\r" in value or "\n" in value:
        raise _error(
            "Dialogue may not contain an ASCII double quote or a line break.",
            "UNSAFE_DIALOGUE_LITERAL",
        )
    if _D_TAG_RE.search(value):
        raise _error("Dialogue may not contain <d> tags.", "D_TAG_FORBIDDEN")
    return value


def _parse_reference_inventory(
    inventory: Sequence[Mapping[str, Any]] | None,
) -> tuple[ReferenceItem, ...]:
    if not inventory:
        return ()
    aliases = {
        "image": "image",
        "picture": "image",
        "photo": "image",
        "video": "video",
        "audio": "audio",
        "sound": "audio",
    }
    counters = {"image": 0, "video": 0, "audio": 0}
    result: list[ReferenceItem] = []
    seen: set[tuple[str, int]] = set()
    for position, raw in enumerate(inventory, start=1):
        if not isinstance(raw, Mapping):
            raise _error(
                f"Reference #{position} must be an object.", "INVALID_REFERENCE_INVENTORY"
            )
        raw_kind = str(raw.get("kind") or raw.get("type") or "").strip().casefold()
        kind = aliases.get(raw_kind)
        if kind is None:
            raise _error(
                f"Reference #{position} has unsupported kind {raw_kind!r}.",
                "INVALID_REFERENCE_KIND",
            )
        tag_value = raw.get("tag")
        tag_index: int | None = None
        if tag_value is not None:
            match = _REFERENCE_TAG_RE.fullmatch(str(tag_value).strip())
            if not match:
                raise _error(
                    f"Reference #{position} has invalid tag {tag_value!r}.",
                    "INVALID_REFERENCE_TAG",
                )
            tag_kind = {"picture": "image", "video": "video", "audio": "audio"}[
                match.group(1).casefold()
            ]
            if tag_kind != kind:
                raise _error(
                    f"Reference #{position} kind and tag disagree.",
                    "REFERENCE_KIND_TAG_MISMATCH",
                )
            tag_index = int(match.group(2))
        raw_index = raw.get("index")
        if raw_index is None:
            index = tag_index if tag_index is not None else counters[kind] + 1
        else:
            if isinstance(raw_index, bool):
                raise _error("Reference index must be an integer.", "INVALID_REFERENCE_INDEX")
            try:
                index = int(raw_index)
            except (TypeError, ValueError) as exc:
                raise _error(
                    f"Reference #{position} index is invalid.", "INVALID_REFERENCE_INDEX"
                ) from exc
            if str(raw_index).strip() != str(index) and not isinstance(raw_index, int):
                raise _error(
                    f"Reference #{position} index must be a whole integer.",
                    "INVALID_REFERENCE_INDEX",
                )
            if tag_index is not None and tag_index != index:
                raise _error(
                    f"Reference #{position} index and tag disagree.",
                    "REFERENCE_INDEX_TAG_MISMATCH",
                )
        if index < 1 or (kind, index) in seen:
            raise _error(
                f"Reference {kind} index {index} is invalid or duplicated.",
                "DUPLICATE_REFERENCE",
            )
        counters[kind] = max(counters[kind], index)
        seen.add((kind, index))
        role = str(raw.get("role") or raw.get("purpose") or "auto").strip().casefold()
        result.append(ReferenceItem(kind=kind, index=index, role=role))
    return tuple(result)


def extract_shot_numbers(text: str) -> tuple[int, ...]:
    numbers = tuple(
        int(match.group("bracket") or match.group("plain"))
        for match in _SHOT_HEADER_RE.finditer(text)
    )
    if len(numbers) != len(set(numbers)):
        raise _error("Cut/Shot numbers are duplicated.", "DUPLICATE_SOURCE_SHOT")
    if numbers and any(right <= left for left, right in zip(numbers, numbers[1:])):
        raise _error(
            f"Cut/Shot numbers must be strictly increasing: {numbers!r}.",
            "SOURCE_SHOT_ORDER_INVALID",
        )
    return numbers


def _camera_direction_flags(text: str) -> frozenset[str]:
    """Return semantic camera-direction evidence found in English controls."""

    flags: set[str] = set()
    if _CAMERA_LOW_VIEWPOINT_RE.search(text):
        flags.add("low")
    if _CAMERA_HIGH_VIEWPOINT_RE.search(text):
        flags.add("high")
    if _CAMERA_UPWARD_VIEW_RE.search(text):
        flags.add("up")
    if _CAMERA_DOWNWARD_VIEW_RE.search(text):
        flags.add("down")
    return frozenset(flags)


def _source_camera_requirements(
    text: str,
) -> tuple[dict[int, frozenset[str]], frozenset[str]]:
    """Map authored Japanese camera cues to their numbered source shot.

    Text before the first explicit Cut/Shot header has no unambiguous target and
    is returned as a global requirement. Text within a numbered block is never
    allowed to be satisfied by a different generated shot.
    """

    matches = list(_SHOT_HEADER_RE.finditer(text))

    def requirements(fragment: str) -> frozenset[str]:
        result: set[str] = set()
        if re.search(r"(?:仰角|煽り)", fragment):
            result.add("low_up")
        if "俯瞰" in fragment:
            result.add("high_down")
        return frozenset(result)

    if not matches:
        return {}, requirements(text)

    per_shot: dict[int, frozenset[str]] = {}
    for index, match in enumerate(matches):
        number = int(match.group("bracket") or match.group("plain"))
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        shot_requirements = requirements(text[match.end() : end])
        if shot_requirements:
            per_shot[number] = shot_requirements
    return per_shot, requirements(text[: matches[0].start()])


_UNIT_ALIASES = {
    "秒": "seconds",
    "s": "seconds",
    "sec": "seconds",
    "second": "seconds",
    "seconds": "seconds",
    "分": "minutes",
    "min": "minutes",
    "minute": "minutes",
    "minutes": "minutes",
    "時間": "hours",
    "h": "hours",
    "hour": "hours",
    "hours": "hours",
    "kg": "kg",
    "キログラム": "kg",
    "g": "g",
    "グラム": "g",
    "km": "km",
    "キロメートル": "km",
    "m": "m",
    "メートル": "m",
    "cm": "cm",
    "センチ": "cm",
    "センチメートル": "cm",
    "mm": "mm",
    "%": "%",
    "％": "%",
    "fps": "fps",
    "フレーム": "frames",
    "frame": "frames",
    "frames": "frames",
    "回": "times",
    "times": "times",
}
_UNIT_PATTERN = "|".join(
    sorted((re.escape(unit) for unit in _UNIT_ALIASES), key=len, reverse=True)
)
_VALUE_UNIT_RE = re.compile(
    rf"(?<![A-Za-z0-9.])(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>{_UNIT_PATTERN})(?![A-Za-z])",
    re.IGNORECASE,
)
_RANGE_UNIT_RE = re.compile(
    rf"(?<![A-Za-z0-9.])(?P<left>\d+(?:\.\d+)?)\s*"
    rf"(?:-|–|—|~|〜|～)\s*(?P<right>\d+(?:\.\d+)?)\s*"
    rf"(?P<unit>{_UNIT_PATTERN})(?![A-Za-z])",
    re.IGNORECASE,
)
_RESOLUTION_RE = re.compile(r"(?<!\d)(?P<w>\d{2,5})\s*[x×]\s*(?P<h>\d{2,5})(?!\d)", re.I)
_SMALL_NUMBER_WORDS = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
}
_WORD_VALUE_UNIT_RE = re.compile(
    r"\b(?P<value>" + "|".join(_SMALL_NUMBER_WORDS) + r")\s+"
    r"(?P<unit>times?|repetitions?|reps?|seconds?|minutes?|hours?|frames?)\b",
    re.IGNORECASE,
)


def _canonical_unit(raw: str) -> str:
    return _UNIT_ALIASES[raw.casefold() if raw.casefold() in _UNIT_ALIASES else raw]


def extract_numeric_facts(text: str) -> tuple[NumericFact, ...]:
    facts: list[NumericFact] = []
    occupied: list[tuple[int, int]] = []
    for match in _RESOLUTION_RE.finditer(text):
        value = f"{int(match.group('w'))}x{int(match.group('h'))}"
        facts.append(NumericFact(value, "resolution", match.group(0)))
        occupied.append(match.span())
    for match in _RANGE_UNIT_RE.finditer(text):
        unit = _canonical_unit(match.group("unit"))
        facts.extend(
            (
                NumericFact(_normal_decimal(match.group("left")), unit, match.group(0)),
                NumericFact(_normal_decimal(match.group("right")), unit, match.group(0)),
            )
        )
        occupied.append(match.span())

    def overlaps(span: tuple[int, int]) -> bool:
        return any(span[0] < end and span[1] > start for start, end in occupied)

    for match in _VALUE_UNIT_RE.finditer(text):
        if overlaps(match.span()):
            continue
        facts.append(
            NumericFact(
                _normal_decimal(match.group("value")),
                _canonical_unit(match.group("unit")),
                match.group(0),
            )
        )
    word_unit_aliases = {
        "time": "times",
        "times": "times",
        "repetition": "times",
        "repetitions": "times",
        "rep": "times",
        "reps": "times",
        "second": "seconds",
        "seconds": "seconds",
        "minute": "minutes",
        "minutes": "minutes",
        "hour": "hours",
        "hours": "hours",
        "frame": "frames",
        "frames": "frames",
    }
    for match in _WORD_VALUE_UNIT_RE.finditer(text):
        facts.append(
            NumericFact(
                str(_SMALL_NUMBER_WORDS[match.group("value").casefold()]),
                word_unit_aliases[match.group("unit").casefold()],
                match.group(0),
            )
        )
    # Preserve order but collapse repeated statements of the same fact.
    unique: list[NumericFact] = []
    seen_keys: set[tuple[str, str]] = set()
    for fact in facts:
        if fact.key not in seen_keys:
            unique.append(fact)
            seen_keys.add(fact.key)
    return tuple(unique)


@dataclass(frozen=True, slots=True)
class _Replacement:
    start: int
    end: int
    value: str


def prepare_planner_input(
    prompt: str,
    *,
    reference_inventory: Sequence[Mapping[str, Any]] | None = None,
    dialogue_texts: Sequence[str] | None = None,
    duration_seconds: float | None = None,
    style_direction: str = "",
    soundscape: str = "",
    audio_preset: str = "auto",
    music_policy: str = "auto",
) -> PreparedPlannerInput:
    """Redact literal dialogue and collect deterministic validation metadata."""

    if not isinstance(prompt, str) or not prompt.strip():
        raise _error("The source prompt is empty.", "EMPTY_SOURCE_PROMPT")
    if _D_TAG_RE.search(prompt):
        raise _error("The community workflow does not accept <d> tags.", "D_TAG_FORBIDDEN")
    if not isinstance(style_direction, str) or not isinstance(soundscape, str):
        raise _error(
            "style_direction and soundscape must be strings.",
            "INVALID_AUXILIARY_CONTROL",
        )
    style_direction = style_direction.strip()
    soundscape = soundscape.strip()
    for label, value in (("style_direction", style_direction), ("soundscape", soundscape)):
        if _D_TAG_RE.search(value) or _ANY_REFERENCE_LIKE_RE.search(value):
            raise _error(
                f"{label} may not contain H3 tags.", "AUXILIARY_CONTROL_TAG_FORBIDDEN"
            )
        if any(
            _contains_japanese(_quote_text(match))
            for match in _QUOTE_RE.finditer(value)
        ):
            raise _error(
                f"{label} contains quoted Japanese. Put literal speech in dialogue_texts.",
                "DIALOGUE_IN_AUXILIARY_CONTROL",
            )
    audio_preset = str(audio_preset or "auto").strip().casefold()
    if audio_preset not in _AUDIO_PRESET_DIRECTIONS:
        raise _error(
            f"Unsupported audio_preset {audio_preset!r}.", "INVALID_AUDIO_PRESET"
        )
    music_policy = str(music_policy or "auto").strip().casefold()
    if music_policy not in {"auto", *_MUSIC_POLICY_DIRECTIONS}:
        raise _error(
            f"Unsupported music_policy {music_policy!r}.", "INVALID_MUSIC_POLICY"
        )
    wardrobe_override, wardrobe_direction, wardrobe_required_terms = (
        _wardrobe_override_for(prompt)
    )
    references = _parse_reference_inventory(reference_inventory)
    if duration_seconds is not None:
        try:
            duration_seconds = float(duration_seconds)
        except (TypeError, ValueError) as exc:
            raise _error("duration_seconds must be numeric.", "INVALID_DURATION") from exc
        if not math.isfinite(duration_seconds) or duration_seconds <= 0:
            raise _error("duration_seconds must be positive and finite.", "INVALID_DURATION")

    requested = [
        _normalize_dialogue_text(value)
        for value in (dialogue_texts or ())
        if isinstance(value, str) and value.strip()
    ]
    if dialogue_texts and len(requested) != len(dialogue_texts):
        raise _error("Every dialogue_texts entry must be a non-empty string.", "INVALID_DIALOGUE_LIST")

    replacements: list[_Replacement] = []
    allowed_references = {item.label.casefold(): item for item in references}
    for match in _REFERENCE_TAG_RE.finditer(prompt):
        canonical = f"<{match.group(1).title()} {int(match.group(2))}>"
        reference = allowed_references.get(canonical.casefold())
        if reference is None:
            raise _error(
                f"Source prompt uses {canonical}, but it is absent from the actual "
                "reference inventory.",
                "SOURCE_REFERENCE_NOT_IN_INVENTORY",
            )
        if reference.kind == "image":
            replacement_text = (
                f"the visible subject from supplied image reference number {reference.index}"
            )
        elif reference.kind == "video":
            replacement_text = f"supplied video reference number {reference.index}"
        else:
            replacement_text = f"supplied audio reference number {reference.index}"
        replacements.append(_Replacement(match.start(), match.end(), replacement_text))

    # The community planner accepts only Picture/Video/Audio tags backed by the
    # actual upload inventory; those canonical tags were handled above.
    prompt_without_reference_tags = _REFERENCE_TAG_RE.sub("", prompt)
    unexpected_tag = _ANY_REFERENCE_LIKE_RE.search(prompt_without_reference_tags)
    if unexpected_tag:
        raise _error(
            f"Source prompt contains unsupported reference tag {unexpected_tag.group(0)}.",
            "SOURCE_REFERENCE_TAG_UNSUPPORTED",
        )
    dialogues: list[DialogueLiteral] = []
    cues: list[NonverbalCue] = []
    consumed_requested: set[int] = set()

    for match in _QUOTE_RE.finditer(prompt):
        literal = _normalize_dialogue_text(_quote_text(match))
        if not _contains_japanese(literal):
            continue
        local = prompt[max(0, match.start() - 56) : min(len(prompt), match.end() + 56)]
        requested_index = next(
            (
                i
                for i, text in enumerate(requested)
                if i not in consumed_requested and text == literal
            ),
            None,
        )
        explicitly_spoken = requested_index is not None or bool(
            _EXPLICIT_SPEECH_CUE_RE.search(local)
        )
        if requested_index is None and _VISUAL_TEXT_CUE_RE.search(local):
            # Exact Japanese visible text is intentionally outside this
            # speech-safe contract. Leave it for semantic English translation.
            continue
        nonverbal = _nonverbal_meaning(literal)
        if nonverbal is not None and not explicitly_spoken:
            cue = NonverbalCue(len(cues) + 1, literal, nonverbal)
            cues.append(cue)
            replacements.append(_Replacement(match.start(), match.end(), cue.placeholder))
            continue
        dialogue = DialogueLiteral(
            len(dialogues) + 1,
            literal,
            _voice_direction_for(local, literal),
        )
        dialogues.append(dialogue)
        replacements.append(_Replacement(match.start(), match.end(), dialogue.placeholder))
        if requested_index is not None:
            consumed_requested.add(requested_index)

    # Explicit dialogue fields remain authoritative even when the prose did not
    # repeat them. Their literal text is never included in the model request.
    for index, literal in enumerate(requested):
        if index in consumed_requested:
            continue
        # dialogue_texts is an explicit user contract. Even an interjection or
        # breath-like literal remains exact speech when supplied here.
        position = prompt.find(literal)
        local = (
            prompt[max(0, position - 56) : min(len(prompt), position + len(literal) + 56)]
            if position >= 0
            else ""
        )
        dialogue = DialogueLiteral(
            len(dialogues) + 1,
            literal,
            _voice_direction_for(local, literal),
        )
        dialogues.append(dialogue)
        if position >= 0 and not any(
            position < item.end and position + len(literal) > item.start
            for item in replacements
        ):
            replacements.append(
                _Replacement(position, position + len(literal), dialogue.placeholder)
            )

    if len({item.text for item in dialogues}) != len(dialogues):
        raise _error(
            "The same dialogue literal was supplied more than once; each literal must be unique.",
            "DUPLICATE_DIALOGUE_TEXT",
        )

    redacted = prompt
    for replacement in sorted(replacements, key=lambda item: item.start, reverse=True):
        redacted = redacted[: replacement.start] + replacement.value + redacted[replacement.end :]

    # Convert obvious unquoted exertion vocalizations into semantic physical
    # sound placeholders before Qwen sees them.
    def replace_unquoted(match: re.Match[str]) -> str:
        source = match.group("sound")
        meaning = _nonverbal_meaning(source) or "a nonverbal exertion sound"
        cue = NonverbalCue(len(cues) + 1, source, meaning)
        cues.append(cue)
        return cue.placeholder

    redacted = _UNQUOTED_NONVERBAL_RE.sub(replace_unquoted, redacted)
    if dialogues:
        redacted += "\n\nDialogue events requested: " + ", ".join(
            item.placeholder for item in dialogues
        )
    if cues:
        redacted += "\nNonverbal physical sounds required: " + "; ".join(
            item.placeholder for item in cues
        )

    for dialogue in dialogues:
        if dialogue.text in redacted:
            raise _error(
                "Dialogue redaction failed; literal words would reach the model.",
                "DIALOGUE_REDACTION_FAILED",
            )

    source_shots = extract_shot_numbers(prompt)
    fact_source = "\n".join(value for value in (redacted, style_direction, soundscape) if value)
    facts = extract_numeric_facts(fact_source)
    return PreparedPlannerInput(
        source_prompt=prompt,
        redacted_prompt=redacted.strip(),
        dialogues=tuple(dialogues),
        nonverbal_cues=tuple(cues),
        references=references,
        source_shot_numbers=source_shots,
        numeric_facts=facts,
        duration_seconds=duration_seconds,
        style_direction=style_direction,
        soundscape=soundscape,
        audio_preset=audio_preset,
        music_policy=music_policy,
        wardrobe_override=wardrobe_override,
        wardrobe_direction=wardrobe_direction,
        wardrobe_required_terms=wardrobe_required_terms,
    )


def build_model_messages(prepared: PreparedPlannerInput) -> list[dict[str, str]]:
    """Build the Qwen conversation without exposing literal dialogue or tags."""

    shot_camera_requirements, global_camera_requirements = _source_camera_requirements(
        prepared.source_prompt
    )
    camera_requirement_labels: list[str] = []
    for number, requirements in shot_camera_requirements.items():
        if "low_up" in requirements:
            camera_requirement_labels.append(f"Shot {number} = low-angle upward view")
        if "high_down" in requirements:
            camera_requirement_labels.append(f"Shot {number} = high-angle downward view")
    if "low_up" in global_camera_requirements:
        camera_requirement_labels.append("unscoped = low-angle upward view")
    if "high_down" in global_camera_requirements:
        camera_requirement_labels.append("unscoped = high-angle downward view")

    lines = [
        "SOURCE REQUEST (literal dialogue has been redacted):",
        prepared.redacted_prompt,
        "",
        "COMPILER CONSTRAINTS:",
        "- Dialogue IDs: "
        + (", ".join(str(item.dialogue_id) for item in prepared.dialogues) or "none"),
        "- Dialogue voice constraints: "
        + (
            "; ".join(
                f"DIALOGUE {item.dialogue_id} = {item.voice_direction}"
                for item in prepared.dialogues
                if item.voice_direction
            )
            or "none explicitly supplied"
        ),
        f"- Image reference count: {sum(item.kind == 'image' for item in prepared.references)}",
        f"- Video reference count: {sum(item.kind == 'video' for item in prepared.references)}",
        f"- Audio reference count: {sum(item.kind == 'audio' for item in prepared.references)}",
        "- Source Cut/Shot order: "
        + (", ".join(map(str, prepared.source_shot_numbers)) or "not explicitly numbered"),
        "- Required camera direction by source shot: "
        + ("; ".join(camera_requirement_labels) or "none explicitly supplied"),
        "- Required numeric facts: "
        + (
            ", ".join(f"{fact.value} {fact.unit}" for fact in prepared.numeric_facts)
            or "none"
        ),
        "- Required nonverbal foley phrases: "
        + (", ".join(item.english_sound for item in prepared.nonverbal_cues) or "none"),
        "- Additional style direction: " + (prepared.style_direction or "none"),
        "- WARDROBE_OVERRIDE: "
        + (
            prepared.wardrobe_direction
            if prepared.wardrobe_override and prepared.wardrobe_direction
            else (
                "The authored source clothing replaces the image-reference outfit; "
                "translate its garments precisely."
                if prepared.wardrobe_override
                else "none; preserve the reference appearance"
            )
        ),
        "- Additional soundscape direction: " + (prepared.soundscape or "none"),
        "- Audio mix preset: "
        + _AUDIO_PRESET_DIRECTIONS[prepared.audio_preset],
        "- Music policy: "
        + (
            "Choose the most natural option from the source request."
            if prepared.music_policy == "auto"
            else _MUSIC_POLICY_DIRECTIONS[prepared.music_policy]
        ),
    ]
    if prepared.duration_seconds is not None:
        lines.append(f"- Exact video duration: {_normal_decimal(prepared.duration_seconds)} seconds")
    user_message = "\n".join(lines)
    for dialogue in prepared.dialogues:
        if dialogue.text in user_message:
            raise _error("Dialogue leaked into the model request.", "DIALOGUE_REDACTION_FAILED")
    if _ANY_REFERENCE_LIKE_RE.search(user_message):
        raise _error("Reference tags leaked into the model request.", "REFERENCE_TAG_LEAK")
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]


def _require_exact_keys(value: Mapping[str, Any], keys: set[str], where: str) -> None:
    actual = set(value)
    if actual != keys:
        missing = sorted(keys - actual)
        extra = sorted(actual - keys)
        raise _error(
            f"{where} has the wrong keys (missing={missing}, extra={extra}).",
            "PLAN_SCHEMA_MISMATCH",
        )


def _strict_int(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _error(f"{where} must be an integer.", "PLAN_TYPE_ERROR")
    return value


def _strict_float(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _error(f"{where} must be numeric.", "PLAN_TYPE_ERROR")
    result = float(value)
    if not math.isfinite(result):
        raise _error(f"{where} must be finite.", "PLAN_TYPE_ERROR")
    return result


def _english_string(value: Any, where: str, *, allow_na: bool = False) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _error(f"{where} must be a non-empty string.", "PLAN_TYPE_ERROR")
    result = re.sub(r"\s+", " ", value).strip()
    if _NON_ENGLISH_RE.search(result):
        raise _error(f"{where} contains non-English control text.", "NON_ENGLISH_CONTROL")
    if _ANY_REFERENCE_LIKE_RE.search(result):
        raise _error(f"{where} contains a model-generated reference tag.", "INVENTED_REFERENCE_TAG")
    if _D_TAG_RE.search(result):
        raise _error(f"{where} contains a forbidden <d> tag.", "D_TAG_FORBIDDEN")
    if any(mark in result for mark in ('"', "“", "”", "「", "」", "『", "』")):
        raise _error(f"{where} contains quotation marks.", "MODEL_QUOTED_TEXT")
    if not allow_na and result.casefold() == "n/a":
        raise _error(f"{where} may not be N/A.", "PLAN_EMPTY_FIELD")
    return result


def _string_list(value: Any, where: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise _error(f"{where} must be an array.", "PLAN_TYPE_ERROR")
    return tuple(_english_string(item, f"{where}[{index}]") for index, item in enumerate(value))


def _document_numeric_facts(plan: CommunityPromptPlan) -> set[tuple[str, str]]:
    facts = set(
        fact.key
        for fact in extract_numeric_facts(
            "\n".join(
                [
                    plan.style,
                    plan.scene,
                    *(shot.framing for shot in plan.shots),
                    *(shot.camera for shot in plan.shots),
                    *(shot.action for shot in plan.shots),
                    *plan.ambient,
                    *plan.foley,
                    plan.music,
                    *(delivery.speaker for delivery in plan.dialogue_delivery),
                    *(delivery.delivery for delivery in plan.dialogue_delivery),
                ]
            )
        )
    )
    for shot in plan.shots:
        facts.add((_normal_decimal(shot.start_seconds), "seconds"))
        facts.add((_normal_decimal(shot.end_seconds), "seconds"))
    for delivery in plan.dialogue_delivery:
        facts.add((_normal_decimal(delivery.start_seconds), "seconds"))
    return facts


def validate_plan(plan: CommunityPromptPlan, prepared: PreparedPlannerInput) -> None:
    numbers = tuple(shot.number for shot in plan.shots)
    if not numbers:
        raise _error("The plan must contain at least one shot.", "NO_SHOTS")
    expected_numbers = prepared.source_shot_numbers or tuple(range(1, len(numbers) + 1))
    if numbers != expected_numbers:
        raise _error(
            f"Shot order {numbers!r} does not match {expected_numbers!r}.",
            "SHOT_ORDER_MISMATCH",
        )
    previous_end = -1.0
    for shot in plan.shots:
        if shot.start_seconds < 0 or shot.end_seconds <= shot.start_seconds:
            raise _error(
                f"Shot {shot.number} has an invalid time range.", "INVALID_SHOT_TIMING"
            )
        if shot.start_seconds + 1e-6 < previous_end:
            raise _error(
                f"Shot {shot.number} overlaps the preceding shot.", "OVERLAPPING_SHOTS"
            )
        camera_flags = _camera_direction_flags(f"{shot.framing} {shot.camera}")
        if camera_flags.intersection({"low", "up"}) and camera_flags.intersection(
            {"high", "down"}
        ):
            raise _error(
                f"Shot {shot.number} contains contradictory camera geometry: "
                f"{', '.join(sorted(camera_flags))}.",
                "CAMERA_DIRECTION_CONFLICT",
            )
        previous_end = shot.end_seconds
    if plan.shots[0].start_seconds > 0.05:
        raise _error("The first shot must begin at 0 seconds.", "SHOT_TIMELINE_GAP")
    if prepared.duration_seconds is not None:
        if abs(plan.shots[-1].end_seconds - prepared.duration_seconds) > 0.05:
            raise _error(
                "The final shot must end at the requested video duration.",
                "DURATION_MISMATCH",
            )

    expected_dialogue_ids = tuple(item.dialogue_id for item in prepared.dialogues)
    actual_dialogue_ids = tuple(item.dialogue_id for item in plan.dialogue_delivery)
    if actual_dialogue_ids != expected_dialogue_ids:
        raise _error(
            f"Dialogue delivery IDs {actual_dialogue_ids!r} do not match "
            f"{expected_dialogue_ids!r}.",
            "DIALOGUE_DELIVERY_MISMATCH",
        )
    shot_lookup = {shot.number: shot for shot in plan.shots}
    for delivery in plan.dialogue_delivery:
        shot = shot_lookup.get(delivery.shot)
        if shot is None:
            raise _error(
                f"Dialogue {delivery.dialogue_id} targets an unknown shot.",
                "DIALOGUE_SHOT_MISMATCH",
            )
        if not (shot.start_seconds <= delivery.start_seconds <= shot.end_seconds):
            raise _error(
                f"Dialogue {delivery.dialogue_id} is outside Shot {delivery.shot}.",
                "DIALOGUE_TIME_MISMATCH",
            )

    control_fields = [plan.style, plan.scene]
    for shot in plan.shots:
        control_fields.extend((shot.framing, shot.camera, shot.action))
    control_fields.extend((*plan.ambient, *plan.foley, plan.music))
    if any(_SPEECH_CONTROL_RE.search(value) for value in control_fields):
        raise _error(
            "Speech or narration instructions may appear only in dialogue_delivery.",
            "INVENTED_SPEECH_CONTROL",
        )
    if any(_FORBIDDEN_AUDIO_SPEECH_RE.search(value) for value in (*plan.ambient, *plan.foley)):
        raise _error(
            "Ambient/foley may not contain speech or narration.",
            "INVENTED_SPEECH_AUDIO",
        )

    wardrobe_control = " ".join(
        [
            plan.scene,
            *(shot.framing for shot in plan.shots),
            *(shot.action for shot in plan.shots),
        ]
    ).casefold()
    if prepared.wardrobe_override:
        if not re.search(
            r"\b(?:wears?|wearing|outfit|wardrobe|clothing|dress|shirt|top|shorts|"
            r"bikini|swimsuit|uniform|tracksuit|skirt|jeans|trousers)\b",
            wardrobe_control,
            re.IGNORECASE,
        ):
            raise _error(
                "The authored wardrobe override is absent from Scene/Shot controls.",
                "WARDROBE_OVERRIDE_MISSING",
            )
        missing_wardrobe = [
            term
            for term in prepared.wardrobe_required_terms
            if term.casefold() not in wardrobe_control
        ]
        if missing_wardrobe:
            raise _error(
                "The model dropped wardrobe details: " + ", ".join(missing_wardrobe) + ".",
                "WARDROBE_DETAIL_MISSING",
            )

    camera_flags_by_shot = {
        shot.number: _camera_direction_flags(f"{shot.framing} {shot.camera}")
        for shot in plan.shots
    }
    source_camera_by_shot, global_camera_requirements = _source_camera_requirements(
        prepared.source_prompt
    )

    def require_camera_direction(
        requirement: str, flags: frozenset[str], *, source_label: str
    ) -> None:
        if requirement == "low_up" and not {"low", "up"}.issubset(flags):
            raise _error(
                f"Japanese 仰角/煽り in {source_label} must compile to a coherent "
                "low-angle upward view in that same Shot.",
                "CAMERA_GLOSSARY_MISMATCH",
            )
        if requirement == "high_down" and not {"high", "down"}.issubset(flags):
            raise _error(
                f"Japanese 俯瞰 in {source_label} must compile to a coherent "
                "high-angle downward view in that same Shot.",
                "CAMERA_GLOSSARY_MISMATCH",
            )

    for shot_number, requirements in source_camera_by_shot.items():
        flags = camera_flags_by_shot[shot_number]
        for requirement in requirements:
            require_camera_direction(requirement, flags, source_label=f"Cut/Shot {shot_number}")

    for requirement in global_camera_requirements:
        if requirement == "low_up":
            satisfied = any(
                {"low", "up"}.issubset(flags) for flags in camera_flags_by_shot.values()
            )
        else:
            satisfied = any(
                {"high", "down"}.issubset(flags) for flags in camera_flags_by_shot.values()
            )
        if not satisfied:
            require_camera_direction(requirement, frozenset(), source_label="the source request")

    joined_foley = "\n".join(plan.foley).casefold()
    for cue in prepared.nonverbal_cues:
        if cue.english_sound.casefold() not in joined_foley:
            raise _error(
                f"Required nonverbal foley is missing: {cue.english_sound}.",
                "NONVERBAL_FOLEY_MISSING",
            )

    actual_facts = _document_numeric_facts(plan)
    missing = [fact for fact in prepared.numeric_facts if fact.key not in actual_facts]
    if missing:
        summary = ", ".join(f"{fact.value} {fact.unit}" for fact in missing)
        raise _error(f"The model dropped numeric facts: {summary}.", "NUMERIC_FACT_MISSING")


def parse_plan_json(raw: str, prepared: PreparedPlannerInput) -> CommunityPromptPlan:
    """Parse one raw model result and enforce the strict plan schema."""

    if not isinstance(raw, str) or not raw.strip():
        raise _error("The planner model returned no text.", "EMPTY_MODEL_RESULT")
    stripped = raw.strip()
    if not stripped.startswith("{") or not stripped.endswith("}"):
        raise _error("The planner must return raw JSON only.", "MODEL_RESULT_NOT_JSON_ONLY")
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise _error(f"The planner returned invalid JSON: {exc}.", "MODEL_JSON_INVALID") from exc
    if not isinstance(value, dict):
        raise _error("The planner result must be a JSON object.", "PLAN_TYPE_ERROR")
    _require_exact_keys(value, _TOP_LEVEL_KEYS, "plan")
    if value["schema_version"] != PLAN_SCHEMA_VERSION:
        raise _error("The planner schema version is wrong.", "PLAN_SCHEMA_VERSION_MISMATCH")

    shots_value = value["shots"]
    if not isinstance(shots_value, list):
        raise _error("shots must be an array.", "PLAN_TYPE_ERROR")
    shots: list[ShotPlan] = []
    for index, raw_shot in enumerate(shots_value):
        if not isinstance(raw_shot, dict):
            raise _error(f"shots[{index}] must be an object.", "PLAN_TYPE_ERROR")
        _require_exact_keys(raw_shot, _SHOT_KEYS, f"shots[{index}]")
        shots.append(
            ShotPlan(
                number=_strict_int(raw_shot["number"], f"shots[{index}].number"),
                start_seconds=_strict_float(
                    raw_shot["start_seconds"], f"shots[{index}].start_seconds"
                ),
                end_seconds=_strict_float(
                    raw_shot["end_seconds"], f"shots[{index}].end_seconds"
                ),
                framing=_english_string(raw_shot["framing"], f"shots[{index}].framing"),
                camera=_english_string(raw_shot["camera"], f"shots[{index}].camera"),
                action=_english_string(raw_shot["action"], f"shots[{index}].action"),
            )
        )

    deliveries_value = value["dialogue_delivery"]
    if not isinstance(deliveries_value, list):
        raise _error("dialogue_delivery must be an array.", "PLAN_TYPE_ERROR")
    deliveries: list[DialogueDelivery] = []
    for index, raw_delivery in enumerate(deliveries_value):
        if not isinstance(raw_delivery, dict):
            raise _error(f"dialogue_delivery[{index}] must be an object.", "PLAN_TYPE_ERROR")
        _require_exact_keys(raw_delivery, _DELIVERY_KEYS, f"dialogue_delivery[{index}]")
        deliveries.append(
            DialogueDelivery(
                dialogue_id=_strict_int(
                    raw_delivery["dialogue_id"], f"dialogue_delivery[{index}].dialogue_id"
                ),
                shot=_strict_int(raw_delivery["shot"], f"dialogue_delivery[{index}].shot"),
                start_seconds=_strict_float(
                    raw_delivery["start_seconds"],
                    f"dialogue_delivery[{index}].start_seconds",
                ),
                speaker=_english_string(
                    raw_delivery["speaker"], f"dialogue_delivery[{index}].speaker"
                ),
                delivery=_english_string(
                    raw_delivery["delivery"], f"dialogue_delivery[{index}].delivery"
                ),
            )
        )

    plan = CommunityPromptPlan(
        schema_version=PLAN_SCHEMA_VERSION,
        style=_english_string(value["style"], "style"),
        scene=_english_string(value["scene"], "scene"),
        shots=tuple(shots),
        ambient=_string_list(value["ambient"], "ambient"),
        foley=_string_list(value["foley"], "foley"),
        music=_english_string(value["music"], "music", allow_na=True),
        dialogue_delivery=tuple(deliveries),
    )
    validate_plan(plan, prepared)
    return plan


def _format_seconds(value: float) -> str:
    return _normal_decimal(value)


def _render_references(prepared: PreparedPlannerInput) -> list[str]:
    references = prepared.references
    if not references:
        return []
    lines = ["Reference material:"]
    image_ordinal = 0
    for item in references:
        if item.kind == "image":
            image_ordinal += 1
            if image_ordinal == 1 and prepared.wardrobe_override:
                lines.append(
                    f"{item.label} defines the primary visible subject's identity, face, "
                    "body shape, and hair; the wardrobe must follow the Scene and Shot "
                    "instructions and replaces the reference outfit."
                )
                continue
            if item.role in {"style", "visual_style"}:
                description = "defines the visual style, palette, and rendering language"
            elif item.role in {"composition", "first_frame", "start_frame"}:
                description = "defines the opening composition and subject placement"
            elif image_ordinal == 1:
                description = "defines the primary visible subject identity and appearance"
            else:
                description = "defines an additional visible subject identity and appearance"
        elif item.kind == "video":
            description = "defines the intended motion rhythm and camera behavior"
        elif item.role in {"music", "bgm"}:
            description = "defines the non-diegetic music character"
        elif item.role in {"ambience", "ambient", "environment"}:
            description = "defines the environmental ambience"
        else:
            description = "defines voice timbre and delivery"
        lines.append(f"{item.label} {description}.")
    return lines


def render_prompt(plan: CommunityPromptPlan, prepared: PreparedPlannerInput) -> str:
    """Render the validated model plan into the public-example prompt shape."""

    validate_plan(plan, prepared)
    blocks: list[str] = [f"Style:\n{plan.style}"]
    reference_lines = _render_references(prepared)
    if reference_lines:
        blocks.append("\n".join(reference_lines))
    scene_lines = [plan.scene]
    if prepared.wardrobe_override and prepared.wardrobe_direction:
        scene_lines.append(
            "Wardrobe: The primary visible subject wears "
            f"{prepared.wardrobe_direction}, replacing the reference outfit."
        )
    blocks.append("Scene:\n" + "\n".join(scene_lines))
    for shot in plan.shots:
        blocks.append(
            f"Shot {shot.number} ({_format_seconds(shot.start_seconds)}-"
            f"{_format_seconds(shot.end_seconds)} seconds):\n"
            f"Framing: {shot.framing}\n"
            f"Camera: {shot.camera}\n"
            f"Action: {shot.action}"
        )
    ambient = "; ".join(plan.ambient) if plan.ambient else "N/A"
    foley = "; ".join(plan.foley) if plan.foley else "N/A"
    effective_audio_preset = prepared.audio_preset
    if effective_audio_preset == "dialogue" and not prepared.dialogues:
        effective_audio_preset = "ambience"
    effective_music = (
        plan.music
        if prepared.music_policy == "auto"
        else _MUSIC_POLICY_DIRECTIONS[prepared.music_policy]
    )
    audio_lines = [
        "Audio:",
        f"Mix: {_AUDIO_PRESET_DIRECTIONS[effective_audio_preset]}",
        f"Ambient: {ambient}",
        f"Foley: {foley}",
        f"Music: {effective_music}",
    ]
    literal_by_id = {item.dialogue_id: item for item in prepared.dialogues}
    for delivery in plan.dialogue_delivery:
        literal = literal_by_id[delivery.dialogue_id]
        effective_delivery = literal.voice_direction or delivery.delivery
        audio_lines.append(
            f"At {_format_seconds(delivery.start_seconds)} seconds in Shot {delivery.shot}, "
            f"{delivery.speaker} says in Japanese with {effective_delivery}: \"{literal.text}\""
        )
    blocks.append("\n".join(audio_lines))
    prompt = "\n\n".join(blocks).strip()
    validate_rendered_prompt(prompt, plan, prepared)
    return prompt


def validate_rendered_prompt(
    prompt: str, plan: CommunityPromptPlan, prepared: PreparedPlannerInput
) -> None:
    if _D_TAG_RE.search(prompt):
        raise _error("Rendered prompt contains a forbidden <d> tag.", "D_TAG_FORBIDDEN")
    quoted = re.findall(r'\"([^\"\r\n]*)\"', prompt)
    expected_quotes = [item.text for item in prepared.dialogues]
    if quoted != expected_quotes:
        raise _error(
            f"Rendered dialogue quotes {quoted!r} do not match {expected_quotes!r}.",
            "DIALOGUE_QUOTE_MISMATCH",
        )
    for literal in expected_quotes:
        if prompt.count(f'\"{literal}\"') != 1:
            raise _error(
                "Every dialogue literal must appear in exactly one ordinary quote pair.",
                "DIALOGUE_NOT_EXACTLY_ONCE",
            )
    control_only = prompt
    for literal in expected_quotes:
        control_only = control_only.replace(f'\"{literal}\"', "[EXACT DIALOGUE]")
    if _NON_ENGLISH_RE.search(control_only):
        raise _error(
            "Rendered prompt contains non-English text outside exact dialogue.",
            "NON_ENGLISH_CONTROL",
        )

    expected_tags = [item.label for item in prepared.references]
    actual_tags = [
        f"<{match.group(1).title()} {int(match.group(2))}>"
        for match in _REFERENCE_TAG_RE.finditer(prompt)
    ]
    if actual_tags != expected_tags or any(actual_tags.count(tag) != 1 for tag in expected_tags):
        raise _error(
            f"Rendered reference tags {actual_tags!r} do not match inventory {expected_tags!r}.",
            "REFERENCE_TAG_SET_MISMATCH",
        )
    rendered_shots = tuple(int(value) for value in re.findall(r"(?m)^Shot ([1-9][0-9]*) ", prompt))
    if rendered_shots != tuple(shot.number for shot in plan.shots):
        raise _error("Rendered Shot order changed.", "SHOT_ORDER_MISMATCH")
    if prepared.wardrobe_override:
        if any(item.kind == "image" for item in prepared.references) and (
            "the wardrobe must follow the Scene and Shot instructions and replaces "
            "the reference outfit"
            not in prompt
        ):
            raise _error(
                "Rendered image-reference wardrobe override is missing.",
                "WARDROBE_OVERRIDE_MISSING",
            )
        for term in prepared.wardrobe_required_terms:
            if term.casefold() not in prompt.casefold():
                raise _error(
                    f"Rendered prompt dropped wardrobe detail {term}.",
                    "WARDROBE_DETAIL_MISSING",
                )
    actual_facts = set(fact.key for fact in extract_numeric_facts(prompt))
    missing = [fact for fact in prepared.numeric_facts if fact.key not in actual_facts]
    if missing:
        raise _error("Rendered prompt dropped a numeric fact.", "NUMERIC_FACT_MISSING")


def compile_model_result(raw: str, prepared: PreparedPlannerInput) -> CompiledCommunityPrompt:
    plan = parse_plan_json(raw, prepared)
    return CompiledCommunityPrompt(render_prompt(plan, prepared), plan, prepared)


def inspect_model_checkout(
    model_path: str | Path,
    *,
    expected_revision: str = MODEL_REVISION,
) -> ModelCheckoutMetadata:
    """Verify the project-owned provenance marker against the pinned lock.

    Hugging Face's private ``.cache`` layout is deliberately ignored: it may be
    removed after download and is not a stable production provenance contract.
    """

    path = Path(model_path).resolve()
    provenance_path = path / MODEL_PROVENANCE_FILENAME
    detected: str | None = None
    marker_lock_sha: str | None = None
    marker_file_count: int | None = None
    marker_total_bytes: int | None = None
    lock_path: Path | None = None
    verified = False
    try:
        # The lock is trusted only from the repository that owns the fixed
        # models/prompt_planner/Qwen3-4B-Instruct-2507 placement.
        repo_root = path.parents[2]
        expected_path = (repo_root / MODEL_RELATIVE_PATH).resolve()
        if path != expected_path:
            raise ValueError("model path is outside the fixed project placement")
        lock_path = (repo_root / MODEL_LOCK_FILENAME).resolve()
        if lock_path.parent != repo_root or not lock_path.is_file():
            raise ValueError("project prompt planner lock is missing")
        if not provenance_path.is_file():
            raise ValueError("project model provenance marker is missing")

        marker = json.loads(provenance_path.read_text(encoding="utf-8"))
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        if not isinstance(marker, dict) or not isinstance(lock, dict):
            raise ValueError("provenance marker and lock must be objects")
        detected_value = marker.get("revision")
        detected = str(detected_value).lower() if detected_value is not None else None
        marker_lock_sha = str(marker.get("lock_sha256") or "").lower() or None
        marker_file_count_value = marker.get("file_count")
        marker_total_bytes_value = marker.get("total_bytes")
        if isinstance(marker_file_count_value, bool) or not isinstance(
            marker_file_count_value, int
        ):
            raise ValueError("marker file_count is invalid")
        if isinstance(marker_total_bytes_value, bool) or not isinstance(
            marker_total_bytes_value, int
        ):
            raise ValueError("marker total_bytes is invalid")
        marker_file_count = marker_file_count_value
        marker_total_bytes = marker_total_bytes_value

        lock_bytes = lock_path.read_bytes()
        actual_lock_sha = hashlib.sha256(lock_bytes).hexdigest()
        source = lock.get("source")
        verification = lock.get("verification")
        files = lock.get("files")
        if not isinstance(source, dict) or not isinstance(verification, dict):
            raise ValueError("lock source/verification is invalid")
        if not isinstance(files, list) or not all(isinstance(item, dict) for item in files):
            raise ValueError("lock files is invalid")
        file_sizes: list[int] = []
        for item in files:
            size = item.get("size")
            relative = item.get("path")
            if isinstance(size, bool) or not isinstance(size, int) or size < 0:
                raise ValueError("lock file size is invalid")
            if not isinstance(relative, str) or not relative:
                raise ValueError("lock file path is invalid")
            locked_target = (repo_root / Path(relative)).resolve()
            try:
                locked_target.relative_to(path)
            except ValueError as exc:
                raise ValueError("lock file escapes the fixed model directory") from exc
            file_sizes.append(size)

        verified = all(
            (
                marker.get("schema_version") == 1,
                marker.get("model_id") == MODEL_ID,
                detected == expected_revision.lower(),
                marker_lock_sha == actual_lock_sha,
                marker_file_count == MODEL_RUNTIME_FILE_COUNT,
                marker_total_bytes == MODEL_RUNTIME_TOTAL_BYTES,
                lock.get("schema_version") == 1,
                source.get("repo_id") == MODEL_ID,
                str(source.get("revision") or "").lower() == expected_revision.lower(),
                verification.get("total_bytes") == MODEL_RUNTIME_TOTAL_BYTES,
                len(files) == MODEL_RUNTIME_FILE_COUNT,
                sum(file_sizes) == MODEL_RUNTIME_TOTAL_BYTES,
            )
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError, IndexError):
        verified = False
    return ModelCheckoutMetadata(
        model_id=MODEL_ID,
        expected_revision=expected_revision,
        detected_revision=detected,
        verified=verified,
        path=str(path),
        provenance_path=str(provenance_path),
        lock_path=str(lock_path) if lock_path is not None else None,
        lock_sha256=marker_lock_sha,
        file_count=marker_file_count,
        total_bytes=marker_total_bytes,
    )


def require_verified_model_checkout(
    model_path: str | Path,
    *,
    expected_revision: str = MODEL_REVISION,
) -> ModelCheckoutMetadata:
    metadata = inspect_model_checkout(model_path, expected_revision=expected_revision)
    if not metadata.verified:
        raise _error(
            "Prompt planner model checkout is missing or does not match the pinned revision "
            f"{expected_revision}; detected={metadata.detected_revision!r}.",
            "MODEL_REVISION_UNVERIFIED",
        )
    required = (
        "config.json",
        "tokenizer_config.json",
        "model.safetensors.index.json",
    )
    missing = [name for name in required if not (Path(metadata.path) / name).is_file()]
    if missing:
        raise _error(
            f"Prompt planner checkout is incomplete; missing {missing!r}.",
            "MODEL_CHECKOUT_INCOMPLETE",
        )
    return metadata


__all__ = [
    "CompiledCommunityPrompt",
    "CommunityPromptPlan",
    "CommunityPromptPlannerError",
    "DialogueDelivery",
    "DialogueLiteral",
    "MODEL_ID",
    "MODEL_RELATIVE_PATH",
    "MODEL_REVISION",
    "ModelCheckoutMetadata",
    "NonverbalCue",
    "PLAN_SCHEMA_VERSION",
    "PreparedPlannerInput",
    "ReferenceItem",
    "SYSTEM_PROMPT",
    "ShotPlan",
    "build_model_messages",
    "compile_model_result",
    "extract_numeric_facts",
    "extract_shot_numbers",
    "inspect_model_checkout",
    "parse_plan_json",
    "prepare_planner_input",
    "render_prompt",
    "require_verified_model_checkout",
    "validate_plan",
    "validate_rendered_prompt",
]
