"""Typed, standard-library-only contracts for the local H3 Studio adapter."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterator, Mapping, Protocol, Sequence


H3_ORIGIN = "http://127.0.0.1:7863"
H3_MUTATION_HEADER = "X-H3-Studio-Request"
H3_MUTATION_HEADER_VALUE = "1"

H3_MODES = frozenset({"t2v", "i2v", "first_last", "omni"})
H3_STYLES = frozenset(
    {"natural", "cinematic", "photoreal", "anime", "illustration", "product", "moody"}
)
H3_ACCELERATIONS = frozenset({"off", "community", "conservative", "balanced"})
H3_REFERENCE_IMAGE_SIZES = frozenset({"match", "max"})
H3_PROMPT_PROCESSING_MODES = frozenset({"community", "raw_en"})
H3_AUDIO_POLICIES = frozenset({"dialogue_priority", "full_content"})
H3_AUDIO_PRESETS = frozenset(
    {"auto", "dialogue", "ambience", "effects", "music", "quiet"}
)
H3_MUSIC_POLICIES = frozenset({"auto", "none", "subtle", "prominent"})
H3_FRAME_COUNTS = frozenset({124, 243, 345})


class H3AdapterError(RuntimeError):
    """Base class for adapter errors that are safe to handle at the sidecar boundary."""


class H3ContractError(H3AdapterError, ValueError):
    """The Story Studio request or H3 response violated the shared contract."""


class H3AssetError(H3AdapterError, ValueError):
    """An asset ID was unknown, unsafe, missing, or outside the allowed roots."""


class H3HTTPError(H3AdapterError):
    """H3 Studio returned a non-success HTTP response."""

    def __init__(self, status: int, method: str, url: str, detail: str = "") -> None:
        suffix = f": {detail}" if detail else ""
        super().__init__(f"H3 Studio returned HTTP {status} for {method} {url}{suffix}")
        self.status = status
        self.method = method
        self.url = url
        self.detail = detail


class H3TransportError(H3AdapterError):
    """The loopback HTTP exchange could not be completed."""


class H3PollTimeout(H3AdapterError, TimeoutError):
    """Polling did not reach a terminal job state before the configured deadline."""


class H3UnsupportedOperation(H3AdapterError):
    """The pinned H3 REST API does not expose the requested operation."""


class RequestBody(Protocol):
    """Replayable body consumed by a transport without buffering large media files."""

    @property
    def content_length(self) -> int: ...

    def iter_bytes(self, chunk_size: int = 1024 * 1024) -> Iterator[bytes]: ...


@dataclass(frozen=True)
class TransportRequest:
    method: str
    url: str
    headers: Mapping[str, str] = field(default_factory=dict)
    body: bytes | RequestBody | None = None


@dataclass(frozen=True)
class TransportResponse:
    status: int
    headers: Mapping[str, str] = field(default_factory=dict)
    body: bytes = b""

    def header(self, name: str, default: str | None = None) -> str | None:
        folded = name.casefold()
        for key, value in self.headers.items():
            if key.casefold() == folded:
                return value
        return default


class Transport(Protocol):
    def send(self, request: TransportRequest) -> TransportResponse: ...


def _string(payload: Mapping[str, Any], key: str, *, allow_empty: bool = False) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise H3ContractError(f"{key} must be a string")
    if not allow_empty and not value.strip():
        raise H3ContractError(f"{key} must not be empty")
    return value


def _integer(payload: Mapping[str, Any], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise H3ContractError(f"{key} must be an integer")
    return value


def _choice(payload: Mapping[str, Any], key: str, allowed: frozenset[str]) -> str:
    value = _string(payload, key)
    if value not in allowed:
        raise H3ContractError(f"unsupported {key}: {value!r}")
    return value


@dataclass(frozen=True)
class H3JobFields:
    """Scalar multipart fields produced by the TypeScript H3 contract."""

    mode: str
    style: str
    prompt: str
    width: int
    height: int
    num_frames: int
    steps: int
    seed: int
    acceleration: str
    ref_image_size: str
    prompt_processing_mode: str
    standalone_audio_policy: str
    audio_preset: str
    dialogue: str
    soundscape: str
    music_policy: str
    audio_gain_db: int

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> H3JobFields:
        mode = _choice(payload, "mode", H3_MODES)
        style = _choice(payload, "style", H3_STYLES)
        prompt = _string(payload, "prompt")
        width = _integer(payload, "width")
        height = _integer(payload, "height")
        num_frames = _integer(payload, "num_frames")
        steps = _integer(payload, "steps")
        seed = _integer(payload, "seed")
        acceleration = _choice(payload, "acceleration", H3_ACCELERATIONS)
        ref_image_size = _choice(
            payload, "ref_image_size", H3_REFERENCE_IMAGE_SIZES
        )
        prompt_processing_mode = _choice(
            payload, "prompt_processing_mode", H3_PROMPT_PROCESSING_MODES
        )
        standalone_audio_policy = _choice(
            payload, "standalone_audio_policy", H3_AUDIO_POLICIES
        )
        audio_preset = _choice(payload, "audio_preset", H3_AUDIO_PRESETS)
        dialogue = _string(payload, "dialogue", allow_empty=True)
        soundscape = _string(payload, "soundscape", allow_empty=True)
        music_policy = _choice(payload, "music_policy", H3_MUSIC_POLICIES)
        audio_gain_db = _integer(payload, "audio_gain_db")

        for name, value in (("width", width), ("height", height)):
            if not 192 <= value <= 1344 or value % 32:
                raise H3ContractError(
                    f"{name} must be a multiple of 32 from 192 through 1344"
                )
        if num_frames not in H3_FRAME_COUNTS:
            raise H3ContractError("num_frames must be one of 124, 243, or 345")
        if not 2 <= steps <= 40:
            raise H3ContractError("steps must be from 2 through 40")
        if not 0 <= seed <= 2**53 - 1:
            raise H3ContractError(
                "seed must be from 0 through JavaScript's maximum safe integer"
            )
        if not 1 <= len(prompt.strip()) <= 4_000:
            raise H3ContractError("prompt must contain 1 through 4000 characters")
        if len(dialogue) > 800 or len(soundscape) > 800:
            raise H3ContractError("dialogue and soundscape are limited to 800 characters")
        if not -24 <= audio_gain_db <= 12:
            raise H3ContractError("audio_gain_db must be from -24 through 12")

        if prompt_processing_mode == "raw_en" and (
            style != "natural"
            or audio_preset != "auto"
            or dialogue.strip()
            or soundscape.strip()
            or music_policy != "auto"
        ):
            raise H3ContractError(
                "raw_en requires natural style and empty/auto auxiliary audio fields"
            )

        return cls(
            mode=mode,
            style=style,
            prompt=prompt.strip(),
            width=width,
            height=height,
            num_frames=num_frames,
            steps=steps,
            seed=seed,
            acceleration=acceleration,
            ref_image_size=ref_image_size,
            prompt_processing_mode=prompt_processing_mode,
            standalone_audio_policy=standalone_audio_policy,
            audio_preset=audio_preset,
            dialogue=dialogue.strip(),
            soundscape=soundscape.strip(),
            music_policy=music_policy,
            audio_gain_db=audio_gain_db,
        )

    def as_form_fields(self) -> tuple[tuple[str, str], ...]:
        return (
            ("mode", self.mode),
            ("style", self.style),
            ("prompt", self.prompt),
            ("width", str(self.width)),
            ("height", str(self.height)),
            ("num_frames", str(self.num_frames)),
            ("steps", str(self.steps)),
            ("seed", str(self.seed)),
            ("acceleration", self.acceleration),
            ("ref_image_size", self.ref_image_size),
            ("prompt_processing_mode", self.prompt_processing_mode),
            ("standalone_audio_policy", self.standalone_audio_policy),
            ("audio_preset", self.audio_preset),
            ("dialogue", self.dialogue),
            ("soundscape", self.soundscape),
            ("music_policy", self.music_policy),
            ("audio_gain_db", str(self.audio_gain_db)),
        )


def _nullable_asset_id(payload: Mapping[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise H3ContractError(f"{key} must be null or a non-empty asset ID")
    return value.strip()


@dataclass(frozen=True)
class H3UploadBindings:
    first_image_asset_id: str | None
    last_image_asset_id: str | None
    reference_asset_ids: tuple[str, ...]

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> H3UploadBindings:
        references = payload.get("reference_asset_ids", ())
        if isinstance(references, (str, bytes)) or not isinstance(references, Sequence):
            raise H3ContractError("reference_asset_ids must be an array")
        normalized: list[str] = []
        for index, value in enumerate(references):
            if not isinstance(value, str) or not value.strip():
                raise H3ContractError(
                    f"reference_asset_ids[{index}] must be a non-empty string"
                )
            normalized.append(value.strip())
        if len(normalized) > 12:
            raise H3ContractError("H3 Omni accepts at most 12 references")
        if len(set(normalized)) != len(normalized):
            raise H3ContractError("reference_asset_ids must not contain duplicates")
        return cls(
            first_image_asset_id=_nullable_asset_id(payload, "first_image_asset_id"),
            last_image_asset_id=_nullable_asset_id(payload, "last_image_asset_id"),
            reference_asset_ids=tuple(normalized),
        )


@dataclass(frozen=True)
class H3SubmitRequest:
    shot_id: str
    fields: H3JobFields
    uploads: H3UploadBindings

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> H3SubmitRequest:
        shot_id = payload.get("shotId")
        fields = payload.get("fields")
        uploads = payload.get("uploads")
        if not isinstance(shot_id, str) or not shot_id.strip():
            raise H3ContractError("shotId must be a non-empty string")
        if not isinstance(fields, Mapping):
            raise H3ContractError("fields must be an object")
        if not isinstance(uploads, Mapping):
            raise H3ContractError("uploads must be an object")
        return cls(
            shot_id=shot_id.strip(),
            fields=H3JobFields.from_mapping(fields),
            uploads=H3UploadBindings.from_mapping(uploads),
        )


class H3JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"

    @property
    def terminal(self) -> bool:
        return self in {
            H3JobStatus.COMPLETED,
            H3JobStatus.FAILED,
            H3JobStatus.CANCELLED,
            H3JobStatus.INTERRUPTED,
        }


@dataclass(frozen=True)
class H3MediaMetadata:
    bytes: int | None = None
    duration_seconds: float | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    width: int | None = None
    height: int | None = None
    video_frames: int | None = None
    audio_sample_rate: int | None = None
    audio_channels: int | None = None
    raw: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any] | None) -> H3MediaMetadata | None:
        if payload is None:
            return None
        return cls(
            bytes=payload.get("bytes") if isinstance(payload.get("bytes"), int) else None,
            duration_seconds=(
                float(payload["duration_seconds"])
                if isinstance(payload.get("duration_seconds"), (int, float))
                else None
            ),
            video_codec=(
                payload.get("video_codec")
                if isinstance(payload.get("video_codec"), str)
                else None
            ),
            audio_codec=(
                payload.get("audio_codec")
                if isinstance(payload.get("audio_codec"), str)
                else None
            ),
            width=payload.get("width") if isinstance(payload.get("width"), int) else None,
            height=payload.get("height") if isinstance(payload.get("height"), int) else None,
            video_frames=(
                payload.get("video_frames")
                if isinstance(payload.get("video_frames"), int)
                else None
            ),
            audio_sample_rate=(
                payload.get("audio_sample_rate")
                if isinstance(payload.get("audio_sample_rate"), int)
                else None
            ),
            audio_channels=(
                payload.get("audio_channels")
                if isinstance(payload.get("audio_channels"), int)
                else None
            ),
            raw=dict(payload),
        )


def _optional_string(payload: Mapping[str, Any], key: str) -> str | None:
    value = payload.get(key)
    return value if isinstance(value, str) and value else None


@dataclass(frozen=True)
class H3Job:
    id: str
    status: H3JobStatus
    phase: str
    message: str
    progress: float
    result_url: str | None
    preview_url: str | None
    can_cancel: bool
    media: H3MediaMetadata | None
    raw: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> H3Job:
        job_id = payload.get("id")
        status_value = payload.get("status")
        if not isinstance(job_id, str) or not job_id:
            raise H3ContractError("H3 job response has no valid id")
        try:
            status = H3JobStatus(status_value)
        except (TypeError, ValueError) as exc:
            raise H3ContractError(f"unsupported H3 job status: {status_value!r}") from exc
        progress_value = payload.get("progress", 0)
        if isinstance(progress_value, bool) or not isinstance(progress_value, (int, float)):
            raise H3ContractError("H3 job progress must be numeric")
        media_payload = payload.get("media")
        if media_payload is not None and not isinstance(media_payload, Mapping):
            raise H3ContractError("H3 job media must be an object")
        return cls(
            id=job_id,
            status=status,
            phase=payload.get("phase") if isinstance(payload.get("phase"), str) else "",
            message=(
                payload.get("message") if isinstance(payload.get("message"), str) else ""
            ),
            progress=float(progress_value),
            result_url=_optional_string(payload, "result"),
            preview_url=_optional_string(payload, "preview"),
            can_cancel=(
                payload.get("can_cancel")
                if isinstance(payload.get("can_cancel"), bool)
                else not status.terminal
            ),
            media=H3MediaMetadata.from_payload(media_payload),
            raw=dict(payload),
        )


@dataclass(frozen=True)
class H3Capabilities:
    backend: str
    ready: bool
    local_only: bool
    modes: Mapping[str, bool]
    model_files: Mapping[str, bool]
    raw: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> H3Capabilities:
        modes = payload.get("modes", {})
        model_files = payload.get("model_files", {})
        if not isinstance(modes, Mapping) or not isinstance(model_files, Mapping):
            raise H3ContractError("H3 capabilities modes/model_files must be objects")
        return cls(
            backend=(
                payload.get("backend") if isinstance(payload.get("backend"), str) else ""
            ),
            ready=payload.get("ready") is True,
            local_only=payload.get("local_only") is True,
            modes={str(key): value is True for key, value in modes.items()},
            model_files={str(key): value is True for key, value in model_files.items()},
            raw=dict(payload),
        )


@dataclass(frozen=True)
class H3State:
    capabilities: H3Capabilities
    active_job_id: str | None
    jobs: tuple[H3Job, ...]
    system: Mapping[str, Any]
    raw: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> H3State:
        capabilities = payload.get("capabilities")
        jobs = payload.get("jobs", ())
        system = payload.get("system", {})
        if not isinstance(capabilities, Mapping):
            raise H3ContractError("H3 state has no capabilities object")
        if isinstance(jobs, (str, bytes)) or not isinstance(jobs, Sequence):
            raise H3ContractError("H3 state jobs must be an array")
        if not isinstance(system, Mapping):
            raise H3ContractError("H3 state system must be an object")
        active = payload.get("active_job_id")
        if active is not None and not isinstance(active, str):
            raise H3ContractError("active_job_id must be null or a string")
        converted_jobs: list[H3Job] = []
        for item in jobs:
            if not isinstance(item, Mapping):
                raise H3ContractError("each H3 state job must be an object")
            converted_jobs.append(H3Job.from_payload(item))
        return cls(
            capabilities=H3Capabilities.from_payload(capabilities),
            active_job_id=active,
            jobs=tuple(converted_jobs),
            system=dict(system),
            raw=dict(payload),
        )


@dataclass(frozen=True)
class H3Result:
    job_id: str
    content: bytes
    content_type: str
    content_disposition: str | None = None
