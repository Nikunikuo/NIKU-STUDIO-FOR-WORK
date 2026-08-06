from __future__ import annotations

import http.client
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sidecar.h3_story_core.models import (  # noqa: E402
    CommitResult,
    EntityState,
    HandoffCapsule,
    ProjectContext,
    ProjectSummary,
    SessionBinding,
)
from sidecar.h3_story_core.repository import RevisionConflictError  # noqa: E402
from sidecar.http_server import (  # noqa: E402
    FileSlice,
    MAX_JSON_BYTES,
    MUTATION_HEADER,
    ApiResponse,
    StoryApi,
    build_default_dependencies,
    make_handler,
)


JSON_HEADERS = {
    "Content-Type": "application/json",
    MUTATION_HEADER: "1",
}
ALLOWED_ORIGIN = "http://localhost:3000"


class FakeRepository:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.conflict = False
        self.entities: tuple[EntityState, ...] = ()
        self.project_root = "projects/night"
        self.context_project_id: str | None = None
        self.context_session_id: str | None = None

    def list_projects(self):
        self.calls.append(("list_projects", None))
        return (
            ProjectSummary(
                project_id="prj_night",
                name="夜明けの鋼",
                project_root="projects/night",
                revision=7,
                digest="sha256:project-seven",
                focus={"shotId": "sh_012"},
                created_at="2026-08-05T00:00:00Z",
                updated_at="2026-08-05T01:00:00Z",
            ),
        )

    def activate_session(self, project_id: str, agent_id: str):
        self.calls.append(("activate_session", (project_id, agent_id)))
        return SessionBinding(
            session_id="session_alpha",
            project_id=project_id,
            agent_id=agent_id,
            revision=7,
            digest="sha256:project-seven",
            focus={"shotId": "sh_012"},
            activated_at="2026-08-05T01:01:00Z",
            updated_at="2026-08-05T01:01:00Z",
        )

    def get_context(self, session_id: str, project_id: str | None = None):
        self.calls.append(("get_context", (session_id, project_id)))
        return ProjectContext(
            project_id=self.context_project_id or project_id or "prj_night",
            project_name="夜明けの鋼",
            project_root=self.project_root,
            revision=7,
            digest="sha256:project-seven",
            focus={"shotId": "sh_012"},
            entities=self.entities,
            approvals=(),
            current_project_revision=7,
            is_current=True,
            session_id=self.context_session_id or session_id,
            agent_id="codex",
        )

    def commit_patch(
        self,
        session_id: str,
        expected_revision: int,
        patch,
        project_id: str | None = None,
    ):
        self.calls.append(
            (
                "commit_patch",
                (project_id, session_id, expected_revision, patch),
            )
        )
        if self.conflict:
            raise RevisionConflictError(
                project_id=project_id or "prj_night",
                session_id=session_id,
                expected_revision=expected_revision,
                actual_revision=8,
                session_revision=7,
            )
        return CommitResult(
            project_id=project_id or "prj_night",
            session_id=session_id,
            previous_revision=7,
            revision=8,
            digest="sha256:project-eight",
            focus={"shotId": "sh_012"},
            entity_change_count=len(patch.entities),
            approval_change_count=len(patch.approvals),
            committed_at="2026-08-05T01:02:00Z",
        )

    def create_handoff_capsule(
        self, session_id: str, project_id: str | None = None
    ):
        self.calls.append(("create_handoff_capsule", (project_id, session_id)))
        return HandoffCapsule(
            handoff_id="handoff_alpha",
            project_id=project_id or "prj_night",
            project_name="夜明けの鋼",
            project_root="projects/night",
            session_id=session_id,
            agent_id="codex",
            revision=7,
            digest="sha256:project-seven",
            focus={"shotId": "sh_012"},
            entities=(),
            approvals=(),
            created_at="2026-08-05T01:03:00Z",
            relative_path="projects/night/.h3story/handoffs/handoff_alpha.json",
        )


class FakeH3:
    def __init__(self) -> None:
        self.calls = 0
        self.failure: Exception | None = None

    def get_state(self):
        self.calls += 1
        if self.failure is not None:
            raise self.failure
        return {
            "capabilities": {"ready": True, "local_only": True},
            "active_job_id": None,
            "jobs": [],
            "system": {"backend": "h3"},
        }


