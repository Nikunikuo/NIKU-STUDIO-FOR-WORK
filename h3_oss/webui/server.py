from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
import webbrowser
from collections import Counter
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

import psutil
import av
import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from .comfy_workflow import (
    AUDIO_VAE_MODEL as COMFY_AUDIO_VAE_FILENAME,
    DEFAULT_EASYCACHE_PRESET,
    EASYCACHE_PRESETS,
    FL2VA_MODEL as COMFY_FL2VA_FILENAME,
    MIN_EASYCACHE_STEPS,
    REF2VA_MODEL as COMFY_REF2VA_FILENAME,
    TEXT_ENCODER_MODEL as COMFY_TEXT_ENCODER_FILENAME,
    VIDEO_VAE_MODEL as COMFY_VIDEO_VAE_FILENAME,
)
from .job_manager import (
    JobManager,
    PUBLIC_ENGINE_DIAGNOSTIC_FIELDS,
    TERMINAL_STATES,
    _validate_reference_tags,
    _validate_native_clean_prompt,
    community_planner_status,
    utc_now,
)
from .resolutions import resolution_catalog


ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = Path(__file__).resolve().parent / "static"
COMFY_MODEL_ROOT = ROOT / "models" / "comfy"
COMFY_MODEL_FILES = {
    "fl2va": COMFY_MODEL_ROOT / "diffusion_models" / COMFY_FL2VA_FILENAME,
    "ref2va": COMFY_MODEL_ROOT / "diffusion_models" / COMFY_REF2VA_FILENAME,
    "text_encoder": COMFY_MODEL_ROOT / "text_encoders" / COMFY_TEXT_ENCODER_FILENAME,
    "video_vae": COMFY_MODEL_ROOT / "vae" / COMFY_VIDEO_VAE_FILENAME,
    "audio_vae": COMFY_MODEL_ROOT / "vae" / COMFY_AUDIO_VAE_FILENAME,
}
manager = JobManager(ROOT)

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm"}
AUDIO_EXTENSIONS = {".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg"}
ALLOWED_FRAMES = {124, 243, 345}
MAX_UPLOAD_BYTES = 2 * 1024**3
MAX_REQUEST_BYTES = 8 * 1024**3
LOCAL_MUTATION_HEADER = "X-H3-Studio-Request"
LOCAL_MUTATION_VALUE = "1"
# ``community`` sends the user's authoring fields to the local Qwen planner.
# ``raw_en`` is the byte-for-byte escape hatch for a proven native H3 prompt.
PROMPT_PROCESSING_MODES = ("community", "raw_en")
STANDALONE_AUDIO_POLICIES = ("dialogue_priority", "full_content")
_LOCAL_HOST = re.compile(r"^127\.0\.0\.1(?::(?P<port>[1-9][0-9]{0,4}))?$")
_ADVANCED_AUDIO_CONTROL_RE = re.compile(
    r"</?d(?:\s[^>]*)?>|"
    r"<(?:Picture|Video|Audio)\s+[1-9][0-9]*>|"
    r"<\|[^>\r\n]+\|>|"
    r"^(?:subject_definitions|summary|retention_analysis|detailed_description|"
    r"integrated_multimodal_description|overall_soundscape|non_diegetic_music)\s*:",
    re.IGNORECASE | re.MULTILINE,
)
_LIKELY_EXPLICIT_DIALOGUE_RE = re.compile(
    r"「[^」\r\n]+」|『[^』\r\n]+』|“[^”\r\n]+”|"
    r'"[^"\r\n]+"'
)
_FORBIDDEN_DIALOGUE_CONTROL_RE = re.compile(
    r"</?d(?:\s[^>]*)?>|<\|[^>\r\n]+\|>|"
    r"^(?:subject_definitions|summary|retention_analysis|detailed_description|"
    r"integrated_multimodal_description|overall_soundscape|non_diegetic_music)\s*:",
    re.IGNORECASE | re.MULTILINE,
)
ATTENTION_BACKEND = os.environ.get("H3_ATTENTION_BACKEND", "sage").strip().lower()
if ATTENTION_BACKEND not in {"sage", "pytorch"}:
    raise RuntimeError("H3_ATTENTION_BACKEND must be 'sage' or 'pytorch'.")

