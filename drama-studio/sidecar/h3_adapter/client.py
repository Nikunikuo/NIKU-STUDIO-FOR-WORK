"""Safe loopback REST client for the pinned local MiniMax H3 Studio API.

The adapter accepts opaque Story Studio asset IDs.  Only ``AssetResolver`` may
turn those IDs into filesystem paths, and it will return a file only after the
file's real path has been proven to live below an explicitly allowed root.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import http.client
import json
import os
from pathlib import Path
import re
import secrets
import threading
import time
from types import MappingProxyType
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence
from urllib.parse import urlsplit

from .models import (
    H3AssetError,
    H3ContractError,
    H3HTTPError,
    H3Job,
    H3PollTimeout,
    H3Result,
    H3State,
    H3SubmitRequest,
    H3TransportError,
    H3UnsupportedOperation,
    H3_MUTATION_HEADER,
    H3_MUTATION_HEADER_VALUE,
    H3_ORIGIN,
    RequestBody,
    Transport,
    TransportRequest,
    TransportResponse,
)


MAX_UPLOAD_BYTES = 2 * 1024**3
MAX_REQUEST_BYTES = 8 * 1024**3
IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".bmp"})
VIDEO_EXTENSIONS = frozenset({".mp4", ".mov", ".mkv", ".webm"})
AUDIO_EXTENSIONS = frozenset({".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg"})
MEDIA_MIME_KINDS = MappingProxyType(
    {
        "image/png": "image",
        "image/jpeg": "image",
        "image/jpg": "image",
        "image/webp": "image",
        "image/bmp": "image",
        "image/x-ms-bmp": "image",
        "video/mp4": "video",
        "video/quicktime": "video",
        "video/x-matroska": "video",
        "video/webm": "video",
        "audio/wav": "audio",
        "audio/x-wav": "audio",
        "audio/mpeg": "audio",
        "audio/flac": "audio",
        "audio/x-flac": "audio",
        "audio/mp4": "audio",
        "audio/aac": "audio",
        "audio/ogg": "audio",
    }
)
MEDIA_EXTENSION_MIMES = MappingProxyType(
    {
        ".png": frozenset({"image/png"}),
        ".jpg": frozenset({"image/jpeg", "image/jpg"}),
        ".jpeg": frozenset({"image/jpeg", "image/jpg"}),
        ".webp": frozenset({"image/webp"}),
        ".bmp": frozenset({"image/bmp", "image/x-ms-bmp"}),
        ".mp4": frozenset({"video/mp4"}),
        ".mov": frozenset({"video/quicktime"}),
        ".mkv": frozenset({"video/x-matroska"}),
        ".webm": frozenset({"video/webm"}),
        ".wav": frozenset({"audio/wav", "audio/x-wav"}),
        ".mp3": frozenset({"audio/mpeg"}),
        ".flac": frozenset({"audio/flac", "audio/x-flac"}),
        ".m4a": frozenset({"audio/mp4"}),
        ".aac": frozenset({"audio/aac"}),
        ".ogg": frozenset({"audio/ogg"}),
    }
)
_BOUNDARY = re.compile(r"^[A-Za-z0-9_-]{1,70}$")
_JOB_ID = re.compile(r"^[0-9a-f]{12}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _is_below(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def media_kind(path: str | os.PathLike[str], mime_type: str | None = None) -> str:
    """Return H3's image/video/audio kind and reject MIME/extension drift.

    Project Core records the declared MIME type while H3 ultimately selects a
    multipart path by extension.  Requiring both views to agree prevents a
    registered image from becoming a video (or vice versa) between the story
    layer and the renderer boundary.
    """

    suffix = Path(path).suffix.casefold()
    if suffix in IMAGE_EXTENSIONS:
        suffix_kind = "image"
    elif suffix in VIDEO_EXTENSIONS:
        suffix_kind = "video"
    elif suffix in AUDIO_EXTENSIONS:
        suffix_kind = "audio"
    else:
        raise H3ContractError(
            f"unsupported H3 upload extension for asset {Path(path).name!r}"
        )

    if mime_type is None:
        return suffix_kind
    if not isinstance(mime_type, str) or not mime_type.strip():
        raise H3AssetError("registered asset MIME type must be non-empty text")
    normalized_mime = mime_type.split(";", 1)[0].strip().casefold()
    try:
        mime_kind = MEDIA_MIME_KINDS[normalized_mime]
    except KeyError as exc:
        raise H3AssetError(
            f"registered asset MIME type is unsupported by H3: {normalized_mime!r}"
        ) from exc
    if mime_kind != suffix_kind or normalized_mime not in MEDIA_EXTENSION_MIMES[suffix]:
        raise H3AssetError(
            f"registered asset MIME type {normalized_mime!r} does not match "
            f"the upload extension {suffix!r}"
        )
    return suffix_kind


@dataclass(frozen=True, slots=True)
class AssetDescriptor:
    """Immutable upload identity carried from Project validation to H3.

    ``path`` is the file H3 must actually stream.  Story Studio uses a private
    per-submission snapshot for that path, while ``source_path`` records the
    registered Project file from which the snapshot was made.  Size and digest
    are checked again while the multipart body yields the exact upload bytes.
    """

    path: str | os.PathLike[str]
    mime_type: str | None = None
    byte_size: int | None = None
    sha256: str | None = None
    source_path: str | os.PathLike[str] | None = None


class AssetResolver:
    """Resolve opaque asset IDs through an allowlisted index and real roots.

    An ID is never interpreted as a path.  Relative values in ``asset_index``
    are resolved against each allowed root, while absolute values must already
    be under one of those roots.  ``Path.resolve(strict=True)`` also closes the
    symlink/junction escape case on Windows.
    """

    def __init__(
        self,
        allowed_roots: Iterable[str | os.PathLike[str]],
        asset_index: Mapping[
            str, str | os.PathLike[str] | AssetDescriptor
        ],
    ) -> None:
        roots: list[Path] = []
        for supplied_root in allowed_roots:
            root = Path(supplied_root).resolve(strict=True)
            if not root.is_dir():
                raise H3AssetError(f"allowed asset root is not a directory: {root}")
            if root not in roots:
                roots.append(root)
        if not roots:
            raise H3AssetError("at least one allowed asset root is required")
        self._roots = tuple(roots)
        # Each resolver owns one immutable index.  RenderService supplies a
        # fresh project-scoped resolver per submission, so identical opaque
        # asset IDs in different projects can never overwrite global state.
        self._asset_index = MappingProxyType(dict(asset_index))

    @property
    def allowed_roots(self) -> tuple[Path, ...]:
        return self._roots

    def resolve(self, asset_id: str) -> Path:
        return Path(self.resolve_descriptor(asset_id).path)

    def resolve_descriptor(self, asset_id: str) -> AssetDescriptor:
        """Resolve one immutable descriptor without consulting global state."""

        if not isinstance(asset_id, str) or not asset_id:
            raise H3AssetError("asset ID must be a non-empty string")
        try:
            indexed_entry = self._asset_index[asset_id]
        except KeyError as exc:
            raise H3AssetError(f"unknown asset ID: {asset_id!r}") from exc

        descriptor = (
            indexed_entry
            if isinstance(indexed_entry, AssetDescriptor)
            else AssetDescriptor(path=indexed_entry)
        )
        indexed_value = descriptor.path
        indexed = Path(indexed_value)
        if ".." in indexed.parts:
            raise H3AssetError(f"asset index contains a traversal path for {asset_id!r}")
        candidates = (indexed,) if indexed.is_absolute() else tuple(
            root / indexed for root in self._roots
        )
        missing: list[str] = []
        unsafe: list[str] = []
        for candidate in candidates:
            try:
                real = candidate.resolve(strict=True)
            except (FileNotFoundError, NotADirectoryError, OSError):
                missing.append(os.fspath(candidate))
                continue
            if not any(_is_below(real, root) for root in self._roots):
                unsafe.append(os.fspath(real))
                continue
            if not real.is_file():
                raise H3AssetError(f"asset {asset_id!r} is not a regular file")
            actual_size = real.stat().st_size
            expected_size = descriptor.byte_size
            if (
                expected_size is not None
                and (
                    isinstance(expected_size, bool)
                    or not isinstance(expected_size, int)
                    or expected_size < 0
                )
            ):
                raise H3AssetError(
                    f"asset {asset_id!r} has an invalid immutable byte size"
                )
            if expected_size is not None and actual_size != expected_size:
                raise H3AssetError(
                    f"asset {asset_id!r} snapshot byte size changed before upload"
                )

            expected_digest = descriptor.sha256
            if expected_digest is not None:
                if not isinstance(expected_digest, str):
                    raise H3AssetError(
                        f"asset {asset_id!r} has an invalid immutable SHA-256"
                    )
                expected_digest = expected_digest.strip().casefold()
                if _SHA256.fullmatch(expected_digest) is None:
                    raise H3AssetError(
                        f"asset {asset_id!r} has an invalid immutable SHA-256"
                    )

            mime_type = descriptor.mime_type
            if mime_type is None:
                # Legacy path-only indexes have no registered MIME.  Derive a
                # stable canonical value from H3's exact extension contract,
                # independent of host-specific ``mimetypes`` registries.
                media_kind(real)
                mime_type = sorted(MEDIA_EXTENSION_MIMES[real.suffix.casefold()])[0]
            else:
                if not isinstance(mime_type, str) or not mime_type.strip():
                    raise H3AssetError(
                        f"asset {asset_id!r} has an invalid immutable MIME type"
                    )
                mime_type = mime_type.split(";", 1)[0].strip().casefold()
                media_kind(real, mime_type)

            return AssetDescriptor(
                path=real,
                mime_type=mime_type,
                byte_size=actual_size if expected_size is None else expected_size,
                sha256=expected_digest,
                source_path=descriptor.source_path,
            )

        if unsafe:
            raise H3AssetError(
                f"asset {asset_id!r} resolves outside every allowed root: {unsafe[0]}"
            )
        location = missing[0] if missing else os.fspath(indexed)
        raise H3AssetError(f"asset {asset_id!r} does not exist: {location}")


def _quoted_disposition_value(value: str) -> str:
    """Make a conservative quoted Content-Disposition parameter value."""

    return value.replace("\r", "").replace("\n", "").replace("\\", "_").replace('"', "%22")


@dataclass(frozen=True)
class MultipartFile:
    field_name: str
    path: Path
    content_type: str
    expected_size: int
    expected_sha256: str | None = None


class MultipartBody:
    """Replayable multipart body whose large file parts remain on disk."""

    def __init__(
        self,
        boundary: str,
        fields: Sequence[tuple[str, str]],
        files: Sequence[MultipartFile],
    ) -> None:
        if not _BOUNDARY.fullmatch(boundary):
            raise H3ContractError("multipart boundary contains unsafe characters")
        self.boundary = boundary
        self.fields = tuple(fields)
        self.files = tuple(files)
        self._field_parts = tuple(self._encode_field(name, value) for name, value in fields)
        self._file_headers = tuple(self._encode_file_header(item) for item in files)
        self._closing = f"--{boundary}--\r\n".encode("ascii")
        self._content_length = (
            sum(len(part) for part in self._field_parts)
            + sum(
                len(header) + item.expected_size + 2
                for header, item in zip(self._file_headers, self.files)
            )
            + len(self._closing)
        )

    def _encode_field(self, name: str, value: str) -> bytes:
        safe_name = _quoted_disposition_value(name)
        return (
            f"--{self.boundary}\r\n"
            f'Content-Disposition: form-data; name="{safe_name}"\r\n\r\n'
            f"{value}\r\n"
        ).encode("utf-8")

    def _encode_file_header(self, item: MultipartFile) -> bytes:
        safe_name = _quoted_disposition_value(item.field_name)
        safe_filename = _quoted_disposition_value(item.path.name)
        return (
            f"--{self.boundary}\r\n"
            f'Content-Disposition: form-data; name="{safe_name}"; '
            f'filename="{safe_filename}"\r\n'
            f"Content-Type: {item.content_type}\r\n\r\n"
        ).encode("utf-8")

    @property
    def content_length(self) -> int:
        return self._content_length

    def iter_bytes(self, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        for part in self._field_parts:
            yield part
        for header, item in zip(self._file_headers, self.files):
            yield header
            byte_count = 0
            digest = sha256() if item.expected_sha256 is not None else None
            with item.path.open("rb") as handle:
                while True:
                    chunk = handle.read(chunk_size)
                    if not chunk:
                        break
                    byte_count += len(chunk)
                    if byte_count > item.expected_size:
                        raise H3AssetError(
                            f"asset snapshot {item.path.name!r} grew during upload"
                        )
                    if digest is not None:
                        digest.update(chunk)
                    yield chunk
            if byte_count != item.expected_size:
                raise H3AssetError(
                    f"asset snapshot {item.path.name!r} changed size during upload"
                )
            if (
                digest is not None
                and digest.hexdigest() != item.expected_sha256
            ):
                # No trailing CRLF or closing boundary has been yielded, so the
                # renderer cannot accept this as a complete multipart request.
                raise H3AssetError(
                    f"asset snapshot {item.path.name!r} changed content during upload"
                )
            yield b"\r\n"
        yield self._closing


class StandardLibraryTransport:
    """Minimal streaming transport pinned to the numeric loopback H3 origin.

    ``H3Client`` still requires transport injection, so tests can prove the
    exact request without touching the live renderer.  Applications may opt in
    to real loopback I/O by explicitly constructing this transport.
    """

    def __init__(self, timeout_seconds: float = 30.0) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.timeout_seconds = timeout_seconds

    def send(self, request: TransportRequest) -> TransportResponse:
        parsed = urlsplit(request.url)
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or parsed.port != 7863
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise H3TransportError("transport refuses every origin except local H3 Studio")
        if any(name.casefold() == "origin" for name in request.headers):
            raise H3TransportError("Origin must never be sent to H3 Studio")

        connection = http.client.HTTPConnection(
            "127.0.0.1", 7863, timeout=self.timeout_seconds
        )
        target = parsed.path or "/"
        if parsed.query:
            target = f"{target}?{parsed.query}"
        try:
            connection.putrequest(request.method, target, skip_accept_encoding=True)
            for name, value in request.headers.items():
                connection.putheader(name, value)
            connection.endheaders()
            if isinstance(request.body, bytes):
                if request.body:
                    connection.send(request.body)
            elif request.body is not None:
                for chunk in request.body.iter_bytes():
                    if chunk:
                        connection.send(chunk)
            response = connection.getresponse()
            body = response.read()
            headers = {name: value for name, value in response.getheaders()}
            return TransportResponse(status=response.status, headers=headers, body=body)
        except (OSError, http.client.HTTPException) as exc:
            raise H3TransportError(f"local H3 Studio request failed: {exc}") from exc
        finally:
            connection.close()


class H3Client:
    """Typed adapter for H3 Studio's single-renderer local REST API."""

    def __init__(
        self,
        transport: Transport,
        asset_resolver: AssetResolver,
        *,
        boundary_factory: Callable[[], str] | None = None,
    ) -> None:
        self._transport = transport
        self._assets = asset_resolver
        self._boundary_factory = boundary_factory or (
            lambda: f"H3StoryStudio_{secrets.token_hex(16)}"
        )
        # H3 itself has one active renderer.  This lock lets orchestrators use
        # submit_and_wait as one serial critical section instead of building a
        # second, competing local scheduler.
        self._serial_render_lock = threading.Lock()

    def get_state(self) -> H3State:
        return H3State.from_payload(self._get_json("/api/state"))

    def submit_job(
        self,
        request: H3SubmitRequest | Mapping[str, Any],
        *,
        asset_resolver: AssetResolver | None = None,
    ) -> H3Job:
        normalized = (
            request
            if isinstance(request, H3SubmitRequest)
            else H3SubmitRequest.from_mapping(request)
        )
        files = self._resolve_uploads(normalized, asset_resolver=asset_resolver)
        body = MultipartBody(
            self._boundary_factory(), normalized.fields.as_form_fields(), files
        )
        if body.content_length > MAX_REQUEST_BYTES:
            raise H3AssetError(
                "complete multipart request exceeds H3 Studio's 8 GiB upload limit"
            )
        payload = self._request_json(
            "POST",
            "/api/jobs",
            body=body,
            headers={
                "Content-Type": f"multipart/form-data; boundary={body.boundary}",
                "Content-Length": str(body.content_length),
            },
        )
        return H3Job.from_payload(payload)

    def get_job(self, job_id: str) -> H3Job:
        return H3Job.from_payload(self._get_json(self._job_path(job_id)))

    def get_result(self, job_id: str) -> H3Result:
        checked = self._validate_job_id(job_id)
        response = self._send(
            TransportRequest(
                method="GET",
                url=f"{H3_ORIGIN}/api/jobs/{checked}/result",
                headers={"Accept": "video/mp4"},
            )
        )
        self._raise_for_status(response, "GET", f"/api/jobs/{checked}/result")
        return H3Result(
            job_id=checked,
            content=response.body,
            content_type=response.header("Content-Type", "video/mp4") or "video/mp4",
            content_disposition=response.header("Content-Disposition"),
        )

    def cancel_job(self, job_id: str) -> H3Job:
        checked = self._validate_job_id(job_id)
        payload = self._request_json("POST", f"/api/jobs/{checked}/cancel", body=b"")
        return H3Job.from_payload(payload)

    def delete_job(self, job_id: str) -> None:
        self._validate_job_id(job_id)
        raise H3UnsupportedOperation(
            "the pinned H3 Studio API has no DELETE job route; delete the Story "
            "Studio record or exported artifact in its owning layer instead"
        )

    def poll_job(
        self,
        job_id: str,
        *,
        interval_seconds: float = 1.0,
        timeout_seconds: float = 60.0 * 60.0,
        on_update: Callable[[H3Job], None] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> H3Job:
        if interval_seconds < 0:
            raise ValueError("interval_seconds must not be negative")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        checked = self._validate_job_id(job_id)
        deadline = clock() + timeout_seconds
        while True:
            job = self.get_job(checked)
            if on_update is not None:
                on_update(job)
            if job.status.terminal:
                return job
            remaining = deadline - clock()
            if remaining <= 0:
                raise H3PollTimeout(
                    f"job {checked} did not finish within {timeout_seconds:g} seconds"
                )
            sleep(min(interval_seconds, remaining))

    def submit_and_wait(
        self,
        request: H3SubmitRequest | Mapping[str, Any],
        *,
        asset_resolver: AssetResolver | None = None,
        interval_seconds: float = 1.0,
        timeout_seconds: float = 60.0 * 60.0,
        on_update: Callable[[H3Job], None] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> H3Job:
        with self._serial_render_lock:
            submitted = self.submit_job(request, asset_resolver=asset_resolver)
            if on_update is not None:
                on_update(submitted)
            if submitted.status.terminal:
                return submitted
            return self.poll_job(
                submitted.id,
                interval_seconds=interval_seconds,
                timeout_seconds=timeout_seconds,
                on_update=on_update,
                sleep=sleep,
                clock=clock,
            )

    def _resolve_uploads(
        self,
        request: H3SubmitRequest,
        *,
        asset_resolver: AssetResolver | None = None,
    ) -> tuple[MultipartFile, ...]:
        fields = request.fields
        uploads = request.uploads
        assets = asset_resolver or self._assets
        first = (
            assets.resolve_descriptor(uploads.first_image_asset_id)
            if uploads.first_image_asset_id is not None
            else None
        )
        last = (
            assets.resolve_descriptor(uploads.last_image_asset_id)
            if uploads.last_image_asset_id is not None
            else None
        )
        references = tuple(
            assets.resolve_descriptor(asset_id)
            for asset_id in uploads.reference_asset_ids
        )

        if fields.mode == "t2v" and (first or last or references):
            raise H3ContractError("t2v cannot include uploaded assets")
        if fields.mode == "i2v" and (first is None or last or references):
            raise H3ContractError("i2v requires only a first image")
        if fields.mode == "first_last" and (
            first is None or last is None or references
        ):
            raise H3ContractError("first_last requires exactly first and last images")
        if fields.mode == "omni" and (first or last or not references):
            raise H3ContractError(
                "omni requires ordered references and forbids first/last frame slots"
            )

        if first is not None:
            self._require_kind(first, "image")
        if last is not None:
            self._require_kind(last, "image")
        counts = {"image": 0, "video": 0, "audio": 0}
        for asset in references:
            counts[self._kind(Path(asset.path))] += 1
        if counts["image"] > 9 or counts["video"] > 3 or counts["audio"] > 3:
            raise H3ContractError(
                "omni references are limited to 9 images, 3 videos, and 3 audio files"
            )
        if counts["audio"] and not (counts["image"] or counts["video"]):
            raise H3ContractError(
                "audio references require at least one image or video reference"
            )

        parts: list[MultipartFile] = []
        if first is not None:
            parts.append(self._file_part("first_image", first))
        if last is not None:
            parts.append(self._file_part("last_image", last))
        parts.extend(self._file_part("references", asset) for asset in references)
        total_bytes = 0
        for part in parts:
            size = part.expected_size
            if size > MAX_UPLOAD_BYTES:
                raise H3AssetError(
                    f"asset {part.path.name!r} exceeds H3 Studio's 2 GiB file limit"
                )
            total_bytes += size
        if total_bytes > MAX_REQUEST_BYTES:
            raise H3AssetError("H3 uploads exceed the 8 GiB aggregate file limit")
        return tuple(parts)

    def _kind(self, path: Path) -> str:
        return media_kind(path)

    def _require_kind(self, asset: AssetDescriptor, expected: str) -> None:
        path = Path(asset.path)
        actual = media_kind(path, asset.mime_type)
        if actual != expected:
            raise H3ContractError(
                f"asset {path.name!r} is {actual}, but H3 requires {expected}"
            )

    def _file_part(self, field_name: str, asset: AssetDescriptor) -> MultipartFile:
        path = Path(asset.path)
        mime_type = asset.mime_type
        media_kind(path, mime_type)
        if asset.byte_size is None:
            raise H3AssetError(f"asset {path.name!r} has no immutable byte size")
        return MultipartFile(
            field_name=field_name,
            path=path,
            content_type=mime_type or "application/octet-stream",
            expected_size=asset.byte_size,
            expected_sha256=asset.sha256,
        )

    def _get_json(self, path: str) -> Mapping[str, Any]:
        return self._request_json("GET", path)

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        body: bytes | RequestBody | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Mapping[str, Any]:
        request_headers = {"Accept": "application/json"}
        if headers:
            request_headers.update(headers)
        if method not in {"GET", "HEAD", "OPTIONS"}:
            request_headers[H3_MUTATION_HEADER] = H3_MUTATION_HEADER_VALUE
            if body is None or isinstance(body, bytes):
                request_headers.setdefault(
                    "Content-Length", str(len(body) if body is not None else 0)
                )
        response = self._send(
            TransportRequest(
                method=method,
                url=f"{H3_ORIGIN}{path}",
                headers=request_headers,
                body=body,
            )
        )
        self._raise_for_status(response, method, path)
        try:
            decoded = json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise H3ContractError(
                f"H3 Studio returned invalid JSON for {method} {path}"
            ) from exc
        if not isinstance(decoded, Mapping):
            raise H3ContractError(
                f"H3 Studio returned a non-object JSON value for {method} {path}"
            )
        return decoded

    def _send(self, request: TransportRequest) -> TransportResponse:
        if not request.url.startswith(f"{H3_ORIGIN}/"):
            raise H3TransportError("H3 request escaped the fixed loopback origin")
        if any(name.casefold() == "origin" for name in request.headers):
            raise H3TransportError("Origin must never be sent to H3 Studio")
        return self._transport.send(request)

    def _raise_for_status(
        self, response: TransportResponse, method: str, path: str
    ) -> None:
        if 200 <= response.status < 300:
            return
        detail = response.body[:4096].decode("utf-8", errors="replace").strip()
        try:
            decoded = json.loads(detail)
        except json.JSONDecodeError:
            pass
        else:
            if isinstance(decoded, Mapping) and isinstance(decoded.get("detail"), str):
                detail = decoded["detail"]
        raise H3HTTPError(response.status, method, f"{H3_ORIGIN}{path}", detail)

    def _job_path(self, job_id: str) -> str:
        return f"/api/jobs/{self._validate_job_id(job_id)}"

    def _validate_job_id(self, job_id: str) -> str:
        if not isinstance(job_id, str) or not _JOB_ID.fullmatch(job_id):
            raise H3ContractError("H3 job ID must be exactly 12 lowercase hex characters")
        return job_id


__all__ = [
    "AssetDescriptor",
    "AssetResolver",
    "H3Client",
    "media_kind",
    "MultipartBody",
    "MultipartFile",
    "StandardLibraryTransport",
]
