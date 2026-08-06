"""Revision-safe bridge between Project Core and the existing H3 renderer."""

from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
import os
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any, Mapping, Protocol, Sequence
from uuid import uuid4

from .h3_adapter import (
    AssetDescriptor,
    AssetResolver,
    H3AssetError,
    H3Client,
    H3Job,
    H3JobStatus,
    H3SubmitRequest,
    media_kind,
)
from .h3_story_core.models import EntityMutation, ProjectPatch


class ProjectRepositoryProtocol(Protocol):
    def get_context(self, session_id: str, project_id: str | None = None): ...

    def commit_patch(
        self,
        session_id: str,
        project_id: str,
        expected_revision: int,
        patch: ProjectPatch,
    ): ...


class RenderServiceError(RuntimeError):
    """Base error for the Story-to-H3 orchestration boundary."""


class RenderReconciliationRequired(RenderServiceError):
    """H3 accepted a job, but Project Core could not record the acceptance."""

    def __init__(self, local_job_id: str, h3_job_id: str, message: str) -> None:
        super().__init__(message)
        self.local_job_id = local_job_id
        self.h3_job_id = h3_job_id


@dataclass(frozen=True, slots=True)
class _ValidatedAsset:
    asset_id: str
    name: str
    relative_path: str
    path: Path
    mime_type: str
    byte_size: int
    sha256: str
    kind: str
    payload: Mapping[str, Any]

    def audit_binding(
        self,
        *,
        slot: str,
        source_index: int | None,
        ordinal: int,
        tag: str,
    ) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "asset_name": self.name,
            "attached": True,
            "slot": slot,
            "source_index": source_index,
            "kind": self.kind,
            "ordinal": ordinal,
            "tag": tag,
            "relative_path": self.relative_path,
            "mime_type": self.mime_type,
            "byte_size": self.byte_size,
            "sha256": self.sha256,
        }


@dataclass(frozen=True, slots=True)
class _PreparedStoryRequest:
    request: H3SubmitRequest
    authoring_prompt: str
    resolved_prompt: str
    bindings: tuple[Mapping[str, Any], ...]
    asset_resolver: AssetResolver
    snapshot_directory: tempfile.TemporaryDirectory[str] | None = None

    def cleanup(self) -> None:
        if self.snapshot_directory is not None:
            self.snapshot_directory.cleanup()


_SHA256_RE = re.compile(r"^(?:sha256:)?(?P<digest>[0-9a-fA-F]{64})$")
_UNRESOLVED_MENTION_RE = re.compile(
    r"@(?P<name>[^\s、。,.!?！？「」『』（）()\[\]{}<>/\\:;；：]+)"
)
_TAG_LABELS = {"image": "Picture", "video": "Video", "audio": "Audio"}
_MENTION_BOUNDARY_PUNCTUATION = frozenset(
    "、。,.!?！？「」『』（）()[]{}<>/\\:;；：・…—–-'’\"@"
)
_JAPANESE_MENTION_PARTICLES = tuple(
    sorted(
        {
            "からの",
            "として",
            "までの",
            "について",
            "によって",
            "による",
            "へと",
            "への",
            "から",
            "まで",
            "より",
            "との",
            "での",
            "には",
            "では",
            "って",
            "は",
            "が",
            "を",
            "に",
            "へ",
            "と",
            "で",
            "も",
            "の",
            "や",
            "か",
            "ね",
            "よ",
            "ぞ",
        },
        key=len,
        reverse=True,
    )
)


@dataclass(frozen=True, slots=True)
class RenderSubmission:
    project_id: str
    shot_id: str
    take_id: str
    local_job_id: str
    h3_job_id: str
    revision: int
    status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "shot_id": self.shot_id,
            "take_id": self.take_id,
            "local_job_id": self.local_job_id,
            "h3_job_id": self.h3_job_id,
            "revision": self.revision,
            "status": self.status,
        }


@dataclass(frozen=True, slots=True)
class RenderSyncResult:
    project_id: str
    shot_id: str
    take_id: str
    local_job_id: str
    h3_job_id: str
    revision: int
    status: str
    progress: float
    artifact_id: str | None = None
    artifact_relative_path: str | None = None
    artifact_sha256: str | None = None
    end_frame_asset_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "shot_id": self.shot_id,
            "take_id": self.take_id,
            "local_job_id": self.local_job_id,
            "h3_job_id": self.h3_job_id,
            "revision": self.revision,
            "status": self.status,
            "progress": self.progress,
            "artifact_id": self.artifact_id,
            "artifact_relative_path": self.artifact_relative_path,
            "artifact_sha256": self.artifact_sha256,
            "end_frame_asset_id": self.end_frame_asset_id,
        }


