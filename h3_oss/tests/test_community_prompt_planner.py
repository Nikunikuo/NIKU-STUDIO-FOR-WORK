from __future__ import annotations

import json
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from webui.community_prompt_planner import (  # noqa: E402
    MODEL_ID,
    MODEL_LOCK_FILENAME,
    MODEL_PROVENANCE_FILENAME,
    MODEL_RELATIVE_PATH,
    MODEL_REVISION,
    MODEL_RUNTIME_FILE_COUNT,
    MODEL_RUNTIME_TOTAL_BYTES,
    PLAN_SCHEMA_VERSION,
    SYSTEM_PROMPT,
    CommunityPromptPlannerError,
    build_model_messages,
    compile_model_result,
    inspect_model_checkout,
    parse_plan_json,
    prepare_planner_input,
    require_verified_model_checkout,
)
from webui.community_prompt_worker import process_request  # noqa: E402


def _plan(
    *,
    action: str = "The athlete lifts the 120 kg barbell 3 times.",
    camera: str = "A locked low-angle camera looks upward without moving.",
    dialogue: bool = True,
    foley: list[str] | None = None,
    music: str = "A restrained instrumental pulse.",
) -> dict[str, object]:
    return {
        "schema_version": PLAN_SCHEMA_VERSION,
        "style": "Polished cinematic realism with stable subject appearance.",
        "scene": "A focused athlete trains in a quiet modern gym.",
        "shots": [
            {
                "number": 1,
                "start_seconds": 0.0,
                "end_seconds": 5.0,
                "framing": "A medium full-body composition from below.",
                "camera": camera,
                "action": action,
            }
        ],
        "ambient": ["Quiet gym room tone and faint ventilation."],
        "foley": foley if foley is not None else ["Barbell plates clink on each lift."],
        "music": music,
        "dialogue_delivery": (
            [
                {
                    "dialogue_id": 1,
                    "shot": 1,
                    "start_seconds": 3.5,
                    "speaker": "the athlete",
                    "delivery": "confident, clear, and energetic",
                }
            ]
            if dialogue
            else []
        ),
    }


def _json(**kwargs: object) -> str:
    return json.dumps(_plan(**kwargs), ensure_ascii=False)


