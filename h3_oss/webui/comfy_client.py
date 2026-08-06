from __future__ import annotations

import json
import mimetypes
import os
import secrets
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterator, Mapping


_MEDIA_EXTENSIONS = {
    "image": {".bmp", ".gif", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"},
    "video": {".avi", ".m4v", ".mkv", ".mov", ".mp4", ".webm"},
    "audio": {".aac", ".flac", ".m4a", ".mp3", ".ogg", ".opus", ".wav"},
}
_DIRECTORY_TYPES = frozenset(("input", "output", "temp"))


class ComfyClientError(RuntimeError):
    """Base error raised by the local ComfyUI API client."""


class ComfyHttpError(ComfyClientError):
    """An HTTP request reached ComfyUI but did not succeed."""

    def __init__(self, status: int, method: str, route: str, detail: str = "") -> None:
        suffix = f": {detail}" if detail else ""
        super().__init__(f"ComfyUI returned HTTP {status} for {method} {route}{suffix}")
        self.status = status
        self.method = method
        self.route = route
        self.detail = detail


class ComfyProtocolError(ComfyClientError):
    """ComfyUI returned a response that does not match its documented API."""


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Never let a compromised localhost server redirect this client elsewhere."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


@dataclass(frozen=True)
class PromptPoll:
    """A queue/history snapshot available without a WebSocket connection."""

    prompt_id: str
    state: str
    progress: int | None
    queue_position: int | None = None
    history: Mapping[str, Any] | None = None
    error: Mapping[str, Any] | None = None

    @property
    def terminal(self) -> bool:
        return self.state in {"completed", "failed", "cancelled"}

    @property
    def outputs(self) -> Mapping[str, Any]:
        if not self.history:
            return {}
        outputs = self.history.get("outputs", {})
        return outputs if isinstance(outputs, Mapping) else {}


class ComfyClient:
    """Small, dependency-free client for one local ComfyUI server.

    The host is deliberately not configurable: every request is sent to numeric
    loopback (127.0.0.1), proxies and redirects are disabled, and only constant
    ComfyUI routes are used.  This keeps workflow data and uploaded references
    local even if environment proxy variables are present.
    """

    def __init__(
        self,
        *,
        port: int = 8188,
        timeout: float = 15.0,
        comfy_root: str | os.PathLike[str] | None = None,
        directory_roots: Mapping[str, str | os.PathLike[str]] | None = None,
        max_upload_bytes: int = 512 * 1024 * 1024,
        opener: Any | None = None,
    ) -> None:
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError("port must be an integer from 1 through 65535")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if max_upload_bytes <= 0:
            raise ValueError("max_upload_bytes must be positive")

        self.port = port
        self.timeout = float(timeout)
        self.max_upload_bytes = int(max_upload_bytes)
        self.base_url = f"http://127.0.0.1:{port}"
        self._opener = opener or urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            _NoRedirectHandler(),
        )

        roots: dict[str, Path] = {}
        if comfy_root is not None:
            root = Path(comfy_root).expanduser().resolve()
            roots.update(
                input=root / "input",
                output=root / "output",
                temp=root / "temp",
            )
        if directory_roots:
            for directory_type, directory in directory_roots.items():
                if directory_type not in _DIRECTORY_TYPES:
                    raise ValueError(f"unsupported ComfyUI directory type: {directory_type!r}")
                roots[directory_type] = Path(directory).expanduser().resolve()
        self.directory_roots = {key: value.resolve() for key, value in roots.items()}

    def health(self) -> dict[str, Any]:
        """Return ComfyUI's system-statistics payload if the server is healthy."""

        return self._json_request("GET", "/system_stats")

    def object_info(self, node_class: str | None = None) -> dict[str, Any]:
        """Return all registered node schemas, or the schema for one node."""

        route = "/object_info"
        if node_class is not None:
            if not isinstance(node_class, str) or not node_class or any(
                character in node_class for character in ("/", "\\", "\0")
            ):
                raise ValueError("node_class must be a non-empty route segment")
            route += "/" + urllib.parse.quote(node_class, safe="")
        return self._json_request("GET", route)

    def upload_image(
        self,
        source: str | os.PathLike[str],
        *,
        subfolder: str = "",
        overwrite: bool = False,
    ) -> dict[str, Any]:
        return self._upload(source, "image", subfolder=subfolder, overwrite=overwrite)

    def upload_video(
        self,
        source: str | os.PathLike[str],
        *,
        subfolder: str = "",
        overwrite: bool = False,
    ) -> dict[str, Any]:
        return self._upload(source, "video", subfolder=subfolder, overwrite=overwrite)

    def upload_audio(
        self,
        source: str | os.PathLike[str],
        *,
        subfolder: str = "",
        overwrite: bool = False,
    ) -> dict[str, Any]:
        return self._upload(source, "audio", subfolder=subfolder, overwrite=overwrite)

    def submit_prompt(
        self,
        workflow: Mapping[str, Any],
        *,
        client_id: str | None = None,
        prompt_id: str | None = None,
        extra_data: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Validate the envelope locally and enqueue an API-format workflow."""

        if not isinstance(workflow, Mapping) or not workflow:
            raise ValueError("workflow must be a non-empty mapping in ComfyUI API format")
        payload: dict[str, Any] = {"prompt": dict(workflow)}
        if client_id is not None:
            payload["client_id"] = self._validate_identifier(client_id, "client_id")
        if prompt_id is not None:
            payload["prompt_id"] = self._validate_prompt_id(prompt_id)
        if extra_data is not None:
            if not isinstance(extra_data, Mapping):
                raise ValueError("extra_data must be a mapping")
            payload["extra_data"] = dict(extra_data)

        response = self._json_request("POST", "/prompt", payload)
        returned_id = response.get("prompt_id")
        if not isinstance(returned_id, str):
            raise ComfyProtocolError("POST /prompt did not return a string prompt_id")
        self._validate_prompt_id(returned_id)
        return response

    # Familiar name used by ComfyUI's own API examples.
    queue_prompt = submit_prompt

    def history(self, prompt_id: str) -> dict[str, Any]:
        prompt_id = self._validate_prompt_id(prompt_id)
        return self._json_request("GET", f"/history/{prompt_id}")

    def queue(self) -> dict[str, Any]:
        response = self._json_request("GET", "/queue")
        for key in ("queue_running", "queue_pending"):
            if key not in response or not isinstance(response[key], list):
                raise ComfyProtocolError(f"GET /queue response has no list field {key!r}")
        return response

    def interrupt(self, prompt_id: str | None = None) -> None:
        """Interrupt one running prompt (or globally if no id is supplied)."""

        payload = {} if prompt_id is None else {"prompt_id": self._validate_prompt_id(prompt_id)}
        self._empty_request("POST", "/interrupt", payload)

    def delete_queued(self, prompt_id: str) -> None:
        """Remove one pending prompt without affecting a running prompt."""

        self._empty_request("POST", "/queue", {"delete": [self._validate_prompt_id(prompt_id)]})

    def cancel(self, prompt_id: str) -> str:
        """Safely target a prompt based on an atomic queue snapshot.

        Returns ``"interrupted"``, ``"deleted"``, ``"finished"``, or
        ``"not_found"``.  Current ComfyUI supports targeted interruption, so a
        job transition cannot accidentally interrupt the next queued workflow.
        """

        prompt_id = self._validate_prompt_id(prompt_id)
        snapshot = self.queue()
        if self._find_queue_item(snapshot["queue_running"], prompt_id) is not None:
            self.interrupt(prompt_id)
            return "interrupted"
        if self._find_queue_item(snapshot["queue_pending"], prompt_id) is not None:
            self.delete_queued(prompt_id)
            return "deleted"
        if prompt_id in self.history(prompt_id):
            return "finished"
        return "not_found"

    def poll_prompt(self, prompt_id: str) -> PromptPoll:
        """Combine history and queue endpoints into one WebSocket-free state."""

        prompt_id = self._validate_prompt_id(prompt_id)
        history_payload = self.history(prompt_id)
        history = history_payload.get(prompt_id)
        if history is not None:
            if not isinstance(history, Mapping):
                raise ComfyProtocolError("history entry must be an object")
            state, error = self._history_state(history)
            return PromptPoll(
                prompt_id=prompt_id,
                state=state,
                progress=100 if state == "completed" else None,
                history=history,
                error=error,
            )

        queue = self.queue()
        if self._find_queue_item(queue["queue_running"], prompt_id) is not None:
            return PromptPoll(prompt_id=prompt_id, state="running", progress=None)
        pending_position = self._find_queue_item(queue["queue_pending"], prompt_id)
        if pending_position is not None:
            return PromptPoll(
                prompt_id=prompt_id,
                state="queued",
                progress=0,
                queue_position=pending_position,
            )
        return PromptPoll(prompt_id=prompt_id, state="unknown", progress=None)

    def iter_prompt(
        self,
        prompt_id: str,
        *,
        poll_interval: float = 1.0,
        timeout: float | None = None,
    ) -> Iterator[PromptPoll]:
        """Yield changed queue/history snapshots until the prompt is terminal."""

        if poll_interval <= 0:
            raise ValueError("poll_interval must be positive")
        if timeout is not None and timeout <= 0:
            raise ValueError("timeout must be positive")
        prompt_id = self._validate_prompt_id(prompt_id)
        started = time.monotonic()
        previous: PromptPoll | None = None
        while True:
            current = self.poll_prompt(prompt_id)
            if current != previous:
                yield current
                previous = current
            if current.terminal:
                return
            if timeout is not None and time.monotonic() - started >= timeout:
                raise TimeoutError(f"timed out waiting for ComfyUI prompt {prompt_id}")
            time.sleep(poll_interval)

    def wait_for_prompt(
        self,
        prompt_id: str,
        *,
        poll_interval: float = 1.0,
        timeout: float | None = None,
        on_update: Callable[[PromptPoll], None] | None = None,
    ) -> PromptPoll:
        final: PromptPoll | None = None
        for current in self.iter_prompt(prompt_id, poll_interval=poll_interval, timeout=timeout):
            final = current
            if on_update is not None:
                on_update(current)
        assert final is not None
        return final

    def view_output(self, output: Mapping[str, Any]) -> bytes:
        """Download one trusted output descriptor through ComfyUI's /view API."""

        filename, subfolder, directory_type = self._validate_asset_descriptor(output)
        query = {"filename": filename, "subfolder": subfolder, "type": directory_type}
        return self._request_bytes("GET", "/view", query=query)

    def download_output(
        self,
        output: Mapping[str, Any],
        destination_dir: str | os.PathLike[str],
        *,
        overwrite: bool = False,
    ) -> Path:
        """Download an output while preventing the server from choosing its path."""

        filename, _, _ = self._validate_asset_descriptor(output)
        destination_root = Path(destination_dir).expanduser().resolve()
        destination_root.mkdir(parents=True, exist_ok=True)
        target = (destination_root / filename).resolve()
        self._require_within(destination_root, target)
        payload = self.view_output(output)
        mode = "wb" if overwrite else "xb"
        with target.open(mode) as handle:
            handle.write(payload)
        return target

    def resolve_output_path(
        self,
        output: Mapping[str, Any],
        *,
        must_exist: bool = True,
    ) -> Path:
        """Resolve a /view descriptor only inside configured Comfy directories."""

        filename, subfolder, directory_type = self._validate_asset_descriptor(output)
        root = self.directory_roots.get(directory_type)
        if root is None:
            raise ComfyClientError(
                f"no local directory root configured for ComfyUI type {directory_type!r}"
            )
        candidate = (root / Path(*PurePosixPath(subfolder).parts) / filename).resolve()
        self._require_within(root, candidate)
        if must_exist and (not candidate.exists() or not candidate.is_file()):
            raise FileNotFoundError(candidate)
        return candidate

    @staticmethod
    def output_descriptors(history: Mapping[str, Any]) -> list[dict[str, str]]:
        """Collect image, video, and audio /view descriptors from history outputs."""

        found: list[dict[str, str]] = []
        outputs = history.get("outputs", {})
        if not isinstance(outputs, Mapping):
            return found
        for node_output in outputs.values():
            if not isinstance(node_output, Mapping):
                continue
            for value in node_output.values():
                if not isinstance(value, list):
                    continue
                for item in value:
                    if not isinstance(item, Mapping):
                        continue
                    if all(isinstance(item.get(key), str) for key in ("filename", "type")):
                        found.append(
                            {
                                "filename": item["filename"],
                                "subfolder": item.get("subfolder", ""),
                                "type": item["type"],
                            }
                        )
        return found

    def _upload(
        self,
        source: str | os.PathLike[str],
        media_kind: str,
        *,
        subfolder: str,
        overwrite: bool,
    ) -> dict[str, Any]:
        source_path = Path(source).expanduser().resolve(strict=True)
        if not source_path.is_file():
            raise ValueError(f"upload source is not a regular file: {source_path}")
        extension = source_path.suffix.lower()
        if extension not in _MEDIA_EXTENSIONS[media_kind]:
            allowed = ", ".join(sorted(_MEDIA_EXTENSIONS[media_kind]))
            raise ValueError(f"unsupported {media_kind} extension {extension!r}; expected one of {allowed}")
        file_size = source_path.stat().st_size
        if file_size > self.max_upload_bytes:
            raise ValueError(
                f"upload is {file_size} bytes, over the configured {self.max_upload_bytes}-byte limit"
            )

        normalized_subfolder = self._validate_subfolder(subfolder)
        boundary = "----H3Studio" + secrets.token_hex(16)
        content_type = mimetypes.guess_type(source_path.name)[0] or "application/octet-stream"
        ascii_name = self._ascii_upload_name(source_path.name)
        encoded_name = urllib.parse.quote(source_path.name, safe="")
        parts = [
            self._multipart_field(boundary, "type", "input"),
            self._multipart_field(boundary, "subfolder", normalized_subfolder),
            self._multipart_field(boundary, "overwrite", "true" if overwrite else "false"),
            (
                f"--{boundary}\r\n"
                "Content-Disposition: form-data; name=\"image\"; "
                f"filename=\"{ascii_name}\"; filename*=UTF-8''{encoded_name}\r\n"
                f"Content-Type: {content_type}\r\n\r\n"
            ).encode("ascii"),
            source_path.read_bytes(),
            f"\r\n--{boundary}--\r\n".encode("ascii"),
        ]
        response = self._json_request(
            "POST",
            "/upload/image",
            raw_data=b"".join(parts),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        self._validate_asset_descriptor(response, expected_type="input")
        return response

    def _json_request(
        self,
        method: str,
        route: str,
        payload: Mapping[str, Any] | None = None,
        *,
        raw_data: bytes | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        if payload is not None and raw_data is not None:
            raise ValueError("payload and raw_data are mutually exclusive")
        request_headers = dict(headers or {})
        data = raw_data
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            request_headers["Content-Type"] = "application/json; charset=utf-8"
        body = self._request_bytes(method, route, data=data, headers=request_headers)
        try:
            decoded = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ComfyProtocolError(f"{method} {route} did not return valid UTF-8 JSON") from exc
        if not isinstance(decoded, dict):
            raise ComfyProtocolError(f"{method} {route} JSON response must be an object")
        return decoded

    def _empty_request(self, method: str, route: str, payload: Mapping[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._request_bytes(
            method,
            route,
            data=data,
            headers={"Content-Type": "application/json; charset=utf-8"},
        )

    def _request_bytes(
        self,
        method: str,
        route: str,
        *,
        data: bytes | None = None,
        headers: Mapping[str, str] | None = None,
        query: Mapping[str, str] | None = None,
    ) -> bytes:
        if not route.startswith("/") or route.startswith("//") or "://" in route:
            raise ValueError("route must be an absolute local path")
        url = self.base_url + route
        if query:
            url += "?" + urllib.parse.urlencode(query)
        request_headers = {
            "Accept": "application/json, application/octet-stream;q=0.9",
            "User-Agent": "H3-Studio-ComfyClient/1",
        }
        request_headers.update(headers or {})
        request = urllib.request.Request(url, data=data, headers=request_headers, method=method)
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                final_url = urllib.parse.urlsplit(response.geturl())
                if final_url.scheme != "http" or final_url.hostname != "127.0.0.1" or final_url.port != self.port:
                    raise ComfyProtocolError("ComfyUI response escaped the configured loopback origin")
                return response.read()
        except urllib.error.HTTPError as exc:
            detail_bytes = exc.read(64 * 1024)
            detail = detail_bytes.decode("utf-8", errors="replace").strip()
            raise ComfyHttpError(exc.code, method, route, detail) from exc
        except urllib.error.URLError as exc:
            raise ComfyClientError(f"cannot reach local ComfyUI at {self.base_url}: {exc.reason}") from exc

    @staticmethod
    def _multipart_field(boundary: str, name: str, value: str) -> bytes:
        return (
            f"--{boundary}\r\n"
            f"Content-Disposition: form-data; name=\"{name}\"\r\n\r\n"
            f"{value}\r\n"
        ).encode("utf-8")

    @staticmethod
    def _ascii_upload_name(filename: str) -> str:
        suffix = "".join(
            character
            for character in unicodedata.normalize("NFKD", Path(filename).suffix.lower())
            if ord(character) < 128 and (character.isalnum() or character == ".")
        )
        normalized_stem = unicodedata.normalize("NFKD", Path(filename).stem)
        stem = "".join(character for character in normalized_stem if ord(character) < 128)
        stem = "".join(
            character if character.isalnum() or character in "_- " else "_" for character in stem
        ).strip(" ._")
        return (stem or "upload") + suffix

    @staticmethod
    def _validate_prompt_id(prompt_id: str) -> str:
        if not isinstance(prompt_id, str):
            raise ValueError("prompt_id must be a canonical UUID string")
        try:
            parsed = uuid.UUID(prompt_id)
        except (ValueError, AttributeError) as exc:
            raise ValueError("prompt_id must be a canonical UUID string") from exc
        canonical = str(parsed)
        if prompt_id != canonical:
            raise ValueError("prompt_id must be a canonical lowercase UUID string")
        return prompt_id

    @staticmethod
    def _validate_identifier(value: str, label: str) -> str:
        if not isinstance(value, str) or not value or len(value) > 128:
            raise ValueError(f"{label} must be a non-empty string of at most 128 characters")
        if any(ord(character) < 32 for character in value):
            raise ValueError(f"{label} contains control characters")
        return value

    @staticmethod
    def _validate_subfolder(subfolder: str) -> str:
        if not isinstance(subfolder, str) or "\0" in subfolder:
            raise ValueError("subfolder must be a relative string")
        normalized = subfolder.replace("\\", "/")
        if normalized.startswith("/") or normalized.endswith("/"):
            raise ValueError("subfolder must be relative and may not have empty edge components")
        if not normalized:
            return ""
        path = PurePosixPath(normalized)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError("subfolder may not be absolute or contain traversal components")
        if any(":" in part or any(ord(character) < 32 for character in part) for part in path.parts):
            raise ValueError("subfolder contains an unsafe path component")
        return path.as_posix()

    @classmethod
    def _validate_asset_descriptor(
        cls,
        output: Mapping[str, Any],
        *,
        expected_type: str | None = None,
    ) -> tuple[str, str, str]:
        if not isinstance(output, Mapping):
            raise ValueError("output descriptor must be a mapping")
        filename = output.get("filename", output.get("name"))
        if not isinstance(filename, str) or not filename or "\0" in filename:
            raise ValueError("output filename must be a non-empty string")
        if filename in {".", ".."} or Path(filename).name != filename or "/" in filename or "\\" in filename:
            raise ValueError("output filename may not contain a path")
        if ":" in filename:
            raise ValueError("output filename may not contain a drive or alternate-stream separator")
        if any(ord(character) < 32 for character in filename):
            raise ValueError("output filename contains control characters")
        subfolder = cls._validate_subfolder(output.get("subfolder", ""))
        directory_type = output.get("type", "output")
        if directory_type not in _DIRECTORY_TYPES:
            raise ValueError(f"unsupported output directory type: {directory_type!r}")
        if expected_type is not None and directory_type != expected_type:
            raise ComfyProtocolError(
                f"ComfyUI returned directory type {directory_type!r}; expected {expected_type!r}"
            )
        return filename, subfolder, directory_type

    @staticmethod
    def _require_within(root: Path, candidate: Path) -> None:
        try:
            common = Path(os.path.commonpath((os.fspath(root), os.fspath(candidate))))
        except ValueError as exc:
            raise ValueError("resolved path is on a different volume") from exc
        if common != root:
            raise ValueError("resolved path escapes its permitted directory")

    @staticmethod
    def _queue_item_prompt_id(item: Any) -> str | None:
        if isinstance(item, (list, tuple)) and len(item) > 1 and isinstance(item[1], str):
            return item[1]
        if isinstance(item, Mapping):
            prompt_id = item.get("prompt_id")
            return prompt_id if isinstance(prompt_id, str) else None
        return None

    @classmethod
    def _find_queue_item(cls, items: list[Any], prompt_id: str) -> int | None:
        for index, item in enumerate(items):
            if cls._queue_item_prompt_id(item) == prompt_id:
                return index
        return None

    @staticmethod
    def _history_state(
        history: Mapping[str, Any],
    ) -> tuple[str, Mapping[str, Any] | None]:
        status = history.get("status")
        if not isinstance(status, Mapping):
            return "failed", {"message": "history entry has no execution status"}
        status_str = status.get("status_str")
        messages = status.get("messages", [])
        error: Mapping[str, Any] | None = None
        interrupted = False
        if isinstance(messages, list):
            for message in messages:
                if not isinstance(message, (list, tuple)) or len(message) != 2:
                    continue
                event, detail = message
                if event == "execution_interrupted":
                    interrupted = True
                elif event == "execution_error" and isinstance(detail, Mapping):
                    error = detail
        if interrupted:
            return "cancelled", error
        if status_str == "success" and status.get("completed") is True:
            return "completed", None
        if status_str == "error" or status.get("completed") is True:
            return "failed", error or {"message": "ComfyUI execution failed"}
        return "running", None
