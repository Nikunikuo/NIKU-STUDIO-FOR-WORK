from __future__ import annotations

import argparse
import json
import os
import queue
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

import psutil

from .comfy_client import ComfyClient, ComfyClientError, PromptPoll
from .comfy_workflow import (
    AUDIO_VAE_MODEL,
    COMFYUI_COMMIT,
    DEFAULT_EASYCACHE_PRESET,
    DEFAULT_WORKFLOW_PROFILE,
    EASYCACHE_PRESETS,
    FL2VA_MODEL,
    MIN_EASYCACHE_STEPS,
    REF2VA_MODEL,
    SAVE_VIDEO_NODE_ID,
    TEXT_ENCODER_MODEL,
    VIDEO_VAE_MODEL,
    SUPPORTED_WORKFLOW_PROFILES,
    build_h3_workflow,
    resolve_scheduler,
)
from .process_guard import ProcessJob


EVENT_PREFIX = "H3EVENT "
ROOT = Path(__file__).resolve().parents[1]
COMFY_MODEL_REVISION = "0543966fbdce5ba05709a8f2031c94bdba629b4a"
SAGEATTENTION_VERSION = "2.2.0+cu130torch2.10.0andhigher.post6"
TRITON_WINDOWS_VERSION = "3.7.1.post27"
STARTUP_TIMEOUT_SECONDS = 180.0
PROMPT_TIMEOUT_SECONDS = 12 * 60 * 60.0
POLL_INTERVAL_SECONDS = 1.0
MAX_REFERENCE_BYTES = 2 * 1024**3
ATTENTION_BACKENDS = {"sage", "pytorch"}