def json_body(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


class StoryApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = FakeRepository()
        self.h3 = FakeH3()
        self.api = StoryApi(self.repository, self.h3, version="test-version")

    def request(
        self,
        method: str,
        target: str,
        body: object | bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> ApiResponse:
        encoded = (
            body
            if isinstance(body, bytes)
            else json_body(body) if body is not None else b""
        )
        return self.api.dispatch(method, target, headers or {}, encoded)

    def assert_error(self, response: ApiResponse, status: int, code: str) -> None:
        self.assertEqual(response.status, status)
        self.assertIsInstance(response.body, dict)
        self.assertEqual(response.body["error"]["code"], code)
        self.assertIsInstance(response.body["error"]["message"], str)

    def media_fixture(
        self,
        local_root: str,
        *,
        content: bytes = b"h3-story-mp4-payload",
        file_name: str = "take_demo.mp4",
        payload_overrides: dict[str, object] | None = None,
        max_media_bytes: int = 1024,
    ) -> tuple[StoryApi, FakeRepository, Path, bytes]:
        media_path = (
            Path(local_root)
            / "projects"
            / "night"
            / "outputs"
            / "sh_012"
            / file_name
        )
        media_path.parent.mkdir(parents=True)
        media_path.write_bytes(content)
        payload: dict[str, object] = {
            "kind": "video",
            "role": "generated_take",
            "relative_path": f"outputs/sh_012/{file_name}",
            "mime_type": "video/mp4",
            "byte_size": len(content),
            "sha256": "sha256:test-fixture",
        }
        payload.update(payload_overrides or {})
        repository = FakeRepository()
        repository.entities = (
            EntityState(
                entity_type="asset",
                entity_id="asset_take_demo",
                revision=7,
                payload=payload,
            ),
        )
        return (
            StoryApi(
                repository,
                self.h3,
                version="test-version",
                local_root=local_root,
                max_media_bytes=max_media_bytes,
            ),
            repository,
            media_path,
            content,
        )

    @staticmethod
    def read_file_slice(body: FileSlice) -> bytes:
        with body.path.open("rb") as media:
            media.seek(body.offset)
            return media.read(body.length)

    def test_health_and_h3_state_are_camel_case_and_do_not_contact_a_real_h3(self) -> None:
        health = self.request("GET", "/api/health")
        self.assertEqual(health.status, 200)
        self.assertEqual(
            health.body,
            {
                "status": "ok",
                "version": "test-version",
                "projectCore": "ready",
                "h3": "ready",
            },
        )

        state = self.request("GET", "/api/h3/state")
        self.assertEqual(state.status, 200)
        self.assertTrue(state.body["capabilities"]["localOnly"])
        self.assertIsNone(state.body["activeJobId"])
        self.assertEqual(self.h3.calls, 2)

    def test_project_urls_activate_and_context_preserve_session_binding(self) -> None:
        projects = self.request("GET", "/api/projects")
        self.assertEqual(projects.status, 200)
        project = projects.body[0]
        self.assertEqual(project["projectId"], "prj_night")
        self.assertEqual(project["title"], "夜明けの鋼")
        self.assertEqual(project["stateDigest"], "sha256:project-seven")
        self.assertNotIn("project_id", project)

        activated = self.request(
            "POST",
            "/api/projects/prj_night/activate",
            {"agentId": "codex"},
            JSON_HEADERS,
        )
        self.assertEqual(activated.status, 201)
        self.assertEqual(activated.body["sessionId"], "session_alpha")
        self.assertEqual(activated.body["boundRevision"], 7)
        self.assertEqual(activated.body["stateDigest"], "sha256:project-seven")

        context = self.request(
            "GET",
            "/api/projects/prj_night/context?sessionId=session_alpha",
        )
        self.assertEqual(context.status, 200)
        self.assertEqual(context.body["sessionId"], "session_alpha")
        self.assertEqual(context.body["projectRevision"], 7)
        self.assertEqual(
            self.repository.calls[-1],
            ("get_context", ("session_alpha", "prj_night")),
        )

    def test_commit_converts_wire_operations_to_project_patch(self) -> None:
        response = self.request(
            "POST",
            "/api/projects/prj_night/patches",
            {
                "sessionId": "session_alpha",
                "expectedRevision": 7,
                "message": "字幕を確定",
                "operations": [
                    {
                        "op": "upsert_entity",
                        "entityType": "asset",
                        "entityId": "asset_hero",
                        "payload": {"caption": "red coat"},
                    },
                    {"op": "set_focus", "payload": {"shotId": "sh_013"}},
                    {
                        "op": "set_approval",
                        "payload": {
                            "approvalId": "approval_take",
                            "targetType": "take",
                            "targetId": "take_007",
                            "status": "approved",
                            "payload": {"note": "採用"},
                        },
                    },
                ],
            },
            JSON_HEADERS,
        )
        self.assertEqual(response.status, 201)
        self.assertEqual(response.body["previousRevision"], 7)
        self.assertEqual(response.body["revision"], 8)
        self.assertEqual(response.body["stateDigest"], "sha256:project-eight")
        _, call = self.repository.calls[-1]
        project_id, session_id, expected_revision, patch = call
        self.assertEqual(
            (project_id, session_id, expected_revision),
            ("prj_night", "session_alpha", 7),
        )
        self.assertEqual(patch.message, "字幕を確定")
        self.assertEqual(patch.entities[0].entity_id, "asset_hero")
        self.assertEqual(patch.focus, {"shotId": "sh_013"})
        self.assertEqual(patch.approvals[0].status, "approved")

    def test_handoff_returns_wrapper_and_complete_camel_case_capsule(self) -> None:
        response = self.request(
            "POST",
            "/api/projects/prj_night/handoffs",
            {"sessionId": "session_alpha"},
            JSON_HEADERS,
        )
        self.assertEqual(response.status, 201)
        self.assertEqual(response.body["handoffId"], "handoff_alpha")
        self.assertEqual(response.body["projectRevision"], 7)
        self.assertEqual(response.body["stateDigest"], "sha256:project-seven")
        self.assertTrue(response.body["path"].endswith("handoff_alpha.json"))
        self.assertEqual(response.body["capsule"]["schemaVersion"], 1)
        self.assertNotIn("schema_version", response.body["capsule"])
        self.assertEqual(
            self.repository.calls[-1],
            ("create_handoff_capsule", ("prj_night", "session_alpha")),
        )

    def test_mutations_require_guard_header_and_allowed_browser_origin(self) -> None:
        missing = self.request(
            "POST",
            "/api/projects/prj_night/activate",
            {"agentId": "codex"},
            {"Content-Type": "application/json"},
        )
        self.assert_error(missing, 403, "MUTATION_HEADER_REQUIRED")

        forbidden = self.request(
            "GET",
            "/api/projects",
            headers={"Origin": "http://evil.example"},
        )
        self.assert_error(forbidden, 403, "ORIGIN_FORBIDDEN")
        self.assertNotIn("Access-Control-Allow-Origin", forbidden.headers or {})

        allowed_headers = {**JSON_HEADERS, "Origin": ALLOWED_ORIGIN}
        allowed = self.request(
            "POST",
            "/api/projects/prj_night/activate",
            {"agentId": "codex"},
            allowed_headers,
        )
        self.assertEqual(allowed.status, 201)
        self.assertEqual(allowed.headers["Access-Control-Allow-Origin"], ALLOWED_ORIGIN)
        self.assertEqual(allowed.headers["Vary"], "Origin")

    def test_options_preflight_exempts_mutation_guard_but_enforces_origin(self) -> None:
        response = self.request(
            "OPTIONS",
            "/api/projects/prj_night/patches",
            headers={"Origin": "http://127.0.0.1:3000"},
        )
        self.assertEqual(response.status, 204)
        self.assertIn("POST", response.headers["Access-Control-Allow-Methods"])
        self.assertIn(MUTATION_HEADER, response.headers["Access-Control-Allow-Headers"])

        forbidden = self.request(
            "OPTIONS",
            "/api/projects/prj_night/patches",
            headers={"Origin": "null"},
        )
        self.assert_error(forbidden, 403, "ORIGIN_FORBIDDEN")

    def test_json_limit_and_malformed_json_are_structured_errors(self) -> None:
        oversized = self.request(
            "POST",
            "/api/projects/prj_night/activate",
            b"{" + b"x" * MAX_JSON_BYTES + b"}",
            JSON_HEADERS,
        )
        self.assert_error(oversized, 413, "PAYLOAD_TOO_LARGE")

        malformed = self.request(
            "POST",
            "/api/projects/prj_night/activate",
            b"{not-json}",
            JSON_HEADERS,
        )
        self.assert_error(malformed, 400, "INVALID_JSON")

    def test_revision_conflict_is_409_with_machine_readable_details(self) -> None:
        self.repository.conflict = True
        response = self.request(
            "POST",
            "/api/projects/prj_night/patches",
            {
                "sessionId": "session_alpha",
                "expectedRevision": 7,
                "message": "stale update",
                "operations": [],
            },
            JSON_HEADERS,
        )
        self.assert_error(response, 409, "REVISION_CONFLICT")
        self.assertEqual(response.body["error"]["details"]["actualRevision"], 8)
        self.assertEqual(response.body["error"]["details"]["sessionRevision"], 7)

    def test_missing_dependencies_and_unknown_routes_use_structured_errors(self) -> None:
        unavailable = StoryApi(None, None)
        projects = unavailable.dispatch("GET", "/api/projects")
        self.assert_error(projects, 503, "PROJECT_CORE_UNAVAILABLE")
        h3 = unavailable.dispatch("GET", "/api/h3/state")
        self.assert_error(h3, 503, "H3_UNAVAILABLE")
        missing = self.request("GET", "/api/not-a-route")
        self.assert_error(missing, 404, "ROUTE_NOT_FOUND")

    def test_registered_video_media_supports_full_and_byte_range_reads(self) -> None:
        with tempfile.TemporaryDirectory() as local_root:
            api, repository, media_path, content = self.media_fixture(local_root)
            target = (
                "/api/projects/prj_night/assets/asset_take_demo/content"
                "?sessionId=session_alpha"
            )

            full = api.dispatch("GET", target)
            self.assertEqual(full.status, 200)
            self.assertIsInstance(full.body, FileSlice)
            self.assertEqual(full.body.path, media_path.resolve())
            self.assertEqual(self.read_file_slice(full.body), content)
            self.assertEqual(full.headers["Content-Type"], "video/mp4")
            self.assertEqual(full.headers["Content-Length"], str(len(content)))
            self.assertEqual(full.headers["Accept-Ranges"], "bytes")
            self.assertEqual(
                repository.calls[-1],
                ("get_context", ("session_alpha", "prj_night")),
            )

            ranged = api.dispatch(
                "GET",
                target,
                {"Range": "bytes=3-8", "Origin": ALLOWED_ORIGIN},
            )
            self.assertEqual(ranged.status, 206)
            self.assertIsInstance(ranged.body, FileSlice)
            self.assertEqual(self.read_file_slice(ranged.body), content[3:9])
            self.assertEqual(ranged.headers["Content-Length"], "6")
            self.assertEqual(
                ranged.headers["Content-Range"],
                f"bytes 3-8/{len(content)}",
            )
            self.assertEqual(
                ranged.headers["Access-Control-Allow-Origin"], ALLOWED_ORIGIN
            )

            suffix = api.dispatch("GET", target, {"Range": "bytes=-4"})
            self.assertEqual(suffix.status, 206)
            self.assertEqual(self.read_file_slice(suffix.body), content[-4:])

    def test_media_endpoint_streams_binary_without_json_encoding(self) -> None:
        with tempfile.TemporaryDirectory() as local_root:
            api, _repository, _media_path, content = self.media_fixture(local_root)
            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(api))
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            connection = http.client.HTTPConnection(
                "127.0.0.1", server.server_address[1], timeout=5
            )
            try:
                connection.request(
                    "GET",
                    (
                        "/api/projects/prj_night/assets/asset_take_demo/content"
                        "?sessionId=session_alpha"
                    ),
                    headers={"Range": "bytes=2-7", "Origin": ALLOWED_ORIGIN},
                )
                response = connection.getresponse()
                streamed = response.read()
                self.assertEqual(response.status, 206)
                self.assertEqual(response.getheader("Content-Type"), "video/mp4")
                self.assertEqual(response.getheader("Content-Length"), "6")
                self.assertEqual(response.getheader("Accept-Ranges"), "bytes")
                self.assertEqual(
                    response.getheader("Access-Control-Allow-Origin"), ALLOWED_ORIGIN
                )
                self.assertEqual(streamed, content[2:8])
            finally:
                connection.close()
                server.shutdown()
                server.server_close()
                worker.join(timeout=5)

    def test_media_endpoint_previews_registered_image_and_audio_assets(self) -> None:
        target = (
            "/api/projects/prj_night/assets/asset_take_demo/content"
            "?sessionId=session_alpha"
        )
        with tempfile.TemporaryDirectory() as local_root:
            image_api, _repository, _path, image_content = self.media_fixture(
                local_root,
                content=b"\x89PNG\r\n\x1a\npreview",
                file_name="hero.png",
                payload_overrides={
                    "kind": "character",
                    "media_kind": "image",
                    "mime_type": "image/png",
                },
            )
            image = image_api.dispatch("GET", target)
            self.assertEqual(image.status, 200)
            self.assertEqual(image.headers["Content-Type"], "image/png")
            self.assertEqual(self.read_file_slice(image.body), image_content)

        with tempfile.TemporaryDirectory() as local_root:
            audio_api, _repository, _path, audio_content = self.media_fixture(
                local_root,
                content=b"RIFF\x10\x00\x00\x00WAVEpreview",
                file_name="voice.wav",
                payload_overrides={
                    "kind": "reference",
                    "media_kind": "audio",
                    "mime_type": "audio/wav",
                },
            )
            audio = audio_api.dispatch("GET", target, {"Range": "bytes=4-11"})
            self.assertEqual(audio.status, 206)
            self.assertEqual(audio.headers["Content-Type"], "audio/wav")
            self.assertEqual(self.read_file_slice(audio.body), audio_content[4:12])

    def test_media_requires_exact_session_project_and_asset_registration(self) -> None:
        with tempfile.TemporaryDirectory() as local_root:
            api, repository, _media_path, _content = self.media_fixture(local_root)
            base = "/api/projects/prj_night/assets/asset_take_demo/content"

            missing_session = api.dispatch("GET", base)
            self.assert_error(missing_session, 400, "INVALID_REQUEST")

            unknown_asset = api.dispatch(
                "GET",
                (
                    "/api/projects/prj_night/assets/asset_other/content"
                    "?sessionId=session_alpha"
                ),
            )
            self.assert_error(unknown_asset, 404, "MEDIA_ASSET_NOT_FOUND")

            repository.context_project_id = "prj_other"
            crossed_project = api.dispatch(
                "GET", f"{base}?sessionId=session_alpha"
            )
            self.assert_error(crossed_project, 403, "PROJECT_BOUNDARY_ERROR")
            repository.context_project_id = None
            repository.context_session_id = "session_other"
            crossed_session = api.dispatch(
                "GET", f"{base}?sessionId=session_alpha"
            )
            self.assert_error(crossed_session, 403, "PROJECT_BOUNDARY_ERROR")

    def test_media_rejects_non_video_and_unregistered_file_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as local_root:
            non_video, _repository, _path, _content = self.media_fixture(
                local_root, payload_overrides={"kind": "image"}
            )
            target = (
                "/api/projects/prj_night/assets/asset_take_demo/content"
                "?sessionId=session_alpha"
            )
            response = non_video.dispatch("GET", target)
            self.assert_error(response, 415, "MEDIA_TYPE_UNSUPPORTED")

        with tempfile.TemporaryDirectory() as local_root:
            mismatched, _repository, _path, _content = self.media_fixture(
                local_root, payload_overrides={"byte_size": 999}
            )
            response = mismatched.dispatch("GET", target)
            self.assert_error(response, 409, "MEDIA_SIZE_MISMATCH")

        with tempfile.TemporaryDirectory() as local_root:
            oversized, _repository, _path, _content = self.media_fixture(
                local_root, content=b"0123456789", max_media_bytes=5
            )
            response = oversized.dispatch("GET", target)
            self.assert_error(response, 413, "MEDIA_TOO_LARGE")

    def test_media_rejects_missing_non_regular_and_unsafe_project_paths(self) -> None:
        target = (
            "/api/projects/prj_night/assets/asset_take_demo/content"
            "?sessionId=session_alpha"
        )
        with tempfile.TemporaryDirectory() as local_root:
            missing, _repository, media_path, _content = self.media_fixture(local_root)
            media_path.unlink()
            response = missing.dispatch("GET", target)
            self.assert_error(response, 404, "MEDIA_FILE_NOT_FOUND")

        with tempfile.TemporaryDirectory() as local_root:
            api, repository, media_path, _content = self.media_fixture(local_root)
            media_path.unlink()
            media_path.mkdir()
            response = api.dispatch("GET", target)
            self.assert_error(response, 409, "MEDIA_FILE_INVALID")

            repository.project_root = "../night"
            unsafe_project = api.dispatch("GET", target)
            self.assert_error(unsafe_project, 403, "MEDIA_PATH_UNSAFE")

    def test_media_rejects_traversal_and_symlink_breakout(self) -> None:
        with tempfile.TemporaryDirectory() as local_root:
            project_directory = Path(local_root, "projects", "night")
            project_directory.mkdir(parents=True)
            outside = Path(local_root, "outside.mp4")
            outside.write_bytes(b"outside-video")
            repository = FakeRepository()
            repository.entities = (
                EntityState(
                    entity_type="asset",
                    entity_id="asset_take_demo",
                    revision=7,
                    payload={
                        "kind": "video",
                        "relative_path": "../outside.mp4",
                        "mime_type": "video/mp4",
                    },
                ),
            )
            api = StoryApi(repository, self.h3, local_root=local_root)
            target = (
                "/api/projects/prj_night/assets/asset_take_demo/content"
                "?sessionId=session_alpha"
            )
            traversal = api.dispatch("GET", target)
            self.assert_error(traversal, 403, "MEDIA_PATH_UNSAFE")

            output_directory = project_directory / "outputs"
            output_directory.mkdir()
            link = output_directory / "take_demo.mp4"
            repository.entities = (
                EntityState(
                    entity_type="asset",
                    entity_id="asset_take_demo",
                    revision=7,
                    payload={
                        "kind": "video",
                        "relative_path": "outputs/take_demo.mp4",
                        "mime_type": "video/mp4",
                        "byte_size": len(b"outside-video"),
                    },
                ),
            )
            original_resolve = Path.resolve

            def resolve_symlink(
                path: Path, strict: bool = False
            ) -> Path:
                if path == link:
                    return outside.resolve()
                return original_resolve(path, strict=strict)

            # Simulate the platform-independent result of resolving an NTFS
            # symlink without requiring Windows Developer Mode in CI.
            with mock.patch.object(Path, "resolve", new=resolve_symlink):
                breakout = api.dispatch("GET", target)
            self.assert_error(breakout, 403, "MEDIA_PATH_UNSAFE")

    def test_media_invalid_range_is_structured_416_with_content_range(self) -> None:
        with tempfile.TemporaryDirectory() as local_root:
            api, _repository, _media_path, content = self.media_fixture(local_root)
            target = (
                "/api/projects/prj_night/assets/asset_take_demo/content"
                "?sessionId=session_alpha"
            )
            outside = api.dispatch(
                "GET", target, {"Range": f"bytes={len(content)}-"}
            )
            self.assert_error(outside, 416, "RANGE_NOT_SATISFIABLE")
            self.assertEqual(
                outside.headers["Content-Range"], f"bytes */{len(content)}"
            )
            self.assertEqual(outside.headers["Accept-Ranges"], "bytes")

            multiple = api.dispatch(
                "GET", target, {"Range": "bytes=0-1,4-5"}
            )
            self.assert_error(multiple, 416, "RANGE_NOT_SATISFIABLE")

    def test_default_dependencies_seed_once_and_use_reopenable_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository, _h3 = build_default_dependencies(temporary_directory)
            try:
                first = repository.list_projects()
                self.assertEqual(len(first), 3)
                first_revisions = {
                    summary.project_id: summary.revision for summary in first
                }
            finally:
                repository.close()

            reopened, _h3_again = build_default_dependencies(temporary_directory)
            try:
                second = reopened.list_projects()
                self.assertEqual(len(second), 3)
                self.assertEqual(
                    {
                        summary.project_id: summary.revision for summary in second
                    },
                    first_revisions,
                )
            finally:
                reopened.close()


if __name__ == "__main__":
    unittest.main()