STYLE_PROMPTS = {
    "natural": "",
    "cinematic": "Cinematic composition, expressive camera movement, natural temporal motion, detailed lighting.",
    "photoreal": "Photorealistic, physically plausible lighting, natural materials, coherent fine details.",
    "anime": "Polished 2D-animated film style, clean linework, coherent character design, rich color design.",
    "illustration": "Painterly editorial illustration, tactile textures, art-directed color palette, graceful motion.",
    "product": "Premium commercial product film, controlled studio lighting, precise materials, elegant camera work.",
    "moody": "Atmospheric and moody, dramatic shadows, restrained colors, cinematic environmental sound.",
}

AUDIO_PRESET_PROMPTS = {
    "auto": "",
    # Ordinary quoted dialogue is carried by the authored prompt/planner input.
    # Repeating a global speech instruction would add a second conditioning cue.
    "dialogue": "",
    "ambience": (
        "Prioritize a detailed spatial soundscape with convincing room tone and environmental ambience. "
        "Keep every sound physically consistent with the visible space and camera distance."
    ),
    "effects": (
        "Prioritize precisely synchronized foley and sound effects with clear transients and natural perspective. "
        "Every important visible action should have an appropriate sound."
    ),
    "music": (
        "Use an instrumental music-led soundtrack with physically matched diegetic sound. "
        "Match the musical pacing, mood, and transitions to the visual edit."
    ),
    "quiet": (
        "Use a restrained, quiet soundscape with subtle room tone and only necessary "
        "diegetic sounds."
    ),
}

MUSIC_POLICY_PROMPTS = {
    "auto": "",
    "none": "N/A",
    "subtle": "A subtle instrumental score supports the physical ambience and important sound effects.",
    "prominent": "A prominent instrumental score follows the rhythm and emotional arc of the edit.",
}


@asynccontextmanager
async def lifespan(_: FastAPI):
    manager.start()
    yield
    manager.stop()


app = FastAPI(title="NIKU H STUDIO", docs_url=None, redoc_url=None, lifespan=lifespan)
app.mount("/assets", StaticFiles(directory=STATIC_DIR), name="assets")


def _valid_local_host(host: str) -> bool:
    match = _LOCAL_HOST.fullmatch(host.strip().lower())
    if match is None:
        return False
    port = match.group("port")
    return port is None or int(port) <= 65535


@app.middleware("http")
async def protect_local_browser_boundary(request: Request, call_next):  # noqa: ANN001
    """Block DNS rebinding and cross-site browser requests to the local GPU app."""

    host = request.headers.get("host", "")
    if not _valid_local_host(host):
        return PlainTextResponse("NIKU H STUDIO only accepts numeric loopback Host headers.", status_code=421)

    fetch_site = request.headers.get("sec-fetch-site", "").lower()
    if fetch_site not in {"", "none", "same-origin"}:
        return PlainTextResponse("Cross-site requests are not allowed.", status_code=403)

    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        if request.headers.get(LOCAL_MUTATION_HEADER) != LOCAL_MUTATION_VALUE:
            return PlainTextResponse("Missing NIKU H STUDIO local request header.", status_code=403)
        origin = request.headers.get("origin")
        if origin is not None and origin.rstrip("/").lower() != f"http://{host}".lower():
            return PlainTextResponse("Cross-origin mutations are not allowed.", status_code=403)
        if request.url.path == "/api/jobs":
            declared_length = request.headers.get("content-length")
            if declared_length is not None:
                try:
                    declared_bytes = int(declared_length)
                except ValueError:
                    return PlainTextResponse("Invalid Content-Length.", status_code=400)
                if declared_bytes < 0:
                    return PlainTextResponse("Invalid Content-Length.", status_code=400)
                if declared_bytes > MAX_REQUEST_BYTES:
                    return PlainTextResponse(
                        "The complete NIKU H STUDIO request exceeds the 8 GiB local upload limit.",
                        status_code=413,
                    )

    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; base-uri 'none'; object-src 'none'; frame-ancestors 'none'; "
        "form-action 'self'; connect-src 'self'; img-src 'self' data: blob:; "
        "media-src 'self' blob:; style-src 'self' 'unsafe-inline'; script-src 'self'"
    )
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    return response