_MEDIA_EXTENSIONS = {
    "image": {".bmp", ".gif", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"},
    "video": {".avi", ".m4v", ".mkv", ".mov", ".mp4", ".webm"},
    "audio": {".aac", ".flac", ".m4a", ".mp3", ".ogg", ".opus", ".wav"},
}
_VIDEO_EXTENSIONS = {".avi", ".m4v", ".mkv", ".mov", ".mp4", ".webm"}

# These are the LFS object markers published by Comfy-Org for the fixed model
# revision above.  Checking the inexpensive local size and download marker on
# every cold start catches partial downloads and accidental model substitutions
# without hashing ~58 GiB before each generation.
MODEL_SPECS: dict[str, tuple[str, int, str]] = {
    FL2VA_MODEL: (
        f"diffusion_models/{FL2VA_MODEL}",
        20_970_379_616,
        "e889202c41dafb67b10d67b97f0d8541508036a6090af23425a5c2615d03c47a",
    ),
    REF2VA_MODEL: (
        f"diffusion_models/{REF2VA_MODEL}",
        20_970_379_616,
        "9255f52b6677845ad238f20dfaafa94727053694127ab7f255c048f0f9365779",
    ),
    TEXT_ENCODER_MODEL: (
        f"text_encoders/{TEXT_ENCODER_MODEL}",
        15_687_142_551,
        "35a88d51044231fe332301d7a62aa81e3f2cba62febeb446e2c1e3e0ef76f2c6",
    ),
    VIDEO_VAE_MODEL: (
        f"vae/{VIDEO_VAE_MODEL}",
        5_207_808_496,
        "7c1f131492e7eddacaac9069a61b81bdd39de5cc96561e677c5eab1cdce5e522",
    ),
    AUDIO_VAE_MODEL: (
        f"vae/{AUDIO_VAE_MODEL}",
        605_254_808,
        "8e505d95dd1561d47abd43d4238fd40d9bb1ae9e147ed0a4cba778d76ae4db48",
    ),
}

_EASYCACHE_SUMMARY = re.compile(
    r"EasyCache\s+-\s+skipped\s+(?P<skipped>\d+)\s*/\s*(?P<total>\d+)\s+steps\s+"
    r"\((?P<speedup>[0-9]+(?:\.[0-9]+)?)x\s+speedup\)",
    re.IGNORECASE,
)
_TQDM_PROGRESS = re.compile(
    r"(?P<percent>\d{1,3})%\|.*?\|\s*(?P<step>\d+)\s*/\s*(?P<total>\d+)"
)


def emit(**payload: Any) -> None:
    """Write one ASCII-safe event for JobManager's line protocol."""

    print(EVENT_PREFIX + json.dumps(payload, ensure_ascii=True), flush=True)


def _is_within(root: Path, candidate: Path) -> bool:
    try:
        return Path(os.path.commonpath((os.fspath(root), os.fspath(candidate)))) == root
    except ValueError:
        return False


def _resolve_within(root: Path, value: str | os.PathLike[str], *, must_exist: bool) -> Path:
    root = root.resolve()
    candidate = Path(value).expanduser().resolve(strict=must_exist)
    if not _is_within(root, candidate):
        raise ValueError(f"path escapes the project root: {candidate}")
    return candidate


def _safe_job_token(value: Any) -> str:
    text = str(value or "job")
    readable = re.sub(r"[^A-Za-z0-9_-]+", "_", text).strip("_")[:32] or "job"
    import hashlib

    digest = hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()[:8]
    return f"{readable}_{digest}"


def _load_request(path: Path, root: Path = ROOT) -> dict[str, Any]:
    request_path = _resolve_within(root.resolve(), path, must_exist=True)
    if not request_path.is_file():
        raise FileNotFoundError(request_path)
    request = json.loads(request_path.read_text(encoding="utf-8"))
    if not isinstance(request, dict):
        raise ValueError("request JSON must contain an object")
    request["request_path"] = os.fspath(request_path)
    return request


def _requested_acceleration(request: Mapping[str, Any]) -> str:
    requested = request.get(
        "easycache",
        request.get("acceleration", DEFAULT_EASYCACHE_PRESET),
    )
    if not isinstance(requested, str) or requested not in EASYCACHE_PRESETS:
        choices = ", ".join(EASYCACHE_PRESETS)
        raise ValueError(f"acceleration must be one of: {choices}")
    return requested


def workflow_profile(request: Mapping[str, Any]) -> str:
    requested = request.get("workflow_profile", DEFAULT_WORKFLOW_PROFILE)
    if not isinstance(requested, str) or requested not in SUPPORTED_WORKFLOW_PROFILES:
        choices = ", ".join(sorted(SUPPORTED_WORKFLOW_PROFILES))
        raise ValueError(f"workflow_profile must be one of: {choices}")
    return requested


def scheduler_schema(request: Mapping[str, Any]) -> dict[str, str]:
    requested = request.get("scheduler", "auto")
    if not isinstance(requested, str):
        raise ValueError("scheduler must be a string")
    mode = str(request.get("mode"))
    raw_steps = request.get("steps")
    steps = int(raw_steps) if raw_steps is not None else None
    return {
        "requested": requested,
        "effective": resolve_scheduler(mode, requested, steps=steps),
    }


def cache_schema(
    requested: str,
    steps: int,
    *,
    skipped_steps: int | None = None,
    total_steps: int | None = None,
    speedup: float | None = None,
) -> dict[str, Any]:
    effective = requested if steps >= MIN_EASYCACHE_STEPS else "off"
    values = EASYCACHE_PRESETS[effective]
    enabled = values is not None
    if requested == "off":
        reason = "disabled"
    elif not enabled:
        reason = f"steps_below_{MIN_EASYCACHE_STEPS}"
    else:
        reason = None
    reuse, start, end = values if values is not None else (None, None, None)
    if not enabled:
        skipped_steps = 0
        total_steps = steps
        speedup = 1.0
    elif skipped_steps is None:
        skipped_steps = 0
    return {
        "requested": requested,
        "effective": effective,
        "enabled": enabled,
        "reason": reason,
        "reuse_threshold": reuse,
        "start_percent": start,
        "end_percent": end,
        "skipped_steps": skipped_steps,
        "total_steps": steps if total_steps is None else total_steps,
        "speedup": speedup,
    }


def _git_output(source: Path, *arguments: str) -> str:
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    result = subprocess.run(
        ["git", "-C", os.fspath(source), *arguments],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
        creationflags=creationflags,
    )
    return result.stdout.strip()


def verify_installation(root: Path, variant: str) -> dict[str, Any]:
    """Verify the pinned source and cheap, authoritative model markers."""

    root = root.resolve()
    source = root / ".upstream" / "ComfyUI"
    main_py = source / "main.py"
    python_exe = root / ".comfy-venv" / "Scripts" / "python.exe"
    models = root / "models" / "comfy"
    if not main_py.is_file() or not python_exe.is_file():
        raise FileNotFoundError("pinned ComfyUI source or .comfy-venv Python is missing")
    actual_commit = _git_output(source, "rev-parse", "HEAD")
    if actual_commit != COMFYUI_COMMIT:
        raise RuntimeError(f"ComfyUI SHA mismatch: expected {COMFYUI_COMMIT}, got {actual_commit}")
    dirty = _git_output(source, "status", "--porcelain", "--untracked-files=all")
    if dirty:
        raise RuntimeError("pinned ComfyUI checkout has local or untracked files")
    for marker in (
        source / "comfy_extras" / "nodes_minimax_h3.py",
        source / "comfy_extras" / "nodes_easycache.py",
    ):
        if not marker.is_file():
            raise RuntimeError(f"ComfyUI feature marker is missing: {marker.name}")

    chosen_model = REF2VA_MODEL if variant == "ref2va" else FL2VA_MODEL
    required = (chosen_model, TEXT_ENCODER_MODEL, VIDEO_VAE_MODEL, AUDIO_VAE_MODEL)
    for filename in required:
        relative, expected_size, expected_lfs = MODEL_SPECS[filename]
        model_path = models / Path(*PurePosixPath(relative).parts)
        if not model_path.is_file():
            raise FileNotFoundError(f"ComfyUI model is missing: {model_path}")
        if model_path.stat().st_size != expected_size:
            raise RuntimeError(
                f"ComfyUI model size mismatch for {filename}: "
                f"expected {expected_size}, got {model_path.stat().st_size}"
            )
        metadata = models / ".cache" / "huggingface" / "download" / Path(
            *PurePosixPath(relative + ".metadata").parts
        )
        try:
            lines = metadata.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise RuntimeError(f"download marker is missing for {filename}") from exc
        if len(lines) < 2 or lines[0].strip() != COMFY_MODEL_REVISION or lines[1].strip() != expected_lfs:
            raise RuntimeError(f"download marker mismatch for {filename}")
        with model_path.open("rb") as handle:
            header_length_bytes = handle.read(8)
            if len(header_length_bytes) != 8:
                raise RuntimeError(f"truncated safetensors file: {filename}")
            header_length = int.from_bytes(header_length_bytes, "little")
            if not 2 <= header_length <= 128 * 1024 * 1024 or handle.read(1) != b"{":
                raise RuntimeError(f"invalid safetensors header: {filename}")
    attention_backend = configured_attention_backend()
    if attention_backend == "sage":
        import importlib.metadata

        if importlib.metadata.version("sageattention") != SAGEATTENTION_VERSION:
            raise RuntimeError("pinned SageAttention runtime is missing or has the wrong version")
        if importlib.metadata.version("triton-windows") != TRITON_WINDOWS_VERSION:
            raise RuntimeError("pinned Triton Windows runtime is missing or has the wrong version")
    return {
        "comfyui_commit": actual_commit,
        "model_revision": COMFY_MODEL_REVISION,
        "variant": variant,
        "models_root": os.fspath(models),
        "attention_backend": attention_backend,
    }


@dataclass(frozen=True)
class RuntimePaths:
    root: Path
    base: Path
    models: Path
    input: Path
    output: Path
    temp_parent: Path
    temp: Path
    user: Path
    database: Path
    log: Path


def make_runtime_paths(root: Path, job_token: str) -> RuntimePaths:
    runtime_root = (root / "webui_data" / "comfy_runtime" / job_token).resolve()
    paths = RuntimePaths(
        root=runtime_root,
        base=runtime_root / "base",
        models=(root / "models" / "comfy").resolve(),
        input=runtime_root / "input",
        output=runtime_root / "output",
        temp_parent=runtime_root / "temp_root",
        temp=runtime_root / "temp_root" / "temp",
        user=runtime_root / "user",
        database=runtime_root / "user" / "comfyui.db",
        log=runtime_root / "comfy.stdout.log",
    )
    for directory in (
        paths.base,
        paths.models,
        paths.input,
        paths.output,
        paths.temp_parent,
        paths.user,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    return paths


def reserve_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def verify_loopback_listener_owner(process: subprocess.Popen[str], port: int) -> None:
    """Require the private Comfy listener to belong to the spawned tree."""

    try:
        root = psutil.Process(process.pid)
        allowed_pids = {root.pid, *(child.pid for child in root.children(recursive=True))}
        listeners = []
        for connection in psutil.net_connections(kind="tcp"):
            if connection.status != psutil.CONN_LISTEN or not connection.laddr:
                continue
            address = connection.laddr
            host = getattr(address, "ip", address[0])
            listening_port = getattr(address, "port", address[1])
            if host == "127.0.0.1" and listening_port == port:
                listeners.append(connection)
    except (psutil.Error, OSError, IndexError, TypeError) as exc:
        raise RuntimeError("could not verify the private ComfyUI listener owner") from exc

    if len(listeners) != 1 or listeners[0].pid not in allowed_pids:
        owners = sorted({connection.pid for connection in listeners})
        raise RuntimeError(
            f"private ComfyUI port {port} is not owned exclusively by the spawned process tree "
            f"(listener PIDs: {owners})"
        )


def configured_attention_backend() -> str:
    backend = os.environ.get("H3_ATTENTION_BACKEND", "sage").strip().lower()
    if backend not in ATTENTION_BACKENDS:
        choices = ", ".join(sorted(ATTENTION_BACKENDS))
        raise ValueError(f"H3_ATTENTION_BACKEND must be one of: {choices}")
    return backend


def build_comfy_command(
    root: Path,
    paths: RuntimePaths,
    port: int,
    *,
    workflow_profile_name: str = DEFAULT_WORKFLOW_PROFILE,
) -> list[str]:
    if workflow_profile_name not in SUPPORTED_WORKFLOW_PROFILES:
        choices = ", ".join(sorted(SUPPORTED_WORKFLOW_PROFILES))
        raise ValueError(f"workflow_profile must be one of: {choices}")
    database_url = "sqlite:///" + paths.database.as_posix()
    command = [
        os.fspath(root / ".comfy-venv" / "Scripts" / "python.exe"),
        os.fspath(root / ".upstream" / "ComfyUI" / "main.py"),
        "--listen",
        "127.0.0.1",
        "--port",
        str(port),
        "--base-directory",
        os.fspath(paths.base),
        "--models-directory",
        os.fspath(paths.models),
        "--input-directory",
        os.fspath(paths.input),
        "--output-directory",
        os.fspath(paths.output),
        "--temp-directory",
        os.fspath(paths.temp_parent),
        "--user-directory",
        os.fspath(paths.user),
        "--database-url",
        database_url,
        "--disable-all-custom-nodes",
        "--disable-api-nodes",
        "--disable-auto-launch",
        "--cache-none",
        "--async-offload",
        "2",
        "--max-upload-size",
        "2048",
        "--log-stdout",
    ]
    if configured_attention_backend() == "sage":
        command.append("--use-sage-attention")
    return command


class LogPump:
    """Continuously drain a child pipe so ComfyUI can never block on logging."""

    def __init__(self, stream: Any, log_path: Path) -> None:
        self._stream = stream
        self._log_path = log_path
        self._lines: queue.SimpleQueue[str] = queue.SimpleQueue()
        self._tail: list[str] = []
        self._thread = threading.Thread(target=self._read, name="comfy-stdout-drain", daemon=True)
        self._thread.start()

    def _record(self, line: str, handle: Any) -> None:
        line = line.replace("\x00", "")[-16_384:]
        if not line.strip():
            return
        handle.write(line + "\n")
        handle.flush()
        self._tail.append(line)
        del self._tail[:-80]
        self._lines.put(line)

    def _read(self) -> None:
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        buffer: list[str] = []
        try:
            with self._log_path.open("a", encoding="utf-8", errors="replace") as handle:
                while True:
                    character = self._stream.read(1)
                    if character == "":
                        break
                    if character in "\r\n":
                        if buffer:
                            self._record("".join(buffer), handle)
                            buffer.clear()
                    else:
                        buffer.append(character)
                        if len(buffer) >= 16_384:
                            self._record("".join(buffer), handle)
                            buffer.clear()
                if buffer:
                    self._record("".join(buffer), handle)
        except (OSError, ValueError):
            pass

    def drain(self) -> list[str]:
        lines: list[str] = []
        while True:
            try:
                lines.append(self._lines.get_nowait())
            except queue.Empty:
                return lines

    def tail(self) -> str:
        return "\n".join(self._tail[-20:])

    def close(self) -> None:
        try:
            self._stream.close()
        except (OSError, ValueError):
            pass
        self._thread.join(timeout=3)

def terminate_process_tree(process: subprocess.Popen[str] | None) -> None:
    if process is None:
        return
    try:
        parent = psutil.Process(process.pid)
        children = parent.children(recursive=True)
        for child in reversed(children):
            child.terminate()
        parent.terminate()
        _, alive = psutil.wait_procs([*children, parent], timeout=8)
        for remaining in alive:
            remaining.kill()
        if alive:
            psutil.wait_procs(alive, timeout=3)
    except (psutil.Error, OSError):
        try:
            process.terminate()
            process.wait(timeout=5)
        except (OSError, subprocess.SubprocessError):
            try:
                process.kill()
            except OSError:
                pass


class ProgressTracker:
    def __init__(
        self,
        requested: str,
        steps: int,
        workflow_profile_name: str = DEFAULT_WORKFLOW_PROFILE,
    ) -> None:
        self.requested = requested
        self.steps = steps
        self.workflow_profile = workflow_profile_name
        self.cache = cache_schema(requested, steps)
        self.progress = 18.0
        self.last_step: int | None = None

    def consume(self, line: str) -> dict[str, Any] | None:
        cache_match = _EASYCACHE_SUMMARY.search(line)
        if cache_match:
            self.cache = cache_schema(
                self.requested,
                self.steps,
                skipped_steps=int(cache_match.group("skipped")),
                total_steps=int(cache_match.group("total")),
                speedup=float(cache_match.group("speedup")),
            )
        match = _TQDM_PROGRESS.search(line)
        if not match or int(match.group("total")) != self.steps:
            return None
        step = int(match.group("step"))
        if step == self.last_step:
            return None
        self.last_step = step
        self.progress = max(self.progress, min(88.0, 48.0 + 40.0 * step / max(self.steps, 1)))
        return {
            "status": "running",
            "phase": "映像と音声を生成しています",
            "message": f"Denoise {step} / {self.steps}",
            "progress": round(self.progress, 2),
            "step": step,
            "total_steps": self.steps,
            "backend": "comfy",
            "workflow_profile": self.workflow_profile,
            "acceleration": self.requested,
            "cache": dict(self.cache),
        }


def _drain_logs(
    pump: LogPump,
    tracker: ProgressTracker | None = None,
    job_log: Path | None = None,
) -> None:
    lines = pump.drain()
    if not lines:
        return
    if job_log is not None:
        job_log.parent.mkdir(parents=True, exist_ok=True)
        with job_log.open("a", encoding="utf-8", errors="replace") as handle:
            for line in lines:
                handle.write(line + "\n")
    for line in lines:
        print("COMFY " + line[-4000:], flush=True)
        if tracker is not None:
            event = tracker.consume(line)
            if event:
                emit(**event)


def _stage_one(source_value: Any, kind: str, stage_dir: Path, index: int, root: Path) -> str:
    if kind not in _MEDIA_EXTENSIONS:
        raise ValueError(f"unsupported reference kind: {kind!r}")
    source = _resolve_within(root, str(source_value), must_exist=True)
    if not source.is_file():
        raise FileNotFoundError(source)
    suffix = source.suffix.lower()
    if suffix not in _MEDIA_EXTENSIONS[kind]:
        raise ValueError(f"unsupported {kind} extension: {source.suffix}")
    size = source.stat().st_size
    if size <= 0 or size > MAX_REFERENCE_BYTES:
        raise ValueError(f"reference size is outside the supported range: {source}")
    destination = stage_dir / f"{index:02d}_{kind}{suffix}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, destination)
    except OSError:
        with source.open("rb") as source_handle, destination.open("xb") as target_handle:
            shutil.copyfileobj(source_handle, target_handle, length=8 * 1024 * 1024)
    if destination.stat().st_size != size:
        raise RuntimeError(f"staged reference size mismatch: {source.name}")
    return PurePosixPath(stage_dir.name, destination.name).as_posix()


def stage_request_inputs(
    request: Mapping[str, Any], paths: RuntimePaths, root: Path
) -> tuple[dict[str, Any], Path]:
    references = request.get("references", [])
    if not isinstance(references, list):
        raise ValueError("references must be a list")
    if request.get("standalone_audio_conditioning") is False and any(
        isinstance(reference, Mapping) and reference.get("kind") == "audio"
        for reference in references
    ):
        raise ValueError(
            "standalone Audio remained in the execution request after conditioning was disabled"
        )
    stage_dir = paths.input / ("h3_" + _safe_job_token(request.get("id")) + "_" + uuid.uuid4().hex[:8])
    stage_dir.mkdir(parents=True, exist_ok=False)
    staged: dict[str, Any] = {
        "input_filename": None,
        "last_frame_filename": None,
        "reference_image_filenames": [],
        "reference_video_filenames": [],
        "reference_audio_filenames": [],
    }
    index = 0
    if request.get("first_image"):
        staged["input_filename"] = _stage_one(request["first_image"], "image", stage_dir, index, root)
        index += 1
    if request.get("last_image"):
        staged["last_frame_filename"] = _stage_one(request["last_image"], "image", stage_dir, index, root)
        index += 1
    for reference in references:
        if not isinstance(reference, Mapping):
            raise ValueError("each reference must be an object")
        kind = reference.get("kind")
        if not isinstance(kind, str):
            raise ValueError("reference kind is missing")
        filename = _stage_one(reference.get("stored_path"), kind, stage_dir, index, root)
        staged[f"reference_{kind}_filenames"].append(filename)
        index += 1
    return staged, stage_dir


def build_request_workflow(
    request: Mapping[str, Any],
    staged: Mapping[str, Any],
    *,
    requested_acceleration: str,
    output_prefix: str,
) -> dict[str, Any]:
    """Translate one persisted UI request into the pinned workflow builder."""

    effective_prompt = request["effective_prompt"]
    if not isinstance(effective_prompt, str):
        raise TypeError("effective_prompt must be a string")
    return build_h3_workflow(
        mode=str(request["mode"]),
        # Do not strip, normalize, translate, tag, or otherwise rewrite this
        # value.  In native_clean it is the already-compiled prompt and must
        # reach MiniMaxH3*ToVideo byte-for-byte.
        prompt=effective_prompt,
        width=int(request["width"]),
        height=int(request["height"]),
        frames=int(request["num_frames"]),
        steps=int(request["steps"]),
        seed=int(request["seed"]),
        output_prefix=output_prefix,
        audio_gain_db=float(request.get("audio_gain_db", 0.0)),
        ref_image_size=str(request.get("ref_image_size", "match")),
        workflow_profile=workflow_profile(request),
        easycache=requested_acceleration,
        scheduler=str(request.get("scheduler", "auto")),
        embedded_video_audio_policy=str(
            request.get("embedded_video_audio_policy", "ignore")
        ),
        embedded_video_audio_indices=request.get("embedded_video_audio_indices"),
        **dict(staged),
    )


def _find_video_descriptor(history: Mapping[str, Any]) -> dict[str, str]:
    outputs = history.get("outputs", {})
    preferred: list[dict[str, str]] = []
    if isinstance(outputs, Mapping):
        save_output = outputs.get(SAVE_VIDEO_NODE_ID, {})
        if isinstance(save_output, Mapping):
            preferred = ComfyClient.output_descriptors({"outputs": {SAVE_VIDEO_NODE_ID: save_output}})
    candidates = preferred or ComfyClient.output_descriptors(history)
    videos = [item for item in candidates if Path(item["filename"]).suffix.lower() in _VIDEO_EXTENSIONS]
    if len(videos) != 1:
        raise RuntimeError(f"expected one SaveVideo output, found {len(videos)}")
    return videos[0]


def validate_media_and_preview(video_path: Path, preview_path: Path) -> dict[str, Any]:
    import av

    video_frames = 0
    audio_frames = 0
    audio_samples_per_channel = 0
    first_frame = None
    with av.open(os.fspath(video_path)) as container:
        videos = list(container.streams.video)
        audios = list(container.streams.audio)
        if not videos or not audios:
            raise RuntimeError("generated video must contain both video and audio streams")
        duration = float(container.duration / av.time_base) if container.duration else None
        video_codec = videos[0].codec_context.name
        audio_codec = audios[0].codec_context.name
        width = videos[0].codec_context.width
        height = videos[0].codec_context.height
        sample_rate = audios[0].codec_context.sample_rate
        channels = audios[0].codec_context.channels
        for packet in container.demux(videos[0], audios[0]):
            for frame in packet.decode():
                if isinstance(frame, av.VideoFrame):
                    video_frames += 1
                    if first_frame is None:
                        first_frame = frame.to_image()
                elif isinstance(frame, av.AudioFrame):
                    audio_frames += 1
                    audio_samples_per_channel += frame.samples
    if first_frame is None or video_frames <= 0 or audio_frames <= 0 or audio_samples_per_channel <= 0:
        raise RuntimeError("generated media could not be fully decoded as video plus audio")
    preview_path.parent.mkdir(parents=True, exist_ok=True)
    first_frame.save(preview_path, format="JPEG", quality=88)
    return {
        "bytes": video_path.stat().st_size,
        "duration_seconds": duration,
        "video_codec": video_codec,
        "audio_codec": audio_codec,
        "width": width,
        "height": height,
        "video_frames": video_frames,
        "audio_frames": audio_frames,
        "audio_samples": audio_samples_per_channel * channels,
        "audio_samples_per_channel": audio_samples_per_channel,
        "audio_sample_rate": sample_rate,
        "audio_channels": channels,
    }


def copy_and_validate_output(
    client: ComfyClient,
    history: Mapping[str, Any],
    request: Mapping[str, Any],
    root: Path,
) -> dict[str, Any]:
    descriptor = _find_video_descriptor(history)
    source = client.resolve_output_path(descriptor, must_exist=True)
    output = _resolve_within(root, str(request["output_path"]), must_exist=False)
    preview = _resolve_within(root, str(request["preview_path"]), must_exist=False)
    if output.suffix.lower() != ".mp4":
        raise ValueError("output_path must end in .mp4")
    if output.exists() or preview.exists():
        raise FileExistsError("output or preview already exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    preview.parent.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    temporary_output = output.with_name(output.name + f".{token}.partial")
    temporary_preview = preview.with_name(preview.name + f".{token}.partial")
    try:
        shutil.copy2(source, temporary_output)
        media = validate_media_and_preview(temporary_output, temporary_preview)
        os.replace(temporary_output, output)
        os.replace(temporary_preview, preview)
        return media
    finally:
        for temporary in (temporary_output, temporary_preview):
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def _failure_detail(poll: PromptPoll) -> str:
    error = poll.error or {}
    for key in ("exception_message", "message", "error"):
        value = error.get(key) if isinstance(error, Mapping) else None
        if value:
            return str(value)
    return f"ComfyUI prompt ended as {poll.state}"


def run(request: dict[str, Any], *, root: Path = ROOT) -> None:
    """Run one request against one isolated, fixed-revision ComfyUI child."""

    root = root.resolve()
    started_at = time.monotonic()
    mode = str(request.get("mode"))
    variant = "ref2va" if mode == "omni" else "fl2va"
    requested = _requested_acceleration(request)
    profile = workflow_profile(request)
    scheduler = scheduler_schema(request)
    attention_backend = configured_attention_backend()
    steps = int(request["steps"])
    tracker = ProgressTracker(requested, steps, profile)
    job_token = _safe_job_token(request.get("id"))
    paths = make_runtime_paths(root, job_token)
    job_log = Path(str(request["request_path"])).resolve().parent / "comfy.log"
    process: subprocess.Popen[str] | None = None
    process_job: ProcessJob | None = None
    pump: LogPump | None = None
    stage_dir: Path | None = None
    timings: dict[str, Any] = {}
    try:
        job_log.parent.mkdir(parents=True, exist_ok=True)
        with job_log.open("a", encoding="utf-8", errors="replace") as handle:
            handle.write(f"NIKU H STUDIO workflow_profile={profile}\n")
        emit(
            status="running",
            phase="ComfyUIを検証しています",
            message="固定版ComfyUIとモデルファイルを検証しています。",
            progress=2,
            backend="comfy",
            workflow_profile=profile,
            attention_backend=attention_backend,
            acceleration=requested,
            scheduler=dict(scheduler),
            cache=dict(tracker.cache),
        )
        checkpoint = time.monotonic()
        markers = verify_installation(root, variant)
        timings["verification_seconds"] = round(time.monotonic() - checkpoint, 3)

        port = reserve_loopback_port()
        command = build_comfy_command(
            root,
            paths,
            port,
            workflow_profile_name=profile,
        )
        environment = os.environ.copy()
        environment.update(PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1", NO_PROXY="127.0.0.1,localhost")
        environment.pop("PYTHONPATH", None)
        creationflags = 0
        if os.name == "nt":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        checkpoint = time.monotonic()
        process_job = ProcessJob()
        process = subprocess.Popen(
            command,
            cwd=root / ".upstream" / "ComfyUI",
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=environment,
            creationflags=creationflags,
        )
        try:
            process_job.attach(process)
        except Exception:
            process_job.terminate()
            terminate_process_tree(process)
            raise
        assert process.stdout is not None
        pump = LogPump(process.stdout, paths.log)
        client = ComfyClient(
            port=port,
            timeout=2.0,
            directory_roots={"input": paths.input, "output": paths.output, "temp": paths.temp},
            max_upload_bytes=MAX_REFERENCE_BYTES,
        )
        deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
        while True:
            process_job.check()
            _drain_logs(pump, tracker, job_log)
            if process.poll() is not None:
                raise RuntimeError(f"ComfyUI exited during startup ({process.returncode})\n{pump.tail()}")
            try:
                client.health()
                break
            except ComfyClientError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"ComfyUI did not become healthy\n{pump.tail()}")
                time.sleep(0.25)
        verify_loopback_listener_owner(process, port)
        client.timeout = 30.0
        timings["startup_seconds"] = round(time.monotonic() - checkpoint, 3)
        emit(
            status="running",
            phase="入力を準備しています",
            message="参照素材をComfyUIの隔離入力へ準備しています。",
            progress=12,
            backend="comfy",
            workflow_profile=profile,
            attention_backend=attention_backend,
            acceleration=requested,
            scheduler=dict(scheduler),
            cache=dict(tracker.cache),
        )

        checkpoint = time.monotonic()
        staged, stage_dir = stage_request_inputs(request, paths, root)
        timings["staging_seconds"] = round(time.monotonic() - checkpoint, 3)
        output_prefix = f"h3/{job_token}_{uuid.uuid4().hex[:8]}"
        workflow = build_request_workflow(
            request,
            staged,
            requested_acceleration=requested,
            output_prefix=output_prefix,
        )
        workflow_scheduler = {
            "requested": str(workflow["metadata"]["requested_scheduler"]),
            "effective": str(workflow["metadata"]["effective_scheduler"]),
        }
        if workflow_scheduler != scheduler:
            raise RuntimeError(
                f"scheduler resolution changed while building the workflow: "
                f"expected {scheduler}, got {workflow_scheduler}"
            )
        scheduler = workflow_scheduler
        if workflow["metadata"].get("workflow_profile") != profile:
            raise RuntimeError(
                "workflow profile changed while building the workflow: "
                f"expected {profile}, got {workflow['metadata'].get('workflow_profile')}"
            )
        node_info = client.object_info()
        required_nodes = {node["class_type"] for node in workflow["prompt"].values()}
        missing_nodes = sorted(required_nodes.difference(node_info))
        if missing_nodes:
            raise RuntimeError("pinned ComfyUI is missing workflow nodes: " + ", ".join(missing_nodes))
        queue_state = client.queue()
        if queue_state["queue_running"] or queue_state["queue_pending"]:
            raise RuntimeError("isolated ComfyUI queue was not empty before submission")

        submitted_at = time.monotonic()
        response = client.submit_prompt(
            workflow["prompt"],
            client_id="h3-studio-" + job_token,
            extra_data={"h3_studio": workflow["metadata"]},
        )
        prompt_id = response["prompt_id"]
        emit(
            status="running",
            phase="ComfyUIで生成しています",
            message="固定H3 workflowを実行しています。",
            progress=18,
            backend="comfy",
            workflow_profile=profile,
            attention_backend=attention_backend,
            acceleration=requested,
            scheduler=dict(scheduler),
            cache=dict(tracker.cache),
        )
        deadline = submitted_at + float(request.get("timeout_seconds", PROMPT_TIMEOUT_SECONDS))
        final: PromptPoll | None = None
        last_heartbeat = 0.0
        consecutive_poll_errors = 0
        while True:
            process_job.check()
            _drain_logs(pump, tracker, job_log)
            if process.poll() is not None:
                raise RuntimeError(f"ComfyUI exited during generation ({process.returncode})\n{pump.tail()}")
            try:
                poll = client.poll_prompt(prompt_id)
                consecutive_poll_errors = 0
            except ComfyClientError:
                consecutive_poll_errors += 1
                if consecutive_poll_errors >= 5:
                    raise
                time.sleep(POLL_INTERVAL_SECONDS)
                continue
            if poll.terminal:
                final = poll
                break
            now = time.monotonic()
            if now >= deadline:
                try:
                    client.cancel(prompt_id)
                finally:
                    raise TimeoutError(f"ComfyUI prompt timed out after {now - submitted_at:.0f}s")
            if now - last_heartbeat >= 15:
                last_heartbeat = now
                emit(
                    status="running",
                    phase="ComfyUIで生成しています",
                    message="ComfyUI処理は継続中です。",
                    progress=round(tracker.progress, 2),
                    step=tracker.last_step,
                    total_steps=steps,
                    backend="comfy",
                    workflow_profile=profile,
                    attention_backend=attention_backend,
                    acceleration=requested,
                    scheduler=dict(scheduler),
                    cache=dict(tracker.cache),
                )
            time.sleep(POLL_INTERVAL_SECONDS)

        for _ in range(4):
            time.sleep(0.1)
            _drain_logs(pump, tracker, job_log)
        assert final is not None
        if final.state != "completed" or final.history is None:
            raise RuntimeError(_failure_detail(final))
        timings["generation_seconds"] = round(time.monotonic() - submitted_at, 3)
        emit(
            status="running",
            phase="出力を検証しています",
            message="動画と音声を全デコードし、プレビューを作成しています。",
            progress=96,
            backend="comfy",
            workflow_profile=profile,
            attention_backend=attention_backend,
            acceleration=requested,
            scheduler=dict(scheduler),
            cache=dict(tracker.cache),
        )
        checkpoint = time.monotonic()
        media = copy_and_validate_output(client, final.history, request, root)
        timings["validation_seconds"] = round(time.monotonic() - checkpoint, 3)
        timings["total_seconds"] = round(time.monotonic() - started_at, 3)
        emit(
            status="completed",
            phase="完了しました",
            message="動画と音声の生成・検証が完了しました。",
            progress=100,
            result=request.get("result_url"),
            preview=request.get("preview_url"),
            backend="comfy",
            workflow_profile=profile,
            attention_backend=attention_backend,
            acceleration=requested,
            scheduler=dict(scheduler),
            cache=dict(tracker.cache),
            timings=timings,
            media=media,
            diagnostics={
                **markers,
                "log_path": os.fspath(job_log),
                "comfy_pid": process.pid,
                "async_offload_streams": 2,
                "attention_backend": attention_backend,
                "workflow_profile": profile,
            },
        )
    finally:
        terminate_process_tree(process)
        if process_job is not None:
            process_job.terminate()
        if pump is not None:
            pump.close()
            _drain_logs(pump, tracker, job_log)
        if stage_dir is not None and stage_dir.exists() and _is_within(paths.input, stage_dir.resolve()):
            try:
                shutil.rmtree(stage_dir)
            except OSError:
                pass


def _emit_failure(exc: Exception, request: Mapping[str, Any] | None = None) -> None:
    requested = DEFAULT_EASYCACHE_PRESET
    profile = DEFAULT_WORKFLOW_PROFILE
    steps = 0
    scheduler: dict[str, str] | None = None
    if request is not None:
        try:
            requested = _requested_acceleration(request)
            steps = int(request.get("steps", 0))
        except (TypeError, ValueError):
            pass
        try:
            profile = workflow_profile(request)
        except (TypeError, ValueError):
            profile = str(request.get("workflow_profile", "invalid"))
        try:
            scheduler = scheduler_schema(request)
        except (TypeError, ValueError):
            pass
    cache = cache_schema(requested, steps) if requested in EASYCACHE_PRESETS and steps >= 0 else None
    try:
        attention_backend = configured_attention_backend()
    except (TypeError, ValueError):
        # Preserve the original startup error even when the configured backend
        # itself is invalid.  Failure reporting must never mask that exception.
        attention_backend = str(os.environ.get("H3_ATTENTION_BACKEND", "invalid")).strip().lower() or "invalid"
    emit(
        status="failed",
        phase="生成に失敗しました",
        message=str(exc) or exc.__class__.__name__,
        progress=0,
        backend="comfy",
        workflow_profile=profile,
        attention_backend=attention_backend,
        acceleration=requested,
        scheduler=scheduler,
        cache=cache,
    )
    traceback.print_exc()


def main() -> int:
    parser = argparse.ArgumentParser(description="MiniMax H3 pinned ComfyUI worker")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--request", type=Path)
    group.add_argument("--serve", action="store_true")
    args = parser.parse_args()
    if args.request is not None:
        request: dict[str, Any] | None = None
        try:
            request = _load_request(args.request.resolve())
            run(request)
            return 0
        except Exception as exc:  # protocol boundary: always convert to one terminal event
            _emit_failure(exc, request)
            return 1

    for raw_line in sys.stdin:
        if not raw_line.strip():
            continue
        request = None
        try:
            command = json.loads(raw_line)
            request = _load_request(Path(command["request"]).resolve())
            run(request)
        except Exception as exc:  # keep stdin service alive for a later clean job
            _emit_failure(exc, request)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