class CommunityPromptPlannerTests(unittest.TestCase):
    def _prepared(self, **kwargs: object):
        return prepare_planner_input(
            "Cut 1\n仰角で120kgのバーベルを3回持ち上げ、セリフ「頑張るぞ！」と言う。",
            duration_seconds=5,
            **kwargs,
        )

    def test_dialogue_is_redacted_before_qwen_and_never_uses_d_tags(self) -> None:
        prepared = self._prepared()
        self.assertEqual([item.text for item in prepared.dialogues], ["頑張るぞ！"])
        self.assertNotIn("頑張るぞ", prepared.redacted_prompt)
        messages = build_model_messages(prepared)
        self.assertNotIn("頑張るぞ", json.dumps(messages, ensure_ascii=False))
        self.assertIn("[DIALOGUE_1]", messages[1]["content"])
        self.assertNotIn("<Picture 1>", messages[1]["content"])

        compiled = compile_model_result(_json(), prepared)
        self.assertEqual(compiled.prompt.count('"頑張るぞ！"'), 1)
        self.assertNotIn("<d>", compiled.prompt.lower())
        without_dialogue = compiled.prompt.replace('"頑張るぞ！"', "")
        self.assertNotRegex(without_dialogue, r"[\u3040-\u30ff\u3400-\u9fff]")

    def test_explicit_unquoted_dialogue_field_is_also_hidden(self) -> None:
        literal = "今日は絶対に成功する！"
        prepared = prepare_planner_input(
            f"Cut 1\n主人公はカメラを見て、{literal}と力強く言う。",
            dialogue_texts=[literal],
            duration_seconds=5,
        )
        self.assertNotIn(literal, prepared.redacted_prompt)
        self.assertNotIn(literal, build_model_messages(prepared)[1]["content"])

    def test_public_example_contract_uses_only_ordinary_quote_for_japanese(self) -> None:
        prepared = prepare_planner_input(
            "Cut 1\n少女が楽しそうに一度だけ『ミニマックス、H3で遊んでみない？』と言う。",
            duration_seconds=5,
        )
        plan = _plan(
            action="The girl smiles toward the camera and gives a small inviting gesture.",
            camera="A locked eye-level medium shot holds steady.",
        )
        plan["dialogue_delivery"] = [
            {
                "dialogue_id": 1,
                "shot": 1,
                "start_seconds": 2.0,
                "speaker": "the girl",
                "delivery": "bright, friendly, and naturally paced",
            }
        ]
        compiled = compile_model_result(json.dumps(plan, ensure_ascii=False), prepared)
        self.assertIn('"ミニマックス、H3で遊んでみない？"', compiled.prompt)
        self.assertEqual(compiled.prompt.count("ミニマックス"), 1)
        self.assertNotIn("<d>", compiled.prompt.casefold())

    def test_reference_tags_are_python_owned_and_match_inventory_exactly(self) -> None:
        prepared = self._prepared(
            reference_inventory=[
                {"kind": "image", "index": 1, "tag": "<Picture 1>"},
                {"kind": "image", "index": 2},
                {"kind": "video", "index": 1},
                {"kind": "audio", "index": 1, "role": "voice"},
            ]
        )
        compiled = compile_model_result(_json(), prepared)
        for tag in ("<Picture 1>", "<Picture 2>", "<Video 1>", "<Audio 1>"):
            self.assertEqual(compiled.prompt.count(tag), 1)
        messages = json.dumps(build_model_messages(prepared), ensure_ascii=False)
        self.assertNotIn("<Picture", messages)
        self.assertNotIn("<Video", messages)
        self.assertNotIn("<Audio", messages)

    def test_source_reference_tags_are_described_to_qwen_then_restored_by_python(self) -> None:
        prepared = prepare_planner_input(
            "Cut 1\n<Picture 1>の女性が、<Video 1>と同じ動きで手を振る。"
            "音の雰囲気は<Audio 1>を参考にする。",
            reference_inventory=[
                {"kind": "image", "index": 1},
                {"kind": "video", "index": 1},
                {"kind": "audio", "index": 1, "role": "ambience"},
            ],
            duration_seconds=5,
        )
        self.assertNotRegex(prepared.redacted_prompt, r"<(?:Picture|Video|Audio)\s")
        self.assertIn("supplied image reference number 1", prepared.redacted_prompt)
        self.assertIn("supplied video reference number 1", prepared.redacted_prompt)
        self.assertIn("supplied audio reference number 1", prepared.redacted_prompt)
        model_input = json.dumps(build_model_messages(prepared), ensure_ascii=False)
        self.assertNotIn("<Picture 1>", model_input)
        self.assertNotIn("<Video 1>", model_input)
        self.assertNotIn("<Audio 1>", model_input)

        compiled = compile_model_result(
            _json(
                dialogue=False,
                action="The woman follows the supplied motion reference and waves once.",
                camera="A locked eye-level medium shot holds steady.",
            ),
            prepared,
        )
        for tag in ("<Picture 1>", "<Video 1>", "<Audio 1>"):
            self.assertEqual(compiled.prompt.count(tag), 1)

    def test_source_reference_tag_must_exist_in_actual_inventory(self) -> None:
        with self.assertRaises(CommunityPromptPlannerError) as ctx:
            prepare_planner_input(
                "Cut 1\n<Picture 2>の人物が歩く。",
                reference_inventory=[{"kind": "image", "index": 1}],
                duration_seconds=5,
            )
        self.assertEqual(ctx.exception.code, "SOURCE_REFERENCE_NOT_IN_INVENTORY")

    def test_latest_workout_job_reference_never_reaches_model_messages(self) -> None:
        source = (
            "日本の高品質なセルアニメ調の塗り、演出。\n"
            "BGMなし。セリフなし。SEのみ。\n---\n"
            "<Picture 1>が、ジムで、トレーニングウェア"
            "（ショートパンツ、ランニング）を着て運動している。\n"
            "Cut1\nウォーキングマシン。斜め構図で仰角煽り視点。鼻息を荒く。\n"
            "Cut2\n両手ダンベルスクワット。ハァハァ息が上がっている。\n"
            "Cut4\nバーベルデッドリフト。重りは120kg。\n"
            "Cut5\nバーベル上げ。重量は200kg。\n"
            "女性キャラクターのセリフ（高いかわいいロリ声）"
            "「うおおおおおおおお！！！」\n"
            "Cut6\n暗転し、甘いプロテインドリンクを飲んで終了。"
        )
        prepared = prepare_planner_input(
            source,
            reference_inventory=[{"kind": "image", "index": 1}],
            duration_seconds=14.375,
        )
        model_input = json.dumps(build_model_messages(prepared), ensure_ascii=False)
        self.assertNotIn("<Picture 1>", model_input)
        self.assertIn("supplied image reference number 1", model_input)
        self.assertEqual(prepared.source_shot_numbers, (1, 2, 4, 5, 6))
        self.assertEqual(
            {(fact.value, fact.unit) for fact in prepared.numeric_facts},
            {("120", "kg"), ("200", "kg")},
        )
        self.assertEqual(
            [dialogue.text for dialogue in prepared.dialogues],
            ["うおおおおおおおお！！！"],
        )
        self.assertEqual(
            prepared.dialogues[0].voice_direction,
            "a high-pitched, cute, youthful anime voice, delivered as a forceful "
            "exertion shout",
        )
        self.assertTrue(prepared.wardrobe_override)
        self.assertEqual(
            prepared.wardrobe_direction,
            "workout wear consisting of a tank top and shorts",
        )
        self.assertEqual(prepared.wardrobe_required_terms, ("tank top", "shorts"))
        self.assertEqual(
            {cue.english_sound for cue in prepared.nonverbal_cues},
            {"strained breathing and panting"},
        )
        self.assertIn("ウォーキングマシン", prepared.redacted_prompt)

    def test_explicit_speech_context_overrides_nonverbal_shape(self) -> None:
        literal = "うおおおおおおおお！！！"
        prepared = prepare_planner_input(
            f"Cut 1\n女性キャラクターのセリフ（高いかわいいロリ声）「{literal}」",
            duration_seconds=5,
        )
        self.assertEqual([item.text for item in prepared.dialogues], [literal])
        self.assertEqual(prepared.nonverbal_cues, ())
        self.assertNotIn(literal, build_model_messages(prepared)[1]["content"])
        self.assertEqual(
            prepared.dialogues[0].voice_direction,
            "a high-pitched, cute, youthful anime voice, delivered as a forceful "
            "exertion shout",
        )
        compiled = compile_model_result(_json(), prepared)
        self.assertIn(
            "with a high-pitched, cute, youthful anime voice, delivered as a "
            "forceful exertion shout",
            compiled.prompt,
        )
        self.assertNotIn("warm, clear, and conversational", compiled.prompt)
        self.assertTrue(
            compiled.metadata()["dialogue_voice_directions"][0][
                "deterministic_override"
            ]
        )

    def test_dialogue_texts_always_wins_for_an_interjection(self) -> None:
        literal = "ハァ、ハァ！"
        prepared = prepare_planner_input(
            "Cut 1\n人物が息を切らしている。",
            dialogue_texts=[literal],
            duration_seconds=5,
        )
        self.assertEqual([item.text for item in prepared.dialogues], [literal])
        self.assertEqual(prepared.nonverbal_cues, ())

    def test_unquoted_sound_match_never_corrupts_walking_machine_word(self) -> None:
        prepared = prepare_planner_input(
            "Cut 1\nウォーキングマシンで歩き、ハァハァゼェゼェ息を切らす。",
            duration_seconds=5,
        )
        self.assertIn("ウォーキングマシン", prepared.redacted_prompt)
        self.assertNotIn("[NONVERBAL", prepared.redacted_prompt.split("で歩き", 1)[0])

    def test_authored_wardrobe_replaces_only_reference_clothing(self) -> None:
        prepared = prepare_planner_input(
            "Cut 1\n<Picture 1>の女性がトレーニングウェア"
            "（ショートパンツ、ランニング）を着て運動する。",
            reference_inventory=[{"kind": "image", "index": 1}],
            duration_seconds=5,
        )
        self.assertTrue(prepared.wardrobe_override)
        missing = _plan(dialogue=False, action="The woman exercises in the gym.")
        with self.assertRaises(CommunityPromptPlannerError) as ctx:
            parse_plan_json(json.dumps(missing), prepared)
        self.assertEqual(ctx.exception.code, "WARDROBE_OVERRIDE_MISSING")

        valid = _plan(
            dialogue=False,
            action="The woman wears a tank top and shorts while exercising in the gym.",
        )
        compiled = compile_model_result(json.dumps(valid), prepared)
        self.assertIn(
            "defines the primary visible subject's identity, face, body shape, and hair",
            compiled.prompt,
        )
        self.assertIn(
            "the wardrobe must follow the Scene and Shot instructions and replaces the "
            "reference outfit",
            compiled.prompt,
        )
        self.assertIn(
            "Wardrobe: The primary visible subject wears workout wear consisting of a "
            "tank top and shorts, replacing the reference outfit.",
            compiled.prompt,
        )
        self.assertNotIn(
            "<Picture 1> defines the primary visible subject identity and appearance",
            compiled.prompt,
        )

    def test_reference_outfit_is_preserved_when_no_new_clothing_is_authored(self) -> None:
        prepared = prepare_planner_input(
            "Cut 1\n<Picture 1>の女性が手を振る。",
            reference_inventory=[{"kind": "image", "index": 1}],
            duration_seconds=5,
        )
        compiled = compile_model_result(
            _json(
                dialogue=False,
                action="The woman waves one hand.",
                camera="A locked eye-level camera holds steady.",
            ),
            prepared,
        )
        self.assertFalse(prepared.wardrobe_override)
        self.assertIn(
            "<Picture 1> defines the primary visible subject identity and appearance.",
            compiled.prompt,
        )

    def test_model_cannot_invent_a_reference_tag(self) -> None:
        prepared = self._prepared(reference_inventory=[])
        plan = _plan(action="The athlete from <Picture 9> lifts the 120 kg barbell 3 times.")
        with self.assertRaisesRegex(CommunityPromptPlannerError, "model-generated reference") as ctx:
            parse_plan_json(json.dumps(plan), prepared)
        self.assertEqual(ctx.exception.code, "INVENTED_REFERENCE_TAG")

    def test_model_result_is_strict_raw_json_with_exact_keys(self) -> None:
        prepared = self._prepared()
        with self.assertRaises(CommunityPromptPlannerError) as fenced:
            parse_plan_json(f"```json\n{_json()}\n```", prepared)
        self.assertEqual(fenced.exception.code, "MODEL_RESULT_NOT_JSON_ONLY")
        plan = _plan()
        plan["commentary"] = "Looks good"
        with self.assertRaises(CommunityPromptPlannerError) as extra:
            parse_plan_json(json.dumps(plan), prepared)
        self.assertEqual(extra.exception.code, "PLAN_SCHEMA_MISMATCH")

    def test_non_english_control_from_model_is_rejected(self) -> None:
        prepared = self._prepared()
        plan = _plan(action="選手が120 kgのバーベルを3 times持ち上げる。")
        with self.assertRaises(CommunityPromptPlannerError) as ctx:
            parse_plan_json(json.dumps(plan, ensure_ascii=False), prepared)
        self.assertEqual(ctx.exception.code, "NON_ENGLISH_CONTROL")

    def test_model_cannot_put_any_quoted_text_in_control_fields(self) -> None:
        prepared = self._prepared()
        plan = _plan(action='The athlete mouths "頑張るぞ" while lifting 120 kg 3 times.')
        with self.assertRaises(CommunityPromptPlannerError) as ctx:
            parse_plan_json(json.dumps(plan, ensure_ascii=False), prepared)
        self.assertIn(ctx.exception.code, {"NON_ENGLISH_CONTROL", "MODEL_QUOTED_TEXT"})

    def test_numeric_values_and_units_are_mandatory(self) -> None:
        prepared = self._prepared()
        plan = _plan(action="The athlete repeatedly lifts the heavy barbell.")
        with self.assertRaises(CommunityPromptPlannerError) as ctx:
            parse_plan_json(json.dumps(plan), prepared)
        self.assertEqual(ctx.exception.code, "NUMERIC_FACT_MISSING")
        self.assertIn("120 kg", str(ctx.exception))
        self.assertIn("3 times", str(ctx.exception))

    def test_source_cut_order_and_non_overlapping_timing_are_mandatory(self) -> None:
        prepared = prepare_planner_input(
            "Cut 1\n静かな部屋。\nCut 2\n人物が歩く。", duration_seconds=5
        )
        plan = _plan(dialogue=False, action="The person waits quietly.")
        with self.assertRaises(CommunityPromptPlannerError) as missing_cut:
            parse_plan_json(json.dumps(plan), prepared)
        self.assertEqual(missing_cut.exception.code, "SHOT_ORDER_MISMATCH")

        plan["shots"] = [
            {
                "number": 1,
                "start_seconds": 0,
                "end_seconds": 3,
                "framing": "A medium shot.",
                "camera": "A locked eye-level camera.",
                "action": "The person waits quietly.",
            },
            {
                "number": 2,
                "start_seconds": 2.5,
                "end_seconds": 5,
                "framing": "A wide shot.",
                "camera": "A slow lateral tracking move.",
                "action": "The person walks across the room.",
            },
        ]
        with self.assertRaises(CommunityPromptPlannerError) as overlap:
            parse_plan_json(json.dumps(plan), prepared)
        self.assertEqual(overlap.exception.code, "OVERLAPPING_SHOTS")

    def test_bracketed_shot_headers_are_preserved(self) -> None:
        prepared = prepare_planner_input(
            "[Shot 1]\n静かな部屋。\n[Shot 2]\n人物が歩く。", duration_seconds=5
        )
        self.assertEqual(prepared.source_shot_numbers, (1, 2))

    def test_camera_glossary_corrects_low_and_high_angle_meaning(self) -> None:
        low = self._prepared()
        bad_low = _plan(camera="A static high-angle camera looks downward.")
        with self.assertRaises(CommunityPromptPlannerError) as ctx:
            parse_plan_json(json.dumps(bad_low), low)
        self.assertEqual(ctx.exception.code, "CAMERA_DIRECTION_CONFLICT")

        high = prepare_planner_input("Cut 1\n俯瞰で部屋全体を映す。", duration_seconds=5)
        high_plan = _plan(
            dialogue=False,
            action="The person stands in the center of the room.",
            camera="A static high-angle camera looks downward over the full room.",
        )
        high_plan["shots"][0]["framing"] = "A wide overhead composition from above."
        parsed = parse_plan_json(json.dumps(high_plan), high)
        self.assertEqual(parsed.shots[0].number, 1)
        self.assertIn("low-angle upward view", SYSTEM_PROMPT)
        self.assertIn("high-angle downward view", SYSTEM_PROMPT)

    def test_opposing_camera_geometry_is_rejected_inside_each_shot(self) -> None:
        neutral = prepare_planner_input("Cut 1\n人物が立っている。", duration_seconds=5)
        conflicts = (
            (
                "A low-angle upward composition from below.",
                "The camera is positioned slightly above the subject.",
            ),
            (
                "A high-angle downward composition from above.",
                "The camera is positioned slightly below the subject and looks upward.",
            ),
        )
        for framing, camera in conflicts:
            with self.subTest(framing=framing, camera=camera):
                plan = _plan(dialogue=False, camera=camera)
                plan["shots"][0]["framing"] = framing
                with self.assertRaises(CommunityPromptPlannerError) as ctx:
                    parse_plan_json(json.dumps(plan), neutral)
                self.assertEqual(ctx.exception.code, "CAMERA_DIRECTION_CONFLICT")

    def test_numbered_source_camera_cue_cannot_be_satisfied_by_another_shot(self) -> None:
        prepared = prepare_planner_input(
            "Cut 1\n仰角煽り視点で人物が歩く。\nCut 2\n人物が立ち止まる。",
            duration_seconds=5,
        )
        self.assertIn(
            "Shot 1 = low-angle upward view",
            build_model_messages(prepared)[1]["content"],
        )
        plan = _plan(dialogue=False, action="The person waits quietly.")
        plan["shots"] = [
            {
                "number": 1,
                "start_seconds": 0,
                "end_seconds": 2.5,
                "framing": "A neutral eye-level medium composition.",
                "camera": "A locked eye-level camera holds steady.",
                "action": "The person walks forward.",
            },
            {
                "number": 2,
                "start_seconds": 2.5,
                "end_seconds": 5,
                "framing": "A low-angle full-body composition from below.",
                "camera": "A low-angle camera looks upward at the person.",
                "action": "The person stops and waits quietly.",
            },
        ]
        with self.assertRaises(CommunityPromptPlannerError) as ctx:
            parse_plan_json(json.dumps(plan), prepared)
        self.assertEqual(ctx.exception.code, "CAMERA_GLOSSARY_MISMATCH")
        self.assertIn("Cut/Shot 1", str(ctx.exception))

    def test_latest_generated_camera_controls_are_rejected(self) -> None:
        latest_shot_1 = prepare_planner_input(
            "Cut 1\nウォーキングマシン。斜め構図で仰角煽り視点。",
            duration_seconds=5,
        )
        plan = _plan(
            dialogue=False,
            action=(
                "The visible subject walks on a treadmill with intense focus, "
                "breathing heavily"
            ),
            camera=(
                "Camera positioned slightly above and to the side, tracking the subject "
                "as she walks on a treadmill with a dynamic upward tilt"
            ),
        )
        plan["shots"][0]["framing"] = (
            "Diagonal composition with a low-angle upward view, emphasizing the "
            "character's motion and physical effort"
        )
        with self.assertRaises(CommunityPromptPlannerError) as shot_1_error:
            parse_plan_json(json.dumps(plan), latest_shot_1)
        self.assertEqual(shot_1_error.exception.code, "CAMERA_DIRECTION_CONFLICT")

        latest_shot_6 = prepare_planner_input(
            "Cut 6\n暗転し、甘いプロテインドリンクを飲んで終了。",
            duration_seconds=5,
        )
        plan["shots"][0].update(
            {
                "number": 6,
                "framing": "Low-angle shot from above, soft focus on the character's hands and drink",
                "camera": (
                    "Slow pan down and in on the character as she drinks from a bottle, "
                    "ending in a still frame"
                ),
                "action": (
                    "The scene darkens gradually, and the character drinks a sweet protein "
                    "drink slowly and deliberately"
                ),
            }
        )
        with self.assertRaises(CommunityPromptPlannerError) as shot_6_error:
            parse_plan_json(json.dumps(plan), latest_shot_6)
        self.assertEqual(shot_6_error.exception.code, "CAMERA_DIRECTION_CONFLICT")

    def test_panting_and_exertion_roar_are_foley_not_dialogue(self) -> None:
        prepared = prepare_planner_input(
            "Cut 1\n選手が『ハァ、ハァ』と息を切らし、うおおお！と力を込める。",
            duration_seconds=5,
        )
        self.assertEqual(prepared.dialogues, ())
        self.assertEqual(len(prepared.nonverbal_cues), 2)
        plan = _plan(
            dialogue=False,
            action="The athlete strains through one final controlled lift.",
            foley=[
                "strained breathing and panting",
                "a nonverbal exertion roar",
                "Metal plates clink under load.",
            ],
        )
        compiled = compile_model_result(json.dumps(plan), prepared)
        self.assertNotIn('"', compiled.prompt)
        self.assertIn("Foley: strained breathing and panting", compiled.prompt)
        self.assertNotIn("ハァ", compiled.prompt)
        self.assertNotIn("うお", compiled.prompt)

    def test_missing_required_nonverbal_foley_is_rejected(self) -> None:
        prepared = prepare_planner_input(
            "Cut 1\n人物が『うおおお！』と力を込める。", duration_seconds=5
        )
        with self.assertRaises(CommunityPromptPlannerError) as ctx:
            parse_plan_json(json.dumps(_plan(dialogue=False)), prepared)
        self.assertEqual(ctx.exception.code, "NONVERBAL_FOLEY_MISSING")

    def test_model_cannot_invent_speech_when_no_dialogue_was_supplied(self) -> None:
        prepared = prepare_planner_input("Cut 1\n人物が静かに歩く。", duration_seconds=5)
        plan = _plan(
            dialogue=False,
            action="The person walks and narrates the full scene.",
        )
        with self.assertRaises(CommunityPromptPlannerError) as ctx:
            parse_plan_json(json.dumps(plan), prepared)
        self.assertEqual(ctx.exception.code, "INVENTED_SPEECH_CONTROL")

    def test_style_sound_audio_and_music_controls_are_separate_and_deterministic(self) -> None:
        prepared = self._prepared(
            style_direction="暖かな夏の2Dアニメ調。",
            soundscape="遠くの蝉と穏やかな風。",
            audio_preset="effects",
            music_policy="none",
        )
        model_input = build_model_messages(prepared)[1]["content"]
        self.assertIn("暖かな夏の2Dアニメ調", model_input)
        self.assertIn("遠くの蝉と穏やかな風", model_input)
        self.assertNotIn("頑張るぞ", model_input)
        compiled = compile_model_result(_json(), prepared)
        self.assertIn("Mix: Prioritize precisely synchronized physical foley", compiled.prompt)
        self.assertIn("Music: N/A", compiled.prompt)
        self.assertNotIn("A restrained instrumental pulse", compiled.prompt)

    def test_auxiliary_control_cannot_smuggle_dialogue_or_tags(self) -> None:
        with self.assertRaises(CommunityPromptPlannerError) as quote:
            self._prepared(soundscape="少女が『秘密だよ』と言う。")
        self.assertEqual(quote.exception.code, "DIALOGUE_IN_AUXILIARY_CONTROL")
        with self.assertRaises(CommunityPromptPlannerError) as tag:
            self._prepared(style_direction="Use <Picture 9>.")
        self.assertEqual(tag.exception.code, "AUXILIARY_CONTROL_TAG_FORBIDDEN")

    def test_visible_text_is_not_automatically_treated_as_dialogue(self) -> None:
        prepared = prepare_planner_input(
            "Cut 1\n店の看板に「営業中」と表示される。", duration_seconds=5
        )
        self.assertEqual(prepared.dialogues, ())

    def test_model_checkout_revision_metadata_is_verified(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            root = repo / MODEL_RELATIVE_PATH
            root.mkdir(parents=True)
            for name in (
                "config.json",
                "tokenizer_config.json",
                "model.safetensors.index.json",
            ):
                (root / name).write_text("{}", encoding="utf-8")
            files = [
                {
                    "path": (MODEL_RELATIVE_PATH / f"runtime-{index}.bin").as_posix(),
                    "size": 0,
                    "sha256": "0" * 64,
                }
                for index in range(MODEL_RUNTIME_FILE_COUNT - 1)
            ]
            files.append(
                {
                    "path": (MODEL_RELATIVE_PATH / "runtime-final.bin").as_posix(),
                    "size": MODEL_RUNTIME_TOTAL_BYTES,
                    "sha256": "0" * 64,
                }
            )
            lock = {
                "schema_version": 1,
                "source": {"repo_id": MODEL_ID, "revision": MODEL_REVISION},
                "verification": {"total_bytes": MODEL_RUNTIME_TOTAL_BYTES},
                "files": files,
            }
            lock_path = repo / MODEL_LOCK_FILENAME
            lock_path.write_text(
                json.dumps(lock, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            lock_sha = hashlib.sha256(lock_path.read_bytes()).hexdigest()
            (root / MODEL_PROVENANCE_FILENAME).write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "model_id": MODEL_ID,
                        "revision": MODEL_REVISION,
                        "lock_sha256": lock_sha,
                        "file_count": MODEL_RUNTIME_FILE_COUNT,
                        "total_bytes": MODEL_RUNTIME_TOTAL_BYTES,
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            inspected = inspect_model_checkout(root)
            self.assertTrue(inspected.verified)
            self.assertEqual(require_verified_model_checkout(root).detected_revision, MODEL_REVISION)
            self.assertEqual(inspected.lock_sha256, lock_sha)
            self.assertFalse((root / ".cache").exists())

            marker = json.loads(
                (root / MODEL_PROVENANCE_FILENAME).read_text(encoding="utf-8")
            )
            marker["lock_sha256"] = "f" * 64
            (root / MODEL_PROVENANCE_FILENAME).write_text(
                json.dumps(marker), encoding="utf-8"
            )
            self.assertFalse(inspect_model_checkout(root).verified)

    def test_unverified_checkout_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(CommunityPromptPlannerError) as ctx:
                require_verified_model_checkout(temporary)
        self.assertEqual(ctx.exception.code, "MODEL_REVISION_UNVERIFIED")

    def test_worker_contract_with_injected_model_and_retry(self) -> None:
        class FakePlanner:
            def __init__(self) -> None:
                self.calls = 0
                self.load_metadata = {"total_load_ms": 12.5}
                self.last_generation_metadata = None

            def generate(self, messages):
                self.calls += 1
                self.last_generation_metadata = {
                    "output_tokens_including_eos": 101 + self.calls,
                    "content_tokens_before_eos": 100 + self.calls,
                    "eos_reached": True,
                    "generation_ms": 250.0,
                    "max_new_tokens": 1280,
                }
                if self.calls == 1:
                    return "not JSON"
                self.last_messages = messages
                return _json()

        fake = FakePlanner()
        response = process_request(
            {
                "prompt": "Cut 1\n仰角で120kgを3回持ち上げ、セリフ「頑張るぞ！」と言う。",
                "references": [{"kind": "image", "index": 1}],
                "duration_seconds": 5,
                "audio_preset": "effects",
                "music_policy": "none",
                "max_attempts": 2,
            },
            planner=fake,
        )
        self.assertTrue(response["ok"])
        self.assertEqual(response["planner_metadata"]["attempts"], 2)
        self.assertEqual(response["planner_metadata"]["max_new_tokens"], 1280)
        self.assertEqual(
            response["planner_metadata"]["runtime_timing"][
                "measured_generation_ms_total"
            ],
            500.0,
        )
        self.assertEqual(
            response["planner_metadata"]["runtime_timing"]["generation_attempts"][-1][
                "content_tokens_before_eos"
            ],
            102,
        )
        self.assertEqual(response["compiled_prompt"].count('"頑張るぞ！"'), 1)
        self.assertIn("<Picture 1>", response["compiled_prompt"])
        self.assertEqual(response["plan"]["schema_version"], PLAN_SCHEMA_VERSION)
        self.assertIn("REJECTED", fake.last_messages[-1]["content"])

    def test_worker_rejects_an_unsafe_generation_cap(self) -> None:
        with self.assertRaises(CommunityPromptPlannerError) as ctx:
            process_request(
                {
                    "prompt": "Cut 1\n人物が歩く。",
                    "duration_seconds": 5,
                    "max_new_tokens": 256,
                },
                planner=lambda _: _json(dialogue=False),
            )
        self.assertEqual(ctx.exception.code, "INVALID_WORKER_REQUEST")


if __name__ == "__main__":
    unittest.main()