def capabilities() -> dict[str, Any]:
    model_files = {name: path.is_file() for name, path in COMFY_MODEL_FILES.items()}
    shared_ready = all(model_files[name] for name in ("text_encoder", "video_vae", "audio_vae"))
    fl2va_ready = shared_ready and model_files["fl2va"]
    ref2va_ready = shared_ready and model_files["ref2va"]
    planner = community_planner_status(ROOT)
    return {
        "backend": "comfy",
        "ready": all(model_files.values()),
        "model_files": model_files,
        "fl2va": fl2va_ready,
        "ref2va": ref2va_ready,
        "modes": {
            "t2v": fl2va_ready,
            "i2v": fl2va_ready,
            "first_last": fl2va_ready,
            "omni": ref2va_ready,
        },
        "acceleration": {
            "supported": True,
            "presets": list(EASYCACHE_PRESETS),
            "default": DEFAULT_EASYCACHE_PRESET,
            "minimum_steps": MIN_EASYCACHE_STEPS,
        },
        "attention": {
            "backend": ATTENTION_BACKEND,
            "default": "sage",
            "fallback": "pytorch",
        },
        "reference_image_size": {
            "supported": True,
            "options": ["match", "max"],
            "default": "match",
        },
        "resolution": resolution_catalog(),
        "audio_controls": {
            "supported": shared_ready,
            "method": "raw_prompt",
            "sample_rate": 32000,
            "channels": 2,
            "reference_audio": {
                "voice_only_supported": False,
                "policy_options": list(STANDALONE_AUDIO_POLICIES),
                "default": "dialogue_priority",
                "ui_default": "dialogue_priority",
                "missing_request_policy": "reject_on_dialogue_conflict",
                "no_dialogue_default": "full_content",
                "full_content_opt_in": True,
            },
        },
        "prompt_planner": planner,
        "prompt_processing": {
            "options": list(PROMPT_PROCESSING_MODES),
            "default": "community",
            "recommended": "community",
            "planner_required_for": ["community"],
            "native_comfy_profile": "native_clean",
        },
        "local_only": True,
    }


def _gpu_snapshot() -> dict[str, Any] | None:
    command = [
        "nvidia-smi",
        "--query-gpu=name,memory.total,memory.used,memory.free,utilization.gpu,temperature.gpu,power.draw",
        "--format=csv,noheader,nounits",
    ]
    try:
        output = subprocess.check_output(command, text=True, encoding="utf-8", timeout=3).strip().splitlines()[0]
        name, total, used, free, utilization, temperature, power = [part.strip() for part in output.split(",")]
        return {
            "name": name,
            "memory_total_mib": int(total),
            "memory_used_mib": int(used),
            "memory_free_mib": int(free),
            "utilization": int(utilization),
            "temperature": int(temperature),
            "power_w": round(float(power), 1),
        }
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None


def system_snapshot() -> dict[str, Any]:
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage(ROOT.drive + os.sep)
    return {
        "gpu": _gpu_snapshot(),
        "ram": {
            "total_gib": round(memory.total / 1024**3, 1),
            "available_gib": round(memory.available / 1024**3, 1),
            "percent": memory.percent,
        },
        "disk": {
            "free_gib": round(disk.free / 1024**3, 1),
            "percent": disk.percent,
        },
    }


def _public_job(job: dict[str, Any]) -> dict[str, Any]:
    public = dict(job)
    public.pop("output_path", None)
    public.pop("preview_path", None)
    technical = public.get("technical")
    if isinstance(technical, dict):
        public_technical = dict(technical)
        engine = public_technical.get("engine")
        if isinstance(engine, dict):
            public_technical["engine"] = {
                key: value
                for key, value in engine.items()
                if key in PUBLIC_ENGINE_DIAGNOSTIC_FIELDS
            }
        public["technical"] = public_technical
    public["can_cancel"] = job.get("status") not in TERMINAL_STATES
    return public


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/state")
def get_state() -> dict[str, Any]:
    jobs = [_public_job(job) for job in manager.list_jobs()[:24]]
    return {
        "capabilities": capabilities(),
        "system": system_snapshot(),
        "active_job_id": manager.current_job_id(),
        "jobs": jobs,
    }


@app.get("/api/jobs")
def list_jobs() -> list[dict[str, Any]]:
    return [_public_job(job) for job in manager.list_jobs()]


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    job = manager.get_job(job_id)
    if not job:
        raise HTTPException(404, "生成ジョブが見つかりません。")
    return _public_job(job)


