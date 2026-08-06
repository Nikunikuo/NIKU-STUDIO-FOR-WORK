#!/usr/bin/env python3
"""Small, dependency-free CLI for the loopback H3 Story Studio Sidecar.

The Sidecar origin and mutation header are intentionally not configurable.
This keeps agent automation on the same local-only boundary as the Web UI.
Every successful command emits exactly one JSON value on stdout.  Expected
validation, transport, and HTTP failures emit structured JSON on stderr and
return a non-zero exit status.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import mimetypes
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from uuid import uuid4


ORIGIN = "http://127.0.0.1:7864"
MUTATION_HEADER = "X-H3-Story-Request"
MUTATION_HEADER_VALUE = "1"
DEFAULT_TIMEOUT_SECONDS = 15.0
MAX_INPUT_BYTES = 2 * 1024 * 1024
MAX_ASSET_BYTES = 128 * 1024 * 1024
MAX_MULTIPART_BYTES = MAX_ASSET_BYTES + 1024 * 1024

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_H3_JOB_ID = re.compile(r"^[a-f0-9]{12}$")


@dataclass(frozen=True, slots=True)
class CliError(RuntimeError):
    code: str
    message: str
    exit_code: int = 2
    details: Mapping[str, Any] | None = None

    def __str__(self) -> str:
        return self.message

    def to_wire(self) -> dict[str, Any]:
        error: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            error["details"] = dict(self.details)
        return {"error": error}


class JsonArgumentParser(argparse.ArgumentParser):
    """Turn argparse failures into the CLI's structured error contract."""

    def error(self, message: str) -> None:
        raise CliError("INVALID_ARGUMENTS", message)


def safe_id(value: str, label: str = "identifier") -> str:
    if not _SAFE_ID.fullmatch(value):
        raise CliError(
            "INVALID_IDENTIFIER",
            f"{label} must match {_SAFE_ID.pattern}",
            details={"field": label},
        )
    return value


def h3_job_id(value: str) -> str:
    if not _H3_JOB_ID.fullmatch(value):
        raise CliError(
            "INVALID_H3_JOB_ID",
            "job-id must be exactly 12 lowercase hexadecimal characters",
            details={"field": "job-id"},
        )
    return value


def non_negative_int(value: str) -> int:
    try:
        parsed = int(value, 10)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be an integer") from error
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be non-negative")
    return parsed


