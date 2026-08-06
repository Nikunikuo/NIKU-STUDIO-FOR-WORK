"""Loopback-only HTTP façade for H3 Story Studio.

The server intentionally uses only Python's standard library.  The routing
layer is kept separate from ``BaseHTTPRequestHandler`` so its complete wire
contract can be tested without opening a socket or contacting the real H3
renderer.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from email import policy
from email.parser import BytesParser
from enum import Enum
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import inspect
import json
import os
from pathlib import Path
import re
import stat
import threading
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import parse_qs, unquote, urlsplit

try:
    from .asset_service import (
        ASSET_MEDIA_TYPES,
        MAX_ASSET_FILE_BYTES,
        AssetCreateSpec,
        AssetUpload,
    )
except ImportError:  # Direct ``python sidecar/http_server.py`` execution.
    from sidecar.asset_service import (  # type: ignore[no-redef]
        ASSET_MEDIA_TYPES,
        MAX_ASSET_FILE_BYTES,
        AssetCreateSpec,
        AssetUpload,
    )


HOST = "127.0.0.1"
PORT = 7864
MAX_JSON_BYTES = 2 * 1024 * 1024
# Generated H3 clips are short local MP4 artifacts.  Keep a generous ceiling
# while refusing to turn the sidecar into an arbitrary multi-gigabyte file
# server if Project Core metadata is damaged or tampered with.
MAX_MEDIA_BYTES = 4 * 1024 * 1024 * 1024
MEDIA_STREAM_CHUNK_BYTES = 1024 * 1024
MAX_ASSET_MULTIPART_BYTES = MAX_ASSET_FILE_BYTES + 1024 * 1024
MAX_MULTIPART_FIELD_BYTES = 64 * 1024
MUTATION_HEADER = "X-H3-Story-Request"
MUTATION_HEADER_VALUE = "1"
ALLOWED_BROWSER_ORIGINS = frozenset(
    {"http://localhost:3000", "http://127.0.0.1:3000"}
)
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_PROJECT_ROUTE = re.compile(
    r"^/api/projects/(?P<project>[^/]+)/(?P<action>activate|context|patches|handoffs)$"
)
_RENDER_SUBMIT_ROUTE = re.compile(
    r"^/api/projects/(?P<project>[^/]+)/shots/(?P<shot>[^/]+)/renders$"
)
_RENDER_SYNC_ROUTE = re.compile(
    r"^/api/projects/(?P<project>[^/]+)/renders/(?P<job>[^/]+)/sync$"
)
_H3_JOB_ROUTE = re.compile(r"^/api/h3/jobs/(?P<job>[a-f0-9]{12})$")
_MEDIA_ROUTE = re.compile(
    r"^/api/projects/(?P<project>[^/]+)/assets/(?P<asset>[^/]+)/content$"
)
_ASSET_CREATE_ROUTE = re.compile(r"^/api/projects/(?P<project>[^/]+)/assets$")
_BYTE_RANGE = re.compile(r"^bytes=(?P<start>\d*)-(?P<end>\d*)$")


class RepositoryLike(Protocol):
    def list_projects(self) -> Any: ...

    def create_project(self, spec: Any) -> Any: ...

    def activate_session(self, project_id: str, agent_id: str) -> Any: ...

    def get_context(self, session_id: str, project_id: str | None = None) -> Any: ...

    def commit_patch(self, *args: Any, **kwargs: Any) -> Any: ...

    def create_handoff_capsule(self, *args: Any, **kwargs: Any) -> Any: ...


class H3Like(Protocol):
    def get_state(self) -> Any: ...


class RenderLike(Protocol):
    def submit_render(self, **kwargs: Any) -> Any: ...

    def sync_render(self, **kwargs: Any) -> Any: ...


class AssetLike(Protocol):
    def create_asset(self, **kwargs: Any) -> Any: ...


class RepositoryFactoryProxy:
    """Open one SQLite repository per method call on the calling thread.

    ``ProjectRepository`` deliberately owns one SQLite connection, and Python's
    default SQLite connection is bound to the thread that created it.  A
    ``ThreadingHTTPServer`` therefore cannot safely share a single repository
    instance created by ``main``.  This proxy preserves the public repository
    interface while opening and closing a short-lived connection in the actual
    request worker.  Repository methods already define atomic transaction
    boundaries, so a method call is the correct lifetime.
    """

    def __init__(self, factory: Callable[[], RepositoryLike]) -> None:
        self._factory = factory
        self._closed = False
        self._lock = threading.Lock()

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)

        def invoke(*args: Any, **kwargs: Any) -> Any:
            with self._lock:
                if self._closed:
                    raise RuntimeError("Repository factory is closed")
            repository = self._factory()
            try:
                method = getattr(repository, name)
                return method(*args, **kwargs)
            finally:
                close = getattr(repository, "close", None)
                if callable(close):
                    close()

        return invoke

    def close(self) -> None:
        with self._lock:
            self._closed = True


@dataclass(frozen=True, slots=True)
class ApiResponse:
    status: int
    body: Any | None = None
    headers: Mapping[str, str] | None = None


@dataclass(frozen=True, slots=True)
class FileSlice:
    """A verified slice of one local media file for the HTTP writer.

    The dispatcher returns this descriptor instead of reading the MP4 into
    memory.  ``path`` is already strict-resolved beneath the active project's
    root; the request handler rechecks it immediately before opening to close
    the most practical local symlink-swap race.
    """

    path: Path
    offset: int
    length: int
    file_size: int


class ApiError(RuntimeError):
    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        details: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self.status = status
        self.code = code
        self.message = message
        self.details = dict(details) if details else None
        self.headers = dict(headers) if headers else None
        super().__init__(message)


def _snake_to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in tail)


def _plain(value: Any) -> Any:
    """Convert public model values to ordinary JSON-compatible objects."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_plain(item) for item in value]
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return _plain(to_dict())
    if is_dataclass(value):
        return _plain(asdict(value))
    raw = getattr(value, "raw", None)
    if isinstance(raw, Mapping):
        return _plain(raw)
    raise TypeError(f"Value is not JSON serializable: {type(value).__name__}")


def _camelize(value: Any) -> Any:
    value = _plain(value)
    if isinstance(value, dict):
        return {_snake_to_camel(key): _camelize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_camelize(item) for item in value]
    return value