def _kind_for_extension(extension: str) -> str | None:
    extension = extension.lower()
    if extension in IMAGE_EXTENSIONS:
        return "image"
    if extension in VIDEO_EXTENSIONS:
        return "video"
    if extension in AUDIO_EXTENSIONS:
        return "audio"
    return None


def _declared_upload_kind(
    upload: UploadFile,
    index: int,
    expected_kind: str | None = None,
) -> str:
    """Validate upload names and counts before spooled data is copied."""

    original_name = Path(upload.filename or f"upload-{index}").name
    kind = _kind_for_extension(Path(original_name).suffix.lower())
    if kind is None or (expected_kind and kind != expected_kind):
        expected = "画像" if expected_kind == "image" else "対応メディア"
        raise HTTPException(400, f"{original_name}は{expected}ファイルとして使用できません。")
    return kind


def _validate_upload_selection(
    mode: str,
    first_image: UploadFile | None,
    last_image: UploadFile | None,
    references: list[UploadFile],
) -> None:
    if first_image is not None:
        _declared_upload_kind(first_image, 0, "image")
    if last_image is not None:
        _declared_upload_kind(last_image, 1, "image")

    if mode != "omni" and references:
        raise HTTPException(400, "参照素材はOmniモードでのみ使用できます。")
    if len(references) > 12:
        raise HTTPException(400, "Omni参照素材は合計12件までです。")

    reference_kinds = [
        _declared_upload_kind(upload, 10 + index)
        for index, upload in enumerate(references)
    ]
    if mode == "i2v" and first_image is None:
        raise HTTPException(400, "i2vには開始画像が必要です。")
    if mode == "first_last" and (first_image is None or last_image is None):
        raise HTTPException(400, "開始／終了フレームには2枚の画像が必要です。")
    if mode == "omni":
        if first_image is not None or last_image is not None:
            raise HTTPException(400, "Omniの素材は参照素材欄へ追加してください。")
        if not references:
            raise HTTPException(400, "Omniには1つ以上の参照素材が必要です。")
        counts = Counter(reference_kinds)
        if counts["image"] > 9 or counts["video"] > 3 or counts["audio"] > 3:
            raise HTTPException(400, "Omniは画像9、動画3、音声3、合計12素材までです。")
        if counts["audio"] and not (counts["image"] or counts["video"]):
            raise HTTPException(400, "音声参照は画像または動画と組み合わせてください。")


async def _save_upload(upload: UploadFile, target_dir: Path, index: int, expected_kind: str | None = None) -> dict[str, Any]:
    original_name = Path(upload.filename or f"upload-{index}").name
    extension = Path(original_name).suffix.lower()
    kind = _kind_for_extension(extension)
    if kind is None or (expected_kind and kind != expected_kind):
        expected = "画像" if expected_kind == "image" else "対応メディア"
        raise HTTPException(400, f"{original_name}は{expected}ファイルとして使用できません。")
    target = target_dir / f"{index:02d}{extension}"
    size = 0
    with target.open("wb") as handle:
        while chunk := await upload.read(8 * 1024**2):
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                handle.close()
                target.unlink(missing_ok=True)
                raise HTTPException(413, f"{original_name}が2GiBを超えています。")
            handle.write(chunk)
    await upload.close()
    metadata = {
        "original_name": original_name,
        "stored_path": os.fspath(target.resolve()),
        "kind": kind,
        "size": size,
        "content_type": upload.content_type,
    }
    if kind == "video":
        try:
            with av.open(os.fspath(target)) as container:
                metadata["has_audio"] = bool(container.streams.audio)
        except (OSError, av.error.FFmpegError):
            # The generation worker performs the authoritative decode.  Keep
            # probe failures distinct from a successfully inspected silent file.
            pass
    return metadata