def _h3_job_payload(job: H3Job) -> dict[str, Any]:
    return {
        "h3_job_id": job.id,
        "status": job.status.value,
        "phase": job.phase,
        "message": job.message,
        "progress": job.progress,
        "result_url": job.result_url,
        "preview_url": job.preview_url,
        "can_cancel": job.can_cancel,
    }


def _is_below(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


_METADATA_QUOTE_TRANSLATION = str.maketrans(
    {
        '"': "＂",
        "「": "［",
        "」": "］",
        "『": "［",
        "』": "］",
        "“": "［",
        "”": "］",
        "[": "［",
        "]": "］",
    }
)


def _metadata_literal(value: str) -> str:
    """Keep asset prose out of H3's ordinary-dialogue quote grammar."""

    return value.strip().translate(_METADATA_QUOTE_TRANSLATION).rstrip("。")


def _metadata_description(name: str, payload: Mapping[str, Any]) -> str:
    caption = payload.get("caption")
    prompt_caption = payload.get("prompt_caption")
    details: list[str] = []
    if isinstance(caption, str) and caption.strip():
        details.append(f"説明: {_metadata_literal(caption)}")
    if isinstance(prompt_caption, str) and prompt_caption.strip():
        details.append(f"H3向け特徴: {_metadata_literal(prompt_caption)}")
    suffix = f"（{'。'.join(details)}）" if details else ""
    return f"素材[名前: {_metadata_literal(name)}]{suffix}"


def _has_mention_boundary(text: str, index: int) -> bool:
    if index >= len(text):
        return True
    following = text[index]
    if following.isspace() or following in _MENTION_BOUNDARY_PUNCTUATION:
        return True
    return any(text.startswith(particle, index) for particle in _JAPANESE_MENTION_PARTICLES)


def _unresolved_mention_name(text: str, index: int) -> str:
    if text.startswith("@{", index):
        end = text.find("}", index + 2)
        if end >= 0:
            return text[index + 2 : end] or "{...}"
    match = _UNRESOLVED_MENTION_RE.match(text, index)
    if match is not None:
        return match.group("name")
    return text[index + 1 : index + 17] or "<empty>"


class RenderService:
    """Records render intent before H3 submission and reconciles every result."""

    def __init__(
        self,
        repository: ProjectRepositoryProtocol,
        h3_client: H3Client,
        local_root: str | Path,
    ) -> None:
        self._repository = repository
        self._h3 = h3_client
        self._local_root = Path(local_root).expanduser().resolve(strict=True)

    def _project_directory(self, project_root: Any) -> Path:
        if not isinstance(project_root, str) or not project_root.strip():
            raise RenderServiceError("Project Core returned an invalid project root")
        relative = Path(project_root)
        if (
            relative.is_absolute()
            or bool(relative.drive)
            or bool(relative.anchor)
            or any(part == ".." for part in relative.parts)
        ):
            raise RenderServiceError("project asset root escaped the local Story root")
        try:
            directory = (self._local_root / relative).resolve(strict=True)
        except (FileNotFoundError, NotADirectoryError, OSError) as error:
            raise RenderServiceError("registered project asset root is unavailable") from error
        if not _is_below(directory, self._local_root) or not directory.is_dir():
            raise RenderServiceError("project asset root escaped the local Story root")
        return directory

    @staticmethod
    def _asset_entities(context: Any) -> dict[str, Any]:
        entities = getattr(context, "entities", ())
        result: dict[str, Any] = {}
        for entity in entities:
            if getattr(entity, "entity_type", None) != "asset":
                continue
            asset_id = getattr(entity, "entity_id", None)
            payload = getattr(entity, "payload", None)
            if not isinstance(asset_id, str) or not asset_id or not isinstance(payload, Mapping):
                raise RenderServiceError("Project Core returned an invalid asset entity")
            if asset_id in result:
                raise RenderServiceError(f"duplicate Project Core asset ID: {asset_id}")
            result[asset_id] = entity
        return result

    @staticmethod
    def _registered_digest(payload: Mapping[str, Any], asset_id: str) -> str:
        value = payload.get("sha256")
        if not isinstance(value, str):
            raise H3AssetError(f"asset {asset_id!r} has no registered SHA-256")
        match = _SHA256_RE.fullmatch(value.strip())
        if match is None:
            raise H3AssetError(f"asset {asset_id!r} has an invalid registered SHA-256")
        return match.group("digest").casefold()

    def _validate_attached_asset(
        self,
        asset_id: str,
        *,
        project_directory: Path,
        asset_entities: Mapping[str, Any],
    ) -> _ValidatedAsset:
        entity = asset_entities.get(asset_id)
        if entity is None:
            raise H3AssetError(
                f"asset {asset_id!r} is not registered in the exact Project context"
            )
        payload = entity.payload
        relative_value = payload.get("relative_path")
        if not isinstance(relative_value, str) or not relative_value.strip():
            raise H3AssetError(f"asset {asset_id!r} has no registered relative_path")
        relative = Path(relative_value.strip())
        if (
            relative.is_absolute()
            or bool(relative.drive)
            or bool(relative.anchor)
            or any(part == ".." for part in relative.parts)
        ):
            raise H3AssetError(f"asset {asset_id!r} has an unsafe relative_path")
        try:
            path = (project_directory / relative).resolve(strict=True)
        except (FileNotFoundError, NotADirectoryError, OSError) as error:
            raise H3AssetError(f"asset {asset_id!r} file is unavailable") from error
        if not _is_below(path, project_directory) or not path.is_file():
            raise H3AssetError(f"asset {asset_id!r} escaped its Project root")

        mime_value = payload.get("mime_type")
        if not isinstance(mime_value, str) or not mime_value.strip():
            raise H3AssetError(f"asset {asset_id!r} has no registered MIME type")
        mime_type = mime_value.split(";", 1)[0].strip().casefold()
        kind = media_kind(path, mime_type)

        byte_size = payload.get("byte_size")
        if isinstance(byte_size, bool) or not isinstance(byte_size, int) or byte_size < 0:
            raise H3AssetError(f"asset {asset_id!r} has an invalid registered byte size")
        actual_size = path.stat().st_size
        if byte_size != actual_size:
            raise H3AssetError(
                f"asset {asset_id!r} byte size mismatch: registered {byte_size}, actual {actual_size}"
            )

        registered_digest = self._registered_digest(payload, asset_id)
        actual_digest = _file_sha256(path)
        if registered_digest != actual_digest:
            raise H3AssetError(f"asset {asset_id!r} SHA-256 mismatch")
        name = payload.get("name")
        return _ValidatedAsset(
            asset_id=asset_id,
            name=name.strip() if isinstance(name, str) and name.strip() else asset_id,
            relative_path=path.relative_to(project_directory).as_posix(),
            path=path,
            mime_type=mime_type,
            byte_size=actual_size,
            sha256=actual_digest,
            kind=kind,
            payload=dict(payload),
        )

    @staticmethod
    def _snapshot_assets(
        project_directory: Path,
        attached: Mapping[str, _ValidatedAsset],
    ) -> tuple[AssetResolver, tempfile.TemporaryDirectory[str] | None]:
        if not attached:
            return AssetResolver((project_directory,), {}), None

        snapshot_directory = tempfile.TemporaryDirectory(
            prefix=".h3-submit-",
            dir=project_directory,
            ignore_cleanup_errors=True,
        )
        snapshot_root = Path(snapshot_directory.name).resolve(strict=True)
        descriptors: dict[str, AssetDescriptor] = {}
        try:
            for index, (asset_id, asset) in enumerate(attached.items(), start=1):
                target = snapshot_root / f"{index:04d}{asset.path.suffix.casefold()}"
                digest = sha256()
                byte_count = 0
                with asset.path.open("rb") as source, target.open("xb") as destination:
                    while True:
                        chunk = source.read(1024 * 1024)
                        if not chunk:
                            break
                        byte_count += len(chunk)
                        if byte_count > asset.byte_size:
                            raise H3AssetError(
                                f"asset {asset_id!r} changed while creating its H3 snapshot"
                            )
                        destination.write(chunk)
                        digest.update(chunk)
                    destination.flush()
                    os.fsync(destination.fileno())
                if byte_count != asset.byte_size or digest.hexdigest() != asset.sha256:
                    raise H3AssetError(
                        f"asset {asset_id!r} changed while creating its H3 snapshot"
                    )
                descriptors[asset_id] = AssetDescriptor(
                    path=target.relative_to(snapshot_root),
                    mime_type=asset.mime_type,
                    byte_size=asset.byte_size,
                    sha256=asset.sha256,
                    source_path=asset.path,
                )
            return AssetResolver((snapshot_root,), descriptors), snapshot_directory
        except Exception:
            snapshot_directory.cleanup()
            raise

    @staticmethod
    def _validate_upload_selection(
        request: H3SubmitRequest,
        attached: Mapping[str, _ValidatedAsset],
    ) -> None:
        uploads = request.uploads
        first = uploads.first_image_asset_id
        last = uploads.last_image_asset_id
        references = uploads.reference_asset_ids
        mode = request.fields.mode
        if mode == "t2v" and (first or last or references):
            raise RenderServiceError("t2v cannot include uploaded assets")
        if mode == "i2v" and (first is None or last is not None or references):
            raise RenderServiceError("i2v requires only a first image")
        if mode == "first_last" and (first is None or last is None or references):
            raise RenderServiceError("first_last requires exactly first and last images")
        if mode == "omni" and (first is not None or last is not None or not references):
            raise RenderServiceError(
                "omni requires ordered references and forbids first/last frame slots"
            )
        for slot, asset_id in (("first_image_asset_id", first), ("last_image_asset_id", last)):
            if asset_id is not None and attached[asset_id].kind != "image":
                raise RenderServiceError(f"{slot} must reference an image asset")
        counts = {"image": 0, "video": 0, "audio": 0}
        for asset_id in references:
            counts[attached[asset_id].kind] += 1
        if counts["image"] > 9 or counts["video"] > 3 or counts["audio"] > 3:
            raise RenderServiceError(
                "omni references are limited to 9 images, 3 videos, and 3 audio files"
            )
        if counts["audio"] and not (counts["image"] or counts["video"]):
            raise RenderServiceError(
                "audio references require at least one image or video reference"
            )

    @staticmethod
    def _attachment_bindings(
        request: H3SubmitRequest,
        attached: Mapping[str, _ValidatedAsset],
    ) -> tuple[list[dict[str, Any]], dict[str, str]]:
        bindings: list[dict[str, Any]] = []
        tags_by_asset_id: dict[str, str] = {}
        uploads = request.uploads
        if uploads.first_image_asset_id is not None:
            asset = attached[uploads.first_image_asset_id]
            tags_by_asset_id[asset.asset_id] = "<Picture 1>"
            bindings.append(
                asset.audit_binding(
                    slot="first_image_asset_id",
                    source_index=None,
                    ordinal=1,
                    tag="<Picture 1>",
                )
            )
        if uploads.last_image_asset_id is not None:
            asset = attached[uploads.last_image_asset_id]
            tags_by_asset_id[asset.asset_id] = "<Picture 2>"
            bindings.append(
                asset.audit_binding(
                    slot="last_image_asset_id",
                    source_index=None,
                    ordinal=2,
                    tag="<Picture 2>",
                )
            )

        ordinals = {"image": 0, "video": 0, "audio": 0}
        for source_index, asset_id in enumerate(uploads.reference_asset_ids):
            asset = attached[asset_id]
            ordinals[asset.kind] += 1
            ordinal = ordinals[asset.kind]
            tag = f"<{_TAG_LABELS[asset.kind]} {ordinal}>"
            tags_by_asset_id[asset_id] = tag
            bindings.append(
                asset.audit_binding(
                    slot="reference_asset_ids",
                    source_index=source_index,
                    ordinal=ordinal,
                    tag=tag,
                )
            )
        return bindings, tags_by_asset_id

    @staticmethod
    def _resolve_authoring_prompt(
        authoring_prompt: str,
        *,
        asset_entities: Mapping[str, Any],
        attachment_bindings: Sequence[Mapping[str, Any]],
        tags_by_asset_id: Mapping[str, str],
    ) -> tuple[str, tuple[Mapping[str, Any], ...]]:
        names: dict[str, list[tuple[str, Mapping[str, Any]]]] = {}
        for asset_id, entity in asset_entities.items():
            payload = entity.payload
            name = payload.get("name")
            if isinstance(name, str) and name.strip():
                names.setdefault(name.strip(), []).append((asset_id, payload))

        metadata_bindings: list[dict[str, Any]] = []
        occurrence_counts: dict[str, int] = {}
        ordered_names = sorted(names, key=len, reverse=True)
        resolved_parts: list[str] = []
        cursor = 0
        while True:
            mention_at = authoring_prompt.find("@", cursor)
            if mention_at < 0:
                resolved_parts.append(authoring_prompt[cursor:])
                break
            resolved_parts.append(authoring_prompt[cursor:mention_at])

            braced = authoring_prompt.startswith("@{", mention_at)
            if braced:
                close_at = authoring_prompt.find("}", mention_at + 2)
                if close_at < 0:
                    raise RenderServiceError(
                        "authoring prompt contains an unterminated braced asset mention"
                    )
                name = authoring_prompt[mention_at + 2 : close_at]
                mention_end = close_at + 1
                if name not in names:
                    raise RenderServiceError(
                        f"authoring prompt contains unresolved asset mention @{{{name}}}"
                    )
                mention_text = authoring_prompt[mention_at:mention_end]
            else:
                name = next(
                    (
                        candidate
                        for candidate in ordered_names
                        if authoring_prompt.startswith(candidate, mention_at + 1)
                        and _has_mention_boundary(
                            authoring_prompt, mention_at + 1 + len(candidate)
                        )
                    ),
                    None,
                )
                if name is None:
                    unresolved_name = _unresolved_mention_name(
                        authoring_prompt, mention_at
                    )
                    raise RenderServiceError(
                        "authoring prompt contains unresolved asset mention "
                        f"@{unresolved_name}"
                    )
                mention_end = mention_at + 1 + len(name)
                mention_text = authoring_prompt[mention_at:mention_end]

            candidates = names[name]
            if len(candidates) != 1:
                raise RenderServiceError(
                    f"authoring prompt mention {mention_text} is ambiguous in this Project"
                )
            asset_id, payload = candidates[0]
            replacement_text = tags_by_asset_id.get(asset_id)
            if replacement_text is None:
                replacement_text = _metadata_description(name, payload)
                if asset_id not in occurrence_counts:
                    metadata_bindings.append(
                        {
                            "asset_id": asset_id,
                            "asset_name": name,
                            "attached": False,
                            "slot": "metadata",
                            "kind": "metadata",
                            "mention": mention_text,
                            "replacement": replacement_text,
                        }
                    )
            occurrence_counts[asset_id] = occurrence_counts.get(asset_id, 0) + 1
            resolved_parts.append(replacement_text)
            cursor = mention_end

        resolved = "".join(resolved_parts)
        # Captions are Project data, not authoring syntax.  If their expansion
        # introduced an @ token, never let it bypass the exact mention parser.
        unresolved_at = resolved.find("@")
        if unresolved_at >= 0:
            unresolved_name = _unresolved_mention_name(resolved, unresolved_at)
            raise RenderServiceError(
                "resolved prompt contains unresolved asset mention "
                f"@{unresolved_name}"
            )
        resolved = resolved.strip()
        if not resolved:
            raise RenderServiceError("resolved H3 prompt must not be empty")
        if len(resolved) > 4_000:
            raise RenderServiceError("resolved H3 prompt exceeds 4000 characters")

        audited_attachments: list[dict[str, Any]] = []
        for binding in attachment_bindings:
            audited = dict(binding)
            asset_id = str(audited["asset_id"])
            audited["mention_count"] = occurrence_counts.get(asset_id, 0)
            audited["replacement"] = audited.get("tag")
            audited_attachments.append(audited)
        for binding in metadata_bindings:
            asset_id = str(binding["asset_id"])
            binding["mention_count"] = occurrence_counts[asset_id]
        return resolved, tuple(audited_attachments + metadata_bindings)

    def _prepare_story_request(
        self,
        context: Any,
        request: H3SubmitRequest,
    ) -> _PreparedStoryRequest:
        project_directory = self._project_directory(getattr(context, "project_root", None))
        asset_entities = self._asset_entities(context)
        uploads = request.uploads
        requested_asset_ids = tuple(
            asset_id
            for asset_id in (
                uploads.first_image_asset_id,
                uploads.last_image_asset_id,
                *uploads.reference_asset_ids,
            )
            if asset_id is not None
        )
        if len(set(requested_asset_ids)) != len(requested_asset_ids):
            raise RenderServiceError("an asset ID cannot occupy more than one H3 upload slot")
        attached = {
            asset_id: self._validate_attached_asset(
                asset_id,
                project_directory=project_directory,
                asset_entities=asset_entities,
            )
            for asset_id in requested_asset_ids
        }
        self._validate_upload_selection(request, attached)
        attachment_bindings, tags_by_asset_id = self._attachment_bindings(
            request, attached
        )
        authoring_prompt = request.fields.prompt
        resolved_prompt, bindings = self._resolve_authoring_prompt(
            authoring_prompt,
            asset_entities=asset_entities,
            attachment_bindings=attachment_bindings,
            tags_by_asset_id=tags_by_asset_id,
        )
        # Story Studio performs only deterministic mention binding.  The
        # existing H3 community planner remains the sole Japanese-to-English
        # and structure transformation path.
        resolved_request = H3SubmitRequest(
            shot_id=request.shot_id,
            fields=replace(
                request.fields,
                prompt=resolved_prompt,
                prompt_processing_mode="community",
            ),
            uploads=request.uploads,
        )
        asset_resolver, snapshot_directory = self._snapshot_assets(
            project_directory, attached
        )
        return _PreparedStoryRequest(
            request=resolved_request,
            authoring_prompt=authoring_prompt,
            resolved_prompt=resolved_prompt,
            bindings=bindings,
            asset_resolver=asset_resolver,
            snapshot_directory=snapshot_directory,
        )

    @staticmethod
    def _normalize_story_request(
        request: H3SubmitRequest | Mapping[str, Any],
    ) -> tuple[H3SubmitRequest, str]:
        """Normalize to H3 community before H3's mode-specific validation.

        A caller may still send ``raw_en`` from an older UI.  Story Studio
        records that requested value for audit, but Japanese translation and
        prompt structuring always belong to H3's existing community planner.
        """

        if isinstance(request, H3SubmitRequest):
            requested_mode = request.fields.prompt_processing_mode
            normalized = H3SubmitRequest(
                shot_id=request.shot_id,
                fields=replace(
                    request.fields,
                    prompt_processing_mode="community",
                ),
                uploads=request.uploads,
            )
        else:
            fields = request.get("fields")
            if isinstance(fields, Mapping):
                requested_mode = fields.get("prompt_processing_mode", "community")
                effective_payload = {
                    **dict(request),
                    "fields": {
                        **dict(fields),
                        "prompt_processing_mode": "community",
                    },
                }
            else:
                requested_mode = "community"
                effective_payload = request
            normalized = H3SubmitRequest.from_mapping(effective_payload)

        if not isinstance(requested_mode, str) or requested_mode not in {
            "community",
            "raw_en",
        }:
            raise RenderServiceError(
                "prompt_processing_mode must be 'community' or 'raw_en'"
            )
        return normalized, str(requested_mode)

    def submit_render(
        self,
        *,
        session_id: str,
        project_id: str,
        expected_revision: int,
        shot_id: str,
        request: H3SubmitRequest | Mapping[str, Any],
    ) -> RenderSubmission:
        normalized, requested_prompt_processing_mode = self._normalize_story_request(
            request
        )
        if normalized.shot_id != shot_id:
            raise RenderServiceError("route shot ID and H3 request shotId must match")

        context = self._repository.get_context(session_id, project_id)
        if context.revision != expected_revision:
            # The repository remains the final authority and will also enforce
            # this precondition.  This early message avoids touching H3.
            raise RenderServiceError(
                f"session is bound to revision {context.revision}, not {expected_revision}"
            )

        # Resolve and validate every Project-scoped reference before reserving
        # a job/take.  Contract, mention, path, MIME, size, or digest failures
        # therefore leave both H3 and the Project revision untouched.
        prepared = self._prepare_story_request(context, normalized)
        effective = prepared.request

        token = uuid4().hex[:12]
        local_job_id = f"job_{token}"
        take_id = f"take_{token}"
        intent_payload = {
            "kind": "h3_video_generation",
            "status": "dispatching",
            "shot_id": shot_id,
            "take_id": take_id,
            "input_project_revision": expected_revision,
            "authoring_prompt": prepared.authoring_prompt,
            "resolved_prompt": prepared.resolved_prompt,
            "prompt_processing_mode_requested": requested_prompt_processing_mode,
            "prompt_processing_mode_effective": effective.fields.prompt_processing_mode,
            "bindings": [dict(binding) for binding in prepared.bindings],
            "request": {
                "shotId": effective.shot_id,
                "fields": dict(effective.fields.as_form_fields()),
                "uploads": {
                    "first_image_asset_id": effective.uploads.first_image_asset_id,
                    "last_image_asset_id": effective.uploads.last_image_asset_id,
                    "reference_asset_ids": list(effective.uploads.reference_asset_ids),
                },
            },
        }
        try:
            reservation = self._repository.commit_patch(
                session_id,
                project_id,
                expected_revision,
                ProjectPatch(
                    entities=(
                        EntityMutation("job", local_job_id, intent_payload),
                        EntityMutation(
                            "take",
                            take_id,
                            {
                                "shot_id": shot_id,
                                "job_id": local_job_id,
                                "status": "queued",
                                "selection": "candidate",
                                "input_project_revision": expected_revision,
                            },
                        ),
                    ),
                    message=f"Reserve H3 render for {shot_id}",
                ),
            )
            try:
                h3_job = self._h3.submit_job(
                    effective,
                    asset_resolver=prepared.asset_resolver,
                )
            except Exception as error:
                self._record_dispatch_failure(
                    session_id=session_id,
                    project_id=project_id,
                    revision=reservation.revision,
                    local_job_id=local_job_id,
                    take_id=take_id,
                    shot_id=shot_id,
                    message=str(error),
                    intent_payload=intent_payload,
                )
                raise
        finally:
            # H3Client consumes the multipart stream synchronously.  Once it
            # returns (or aborts), the immutable per-call snapshots are no
            # longer reachable and can be removed.
            prepared.cleanup()

        accepted_payload = {
            **intent_payload,
            **_h3_job_payload(h3_job),
            "status": h3_job.status.value,
        }
        try:
            accepted = self._repository.commit_patch(
                session_id,
                project_id,
                reservation.revision,
                ProjectPatch(
                    entities=(
                        EntityMutation("job", local_job_id, accepted_payload),
                        EntityMutation(
                            "take",
                            take_id,
                            {
                                "shot_id": shot_id,
                                "job_id": local_job_id,
                                "h3_job_id": h3_job.id,
                                "status": (
                                    "ready"
                                    if h3_job.status is H3JobStatus.COMPLETED
                                    else "generating"
                                ),
                                "selection": "candidate",
                                "input_project_revision": expected_revision,
                            },
                        ),
                    ),
                    message=f"H3 accepted {h3_job.id} for {shot_id}",
                ),
            )
        except Exception as error:
            raise RenderReconciliationRequired(
                local_job_id,
                h3_job.id,
                "H3 accepted the render, but Project Core could not record it",
            ) from error

        return RenderSubmission(
            project_id=project_id,
            shot_id=shot_id,
            take_id=take_id,
            local_job_id=local_job_id,
            h3_job_id=h3_job.id,
            revision=accepted.revision,
            status=h3_job.status.value,
        )

    def sync_render(
        self,
        *,
        session_id: str,
        project_id: str,
        expected_revision: int,
        local_job_id: str,
        download_result: bool = True,
        extract_end_frame: bool = False,
    ) -> RenderSyncResult:
        context = self._repository.get_context(session_id, project_id)
        if context.revision != expected_revision:
            raise RenderServiceError(
                f"session is bound to revision {context.revision}, not {expected_revision}"
            )
        entity = next(
            (
                item
                for item in context.entities
                if item.entity_type == "job" and item.entity_id == local_job_id
            ),
            None,
        )
        if entity is None:
            raise RenderServiceError(f"render job {local_job_id!r} was not found")
        h3_job_id = entity.payload.get("h3_job_id")
        shot_id = entity.payload.get("shot_id")
        take_id = entity.payload.get("take_id")
        if not all(isinstance(value, str) and value for value in (h3_job_id, shot_id, take_id)):
            raise RenderServiceError("render job has not reached the H3 accepted state")

        h3_job = self._h3.get_job(h3_job_id)
        artifact_relative_path: str | None = None
        artifact_digest: str | None = None
        artifact_payload: dict[str, Any] | None = None
        end_frame_asset_id: str | None = None
        end_frame_payload: dict[str, Any] | None = None
        if h3_job.status is H3JobStatus.COMPLETED and download_result:
            result = self._h3.get_result(h3_job_id)
            artifact_relative_path, artifact_digest = self._write_result(
                context.project_root,
                shot_id,
                take_id,
                result.content,
            )
            artifact_payload = {
                "kind": "video",
                "role": "generated_take",
                "relative_path": artifact_relative_path,
                "mime_type": result.content_type,
                "byte_size": len(result.content),
                "sha256": artifact_digest,
                "h3_job_id": h3_job_id,
                "shot_id": shot_id,
                "take_id": take_id,
            }
            if extract_end_frame:
                end_frame_relative_path, end_frame_digest, end_frame_size = self._extract_end_frame(
                    context.project_root,
                    shot_id,
                    take_id,
                    artifact_relative_path,
                )
                end_frame_asset_id = f"asset_{take_id}_end_frame"
                end_frame_payload = {
                    "kind": "image",
                    "role": "generated_end_frame",
                    "relative_path": end_frame_relative_path,
                    "mime_type": "image/png",
                    "byte_size": end_frame_size,
                    "sha256": end_frame_digest,
                    "source": "h3_render_extraction",
                    "h3_job_id": h3_job_id,
                    "shot_id": shot_id,
                    "take_id": take_id,
                }

        job_status = h3_job.status.value
        take_status = {
            H3JobStatus.COMPLETED: "ready",
            H3JobStatus.FAILED: "failed",
            H3JobStatus.CANCELLED: "failed",
            H3JobStatus.INTERRUPTED: "failed",
        }.get(h3_job.status, "generating")
        job_payload = {**dict(entity.payload), **_h3_job_payload(h3_job)}
        mutations = [
            EntityMutation("job", local_job_id, job_payload),
            EntityMutation(
                "take",
                take_id,
                {
                    "shot_id": shot_id,
                    "job_id": local_job_id,
                    "h3_job_id": h3_job_id,
                    "status": take_status,
                    "selection": "candidate",
                    "artifact_id": f"asset_{take_id}" if artifact_payload else None,
                    "artifact_relative_path": artifact_relative_path,
                    "artifact_sha256": artifact_digest,
                    "end_frame_asset_id": end_frame_asset_id,
                },
            ),
        ]
        if artifact_payload is not None:
            mutations.append(EntityMutation("asset", f"asset_{take_id}", artifact_payload))
        if end_frame_payload is not None and end_frame_asset_id is not None:
            mutations.append(EntityMutation("asset", end_frame_asset_id, end_frame_payload))
        committed = self._repository.commit_patch(
            session_id,
            project_id,
            expected_revision,
            ProjectPatch(
                entities=tuple(mutations),
                message=f"Sync H3 render {h3_job_id}: {job_status}",
            ),
        )
        return RenderSyncResult(
            project_id=project_id,
            shot_id=shot_id,
            take_id=take_id,
            local_job_id=local_job_id,
            h3_job_id=h3_job_id,
            revision=committed.revision,
            status=job_status,
            progress=h3_job.progress,
            artifact_id=f"asset_{take_id}" if artifact_payload else None,
            artifact_relative_path=artifact_relative_path,
            artifact_sha256=artifact_digest,
            end_frame_asset_id=end_frame_asset_id,
        )

    def _record_dispatch_failure(
        self,
        *,
        session_id: str,
        project_id: str,
        revision: int,
        local_job_id: str,
        take_id: str,
        shot_id: str,
        message: str,
        intent_payload: Mapping[str, Any],
    ) -> None:
        self._repository.commit_patch(
            session_id,
            project_id,
            revision,
            ProjectPatch(
                entities=(
                    EntityMutation(
                        "job",
                        local_job_id,
                        {
                            **dict(intent_payload),
                            "status": "failed",
                            "error": message,
                        },
                    ),
                    EntityMutation(
                        "take",
                        take_id,
                        {
                            "shot_id": shot_id,
                            "job_id": local_job_id,
                            "status": "failed",
                            "selection": "candidate",
                            "error": message,
                        },
                    ),
                ),
                message=f"H3 dispatch failed for {shot_id}",
            ),
        )

    def _write_result(
        self,
        project_root: str,
        shot_id: str,
        take_id: str,
        content: bytes,
    ) -> tuple[str, str]:
        root = (self._local_root / project_root).resolve(strict=False)
        if root != self._local_root and self._local_root not in root.parents:
            raise RenderServiceError("project output root escaped the local Story root")
        output_dir = (root / "outputs" / shot_id).resolve(strict=False)
        if root != output_dir and root not in output_dir.parents:
            raise RenderServiceError("render output path escaped the project root")
        output_dir.mkdir(parents=True, exist_ok=True)
        target = output_dir / f"{take_id}.mp4"
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=output_dir, prefix=f".{take_id}-", suffix=".tmp", delete=False
        ) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        temporary.replace(target)
        return target.relative_to(root).as_posix(), sha256(content).hexdigest()

    def _extract_end_frame(
        self,
        project_root: str,
        shot_id: str,
        take_id: str,
        artifact_relative_path: str,
    ) -> tuple[str, str, int]:
        """Extract the last decoded video frame for an optional continuity handoff.

        This runs only when the caller explicitly requests ``extract_end_frame``.
        The H3 renderer remains untouched; the frame becomes a normal Project
        asset so the next render can bind it through ``first_image_asset_id``.
        """
        try:
            import imageio_ffmpeg  # type: ignore[import-not-found]
            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception as error:  # pragma: no cover - platform dependency
            raise RenderServiceError(
                "end-frame extraction requires the imageio-ffmpeg runtime"
            ) from error

        root = (self._local_root / project_root).resolve(strict=False)
        source = (root / artifact_relative_path).resolve(strict=False)
        if not _is_below(source, root) or not source.is_file():
            raise RenderServiceError("generated video is unavailable for end-frame extraction")
        output_dir = (root / "outputs" / shot_id).resolve(strict=False)
        if not _is_below(output_dir, root):
            raise RenderServiceError("end-frame output escaped the project root")
        output_dir.mkdir(parents=True, exist_ok=True)
        target = output_dir / f"{take_id}-end-frame.png"
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=output_dir, prefix=f".{take_id}-end-frame-", suffix=".tmp.png", delete=False
        ) as handle:
            temporary = Path(handle.name)
        try:
            subprocess.run(
                [
                    ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-sseof",
                    "-0.1",
                    "-i",
                    str(source),
                    "-frames:v",
                    "1",
                    "-y",
                    str(temporary),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            if not temporary.is_file() or temporary.stat().st_size == 0:
                raise RenderServiceError("ffmpeg produced no end-frame image")
            temporary.replace(target)
        except subprocess.CalledProcessError as error:
            detail = (error.stderr or "").strip()
            raise RenderServiceError(f"end-frame extraction failed: {detail or error}") from error
        finally:
            temporary.unlink(missing_ok=True)
        content = target.read_bytes()
        return (
            target.relative_to(root).as_posix(),
            sha256(content).hexdigest(),
            len(content),
        )


__all__ = [
    "RenderReconciliationRequired",
    "RenderService",
    "RenderServiceError",
    "RenderSubmission",
    "RenderSyncResult",
]