def _decode_json(raw: str, source: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError as error:
        raise CliError(
            "INVALID_JSON",
            f"{source} is not valid JSON: {error.msg}",
            details={"line": error.lineno, "column": error.colno},
        ) from error


def _load_json_argument(
    *, inline: str | None, file_name: str | None, source_label: str
) -> Any:
    if (inline is None) == (file_name is None):
        raise CliError(
            "INVALID_ARGUMENTS",
            f"provide exactly one of --{source_label}-json or --{source_label}-file",
        )
    if inline is not None:
        if len(inline.encode("utf-8")) > MAX_INPUT_BYTES:
            raise CliError("INPUT_TOO_LARGE", f"{source_label} JSON exceeds 2 MiB")
        return _decode_json(inline, f"--{source_label}-json")

    assert file_name is not None
    path = Path(file_name)
    try:
        size = path.stat().st_size
        if size > MAX_INPUT_BYTES:
            raise CliError("INPUT_TOO_LARGE", f"{source_label} file exceeds 2 MiB")
        raw = path.read_text(encoding="utf-8")
    except CliError:
        raise
    except (OSError, UnicodeError) as error:
        raise CliError(
            "INPUT_FILE_ERROR",
            f"cannot read {source_label} file as UTF-8: {error}",
            details={"path": str(path)},
        ) from error
    return _decode_json(raw, f"--{source_label}-file")


class SidecarClient:
    def __init__(self, *, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        if timeout <= 0:
            raise CliError("INVALID_ARGUMENTS", "timeout must be greater than zero")
        self.timeout = timeout

    def request(
        self,
        method: str,
        path: str,
        payload: Mapping[str, Any] | None = None,
    ) -> Any:
        if not path.startswith("/api/") or "://" in path:
            raise CliError("INVALID_REQUEST_PATH", "only origin-relative /api paths are allowed")

        method = method.upper()
        headers = {
            "Accept": "application/json",
            "User-Agent": "h3-story-studio-storyctl/0.1",
        }
        body: bytes | None = None
        if method == "POST":
            if payload is None:
                payload = {}
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            )
            headers["Content-Type"] = "application/json"
            headers[MUTATION_HEADER] = MUTATION_HEADER_VALUE
        elif payload is not None:
            raise CliError("INVALID_REQUEST", "GET requests cannot include a JSON body")

        request = Request(ORIGIN + path, data=body, headers=headers, method=method)
        return self._send(request)

    def request_multipart(
        self,
        path: str,
        fields: Mapping[str, str | int | bool],
        file_name: str | Path,
    ) -> Any:
        if not path.startswith("/api/") or "://" in path:
            raise CliError("INVALID_REQUEST_PATH", "only origin-relative /api paths are allowed")
        source = Path(file_name).expanduser().resolve()
        try:
            size = source.stat().st_size
        except OSError as error:
            raise CliError(
                "ASSET_FILE_ERROR",
                f"cannot read asset file: {error}",
                details={"path": str(source)},
            ) from error
        if not source.is_file() or size <= 0:
            raise CliError("ASSET_FILE_ERROR", "asset file must be a non-empty regular file")
        if size > MAX_ASSET_BYTES:
            raise CliError("ASSET_FILE_TOO_LARGE", "asset file exceeds 128 MiB")

        boundary = f"h3storyctl-{uuid4().hex}"
        body_parts: list[bytes] = []
        for name, value in fields.items():
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", name):
                raise CliError("INVALID_MULTIPART_FIELD", f"unsafe multipart field: {name}")
            normalized = str(value).lower() if isinstance(value, bool) else str(value)
            body_parts.append(
                (
                    f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                    f"{normalized}\r\n"
                ).encode("utf-8")
            )
        safe_filename = source.name.replace("\r", "_").replace("\n", "_").replace('"', "_")
        mime_type = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
        body_parts.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="file"; filename="{safe_filename}"\r\n'
                f"Content-Type: {mime_type}\r\n\r\n"
            ).encode("utf-8")
        )
        try:
            body_parts.append(source.read_bytes())
        except OSError as error:
            raise CliError(
                "ASSET_FILE_ERROR",
                f"cannot read asset file: {error}",
                details={"path": str(source)},
            ) from error
        body_parts.append(f"\r\n--{boundary}--\r\n".encode("ascii"))
        body = b"".join(body_parts)
        if len(body) > MAX_MULTIPART_BYTES:
            raise CliError("ASSET_FILE_TOO_LARGE", "multipart asset request exceeds the local limit")
        request = Request(
            ORIGIN + path,
            data=body,
            headers={
                "Accept": "application/json",
                "User-Agent": "h3-story-studio-storyctl/0.1",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                MUTATION_HEADER: MUTATION_HEADER_VALUE,
            },
            method="POST",
        )
        return self._send(request)

    def _send(self, request: Request) -> Any:
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except HTTPError as error:
            raw = error.read()
            parsed = _parse_response_json(raw, f"HTTP {error.code}")
            server_error = parsed.get("error") if isinstance(parsed, Mapping) else None
            if isinstance(server_error, Mapping):
                code = str(server_error.get("code") or "SIDECAR_HTTP_ERROR")
                message = str(server_error.get("message") or error.reason)
                details = dict(server_error.get("details") or {})
            else:
                code = "SIDECAR_HTTP_ERROR"
                message = f"Sidecar returned HTTP {error.code}: {error.reason}"
                details = {}
            details["httpStatus"] = error.code
            raise CliError(code, message, 3, details) from error
        except (URLError, TimeoutError, OSError) as error:
            reason = getattr(error, "reason", error)
            raise CliError(
                "SIDECAR_UNAVAILABLE",
                f"cannot reach the local Sidecar at {ORIGIN}: {reason}",
                4,
            ) from error
        return _parse_response_json(raw, "Sidecar response")


def _parse_response_json(raw: bytes, source: str) -> Any:
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CliError(
            "INVALID_SIDECAR_RESPONSE",
            f"{source} was not valid UTF-8 JSON",
            4,
        ) from error