def _validate_parameters(
    mode: str,
    style: str,
    prompt: str,
    width: int,
    height: int,
    frames: int,
    steps: int,
    seed: int,
    acceleration: str,
    ref_image_size: str,
    prompt_processing_mode: str,
    standalone_audio_policy: str | None,
    audio_preset: str,
    dialogue: str,
    soundscape: str,
    music_policy: str,
    audio_gain_db: float,
) -> None:
    caps = capabilities()
    if mode not in caps["modes"]:
        raise HTTPException(400, "不明な生成モードです。")
    if not caps["modes"][mode]:
        if mode == "omni":
            raise HTTPException(409, "Omni用Ref2VA重みはまだ準備されていません。")
        raise HTTPException(409, "FL2VAモデルが準備されていません。")
    if style not in STYLE_PROMPTS:
        raise HTTPException(400, "不明な生成スタイルです。")
    if acceleration not in EASYCACHE_PRESETS:
        raise HTTPException(400, "不明な高速化設定です。")
    if ref_image_size not in {"match", "max"}:
        raise HTTPException(400, "不明なOmni参照精度です。")
    if prompt_processing_mode not in PROMPT_PROCESSING_MODES:
        raise HTTPException(400, "不明なプロンプト処理方式です。")
    if (
        standalone_audio_policy is not None
        and standalone_audio_policy not in STANDALONE_AUDIO_POLICIES
    ):
        raise HTTPException(400, "不明な音声参照ポリシーです。")
    if audio_preset not in AUDIO_PRESET_PROMPTS:
        raise HTTPException(400, "不明な音響プリセットです。")
    if music_policy not in MUSIC_POLICY_PROMPTS:
        raise HTTPException(400, "不明なBGM設定です。")
    if not prompt.strip() or len(prompt) > 4000:
        raise HTTPException(400, "プロンプトは1～4000文字で入力してください。")
    if len(dialogue) > 800 or len(soundscape) > 800:
        raise HTTPException(400, "台詞・声質と環境音・効果音は、それぞれ800文字以内で入力してください。")
    if _ADVANCED_AUDIO_CONTROL_RE.search(soundscape):
        raise HTTPException(
            400,
            "環境音・効果音欄にはH3制御タグやセクション見出しを入力できません。"
            "参照タグはメインプロンプトまたは台詞・声質欄で指定してください。",
        )
    if _FORBIDDEN_DIALOGUE_CONTROL_RE.search(dialogue):
        raise HTTPException(
            400,
            "台詞欄には旧式の <d> タグ、予約制御トークン、公式セクション見出しを入力できません。"
            "自然な台詞文と、必要な場合だけ <Audio N> 参照を入力してください。",
        )
    if not -24 <= audio_gain_db <= 12 or not float(audio_gain_db).is_integer():
        raise HTTPException(400, "最終出力音量は−24～+12 dBの整数で指定してください。")
    if width % 32 or height % 32 or not (192 <= width <= 1344) or not (192 <= height <= 1344):
        raise HTTPException(400, "解像度は192～1344の範囲で32の倍数にしてください。")
    if frames not in ALLOWED_FRAMES:
        raise HTTPException(400, "動画の長さは5秒・10秒・約14秒から選んでください。")
    if not 2 <= steps <= 40:
        raise HTTPException(400, "品質ステップは2～40で指定してください。")
    if not 0 <= seed <= 2**63 - 1:
        raise HTTPException(400, "seedの値が範囲外です。")


def _cache_configuration(acceleration: str, steps: int) -> dict[str, Any]:
    effective = acceleration if steps >= MIN_EASYCACHE_STEPS else "off"
    values = EASYCACHE_PRESETS[effective]
    reason = None
    if acceleration == "off":
        reason = "disabled"
    elif effective == "off":
        reason = "steps_below_12"
    return {
        "requested": acceleration,
        "effective": effective,
        "enabled": values is not None,
        "reason": reason,
        "reuse_threshold": values[0] if values else None,
        "start_percent": values[1] if values else None,
        "end_percent": values[2] if values else None,
        "skipped_steps": 0,
        "total_steps": steps,
        "speedup": None,
    }


