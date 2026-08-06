"""Safe Project Core asset creation and local file persistence."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import os
from pathlib import Path
import secrets
from typing import Any, Mapping, Protocol

from .h3_story_core.models import EntityMutation, ProjectPatch
from .h3_story_core.repository import (
    ProjectBoundaryError,
    RevisionConflictError,
    ValidationError,
)


MAX_ASSET_FILE_BYTES = 128 * 1024 * 1024
ASSET_CATEGORIES = frozenset(("character", "environment", "prop", "reference"))
ASSET_SOURCES = frozenset(("human", "codex_gpt_image_2"))

# One canonical browser-safe MIME per extension.  The signature check below
# prevents a renamed executable or arbitrary local file from entering the
# Project asset vault merely by claiming one of these MIME values.
ASSET_MEDIA_TYPES: Mapping[str, tuple[str, str]] = {
    ".png": ("image/png", "image"),
    ".jpg": ("image/jpeg", "image"),
    ".jpeg": ("image/jpeg", "image"),
    ".webp": ("image/webp", "image"),
    ".gif": ("image/gif", "image"),
    ".mp4": ("video/mp4", "video"),
    ".webm": ("video/webm", "video"),
    ".wav": ("audio/wav", "audio"),
    ".mp3": ("audio/mpeg", "audio"),
    ".m4a": ("audio/mp4", "audio"),
    ".ogg": ("audio/ogg", "audio"),
    ".flac": ("audio/flac", "audio"),
}


class AssetServiceError(RuntimeError):
    """Base class for asset creation failures."""


class AssetValidationError(AssetServiceError):
    pass


class AssetMediaTypeError(AssetServiceError):
    pass


class AssetTooLargeError(AssetServiceError):
    def __init__(self, size: int, maximum: int) -> None:
        self.size = size
        self.maximum = maximum
        super().__init__(f"asset file is {size} bytes; maximum is {maximum} bytes")


class AssetPathError(AssetServiceError):
    pass


class AssetStorageError(AssetServiceError):
    pass


@dataclass(frozen=True, slots=True)
class AssetUpload:
    file_name: str
    declared_mime_type: str
    content: bytes


@dataclass(frozen=True, slots=True)
class AssetCreateSpec:
    session_id: str
    expected_revision: int
    name: str
    category: str
    role: str
    caption: str
    prompt_caption: str
    locked: bool
    source: str


@dataclass(frozen=True, slots=True)
class AssetCreateResult:
    asset_id: str
    revision: int
    digest: str
    payload: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "revision": self.revision,
            "digest": self.digest,
            "asset": dict(self.payload),
        }


class RepositoryLike(Protocol):
    def get_context(self, session_id: str, project_id: str | None = None) -> Any: ...

    def commit_patch(self, *args: Any, **kwargs: Any) -> Any: ...


def _member(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _text(
    value: str,
    label: str,
    *,
    maximum: int,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str) or "\x00" in value:
        raise AssetValidationError(f"{label} must be a string without NUL")
    normalized = value.strip()
    if not allow_empty and not normalized:
        raise AssetValidationError(f"{label} must be non-empty")
    if len(normalized) > maximum:
        raise AssetValidationError(f"{label} exceeds {maximum} characters")
    return normalized


def _is_contained(parent: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(parent)
        return True
    except ValueError:
        return False


def _has_expected_signature(extension: str, content: bytes) -> bool:
    if extension == ".png":
        return content.startswith(b"\x89PNG\r\n\x1a\n")
    if extension in {".jpg", ".jpeg"}:
        return content.startswith(b"\xff\xd8\xff")
    if extension == ".webp":
        return len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP"
    if extension == ".gif":
        return content.startswith((b"GIF87a", b"GIF89a"))
    if extension in {".mp4", ".m4a"}:
        return len(content) >= 12 and content[4:8] == b"ftyp"
    if extension == ".webm":
        return content.startswith(b"\x1a\x45\xdf\xa3")
    if extension == ".wav":
        return len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WAVE"
    if extension == ".mp3":
        return content.startswith(b"ID3") or (
            len(content) >= 2 and content[0] == 0xFF and content[1] & 0xE0 == 0xE0
        )
    if extension == ".ogg":
        return content.startswith(b"OggS")
    if extension == ".flac":
        return content.startswith(b"fLaC")
    return False


class AssetService:
    def __init__(
        self,
        repository: RepositoryLike,
        local_root: str | os.PathLike[str],
        *,
        max_file_bytes: int = MAX_ASSET_FILE_BYTES,
    ) -> None:
        self._repository = repository
        self._local_root = Path(local_root).expanduser().resolve()
        if (
            isinstance(max_file_bytes, bool)
            or not isinstance(max_file_bytes, int)
            or max_file_bytes <= 0
        ):
            raise ValueError("max_file_bytes must be a positive integer")
        self._max_file_bytes = max_file_bytes

    def create_asset(
        self,
        *,
        project_id: str,
        spec: AssetCreateSpec,
        upload: AssetUpload | None = None,
    ) -> AssetCreateResult:
        project_id = _text(project_id, "project_id", maximum=128)
        if not isinstance(spec, AssetCreateSpec):
            raise AssetValidationError("spec must be an AssetCreateSpec")
        session_id = _text(spec.session_id, "session_id", maximum=128)
        if (
            isinstance(spec.expected_revision, bool)
            or not isinstance(spec.expected_revision, int)
            or spec.expected_revision < 0
        ):
            raise AssetValidationError(
                "expected_revision must be a non-negative integer"
            )
        name = _text(spec.name, "name", maximum=300)
        category = _text(spec.category, "category", maximum=32)
        if category not in ASSET_CATEGORIES:
            raise AssetValidationError(f"unsupported asset category: {category}")
        source = _text(spec.source, "source", maximum=64)
        if source not in ASSET_SOURCES:
            raise AssetValidationError(f"unsupported asset source: {source}")
        if not isinstance(spec.locked, bool):
            raise AssetValidationError("locked must be a boolean")
        role = _text(spec.role, "role", maximum=500, allow_empty=True)
        caption = _text(spec.caption, "caption", maximum=20_000, allow_empty=True)
        prompt_caption = _text(
            spec.prompt_caption,
            "prompt_caption",
            maximum=20_000,
            allow_empty=True,
        )

        context = self._repository.get_context(session_id, project_id)
        bound_project_id = str(_member(context, "project_id", ""))
        if bound_project_id != project_id:
            raise ProjectBoundaryError(
                session_id=session_id,
                bound_project_id=bound_project_id,
                requested_project_id=project_id,
            )
        session_revision = int(_member(context, "revision", -1))
        actual_revision = int(_member(context, "current_project_revision", -1))
        if (
            spec.expected_revision != session_revision
            or spec.expected_revision != actual_revision
            or _member(context, "is_current", False) is not True
        ):
            raise RevisionConflictError(
                project_id=project_id,
                session_id=session_id,
                expected_revision=spec.expected_revision,
                actual_revision=actual_revision,
                session_revision=session_revision,
            )

        asset_id = f"asset_{secrets.token_hex(16)}"
        payload: dict[str, Any] = {
            "kind": category,
            "category": category,
            "name": name,
            "role": role,
            "caption": caption,
            "prompt_caption": prompt_caption,
            "locked": spec.locked,
            "source": source,
            "tone": {
                "character": "ember",
                "environment": "night",
                "prop": "signal",
                "reference": "steel",
            }[category],
            "revision_hint": spec.expected_revision,
        }

        final_path: Path | None = None
        temporary_path: Path | None = None
        assets_directory: Path | None = None
        assets_directory_created = False
        committed = False
        try:
            if upload is not None:
                upload_info = self._validate_upload(upload)
                project_directory = self._project_directory(context)
                assets_directory = project_directory / "assets"
                if assets_directory.exists():
                    assets_directory = assets_directory.resolve(strict=True)
                    if not assets_directory.is_dir() or not _is_contained(
                        project_directory, assets_directory
                    ):
                        raise AssetPathError(
                            "project assets directory escapes the project boundary"
                        )
                else:
                    assets_directory.mkdir(parents=False, exist_ok=False)
                    assets_directory_created = True
                    assets_directory = assets_directory.resolve(strict=True)

                extension, mime_type, media_kind, original_file_name = upload_info
                final_path = (assets_directory / f"{asset_id}{extension}").resolve()
                if not _is_contained(assets_directory, final_path) or final_path.exists():
                    raise AssetPathError("generated asset path is unsafe or already exists")
                temporary_path = (
                    assets_directory
                    / f".{asset_id}.{secrets.token_hex(8)}.upload"
                )
                with temporary_path.open("xb") as handle:
                    handle.write(upload.content)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary_path, final_path)
                temporary_path = None
                relative_path = final_path.relative_to(project_directory).as_posix()
                payload.update(
                    {
                        "media_kind": media_kind,
                        "relative_path": relative_path,
                        "mime_type": mime_type,
                        "byte_size": len(upload.content),
                        "sha256": sha256(upload.content).hexdigest(),
                        "original_file_name": original_file_name,
                    }
                )

            result = self._repository.commit_patch(
                session_id=session_id,
                project_id=project_id,
                expected_revision=spec.expected_revision,
                patch=ProjectPatch(
                    entities=(EntityMutation("asset", asset_id, payload),),
                    message=f"Create asset: {asset_id}",
                ),
            )
            committed = True
            return AssetCreateResult(
                asset_id=asset_id,
                revision=int(_member(result, "revision")),
                digest=str(_member(result, "digest")),
                payload=payload,
            )
        except (AssetServiceError, ProjectBoundaryError, RevisionConflictError, ValidationError):
            raise
        except OSError as error:
            raise AssetStorageError(f"asset file persistence failed: {error}") from error
        finally:
            if not committed:
                for path in (temporary_path, final_path):
                    if path is not None:
                        try:
                            path.unlink(missing_ok=True)
                        except OSError:
                            pass
                if assets_directory_created and assets_directory is not None:
                    try:
                        assets_directory.rmdir()
                    except OSError:
                        pass

    def _project_directory(self, context: Any) -> Path:
        project_root = _member(context, "project_root")
        if not isinstance(project_root, str) or not project_root.strip():
            raise AssetPathError("Project Core returned an invalid project_root")
        relative = Path(project_root)
        if (
            relative.is_absolute()
            or relative.drive
            or ".." in relative.parts
            or "\x00" in project_root
        ):
            raise AssetPathError("project_root is unsafe")
        try:
            project_directory = (self._local_root / relative).resolve(strict=True)
        except OSError as error:
            raise AssetPathError("project directory is unavailable") from error
        if not project_directory.is_dir() or not _is_contained(
            self._local_root, project_directory
        ):
            raise AssetPathError("project directory escapes the local root")
        return project_directory

    def _validate_upload(self, upload: AssetUpload) -> tuple[str, str, str, str]:
        if not isinstance(upload, AssetUpload):
            raise AssetValidationError("upload must be an AssetUpload")
        file_name = _text(upload.file_name, "file_name", maximum=500)
        original_file_name = Path(file_name).name
        if original_file_name in {"", ".", ".."}:
            raise AssetValidationError("file_name is invalid")
        if not isinstance(upload.content, bytes) or not upload.content:
            raise AssetValidationError("asset file must contain bytes")
        if len(upload.content) > self._max_file_bytes:
            raise AssetTooLargeError(len(upload.content), self._max_file_bytes)
        extension = Path(original_file_name).suffix.casefold()
        media = ASSET_MEDIA_TYPES.get(extension)
        if media is None:
            raise AssetMediaTypeError(f"unsupported asset extension: {extension or '(none)'}")
        expected_mime, media_kind = media
        declared_mime = _text(
            upload.declared_mime_type,
            "declared_mime_type",
            maximum=200,
        ).split(";", 1)[0].strip().casefold()
        if declared_mime != expected_mime:
            raise AssetMediaTypeError(
                f"asset MIME {declared_mime!r} does not match {extension} ({expected_mime})"
            )
        if not _has_expected_signature(extension, upload.content):
            raise AssetMediaTypeError(
                f"asset bytes do not match the expected {extension} signature"
            )
        return extension, expected_mime, media_kind, original_file_name


__all__ = [
    "ASSET_CATEGORIES",
    "ASSET_MEDIA_TYPES",
    "ASSET_SOURCES",
    "AssetCreateResult",
    "AssetCreateSpec",
    "AssetMediaTypeError",
    "AssetPathError",
    "AssetService",
    "AssetServiceError",
    "AssetStorageError",
    "AssetTooLargeError",
    "AssetUpload",
    "AssetValidationError",
    "MAX_ASSET_FILE_BYTES",
]