def _attribute(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _project_wire(summary: Any) -> dict[str, Any]:
    wire = _camelize(summary)
    if not isinstance(wire, dict):
        raise TypeError("project summary must serialize as an object")
    project_id = _attribute(summary, "project_id", wire.get("projectId"))
    name = _attribute(summary, "name", wire.get("name", project_id))
    digest = _attribute(summary, "digest", wire.get("digest"))
    # These aliases are the stable UI contract.  The remaining model fields
    # stay present in camelCase for diagnostics and forward compatibility.
    wire["projectId"] = project_id
    metadata = _attribute(summary, "metadata", wire.get("metadata", {}))
    metadata_wire = _camelize(metadata) if isinstance(metadata, Mapping) else {}
    wire["metadata"] = metadata_wire
    wire["code"] = metadata_wire.get("code", project_id)
    wire["title"] = metadata_wire.get("title", name)
    if digest is not None:
        wire["stateDigest"] = digest
    return wire


def _project_create_wire(result: Any) -> dict[str, Any]:
    wire = _camelize(result)
    if not isinstance(wire, dict):
        raise TypeError("project creation result must serialize as an object")
    digest = _attribute(result, "digest", wire.get("digest"))
    if digest is not None:
        wire["stateDigest"] = digest
        wire.pop("digest", None)
    return wire


def _asset_create_wire(result: Any) -> dict[str, Any]:
    wire = _camelize(result)
    if not isinstance(wire, dict):
        raise TypeError("asset creation result must serialize as an object")
    digest = _attribute(result, "digest", wire.get("digest"))
    if digest is not None:
        wire["stateDigest"] = digest
        wire.pop("digest", None)
    return wire


def _session_wire(binding: Any) -> dict[str, Any]:
    wire = _camelize(binding)
    if not isinstance(wire, dict):
        raise TypeError("session binding must serialize as an object")
    revision = _attribute(binding, "revision", wire.get("revision"))
    digest = _attribute(binding, "digest", wire.get("digest"))
    if revision is not None:
        wire["boundRevision"] = revision
    if digest is not None:
        wire["stateDigest"] = digest
    return wire


def _context_wire(context: Any) -> dict[str, Any]:
    wire = _camelize(context)
    if not isinstance(wire, dict):
        raise TypeError("project context must serialize as an object")
    digest = _attribute(context, "digest", wire.get("digest"))
    revision = _attribute(context, "revision", wire.get("revision"))
    if digest is not None:
        wire["stateDigest"] = digest
    if revision is not None:
        wire["projectRevision"] = revision
    return wire


def _commit_wire(result: Any) -> dict[str, Any]:
    wire = _camelize(result)
    if not isinstance(wire, dict):
        raise TypeError("commit result must serialize as an object")
    digest = _attribute(result, "digest", wire.get("digest"))
    if digest is not None:
        wire["stateDigest"] = digest
    return wire


def _handoff_wire(capsule: Any) -> dict[str, Any]:
    capsule_wire = _camelize(capsule)
    if not isinstance(capsule_wire, dict):
        raise TypeError("handoff capsule must serialize as an object")
    return {
        "handoffId": _attribute(capsule, "handoff_id", capsule_wire.get("handoffId")),
        "projectId": _attribute(capsule, "project_id", capsule_wire.get("projectId")),
        "projectRevision": _attribute(capsule, "revision", capsule_wire.get("revision")),
        "stateDigest": _attribute(capsule, "digest", capsule_wire.get("digest")),
        "path": _attribute(
            capsule, "relative_path", capsule_wire.get("relativePath")
        ),
        "capsule": capsule_wire,
    }


def _headers_lower(headers: Mapping[str, str]) -> dict[str, str]:
    return {str(name).casefold(): str(value) for name, value in headers.items()}


def _safe_identifier(value: str, label: str) -> str:
    decoded = unquote(value)
    if not _SAFE_ID.fullmatch(decoded):
        raise ApiError(400, "INVALID_IDENTIFIER", f"{label} contains unsafe characters")
    return decoded


def _member(value: Any, *names: str, default: Any = None) -> Any:
    """Read a model or mapping member without changing the JSON wire shape."""

    if isinstance(value, Mapping):
        for name in names:
            if name in value:
                return value[name]
        return default
    for name in names:
        if hasattr(value, name):
            return getattr(value, name)
    return default


def _contained_path(parent: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(parent)
        return True
    except ValueError:
        return False


def _registered_relative_path(value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ApiError(
            409,
            "MEDIA_ASSET_INVALID",
            f"{label} is missing or invalid in Project Core",
        )
    relative = Path(value.strip())
    if relative.is_absolute() or relative.drive or ".." in relative.parts:
        raise ApiError(
            403,
            "MEDIA_PATH_UNSAFE",
            f"{label} escapes the registered project boundary",
        )
    return relative


def _parse_media_range(value: str | None, file_size: int) -> tuple[int, int, bool]:
    """Return ``(offset, length, is_partial)`` for one RFC 7233 byte range."""

    if value is None:
        return 0, file_size, False
    if len(value) > 128:
        raise ApiError(
            416,
            "RANGE_NOT_SATISFIABLE",
            "The requested byte range is too long",
            {"fileSize": file_size},
            {"Content-Range": f"bytes */{file_size}", "Accept-Ranges": "bytes"},
        )
    match = _BYTE_RANGE.fullmatch(value.strip())
    if match is None or "," in value:
        raise ApiError(
            416,
            "RANGE_NOT_SATISFIABLE",
            "Only one valid bytes range is supported",
            {"fileSize": file_size},
            {"Content-Range": f"bytes */{file_size}", "Accept-Ranges": "bytes"},
        )

    raw_start = match.group("start")
    raw_end = match.group("end")
    if not raw_start and not raw_end:
        raise ApiError(
            416,
            "RANGE_NOT_SATISFIABLE",
            "The requested byte range is empty",
            {"fileSize": file_size},
            {"Content-Range": f"bytes */{file_size}", "Accept-Ranges": "bytes"},
        )

    if raw_start:
        start = int(raw_start)
        end = int(raw_end) if raw_end else file_size - 1
        if start >= file_size or end < start:
            raise ApiError(
                416,
                "RANGE_NOT_SATISFIABLE",
                "The requested byte range is outside the media file",
                {"fileSize": file_size},
                {"Content-Range": f"bytes */{file_size}", "Accept-Ranges": "bytes"},
            )
        end = min(end, file_size - 1)
    else:
        suffix_length = int(raw_end)
        if suffix_length <= 0:
            raise ApiError(
                416,
                "RANGE_NOT_SATISFIABLE",
                "The requested suffix range must be greater than zero",
                {"fileSize": file_size},
                {"Content-Range": f"bytes */{file_size}", "Accept-Ranges": "bytes"},
            )
        suffix_length = min(suffix_length, file_size)
        start = file_size - suffix_length
        end = file_size - 1
    return start, end - start + 1, True


def _required_text(payload: Mapping[str, Any], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ApiError(400, "INVALID_REQUEST", f"{name} must be a non-empty string")
    return value.strip()


def _required_string(
    payload: Mapping[str, Any], name: str, *, allow_empty: bool = False
) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or "\x00" in value:
        raise ApiError(400, "INVALID_REQUEST", f"{name} must be a string")
    normalized = value.strip()
    if not allow_empty and not normalized:
        raise ApiError(400, "INVALID_REQUEST", f"{name} must be non-empty")
    return normalized


def _load_project_create(payload: Mapping[str, Any]) -> Any:
    try:
        from .h3_story_core.models import ProjectCreateSpec
    except ImportError:  # Direct ``python sidecar/http_server.py`` execution.
        from h3_story_core.models import ProjectCreateSpec  # type: ignore[no-redef]

    return ProjectCreateSpec(
        title=_required_string(payload, "title"),
        code=_required_string(payload, "code"),
        subtitle=_required_string(payload, "subtitle", allow_empty=True),
        episode_title=_required_string(payload, "episodeTitle"),
        scene_title=_required_string(payload, "sceneTitle"),
        shot_title=_required_string(payload, "shotTitle"),
        logline=_required_string(payload, "logline", allow_empty=True),
        summary=_required_string(payload, "summary", allow_empty=True),
    )


def _load_asset_create(payload: Mapping[str, Any]) -> AssetCreateSpec:
    session_id = _safe_identifier(
        _required_string(payload, "sessionId"), "sessionId"
    )
    expected_revision = payload.get("expectedRevision")
    if isinstance(expected_revision, str):
        if re.fullmatch(r"[0-9]{1,18}", expected_revision) is not None:
            expected_revision = int(expected_revision)
    if (
        isinstance(expected_revision, bool)
        or not isinstance(expected_revision, int)
        or expected_revision < 0
    ):
        raise ApiError(
            400,
            "INVALID_REQUEST",
            "expectedRevision must be a non-negative integer",
        )
    locked = payload.get("locked")
    if isinstance(locked, str):
        normalized_locked = locked.strip().casefold()
        if normalized_locked == "true":
            locked = True
        elif normalized_locked == "false":
            locked = False
    if not isinstance(locked, bool):
        raise ApiError(400, "INVALID_REQUEST", "locked must be a boolean")
    return AssetCreateSpec(
        session_id=session_id,
        expected_revision=expected_revision,
        name=_required_string(payload, "name"),
        category=_required_string(payload, "category"),
        role=_required_string(payload, "role", allow_empty=True),
        caption=_required_string(payload, "caption", allow_empty=True),
        prompt_caption=_required_string(
            payload, "promptCaption", allow_empty=True
        ),
        locked=locked,
        source=_required_string(payload, "source"),
    )


def _parse_multipart_asset(
    content_type: str,
    body: bytes,
) -> tuple[Mapping[str, Any], AssetUpload | None]:
    if len(body) > MAX_ASSET_MULTIPART_BYTES:
        raise ApiError(
            413,
            "PAYLOAD_TOO_LARGE",
            f"multipart request exceeds {MAX_ASSET_MULTIPART_BYTES} bytes",
        )
    if "\r" in content_type or "\n" in content_type:
        raise ApiError(400, "INVALID_MULTIPART", "multipart Content-Type is invalid")
    try:
        synthetic = (
            f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode(
                "ascii", "strict"
            )
            + body
        )
        message = BytesParser(policy=policy.default).parsebytes(synthetic)
    except (ValueError, UnicodeError) as error:
        raise ApiError(
            400, "INVALID_MULTIPART", "multipart body could not be parsed"
        ) from error
    if not message.is_multipart():
        raise ApiError(
            400,
            "INVALID_MULTIPART",
            "Content-Type must contain one valid multipart/form-data boundary",
        )

    fields: dict[str, str] = {}
    upload: AssetUpload | None = None
    parts = list(message.iter_parts())
    if len(parts) > 16:
        raise ApiError(400, "INVALID_MULTIPART", "multipart body has too many parts")
    allowed_fields = {
        "sessionId",
        "expectedRevision",
        "name",
        "category",
        "role",
        "caption",
        "promptCaption",
        "locked",
        "source",
    }
    for part in parts:
        if part.is_multipart() or part.get_content_disposition() != "form-data":
            raise ApiError(400, "INVALID_MULTIPART", "multipart part is invalid")
        name = part.get_param("name", header="content-disposition")
        if not isinstance(name, str) or not name:
            raise ApiError(400, "INVALID_MULTIPART", "multipart part name is missing")
        transfer_encoding = part.get("Content-Transfer-Encoding")
        if transfer_encoding and transfer_encoding.casefold() not in {"binary", "8bit"}:
            raise ApiError(
                400,
                "INVALID_MULTIPART",
                "encoded multipart parts are not supported",
            )
        raw = part.get_payload(decode=True)
        if not isinstance(raw, bytes):
            raise ApiError(400, "INVALID_MULTIPART", "multipart part has no bytes")
        file_name = part.get_filename()
        if name == "file":
            if upload is not None:
                raise ApiError(
                    400, "INVALID_MULTIPART", "file part must appear at most once"
                )
            if file_name in (None, "") and not raw:
                continue
            if not isinstance(file_name, str) or not file_name:
                raise ApiError(
                    400, "INVALID_MULTIPART", "file part requires a filename"
                )
            if len(raw) > MAX_ASSET_FILE_BYTES:
                raise ApiError(
                    413,
                    "ASSET_TOO_LARGE",
                    f"asset file exceeds {MAX_ASSET_FILE_BYTES} bytes",
                )
            upload = AssetUpload(
                file_name=file_name,
                declared_mime_type=part.get_content_type(),
                content=raw,
            )
            continue
        if name not in allowed_fields:
            raise ApiError(
                400, "INVALID_MULTIPART", f"unsupported multipart field: {name}"
            )
        if name in fields:
            raise ApiError(
                400, "INVALID_MULTIPART", f"duplicate multipart field: {name}"
            )
        if file_name is not None:
            raise ApiError(
                400,
                "INVALID_MULTIPART",
                f"text field {name} must not contain a filename",
            )
        if len(raw) > MAX_MULTIPART_FIELD_BYTES:
            raise ApiError(
                413, "PAYLOAD_TOO_LARGE", f"multipart field {name} is too large"
            )
        try:
            fields[name] = raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ApiError(
                400, "INVALID_MULTIPART", f"multipart field {name} is not UTF-8"
            ) from error
    return fields, upload


def _call_with_known_keywords(method: Callable[..., Any], values: Mapping[str, Any]) -> Any:
    """Call an evolving repository API using only keywords it declares.

    Project Core and the HTTP façade are developed independently.  Filtering
    the dependency-injected arguments by signature keeps this module usable
    with the current repository, test doubles, and later compatible additions
    without importing or constructing repository state at module import time.
    """

    signature = inspect.signature(method)
    accepts_var_keywords = any(
        item.kind is inspect.Parameter.VAR_KEYWORD
        for item in signature.parameters.values()
    )
    keywords = (
        dict(values)
        if accepts_var_keywords
        else {name: value for name, value in values.items() if name in signature.parameters}
    )
    return method(**keywords)


def _load_patch(payload: Mapping[str, Any]) -> tuple[str, int, Any]:
    try:
        from .h3_story_core.models import (
            ApprovalMutation,
            EntityMutation,
            ProjectPatch,
        )
    except ImportError:  # Direct ``python sidecar/http_server.py`` execution.
        from h3_story_core.models import (  # type: ignore[no-redef]
            ApprovalMutation,
            EntityMutation,
            ProjectPatch,
        )

    session_id = _safe_identifier(_required_text(payload, "sessionId"), "sessionId")
    expected_revision = payload.get("expectedRevision")
    if (
        isinstance(expected_revision, bool)
        or not isinstance(expected_revision, int)
        or expected_revision < 0
    ):
        raise ApiError(
            400,
            "INVALID_REQUEST",
            "expectedRevision must be a non-negative integer",
        )
    message = _required_text(payload, "message")
    raw_operations = payload.get("operations")
    if not isinstance(raw_operations, list):
        raise ApiError(400, "INVALID_REQUEST", "operations must be an array")

    entities: list[Any] = []
    approvals: list[Any] = []
    focus: Mapping[str, Any] | None = None
    for index, operation in enumerate(raw_operations):
        if not isinstance(operation, Mapping):
            raise ApiError(
                400, "INVALID_REQUEST", f"operations[{index}] must be an object"
            )
        kind = operation.get("op")
        if kind in {"upsert_entity", "delete_entity"}:
            entity_type = _required_text(operation, "entityType")
            entity_id = _required_text(operation, "entityId")
            raw_payload = operation.get("payload")
            if kind == "upsert_entity" and not isinstance(raw_payload, Mapping):
                raise ApiError(
                    400,
                    "INVALID_REQUEST",
                    f"operations[{index}].payload must be an object",
                )
            entities.append(
                EntityMutation(
                    entity_type=entity_type,
                    entity_id=entity_id,
                    payload=dict(raw_payload) if isinstance(raw_payload, Mapping) else None,
                    operation="upsert" if kind == "upsert_entity" else "delete",
                )
            )
        elif kind == "set_focus":
            raw_focus = operation.get("payload")
            if not isinstance(raw_focus, Mapping):
                raise ApiError(
                    400,
                    "INVALID_REQUEST",
                    f"operations[{index}].payload must be an object",
                )
            focus = dict(raw_focus)
        elif kind == "set_approval":
            raw_approval = operation.get("payload")
            if not isinstance(raw_approval, Mapping):
                raise ApiError(
                    400,
                    "INVALID_REQUEST",
                    f"operations[{index}].payload must be an object",
                )
            approval_id = _required_text(raw_approval, "approvalId")
            target_type = _required_text(raw_approval, "targetType")
            target_id = _required_text(raw_approval, "targetId")
            status = _required_text(raw_approval, "status")
            approval_payload = raw_approval.get("payload", {})
            if not isinstance(approval_payload, Mapping):
                raise ApiError(
                    400,
                    "INVALID_REQUEST",
                    f"operations[{index}].payload.payload must be an object",
                )
            approvals.append(
                ApprovalMutation(
                    approval_id=approval_id,
                    target_type=target_type,
                    target_id=target_id,
                    status=status,
                    payload=dict(approval_payload),
                )
            )
        else:
            raise ApiError(
                400, "INVALID_REQUEST", f"operations[{index}].op is unsupported"
            )
    return (
        session_id,
        expected_revision,
        ProjectPatch(
            entities=tuple(entities),
            approvals=tuple(approvals),
            focus=focus,
            message=message,
        ),
    )


def _exception_details(error: Exception) -> dict[str, Any] | None:
    names = (
        "project_id",
        "session_id",
        "bound_project_id",
        "requested_project_id",
        "expected_revision",
        "actual_revision",
        "session_revision",
        "status",
        "local_job_id",
        "h3_job_id",
        "size",
        "maximum",
    )
    details = {
        _snake_to_camel(name): _plain(getattr(error, name))
        for name in names
        if hasattr(error, name)
    }
    return details or None


def _structured_exception(error: Exception) -> ApiError:
    name = type(error).__name__
    mappings = {
        "ValidationError": (400, "VALIDATION_ERROR"),
        "UnsafePathError": (400, "UNSAFE_PATH"),
        "H3ContractError": (400, "H3_CONTRACT_ERROR"),
        "H3AssetError": (400, "H3_ASSET_ERROR"),
        "ProjectBoundaryError": (403, "PROJECT_BOUNDARY_ERROR"),
        "ProjectNotFoundError": (404, "PROJECT_NOT_FOUND"),
        "SessionNotFoundError": (404, "SESSION_NOT_FOUND"),
        "RevisionConflictError": (409, "REVISION_CONFLICT"),
        "ProjectAlreadyExistsError": (409, "PROJECT_ALREADY_EXISTS"),
        "ProjectCodeAlreadyExistsError": (409, "PROJECT_CODE_ALREADY_EXISTS"),
        "StorageIntegrityError": (503, "PROJECT_CORE_INTEGRITY_ERROR"),
        "ProjectCoreError": (503, "PROJECT_CORE_UNAVAILABLE"),
        "H3HTTPError": (503, "H3_UNAVAILABLE"),
        "H3TransportError": (503, "H3_UNAVAILABLE"),
        "H3PollTimeout": (503, "H3_TIMEOUT"),
        "H3UnsupportedOperation": (503, "H3_INCOMPATIBLE"),
        "H3AdapterError": (503, "H3_UNAVAILABLE"),
        "RenderReconciliationRequired": (409, "RENDER_RECONCILIATION_REQUIRED"),
        "RenderServiceError": (400, "RENDER_REQUEST_ERROR"),
        "AssetValidationError": (400, "ASSET_VALIDATION_ERROR"),
        "AssetMediaTypeError": (415, "ASSET_MEDIA_TYPE_ERROR"),
        "AssetTooLargeError": (413, "ASSET_TOO_LARGE"),
        "AssetPathError": (403, "ASSET_PATH_ERROR"),
        "AssetStorageError": (503, "ASSET_STORAGE_ERROR"),
        "AssetServiceError": (400, "ASSET_REQUEST_ERROR"),
    }
    for error_class in type(error).__mro__:
        mapped = mappings.get(error_class.__name__)
        if mapped is not None:
            status, code = mapped
            return ApiError(status, code, str(error), _exception_details(error))
    # A dependency failure must not tear down the threaded server or leak a
    # traceback to the browser.  Programming failures remain visible in logs
    # through ``log_error`` in the handler.
    return ApiError(503, "LOCAL_SERVICE_UNAVAILABLE", str(error) or name)


class StoryApi:
    """Dependency-injected, socket-free request dispatcher."""

    def __init__(
        self,
        repository: RepositoryLike | None,
        h3_client: H3Like | None,
        render_service: RenderLike | None = None,
        asset_service: AssetLike | None = None,
        *,
        version: str = "0.1.0",
        local_root: str | os.PathLike[str] | None = None,
        max_media_bytes: int = MAX_MEDIA_BYTES,
    ) -> None:
        self.repository = repository
        self.h3_client = h3_client
        self.render_service = render_service
        self.asset_service = asset_service
        self.version = version
        self.local_root = Path(local_root or Path.cwd()).expanduser().resolve()
        if (
            isinstance(max_media_bytes, bool)
            or not isinstance(max_media_bytes, int)
            or max_media_bytes <= 0
        ):
            raise ValueError("max_media_bytes must be a positive integer")
        self.max_media_bytes = int(max_media_bytes)

    def dispatch(
        self,
        method: str,
        target: str,
        headers: Mapping[str, str] | None = None,
        body: bytes = b"",
    ) -> ApiResponse:
        method = method.upper()
        supplied_headers = _headers_lower(headers or {})
        origin = supplied_headers.get("origin")
        cors_headers: dict[str, str] = {}
        if origin is not None:
            if origin not in ALLOWED_BROWSER_ORIGINS:
                return self._error_response(
                    ApiError(403, "ORIGIN_FORBIDDEN", "Browser origin is not allowed")
                )
            cors_headers = {
                "Access-Control-Allow-Origin": origin,
                "Vary": "Origin",
            }

        if method == "OPTIONS":
            return ApiResponse(
                204,
                None,
                {
                    **cors_headers,
                    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                    "Access-Control-Allow-Headers": (
                        "Accept, Content-Type, X-H3-Story-Request"
                    ),
                    "Access-Control-Max-Age": "600",
                },
            )

        if method == "POST" and supplied_headers.get(
            MUTATION_HEADER.casefold()
        ) != MUTATION_HEADER_VALUE:
            return self._with_headers(
                self._error_response(
                    ApiError(
                        403,
                        "MUTATION_HEADER_REQUIRED",
                        f"{MUTATION_HEADER}: {MUTATION_HEADER_VALUE} is required",
                    )
                ),
                cors_headers,
            )

        try:
            response = self._route(method, target, supplied_headers, body)
        except ApiError as error:
            response = self._error_response(error)
        except Exception as error:  # Dependency exception translation boundary.
            response = self._error_response(_structured_exception(error))
        return self._with_headers(response, cors_headers)

    def _route(
        self,
        method: str,
        target: str,
        headers: Mapping[str, str],
        body: bytes,
    ) -> ApiResponse:
        parsed = urlsplit(target)
        if parsed.scheme or parsed.netloc:
            raise ApiError(
                400,
                "INVALID_REQUEST_TARGET",
                "Request target must be an origin-relative local path",
            )
        path = parsed.path
        if method == "GET" and path == "/api/health":
            return ApiResponse(200, self._health())
        if method == "GET" and path == "/api/projects":
            repository = self._repository()
            return ApiResponse(
                200, [_project_wire(item) for item in repository.list_projects()]
            )
        if method == "POST" and path == "/api/projects":
            repository = self._repository()
            payload = self._json_object(headers, body)
            result = repository.create_project(_load_project_create(payload))
            return ApiResponse(201, _project_create_wire(result))
        if method == "GET" and path == "/api/h3/state":
            h3_client = self._h3()
            state = h3_client.get_state()
            raw = getattr(state, "raw", None)
            return ApiResponse(200, _camelize(raw if isinstance(raw, Mapping) else state))
        h3_job = _H3_JOB_ROUTE.fullmatch(path)
        if method == "GET" and h3_job is not None:
            job = self._h3().get_job(h3_job.group("job"))
            raw = getattr(job, "raw", None)
            return ApiResponse(200, _camelize(raw if isinstance(raw, Mapping) else job))

        asset_create = _ASSET_CREATE_ROUTE.fullmatch(path)
        if asset_create is not None:
            if method != "POST":
                raise ApiError(404, "ROUTE_NOT_FOUND", "API route was not found")
            project_id = _safe_identifier(
                asset_create.group("project"), "projectId"
            )
            content_type = headers.get("content-type", "")
            media_type = content_type.split(";", 1)[0].strip().casefold()
            upload: AssetUpload | None = None
            if media_type == "application/json":
                payload = self._json_object(headers, body)
            elif media_type == "multipart/form-data":
                payload, upload = _parse_multipart_asset(content_type, body)
            else:
                raise ApiError(
                    400,
                    "INVALID_CONTENT_TYPE",
                    "Content-Type must be application/json or multipart/form-data",
                )
            result = self._assets().create_asset(
                project_id=project_id,
                spec=_load_asset_create(payload),
                upload=upload,
            )
            return ApiResponse(201, _asset_create_wire(result))

        media = _MEDIA_ROUTE.fullmatch(path)
        if media is not None:
            if method != "GET":
                raise ApiError(404, "ROUTE_NOT_FOUND", "API route was not found")
            project_id = _safe_identifier(media.group("project"), "projectId")
            asset_id = _safe_identifier(media.group("asset"), "assetId")
            query = parse_qs(parsed.query, keep_blank_values=True)
            session_values = query.get("sessionId", [])
            if len(session_values) != 1:
                raise ApiError(
                    400,
                    "INVALID_REQUEST",
                    "exactly one sessionId query parameter is required",
                )
            session_id = _safe_identifier(session_values[0], "sessionId")
            return self._media_response(
                project_id,
                asset_id,
                session_id,
                headers.get("range"),
            )

        render_submit = _RENDER_SUBMIT_ROUTE.fullmatch(path)
        if render_submit is not None:
            if method != "POST":
                raise ApiError(404, "ROUTE_NOT_FOUND", "API route was not found")
            project_id = _safe_identifier(render_submit.group("project"), "projectId")
            shot_id = _safe_identifier(render_submit.group("shot"), "shotId")
            payload = self._json_object(headers, body)
            session_id = _safe_identifier(
                _required_text(payload, "sessionId"), "sessionId"
            )
            expected_revision = self._expected_revision(payload)
            request = payload.get("request")
            if not isinstance(request, Mapping):
                raise ApiError(400, "INVALID_REQUEST", "request must be an object")
            result = self._render().submit_render(
                session_id=session_id,
                project_id=project_id,
                expected_revision=expected_revision,
                shot_id=shot_id,
                request=dict(request),
            )
            return ApiResponse(202, _camelize(result))

        render_sync = _RENDER_SYNC_ROUTE.fullmatch(path)
        if render_sync is not None:
            if method != "POST":
                raise ApiError(404, "ROUTE_NOT_FOUND", "API route was not found")
            project_id = _safe_identifier(render_sync.group("project"), "projectId")
            local_job_id = _safe_identifier(render_sync.group("job"), "localJobId")
            payload = self._json_object(headers, body)
            session_id = _safe_identifier(
                _required_text(payload, "sessionId"), "sessionId"
            )
            expected_revision = self._expected_revision(payload)
            download_result = payload.get("downloadResult", True)
            if not isinstance(download_result, bool):
                raise ApiError(
                    400, "INVALID_REQUEST", "downloadResult must be a boolean"
                )
            extract_end_frame = payload.get("extractEndFrame", False)
            if not isinstance(extract_end_frame, bool):
                raise ApiError(
                    400, "INVALID_REQUEST", "extractEndFrame must be a boolean"
                )
            result = self._render().sync_render(
                session_id=session_id,
                project_id=project_id,
                expected_revision=expected_revision,
                local_job_id=local_job_id,
                download_result=download_result,
                extract_end_frame=extract_end_frame,
            )
            return ApiResponse(200, _camelize(result))

        match = _PROJECT_ROUTE.fullmatch(path)
        if match is None:
            raise ApiError(404, "ROUTE_NOT_FOUND", "API route was not found")
        project_id = _safe_identifier(match.group("project"), "projectId")
        action = match.group("action")
        repository = self._repository()

        if method == "GET" and action == "context":
            query = parse_qs(parsed.query, keep_blank_values=True)
            session_values = query.get("sessionId", [])
            if len(session_values) != 1:
                raise ApiError(
                    400,
                    "INVALID_REQUEST",
                    "exactly one sessionId query parameter is required",
                )
            session_id = _safe_identifier(session_values[0], "sessionId")
            context = _call_with_known_keywords(
                repository.get_context,
                {"session_id": session_id, "project_id": project_id},
            )
            return ApiResponse(200, _context_wire(context))

        if method != "POST":
            raise ApiError(404, "ROUTE_NOT_FOUND", "API route was not found")
        payload = self._json_object(headers, body)

        if action == "activate":
            agent_id = _required_text(payload, "agentId")
            binding = _call_with_known_keywords(
                repository.activate_session,
                {"project_id": project_id, "agent_id": agent_id},
            )
            return ApiResponse(201, _session_wire(binding))
        if action == "patches":
            session_id, expected_revision, patch = _load_patch(payload)
            result = _call_with_known_keywords(
                repository.commit_patch,
                {
                    "project_id": project_id,
                    "session_id": session_id,
                    "expected_revision": expected_revision,
                    "patch": patch,
                },
            )
            return ApiResponse(201, _commit_wire(result))
        if action == "handoffs":
            session_id = _safe_identifier(
                _required_text(payload, "sessionId"), "sessionId"
            )
            capsule = _call_with_known_keywords(
                repository.create_handoff_capsule,
                {"project_id": project_id, "session_id": session_id},
            )
            return ApiResponse(201, _handoff_wire(capsule))
        raise ApiError(404, "ROUTE_NOT_FOUND", "API route was not found")

    def _json_object(
        self, headers: Mapping[str, str], body: bytes
    ) -> Mapping[str, Any]:
        if len(body) > MAX_JSON_BYTES:
            raise ApiError(
                413,
                "PAYLOAD_TOO_LARGE",
                f"JSON request exceeds the {MAX_JSON_BYTES}-byte limit",
            )
        content_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            raise ApiError(400, "INVALID_CONTENT_TYPE", "Content-Type must be application/json")
        try:
            decoded = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ApiError(400, "INVALID_JSON", "Request body is not valid UTF-8 JSON") from error
        if not isinstance(decoded, Mapping):
            raise ApiError(400, "INVALID_REQUEST", "JSON request body must be an object")
        return decoded

    def _repository(self) -> RepositoryLike:
        if self.repository is None:
            raise ApiError(503, "PROJECT_CORE_UNAVAILABLE", "Project Core is unavailable")
        return self.repository

    def _render(self) -> RenderLike:
        if self.render_service is None:
            raise ApiError(
                503, "RENDER_SERVICE_UNAVAILABLE", "Render service is unavailable"
            )
        return self.render_service

    def _assets(self) -> AssetLike:
        if self.asset_service is None:
            raise ApiError(
                503, "ASSET_SERVICE_UNAVAILABLE", "Asset service is unavailable"
            )
        return self.asset_service

    def _media_response(
        self,
        project_id: str,
        asset_id: str,
        session_id: str,
        range_header: str | None,
    ) -> ApiResponse:
        repository = self._repository()
        context = _call_with_known_keywords(
            repository.get_context,
            {"session_id": session_id, "project_id": project_id},
        )

        context_project_id = _member(context, "project_id", "projectId")
        context_session_id = _member(context, "session_id", "sessionId")
        if context_project_id != project_id or (
            context_session_id is not None and context_session_id != session_id
        ):
            raise ApiError(
                403,
                "PROJECT_BOUNDARY_ERROR",
                "Project Core returned a context outside the requested session boundary",
            )

        asset: Any | None = None
        entities = _member(context, "entities", default=())
        if not isinstance(entities, (tuple, list)):
            raise ApiError(
                503,
                "PROJECT_CORE_INTEGRITY_ERROR",
                "Project Core returned an invalid entity collection",
            )
        for entity in entities:
            if (
                _member(entity, "entity_type", "entityType") == "asset"
                and _member(entity, "entity_id", "entityId") == asset_id
            ):
                asset = entity
                break
        if asset is None:
            raise ApiError(
                404,
                "MEDIA_ASSET_NOT_FOUND",
                "The requested preview asset is not registered in this project revision",
            )

        payload = _member(asset, "payload")
        if not isinstance(payload, Mapping):
            raise ApiError(
                409,
                "MEDIA_ASSET_INVALID",
                "The registered asset payload is invalid",
            )
        project_root = _registered_relative_path(
            _member(context, "project_root", "projectRoot"),
            "projectRoot",
        )
        relative_path = _registered_relative_path(
            _member(payload, "relative_path", "relativePath"),
            "relativePath",
        )
        extension = relative_path.suffix.casefold()
        media_contract = ASSET_MEDIA_TYPES.get(extension)
        if media_contract is None:
            raise ApiError(
                415,
                "MEDIA_TYPE_UNSUPPORTED",
                "The registered asset extension is not previewable",
            )
        expected_mime, expected_media_kind = media_contract
        registered_media_kind = _member(payload, "media_kind", "mediaKind")
        if registered_media_kind is None:
            legacy_kind = _member(payload, "kind")
            registered_media_kind = (
                legacy_kind if legacy_kind in {"image", "video", "audio"} else None
            )
        if registered_media_kind != expected_media_kind:
            raise ApiError(
                415,
                "MEDIA_TYPE_UNSUPPORTED",
                "The registered media kind does not match its file extension",
            )
        registered_mime = _member(payload, "mime_type", "mimeType")
        if (
            not isinstance(registered_mime, str)
            or registered_mime.split(";", 1)[0].strip().casefold() != expected_mime
        ):
            raise ApiError(
                415,
                "MEDIA_TYPE_UNSUPPORTED",
                "The registered asset MIME type does not match its file extension",
            )

        try:
            project_directory = (self.local_root / project_root).resolve(strict=True)
        except (OSError, RuntimeError) as error:
            raise ApiError(
                404,
                "PROJECT_DIRECTORY_NOT_FOUND",
                "The registered project directory is unavailable",
            ) from error
        if not _contained_path(self.local_root, project_directory):
            raise ApiError(
                403,
                "MEDIA_PATH_UNSAFE",
                "The registered project directory escapes the local Story Studio root",
            )

        try:
            media_path = (project_directory / relative_path).resolve(strict=True)
        except FileNotFoundError as error:
            raise ApiError(
                404,
                "MEDIA_FILE_NOT_FOUND",
                "The registered media file does not exist",
            ) from error
        except (OSError, RuntimeError) as error:
            raise ApiError(
                409,
                "MEDIA_FILE_UNAVAILABLE",
                "The registered media file cannot be resolved",
            ) from error
        if not _contained_path(project_directory, media_path):
            raise ApiError(
                403,
                "MEDIA_PATH_UNSAFE",
                "The registered media path escapes its project directory",
            )

        try:
            media_stat = media_path.stat()
        except OSError as error:
            raise ApiError(
                404,
                "MEDIA_FILE_NOT_FOUND",
                "The registered media file is unavailable",
            ) from error
        if not stat.S_ISREG(media_stat.st_mode):
            raise ApiError(
                409,
                "MEDIA_FILE_INVALID",
                "The registered media path is not a regular file",
            )
        file_size = media_stat.st_size
        if file_size <= 0:
            raise ApiError(
                409,
                "MEDIA_FILE_INVALID",
                "The registered media file is empty",
            )
        if file_size > self.max_media_bytes:
            raise ApiError(
                413,
                "MEDIA_TOO_LARGE",
                "The registered media exceeds the local playback size limit",
                {"fileSize": file_size, "maxMediaBytes": self.max_media_bytes},
            )

        registered_size = _member(payload, "byte_size", "byteSize")
        if registered_size is not None:
            if (
                isinstance(registered_size, bool)
                or not isinstance(registered_size, int)
                or registered_size < 0
                or registered_size != file_size
            ):
                raise ApiError(
                    409,
                    "MEDIA_SIZE_MISMATCH",
                    "The media file size does not match its Project Core registration",
                    {"fileSize": file_size, "registeredSize": registered_size},
                )

        offset, length, partial = _parse_media_range(range_header, file_size)
        response_headers = {
            "Content-Type": expected_mime,
            "Content-Length": str(length),
            "Accept-Ranges": "bytes",
            "Content-Disposition": "inline",
        }
        if partial:
            response_headers["Content-Range"] = (
                f"bytes {offset}-{offset + length - 1}/{file_size}"
            )
        return ApiResponse(
            206 if partial else 200,
            FileSlice(media_path, offset, length, file_size),
            response_headers,
        )

    @staticmethod
    def _expected_revision(payload: Mapping[str, Any]) -> int:
        expected_revision = payload.get("expectedRevision")
        if (
            isinstance(expected_revision, bool)
            or not isinstance(expected_revision, int)
            or expected_revision < 0
        ):
            raise ApiError(
                400,
                "INVALID_REQUEST",
                "expectedRevision must be a non-negative integer",
            )
        return expected_revision

    def _h3(self) -> H3Like:
        if self.h3_client is None:
            raise ApiError(503, "H3_UNAVAILABLE", "H3 Studio is unavailable")
        return self.h3_client

    def _health(self) -> dict[str, str]:
        project_core = "ready"
        try:
            self._repository().list_projects()
        except Exception:
            project_core = "unavailable"

        h3_status = "offline"
        try:
            state = self._h3().get_state()
            capabilities = _attribute(state, "capabilities", None)
            if capabilities is None and isinstance(state, Mapping):
                capabilities = state.get("capabilities")
            ready = _attribute(capabilities, "ready", True)
            active_job_id = _attribute(state, "active_job_id", None)
            if active_job_id is None and isinstance(state, Mapping):
                active_job_id = state.get("active_job_id", state.get("activeJobId"))
            h3_status = "incompatible" if ready is False else (
                "busy" if active_job_id else "ready"
            )
        except Exception:
            h3_status = "offline"
        return {
            "status": (
                "ok"
                if project_core == "ready" and h3_status in {"ready", "busy"}
                else "degraded"
            ),
            "version": self.version,
            "projectCore": project_core,
            "h3": h3_status,
        }

    @staticmethod
    def _error_response(error: ApiError) -> ApiResponse:
        payload: dict[str, Any] = {
            "error": {"code": error.code, "message": error.message}
        }
        if error.details:
            payload["error"]["details"] = _camelize(error.details)
        return ApiResponse(error.status, payload, error.headers)

    @staticmethod
    def _with_headers(response: ApiResponse, headers: Mapping[str, str]) -> ApiResponse:
        return ApiResponse(
            response.status,
            response.body,
            {**dict(response.headers or {}), **dict(headers)},
        )


def make_handler(api: StoryApi) -> type[BaseHTTPRequestHandler]:
    class StoryRequestHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "H3StoryStudio/0.1"

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler naming contract
            self._dispatch()

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler naming contract
            self._dispatch()

        def do_OPTIONS(self) -> None:  # noqa: N802 - stdlib handler naming contract
            self._dispatch()

        def _dispatch(self) -> None:
            body = b""
            if self.command == "POST":
                raw_length = self.headers.get("Content-Length")
                try:
                    content_length = int(raw_length) if raw_length is not None else 0
                except ValueError:
                    self._send(
                        api._error_response(
                            ApiError(
                                400,
                                "INVALID_CONTENT_LENGTH",
                                "Content-Length is invalid",
                            )
                        )
                    )
                    return
                if content_length < 0:
                    self._send(
                        api._error_response(
                            ApiError(
                                400,
                                "INVALID_CONTENT_LENGTH",
                                "Content-Length is invalid",
                            )
                        )
                    )
                    return
                request_path = urlsplit(self.path).path
                request_media_type = (
                    self.headers.get("Content-Type", "")
                    .split(";", 1)[0]
                    .strip()
                    .casefold()
                )
                multipart_asset = (
                    _ASSET_CREATE_ROUTE.fullmatch(request_path) is not None
                    and request_media_type == "multipart/form-data"
                )
                maximum_body = (
                    MAX_ASSET_MULTIPART_BYTES if multipart_asset else MAX_JSON_BYTES
                )
                if content_length > maximum_body:
                    self._send(
                        api._error_response(
                            ApiError(
                                413,
                                "PAYLOAD_TOO_LARGE",
                                f"request exceeds the {maximum_body}-byte limit",
                            )
                        )
                    )
                    return
                body = self.rfile.read(content_length)
            response = api.dispatch(self.command, self.path, dict(self.headers.items()), body)
            self._send(response)

        def _send(self, response: ApiResponse) -> None:
            encoded = b""
            media_source = None
            media_body = response.body if isinstance(response.body, FileSlice) else None
            if media_body is not None:
                try:
                    # ``media_body.path`` was strict-resolved by StoryApi.  A
                    # second resolve prevents opening it if an in-project file
                    # was replaced by a symlink after dispatch.
                    if media_body.path.resolve(strict=True) != media_body.path:
                        raise OSError("media path changed after validation")
                    media_source = media_body.path.open("rb")
                    opened_stat = os.fstat(media_source.fileno())
                    if (
                        not stat.S_ISREG(opened_stat.st_mode)
                        or opened_stat.st_size != media_body.file_size
                    ):
                        raise OSError("media file changed after validation")
                    media_source.seek(media_body.offset)
                except OSError:
                    if media_source is not None:
                        media_source.close()
                    self._send(
                        api._error_response(
                            ApiError(
                                409,
                                "MEDIA_FILE_CHANGED",
                                "The video file changed while preparing playback",
                            )
                        )
                    )
                    return
            elif response.body is not None:
                encoded = json.dumps(
                    response.body,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")

            response_headers = dict(response.headers or {})

            def has_header(name: str) -> bool:
                folded = name.casefold()
                return any(key.casefold() == folded for key in response_headers)

            self.send_response(response.status)
            if response.body is not None and media_body is None and not has_header("Content-Type"):
                self.send_header("Content-Type", "application/json; charset=utf-8")
            if not has_header("Content-Length"):
                self.send_header("Content-Length", str(len(encoded)))
            if not has_header("Cache-Control"):
                self.send_header("Cache-Control", "no-store")
            if not has_header("X-Content-Type-Options"):
                self.send_header("X-Content-Type-Options", "nosniff")
            for name, value in response_headers.items():
                self.send_header(name, value)
            self.end_headers()
            if media_source is not None and media_body is not None:
                try:
                    remaining = media_body.length
                    while remaining:
                        chunk = media_source.read(
                            min(remaining, MEDIA_STREAM_CHUNK_BYTES)
                        )
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
                except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                    return
                finally:
                    media_source.close()
            elif encoded:
                try:
                    self.wfile.write(encoded)
                except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                    # A browser may cancel an obsolete health request during
                    # navigation or React development remounting.  The request
                    # is read-only and already finished; keep the sidecar log
                    # free of a misleading server traceback.
                    return

        def log_message(self, format: str, *args: Any) -> None:
            # Keep the local terminal useful while avoiding default reverse DNS
            # formatting.  No body, prompt, or asset content is logged.
            print(f"[{self.log_date_time_string()}] {self.address_string()} {format % args}")

    return StoryRequestHandler


class StoryHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def create_server(
    repository: RepositoryLike,
    h3_client: H3Like,
    render_service: RenderLike | None = None,
    asset_service: AssetLike | None = None,
    *,
    version: str = "0.1.0",
    local_root: str | os.PathLike[str] | None = None,
) -> StoryHTTPServer:
    """Create the production server on the one permitted numeric loopback."""

    api = StoryApi(
        repository,
        h3_client,
        render_service,
        asset_service,
        version=version,
        local_root=local_root,
    )
    return StoryHTTPServer((HOST, PORT), make_handler(api))


def build_default_dependencies(local_root: str | os.PathLike[str]) -> tuple[Any, Any]:
    """Construct real local dependencies lazily; importing this module is inert."""

    try:
        from .h3_adapter import AssetResolver, H3Client, StandardLibraryTransport
        from .h3_story_core.repository import (
            ProjectAlreadyExistsError,
            ProjectRepository,
        )
        from .seed_projects import demo_project_manifests
    except ImportError:  # Direct ``python sidecar/http_server.py`` execution.
        import sys

        workspace_root = str(Path(__file__).resolve().parent.parent)
        if workspace_root not in sys.path:
            sys.path.insert(0, workspace_root)
        from sidecar.h3_adapter import (  # type: ignore[no-redef]
            AssetResolver,
            H3Client,
            StandardLibraryTransport,
        )
        from sidecar.h3_story_core.repository import (  # type: ignore[no-redef]
            ProjectAlreadyExistsError,
            ProjectRepository,
        )
        from sidecar.seed_projects import demo_project_manifests  # type: ignore[no-redef]

    root = Path(local_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    seed_repository = ProjectRepository(root)
    try:
        existing_project_ids = {
            summary.project_id for summary in seed_repository.list_projects()
        }
        for manifest in demo_project_manifests():
            if manifest.project_id in existing_project_ids:
                continue
            try:
                seed_repository.seed_project(manifest)
            except ProjectAlreadyExistsError:
                # Harmless if another local startup seeded between the listing
                # and the insert.  Never update or replace existing projects.
                pass
    finally:
        seed_repository.close()

    repository = RepositoryFactoryProxy(lambda: ProjectRepository(root))
    h3_client = H3Client(StandardLibraryTransport(), AssetResolver((root,), {}))
    return repository, h3_client


def main() -> None:
    local_root = os.environ.get("H3_STORY_ROOT", str(Path.cwd()))
    repository, h3_client = build_default_dependencies(local_root)
    try:
        from .render_service import RenderService
        from .asset_service import AssetService
    except ImportError:  # Direct ``python sidecar/http_server.py`` execution.
        from sidecar.render_service import RenderService  # type: ignore[no-redef]
        from sidecar.asset_service import AssetService  # type: ignore[no-redef]
    render_service = RenderService(repository, h3_client, local_root)
    asset_service = AssetService(repository, local_root)
    server = create_server(
        repository,
        h3_client,
        render_service,
        asset_service,
        local_root=local_root,
    )
    print(f"H3 Story Studio sidecar listening on http://{HOST}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        close = getattr(repository, "close", None)
        if callable(close):
            close()


if __name__ == "__main__":
    main()


__all__ = [
    "ALLOWED_BROWSER_ORIGINS",
    "ApiResponse",
    "FileSlice",
    "HOST",
    "MAX_JSON_BYTES",
    "MAX_MEDIA_BYTES",
    "MUTATION_HEADER",
    "MUTATION_HEADER_VALUE",
    "PORT",
    "RepositoryFactoryProxy",
    "StoryApi",
    "StoryHTTPServer",
    "build_default_dependencies",
    "create_server",
    "make_handler",
]