@app.post("/api/jobs")
async def create_job(
    mode: Annotated[str, Form()],
    style: Annotated[str, Form()],
    prompt: Annotated[str, Form()],
    width: Annotated[int, Form()],
    height: Annotated[int, Form()],
    num_frames: Annotated[int, Form()],
    steps: Annotated[int, Form()],
    seed: Annotated[int, Form()],
    acceleration: Annotated[str, Form()] = DEFAULT_EASYCACHE_PRESET,
    ref_image_size: Annotated[str, Form()] = "match",
    prompt_processing_mode: Annotated[str, Form()] = "community",
    standalone_audio_policy: Annotated[str | None, Form()] = None,
    audio_preset: Annotated[str, Form()] = "auto",
    dialogue: Annotated[str, Form()] = "",
    soundscape: Annotated[str, Form()] = "",
    music_policy: Annotated[str, Form()] = "auto",
    audio_gain_db: Annotated[float, Form()] = 0.0,
    first_image: Annotated[UploadFile | None, File()] = None,
    last_image: Annotated[UploadFile | None, File()] = None,
    references: Annotated[list[UploadFile] | None, File()] = None,
) -> dict[str, Any]:
    _validate_parameters(
        mode,
        style,
        prompt,
        width,
        height,
        num_frames,
        steps,
        seed,
        acceleration,
        ref_image_size,
        prompt_processing_mode,
        standalone_audio_policy,
        audio_preset,
        dialogue,
        soundscape,
        music_policy,
        audio_gain_db,
    )
    reference_uploads = list(references or [])
    _validate_upload_selection(mode, first_image, last_image, reference_uploads)
    job_id = uuid.uuid4().hex[:12]
    job_dir = manager.jobs_dir / job_id
    input_dir = job_dir / "inputs"
    input_dir.mkdir(parents=True, exist_ok=False)
    try:
        first_meta = await _save_upload(first_image, input_dir, 0, "image") if first_image else None
        last_meta = await _save_upload(last_image, input_dir, 1, "image") if last_image else None
        reference_meta = [
            await _save_upload(upload, input_dir, 10 + index)
            for index, upload in enumerate(reference_uploads)
        ]

        reference_audio_count = sum(
            1 for item in reference_meta if item.get("kind") == "audio"
        )
        raw_reference_diagnostics = _validate_reference_tags(
            "\n".join(part for part in (prompt, dialogue) if part),
            mode=mode,
            references=reference_meta,
        )
        if raw_reference_diagnostics:
            diagnostic = raw_reference_diagnostics[0]
            status_code = (
                409
                if diagnostic.get("code") == "PROMPT_REFERENCE_OUT_OF_RANGE"
                else 400
            )
            raise HTTPException(status_code, str(diagnostic.get("message")))
        effective_prompt = prompt
        has_explicit_dialogue = bool(
            dialogue.strip() or _LIKELY_EXPLICIT_DIALOGUE_RE.search(prompt)
        )
        if prompt_processing_mode == "raw_en":
            if any(
                (
                    style != "natural",
                    audio_preset != "auto",
                    bool(dialogue.strip()),
                    bool(soundscape.strip()),
                    music_policy != "auto",
                )
            ):
                raise HTTPException(
                    400,
                    "英語プロンプト直結ではスタイル・台詞・音響の別欄を追記しません。"
                    "必要な指示をメインプロンプト1本へまとめ、各別欄を自動／空欄に戻してください。",
                )
            native_diagnostics = _validate_native_clean_prompt(
                effective_prompt,
                mode=mode,
                references=reference_meta,
            )
            if native_diagnostics:
                diagnostic = native_diagnostics[0]
                status_code = (
                    409
                    if diagnostic.get("code") == "PROMPT_REFERENCE_OUT_OF_RANGE"
                    else 400
                )
                raise HTTPException(status_code, str(diagnostic.get("message")))
        prompt_processing = {
            "mode": (
                "community_planner"
                if prompt_processing_mode == "community"
                else "native_raw"
            ),
            "status": "queued" if prompt_processing_mode == "community" else "raw_en",
            "dialogue_policy": "ordinary_quote" if has_explicit_dialogue else "none",
            "dialogue_count": 1 if has_explicit_dialogue else 0,
            "dialogue_events": [],
            "processing_mode_requested": prompt_processing_mode,
            "processing_mode_effective": prompt_processing_mode,
            "local_only": True,
            "model_inference": prompt_processing_mode == "community",
            "auto_adjustments": [],
            "diagnostics": [],
        }
        if (
            reference_audio_count
            and has_explicit_dialogue
            and standalone_audio_policy is None
        ):
            raise HTTPException(
                409,
                "明示台詞とstandalone Audioを併用する場合は、音声参照の使い方を明示してください。"
                "指定台詞を優先してAudio条件を外すdialogue_priority、または元音声全体を使う"
                "full_contentのどちらかを選択できます。",
            )
        exclude_reference_audio = bool(
            reference_audio_count
            and has_explicit_dialogue
            and standalone_audio_policy == "dialogue_priority"
        )
        effective_reference_meta = (
            [item for item in reference_meta if item.get("kind") != "audio"]
            if exclude_reference_audio
            else list(reference_meta)
        )
        excluded_audio_reference_ids = (
            list(range(1, reference_audio_count + 1))
            if exclude_reference_audio
            else []
        )
        if exclude_reference_audio and re.search(
            r"<Audio\s+[1-9][0-9]*>",
            "\n".join(part for part in (effective_prompt, dialogue) if part),
        ):
            raise HTTPException(
                409,
                "指定台詞を優先するとstandalone AudioはH3条件から外れますが、"
                "発話句以外にも<Audio N>参照が残っています。Audioタグを外すか、"
                "元音声全体を使う設定へ変更してください。",
            )
        requested_audio_policy = standalone_audio_policy or (
            "full_content" if reference_audio_count else "dialogue_priority"
        )
        effective_audio_policy = requested_audio_policy
        if (
            reference_audio_count
            and not has_explicit_dialogue
            and requested_audio_policy == "dialogue_priority"
        ):
            # With no competing spoken line there is nothing to prioritize.
            # The audio attachment remains connected to H3 in full, so expose
            # the effective policy that matches the actual conditioning graph.
            effective_audio_policy = "full_content"
            prompt_processing.setdefault("diagnostics", []).append(
                "AUDIO_POLICY_NORMALIZED_TO_FULL_CONTENT_WITHOUT_DIALOGUE"
            )
        standalone_audio_conditioning = bool(
            reference_audio_count and not exclude_reference_audio
        )
        if (
            reference_audio_count
            and has_explicit_dialogue
            and standalone_audio_policy == "full_content"
        ):
            prompt_processing.setdefault("diagnostics", []).append(
                "FULL_CONTENT_AUDIO_MAY_OVERRIDE_DIALOGUE"
            )
        prompt_processing.update(
            reference_audio_count=reference_audio_count,
            standalone_audio_policy_requested=requested_audio_policy,
            standalone_audio_policy_effective=effective_audio_policy,
            standalone_audio_conditioning=standalone_audio_conditioning,
            excluded_audio_reference_ids=excluded_audio_reference_ids,
            voice_only_reference_supported=False,
        )
        cache = _cache_configuration(acceleration, steps)
        output_path = manager.outputs_dir / f"{job_id}.mp4"
        preview_path = job_dir / "preview.jpg"
        request = {
            "id": job_id,
            "backend": "comfy",
            "workflow_profile": "native_clean",
            "attention_backend": ATTENTION_BACKEND,
            "acceleration": acceleration,
            "cache": cache,
            "ref_image_size": ref_image_size,
            "mode": mode,
            "style": style,
            "style_direction": STYLE_PROMPTS[style],
            "prompt": prompt,
            "effective_prompt": effective_prompt,
            "prompt_processing_mode": prompt_processing_mode,
            "standalone_audio_policy_requested": requested_audio_policy,
            "standalone_audio_policy_effective": effective_audio_policy,
            "standalone_audio_conditioning": standalone_audio_conditioning,
            "excluded_audio_reference_ids": excluded_audio_reference_ids,
            "prompt_processing": prompt_processing,
            "audio_preset": audio_preset,
            "dialogue": dialogue.strip(),
            "soundscape": soundscape.strip(),
            "music_policy": music_policy,
            "audio_gain_db": audio_gain_db,
            "audio_output": {"sample_rate": 32000, "channels": 2, "generation": "joint", "gain_db": audio_gain_db},
            "width": width,
            "height": height,
            "num_frames": num_frames,
            "steps": steps,
            "seed": seed,
            "first_image": first_meta["stored_path"] if first_meta else None,
            "last_image": last_meta["stored_path"] if last_meta else None,
            "attachments": [item for item in (first_meta, last_meta) if item] + reference_meta,
            "uploaded_references": reference_meta,
            "references": effective_reference_meta,
            "model_path": os.fspath(COMFY_MODEL_ROOT),
            "output_path": os.fspath(output_path),
            "preview_path": os.fspath(preview_path),
            "result_url": f"/api/jobs/{job_id}/result",
            "preview_url": f"/api/jobs/{job_id}/preview",
        }
        (job_dir / "request.json").write_text(json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8")
        job = {
            "id": job_id,
            "backend": "comfy",
            "workflow_profile": "native_clean",
            "attention_backend": ATTENTION_BACKEND,
            "acceleration": acceleration,
            "cache": cache,
            "ref_image_size": ref_image_size,
            "status": "queued",
            "phase": "待機中",
            "message": "生成キューへ追加しました。",
            "progress": 0,
            "created_at": utc_now(),
            "progress_updated_at": utc_now(),
            "started_at": None,
            "finished_at": None,
            "mode": mode,
            "style": style,
            "prompt": prompt,
            "prompt_processing_mode": prompt_processing_mode,
            "standalone_audio_policy_requested": requested_audio_policy,
            "standalone_audio_policy_effective": effective_audio_policy,
            "standalone_audio_conditioning": standalone_audio_conditioning,
            "excluded_audio_reference_ids": excluded_audio_reference_ids,
            "prompt_processing": prompt_processing,
            "audio_preset": audio_preset,
            "dialogue": dialogue.strip(),
            "soundscape": soundscape.strip(),
            "music_policy": music_policy,
            "audio_gain_db": audio_gain_db,
            "audio_output": {"sample_rate": 32000, "channels": 2, "generation": "joint", "gain_db": audio_gain_db},
            "width": width,
            "height": height,
            "num_frames": num_frames,
            "steps": steps,
            "seed": seed,
            "attachments": [
                {
                    "index": index,
                    "name": item["original_name"],
                    "kind": item["kind"],
                    "size": item["size"],
                    "url": f"/api/jobs/{job_id}/attachments/{index}",
                }
                for index, item in enumerate(request["attachments"])
            ],
            "output_path": os.fspath(output_path),
            "preview_path": os.fspath(preview_path),
            "result": None,
            "preview": None,
            "logs": [],
        }
        return _public_job(manager.submit(job))
    except Exception:
        if job_dir.is_dir():
            shutil.rmtree(job_dir)
        raise


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict[str, Any]:
    job = manager.cancel(job_id)
    if not job:
        raise HTTPException(404, "生成ジョブが見つかりません。")
    return _public_job(job)


@app.get("/api/jobs/{job_id}/result")
def job_result(job_id: str) -> FileResponse:
    job = manager.get_job(job_id)
    if not job:
        raise HTTPException(404, "生成ジョブが見つかりません。")
    path = Path(job["output_path"])
    if not path.is_file():
        raise HTTPException(404, "生成動画はまだ完成していません。")
    return FileResponse(path, media_type="video/mp4", filename=f"MiniMax-H3-{job_id}.mp4")


@app.get("/api/jobs/{job_id}/preview")
def job_preview(job_id: str) -> FileResponse:
    job = manager.get_job(job_id)
    if not job:
        raise HTTPException(404, "生成ジョブが見つかりません。")
    path = Path(job["preview_path"])
    if not path.is_file():
        raise HTTPException(404, "プレビューはまだありません。")
    return FileResponse(path, media_type="image/jpeg")


@app.get("/api/jobs/{job_id}/attachments/{index}")
def job_attachment(job_id: str, index: int) -> FileResponse:
    job = manager.get_job(job_id)
    if not job or not 0 <= index < len(job.get("attachments", [])):
        raise HTTPException(404, "参照素材が見つかりません。")
    request = json.loads((manager.jobs_dir / job_id / "request.json").read_text(encoding="utf-8"))
    metadata = request["attachments"][index]
    return FileResponse(metadata["stored_path"], media_type=metadata.get("content_type"))


@app.post("/api/open-output-folder")
def open_output_folder() -> dict[str, bool]:
    manager.outputs_dir.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        os.startfile(manager.outputs_dir)  # type: ignore[attr-defined]
    else:
        raise HTTPException(501, "このOSではフォルダを自動で開けません。")
    return {"opened": True}


def _open_browser_when_ready(port: int) -> None:
    time.sleep(1.2)
    webbrowser.open(f"http://127.0.0.1:{port}")


def main() -> None:
    parser = argparse.ArgumentParser(description="MiniMax H3 local browser studio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7863)
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost"}:
        raise SystemExit("安全のためNIKU H STUDIOはlocalhostでのみ起動できます。")
    if args.open:
        threading.Thread(target=_open_browser_when_ready, args=(args.port,), daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")


if __name__ == "__main__":
    main()