def build_parser() -> JsonArgumentParser:
    parser = JsonArgumentParser(
        prog="storyctl",
        description="Operate the local H3 Story Studio Project Core and renderer.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help="local HTTP timeout in seconds (default: 15)",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("health", help="show Sidecar, Project Core, and H3 health")
    commands.add_parser("projects", help="list Project Core projects")
    commands.add_parser("h3-state", help="show the existing H3 Studio state")

    create_project = commands.add_parser(
        "create-project", help="create a new runtime Project Core scaffold"
    )
    create_inputs = create_project.add_mutually_exclusive_group(required=True)
    create_inputs.add_argument("--spec-json")
    create_inputs.add_argument("--spec-file")

    activate = commands.add_parser("activate", help="bind a new exact-project session")
    activate.add_argument("--project-id", required=True)
    activate.add_argument("--agent-id", default="codex-storyctl")

    context = commands.add_parser("context", help="read a bound project context")
    context.add_argument("--project-id", required=True)
    context.add_argument("--session-id", required=True)

    add_asset = commands.add_parser(
        "add-asset", help="add metadata or upload one Project-scoped asset"
    )
    add_asset.add_argument("--project-id", required=True)
    add_asset.add_argument("--session-id", required=True)
    add_asset.add_argument("--expected-revision", required=True, type=non_negative_int)
    asset_inputs = add_asset.add_mutually_exclusive_group(required=True)
    asset_inputs.add_argument("--spec-json")
    asset_inputs.add_argument("--spec-file")
    add_asset.add_argument("--file", help="optional image, video, or audio file")

    patch = commands.add_parser("patch", help="commit operations with revision CAS")
    patch.add_argument("--project-id", required=True)
    patch.add_argument("--session-id", required=True)
    patch.add_argument("--expected-revision", required=True, type=non_negative_int)
    patch.add_argument("--message", required=True)
    patch_inputs = patch.add_mutually_exclusive_group(required=True)
    patch_inputs.add_argument("--operations-json")
    patch_inputs.add_argument("--operations-file")

    handoff = commands.add_parser("handoff", help="export a fresh handoff capsule")
    handoff.add_argument("--project-id", required=True)
    handoff.add_argument("--session-id", required=True)

    render = commands.add_parser("render", help="submit one shot to existing H3 Studio")
    render.add_argument("--project-id", required=True)
    render.add_argument("--shot-id", required=True)
    render.add_argument("--session-id", required=True)
    render.add_argument("--expected-revision", required=True, type=non_negative_int)
    request_inputs = render.add_mutually_exclusive_group(required=True)
    request_inputs.add_argument("--request-json")
    request_inputs.add_argument("--request-file")

    job = commands.add_parser("job", help="read an H3 job by its pinned ID")
    job.add_argument("--job-id", required=True)

    sync = commands.add_parser("sync", help="sync a submitted render into Project Core")
    sync.add_argument("--project-id", required=True)
    sync.add_argument("--local-job-id", required=True)
    sync.add_argument("--session-id", required=True)
    sync.add_argument("--expected-revision", required=True, type=non_negative_int)
    sync.add_argument(
        "--no-download",
        action="store_true",
        help="update status without downloading a completed artifact",
    )
    sync.add_argument(
        "--extract-end-frame",
        action="store_true",
        help="extract the completed take's end frame and register it for continuity",
    )
    return parser


def execute(args: argparse.Namespace, client: SidecarClient) -> Any:
    command = args.command
    if command == "health":
        return client.request("GET", "/api/health")
    if command == "projects":
        return client.request("GET", "/api/projects")
    if command == "h3-state":
        return client.request("GET", "/api/h3/state")

    if command == "create-project":
        spec = _load_json_argument(
            inline=args.spec_json,
            file_name=args.spec_file,
            source_label="spec",
        )
        if not isinstance(spec, Mapping):
            raise CliError("INVALID_PROJECT_SPEC", "project spec JSON must be an object")
        return client.request("POST", "/api/projects", dict(spec))

    if command == "activate":
        project_id = safe_id(args.project_id, "project-id")
        agent_id = safe_id(args.agent_id, "agent-id")
        return client.request(
            "POST",
            f"/api/projects/{quote(project_id, safe='')}/activate",
            {"agentId": agent_id},
        )
    if command == "context":
        project_id = safe_id(args.project_id, "project-id")
        session_id = safe_id(args.session_id, "session-id")
        query = urlencode({"sessionId": session_id})
        return client.request(
            "GET", f"/api/projects/{quote(project_id, safe='')}/context?{query}"
        )
    if command == "add-asset":
        project_id = safe_id(args.project_id, "project-id")
        session_id = safe_id(args.session_id, "session-id")
        spec = _load_json_argument(
            inline=args.spec_json,
            file_name=args.spec_file,
            source_label="spec",
        )
        if not isinstance(spec, Mapping):
            raise CliError("INVALID_ASSET_SPEC", "asset spec JSON must be an object")
        expected_fields = {
            "name", "category", "role", "caption", "promptCaption", "locked", "source"
        }
        if set(spec) != expected_fields:
            raise CliError(
                "INVALID_ASSET_SPEC",
                "asset spec must contain exactly name, category, role, caption, "
                "promptCaption, locked, and source",
            )
        text_fields = ("name", "category", "role", "caption", "promptCaption", "source")
        if any(not isinstance(spec[field], str) for field in text_fields) or not isinstance(spec["locked"], bool):
            raise CliError("INVALID_ASSET_SPEC", "asset spec fields have invalid types")
        envelope: dict[str, str | int | bool] = {
            "sessionId": session_id,
            "expectedRevision": args.expected_revision,
            **{field: spec[field] for field in text_fields},
            "locked": spec["locked"],
        }
        path = f"/api/projects/{quote(project_id, safe='')}/assets"
        if args.file:
            return client.request_multipart(path, envelope, args.file)
        return client.request("POST", path, envelope)
    if command == "patch":
        project_id = safe_id(args.project_id, "project-id")
        session_id = safe_id(args.session_id, "session-id")
        if not args.message.strip() or "\x00" in args.message:
            raise CliError("INVALID_ARGUMENTS", "message must be non-empty text")
        operations = _load_json_argument(
            inline=args.operations_json,
            file_name=args.operations_file,
            source_label="operations",
        )
        if not isinstance(operations, list):
            raise CliError("INVALID_OPERATIONS", "operations JSON must be an array")
        return client.request(
            "POST",
            f"/api/projects/{quote(project_id, safe='')}/patches",
            {
                "sessionId": session_id,
                "expectedRevision": args.expected_revision,
                "message": args.message.strip(),
                "operations": operations,
            },
        )
    if command == "handoff":
        project_id = safe_id(args.project_id, "project-id")
        session_id = safe_id(args.session_id, "session-id")
        return client.request(
            "POST",
            f"/api/projects/{quote(project_id, safe='')}/handoffs",
            {"sessionId": session_id},
        )
    if command == "render":
        project_id = safe_id(args.project_id, "project-id")
        shot_id = safe_id(args.shot_id, "shot-id")
        session_id = safe_id(args.session_id, "session-id")
        render_request = _load_json_argument(
            inline=args.request_json,
            file_name=args.request_file,
            source_label="request",
        )
        if not isinstance(render_request, Mapping):
            raise CliError("INVALID_RENDER_REQUEST", "request JSON must be an object")
        return client.request(
            "POST",
            (
                f"/api/projects/{quote(project_id, safe='')}/shots/"
                f"{quote(shot_id, safe='')}/renders"
            ),
            {
                "sessionId": session_id,
                "expectedRevision": args.expected_revision,
                "request": dict(render_request),
            },
        )
    if command == "job":
        job_id = h3_job_id(args.job_id)
        return client.request("GET", f"/api/h3/jobs/{job_id}")
    if command == "sync":
        project_id = safe_id(args.project_id, "project-id")
        local_job_id = safe_id(args.local_job_id, "local-job-id")
        session_id = safe_id(args.session_id, "session-id")
        return client.request(
            "POST",
            (
                f"/api/projects/{quote(project_id, safe='')}/renders/"
                f"{quote(local_job_id, safe='')}/sync"
            ),
            {
                "sessionId": session_id,
                "expectedRevision": args.expected_revision,
                "downloadResult": not args.no_download,
                "extractEndFrame": args.extract_end_frame,
            },
        )
    raise CliError("INVALID_ARGUMENTS", f"unsupported command: {command}")


def _write_json(stream: Any, value: Any) -> None:
    json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
    stream.write("\n")


def _configure_utf8_standard_streams() -> None:
    """Avoid Windows legacy-codepage failures on valid Sidecar Unicode JSON."""

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="strict")


def main(argv: Sequence[str] | None = None) -> int:
    _configure_utf8_standard_streams()
    try:
        args = build_parser().parse_args(argv)
        result = execute(args, SidecarClient(timeout=args.timeout))
    except CliError as error:
        _write_json(sys.stderr, error.to_wire())
        return error.exit_code
    _write_json(sys.stdout, result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
