"""Immutable demo manifests derived from the three Story Studio UI projects.

The data in this module mirrors the ``PROJECTS``, ``ASSETS_BY_PROJECT``,
``SHOTS_BY_PROJECT`` and ``PROMPT_BY_PROJECT`` constants in ``app/page.tsx``.
It deliberately performs no I/O and never opens a Project Core repository.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .h3_story_core.models import ApprovalSeed, EntitySeed, ProjectManifest


class _FrozenDict(dict[str, Any]):
    """A JSON-serializable dict that rejects mutation after construction."""

    @staticmethod
    def _immutable(*_args: object, **_kwargs: object) -> None:
        raise TypeError("demo seed mappings are immutable")

    __setitem__ = _immutable
    __delitem__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable
    setdefault = _immutable
    update = _immutable
    __ior__ = _immutable


def _freeze(value: Any) -> Any:
    """Recursively freeze JSON-shaped values without losing JSON compatibility."""

    if isinstance(value, Mapping):
        return _FrozenDict({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


@dataclass(frozen=True, slots=True)
class _DemoProject:
    project_id: str
    code: str
    title: str
    subtitle: str
    revision: int
    phase: str
    progress: int
    accent: str
    updated: str
    episode_title: str
    scene_title: str
    logline: str
    summary: str


@dataclass(frozen=True, slots=True)
class _DemoAsset:
    asset_id: str
    kind: str
    name: str
    role: str
    caption: str
    prompt_caption: str
    locked: bool
    tone: str


@dataclass(frozen=True, slots=True)
class _DemoShot:
    shot_id: str
    label: str
    title: str
    status: str


_PROJECTS = (
    _DemoProject(
        project_id="prj_01K1ZJ7M5X9JY4Q6C8P0V3TN2A",
        code="YOAKE-01",
        title="夜明けの鋼",
        subtitle="全8話・縦型ミステリー",
        revision=184,
        phase="動画制作",
        progress=72,
        accent="#f4a75e",
        updated="2分前",
        episode_title="停止した塔",
        scene_title="旧軌道工場",
        logline="帰る場所を失った二人が、封印された記録から自分たちの過去を見つける。",
        summary="夜明け前の旧軌道工場。整備士の凛と帰還兵ユウは、停止していた記録端末に自分たちの名前が残されていることを知る。",
    ),
    _DemoProject(
        project_id="prj_01K0XT4Y9G2P7Q8M3V6D1HRK5C",
        code="SAZANAMI-02",
        title="さざなみ食堂",
        subtitle="全6話・日常ドラマ",
        revision=61,
        phase="世界観設定",
        progress=38,
        accent="#65cdb9",
        updated="昨日",
        episode_title="最後の一杯",
        scene_title="閉店後の食堂",
        logline="閉店を決めた店主と常連客が、最後の夜に言えなかった本音を料理へ託す。",
        summary="海辺の小さな食堂。最後の営業を終えた澪は、帰らずに残った常連の奏へ、十年前と同じ出汁巻きを差し出す。",
    ),
    _DemoProject(
        project_id="prj_01JZQ9B2S6C4F8V1N5M7X3AD0E",
        code="ORBIT-03",
        title="軌道上の手紙",
        subtitle="短編・SFロマンス",
        revision=24,
        phase="企画確認",
        progress=18,
        accent="#879df5",
        updated="4日前",
        episode_title="未送信の声",
        scene_title="静止軌道ステーション",
        logline="地球へ帰れない通信士が、届かないはずの手紙に返事を見つける。",
        summary="無人に近い軌道ステーション。通信士レイは古い中継器から、五年前に失われた乗員の返信を受信する。",
    ),
)


_ASSETS_BY_PROJECT: Mapping[str, tuple[_DemoAsset, ...]] = _FrozenDict(
    {
        _PROJECTS[0].project_id: (
            _DemoAsset(
                "ch_001",
                "character",
                "凛",
                "主人公 / 整備士",
                "黒いショートヘア。右頬の細い傷と、使い込まれた作業服。",
                "young Japanese mechanic, short black hair, thin scar on right cheek, worn charcoal workwear",
                True,
                "ember",
            ),
            _DemoAsset(
                "ch_002",
                "character",
                "ユウ",
                "帰還兵 / 相棒",
                "白髪まじりの青年。左腕は旧式の金属義手。",
                "lean young veteran, ash-gray hair, old brass mechanical left arm, calm gaze",
                True,
                "steel",
            ),
            _DemoAsset(
                "env_004",
                "environment",
                "旧軌道工場",
                "Scene 03 の舞台",
                "崩れた高架と巨大な整備塔。夜明け前の青い逆光。",
                "abandoned orbital maintenance yard, collapsed gantries, blue pre-dawn backlight, drifting dust",
                False,
                "night",
            ),
            _DemoAsset(
                "prop_006",
                "prop",
                "記録端末",
                "物語の鍵",
                "掌サイズの古い端末。角が欠け、橙色の警告灯が明滅する。",
                "weathered palm-sized data terminal, chipped corner, blinking amber warning light",
                False,
                "signal",
            ),
        ),
        _PROJECTS[1].project_id: (
            _DemoAsset(
                "ch_101",
                "character",
                "澪",
                "食堂店主",
                "肩までの黒髪。生成りのシャツと深緑の前掛け。穏やかな目元。",
                "Japanese woman in her thirties, shoulder-length black hair, ivory shirt, deep green apron",
                True,
                "ember",
            ),
            _DemoAsset(
                "ch_102",
                "character",
                "奏",
                "最後の常連客",
                "短い茶髪の青年。紺色のコート。左手に古い腕時計。",
                "young Japanese man, short brown hair, navy coat, old wristwatch on left hand",
                True,
                "steel",
            ),
            _DemoAsset(
                "env_104",
                "environment",
                "さざなみ食堂",
                "Episode 01 の舞台",
                "海辺の小さな食堂。閉店後の暖色照明と、雨に濡れた窓。",
                "small seaside diner after closing, warm pendant lights, rain-streaked windows",
                False,
                "night",
            ),
            _DemoAsset(
                "prop_106",
                "prop",
                "欠けた小皿",
                "二人の記憶",
                "縁が少し欠けた白磁の小皿。青い波模様。",
                "small chipped white porcelain plate with a hand-painted blue wave pattern",
                False,
                "signal",
            ),
        ),
        _PROJECTS[2].project_id: (
            _DemoAsset(
                "ch_201",
                "character",
                "レイ",
                "軌道通信士",
                "銀灰色の短髪。青い保守制服。左耳に通信インプラント。",
                "orbital communications officer, short silver-gray hair, blue maintenance uniform, left ear implant",
                True,
                "steel",
            ),
            _DemoAsset(
                "ch_202",
                "character",
                "ミナの記録",
                "失われた乗員",
                "琥珀色のホログラムとして残る女性乗員の記録。",
                "female astronaut preserved as a translucent amber hologram, calm expression",
                True,
                "ember",
            ),
            _DemoAsset(
                "env_204",
                "environment",
                "通信管制室",
                "短編の舞台",
                "地球光が差す狭い管制室。古い中継器と浮遊する埃。",
                "narrow orbital communications room, Earthlight through viewport, old relay consoles, floating dust",
                False,
                "night",
            ),
            _DemoAsset(
                "prop_206",
                "prop",
                "未送信パケット",
                "物語の鍵",
                "赤い未送信表示を残す古い光学メモリ。",
                "old optical memory module with a small red unsent indicator",
                False,
                "signal",
            ),
        ),
    }
)


_SHOTS_BY_PROJECT: Mapping[str, tuple[_DemoShot, ...]] = _FrozenDict(
    {
        _PROJECTS[0].project_id: (
            _DemoShot("sh_009", "Shot 09", "工場へ進入", "selected"),
            _DemoShot("sh_010", "Shot 10", "足音に気づく", "selected"),
            _DemoShot("sh_011", "Shot 11", "端末を発見", "selected"),
            _DemoShot("sh_012", "Shot 12", "警告灯が点く", "active"),
            _DemoShot("sh_013", "Shot 13", "記録を再生", "draft"),
            _DemoShot("sh_014", "Shot 14", "塔が起動する", "draft"),
        ),
        _PROJECTS[1].project_id: (
            _DemoShot("sh_109", "Shot 09", "看板を消す", "selected"),
            _DemoShot("sh_110", "Shot 10", "奏が残る", "selected"),
            _DemoShot("sh_111", "Shot 11", "出汁を温める", "selected"),
            _DemoShot("sh_112", "Shot 12", "小皿を差し出す", "active"),
            _DemoShot("sh_113", "Shot 13", "十年前を話す", "draft"),
            _DemoShot("sh_114", "Shot 14", "雨が止む", "draft"),
        ),
        _PROJECTS[2].project_id: (
            _DemoShot("sh_209", "Shot 09", "中継器を起動", "selected"),
            _DemoShot("sh_210", "Shot 10", "ノイズを分離", "selected"),
            _DemoShot("sh_211", "Shot 11", "声を認識する", "selected"),
            _DemoShot("sh_212", "Shot 12", "返信が届く", "active"),
            _DemoShot("sh_213", "Shot 13", "地球を見る", "draft"),
            _DemoShot("sh_214", "Shot 14", "手紙を送信", "draft"),
        ),
    }
)


_PROMPT_BY_PROJECT: Mapping[str, str] = _FrozenDict(
    {
        _PROJECTS[0].project_id: "@凛 と @ユウ が旧軌道工場の制御卓を覗き込む。凛が埃を払うと、@記録端末 の橙色の警告灯が点灯する。二人は言葉を止め、互いの顔を見る。夜明け前の青い逆光。ゆっくりしたプッシュイン。",
        _PROJECTS[1].project_id: "閉店後の @さざなみ食堂。@澪 が湯気の立つ出汁巻きを @欠けた小皿 に載せ、@奏 の前へ静かに差し出す。窓を雨粒が流れ、二人は言葉にせず微笑む。暖色の灯り、ゆっくりした横移動。",
        _PROJECTS[2].project_id: "@レイ が暗い通信管制室で古い中継器を操作する。@未送信パケット が赤く点滅し、@ミナの記録 の琥珀色ホログラムが空中に現れる。窓の外に青い地球。静かなプッシュイン。",
    }
)


def _numbered_id(prefix: str, project_index: int, number: int) -> str:
    return f"{prefix}_{project_index * 100 + number:03d}"


def _approval_id(project: _DemoProject, target_type: str, target_id: str) -> str:
    return f"apr_{project.code.lower()}_{target_type}_{target_id}"


def _manifest(project: _DemoProject, project_index: int) -> ProjectManifest:
    episode_id = _numbered_id("ep", project_index, 1)
    scene_id = _numbered_id("sc", project_index, 3)
    assets = _ASSETS_BY_PROJECT[project.project_id]
    shots = _SHOTS_BY_PROJECT[project.project_id]
    active_shot = next(shot for shot in shots if shot.status == "active")
    prompt_id = _numbered_id("prompt", project_index, 12)

    entities: list[EntitySeed] = [
        EntitySeed(
            entity_type="episode",
            entity_id=episode_id,
            payload=_freeze(
                {
                    "code": "EP01",
                    "number": 1,
                    "title": project.episode_title,
                    "subtitle": project.subtitle,
                    "logline": project.logline,
                    "summary": project.summary,
                    "revision_hint": project.revision,
                }
            ),
        ),
        EntitySeed(
            entity_type="scene",
            entity_id=scene_id,
            payload=_freeze(
                {
                    "code": "SC03",
                    "number": 3,
                    "episode_id": episode_id,
                    "title": project.scene_title,
                    "summary": project.summary,
                    "revision_hint": project.revision,
                }
            ),
        ),
    ]

    approvals: list[ApprovalSeed] = []
    for asset in assets:
        entities.append(
            EntitySeed(
                entity_type="asset",
                entity_id=asset.asset_id,
                payload=_freeze(
                    {
                        "kind": asset.kind,
                        "name": asset.name,
                        "role": asset.role,
                        "caption": asset.caption,
                        "prompt_caption": asset.prompt_caption,
                        "locked": asset.locked,
                        "tone": asset.tone,
                        "revision_hint": project.revision,
                    }
                ),
            )
        )
        approvals.append(
            ApprovalSeed(
                approval_id=_approval_id(project, "asset", asset.asset_id),
                target_type="asset",
                target_id=asset.asset_id,
                status="approved" if asset.locked else "pending",
                payload=_freeze(
                    {
                        "source_field": "locked",
                        "source_value": asset.locked,
                        "revision_hint": project.revision,
                    }
                ),
            )
        )

    for order, shot in enumerate(shots, start=9):
        entities.append(
            EntitySeed(
                entity_type="shot",
                entity_id=shot.shot_id,
                payload=_freeze(
                    {
                        "code": f"SH{order:03d}",
                        "label": shot.label,
                        "title": shot.title,
                        "status": shot.status,
                        "order": order,
                        "episode_id": episode_id,
                        "scene_id": scene_id,
                        "revision_hint": project.revision,
                    }
                ),
            )
        )
        approvals.append(
            ApprovalSeed(
                approval_id=_approval_id(project, "shot", shot.shot_id),
                target_type="shot",
                target_id=shot.shot_id,
                status="approved" if shot.status == "selected" else "pending",
                payload=_freeze(
                    {
                        "source_field": "status",
                        "source_value": shot.status,
                        "revision_hint": project.revision,
                    }
                ),
            )
        )

    entities.append(
        EntitySeed(
            entity_type="prompt",
            entity_id=prompt_id,
            payload=_freeze(
                {
                    "code": f"PROMPT-{active_shot.shot_id.replace('_', '').upper()}",
                    "prompt": _PROMPT_BY_PROJECT[project.project_id],
                    "mode": "omni",
                    "episode_id": episode_id,
                    "scene_id": scene_id,
                    "shot_id": active_shot.shot_id,
                    "reference_asset_ids": tuple(asset.asset_id for asset in assets),
                    "revision_hint": project.revision,
                }
            ),
        )
    )
    approvals.append(
        ApprovalSeed(
            approval_id=_approval_id(project, "prompt", prompt_id),
            target_type="prompt",
            target_id=prompt_id,
            status="pending",
            payload=_freeze(
                {
                    "source_field": "production_state",
                    "source_value": "unreviewed",
                    "revision_hint": project.revision,
                }
            ),
        )
    )

    return ProjectManifest(
        project_id=project.project_id,
        name=project.title,
        project_root=f"projects/{project.code.lower()}",
        focus=_freeze(
            {
                "episode_id": episode_id,
                "scene_id": scene_id,
                "shot_id": active_shot.shot_id,
                "prompt_id": prompt_id,
            }
        ),
        metadata=_freeze(
            {
                "code": project.code,
                "revision_hint": project.revision,
                "phase": project.phase,
                "progress": project.progress,
                "subtitle": project.subtitle,
                "accent": project.accent,
                "updated": project.updated,
                "source": "app/page.tsx demo",
            }
        ),
        entities=tuple(entities),
        approvals=tuple(approvals),
    )


def demo_project_manifests() -> tuple[ProjectManifest, ...]:
    """Return deterministic, immutable manifests for all three UI demo projects.

    The function is intentionally pure: every call constructs a fresh tuple of
    frozen data contracts and performs no repository or filesystem operations.
    """

    return tuple(_manifest(project, index) for index, project in enumerate(_PROJECTS))


__all__ = ["demo_project_manifests"]
