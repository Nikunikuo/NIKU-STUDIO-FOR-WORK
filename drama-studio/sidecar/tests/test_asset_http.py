from __future__ import annotations

import http.client
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest

from sidecar.asset_service import AssetCreateResult, AssetService, AssetUpload
from sidecar.h3_story_core import (
    ProjectCreateResult,
    ProjectCreateSpec,
    ProjectRepository,
    ProjectSummary,
)
from sidecar.http_server import FileSlice, MUTATION_HEADER, StoryApi, make_handler


class FakeProjectRepository:
    def __init__(self) -> None:
        self.created = None

    def list_projects(self):
        return (
            ProjectSummary(
                project_id="prj_runtime",
                name="Runtime Story",
                project_root="projects/runtime-abcd",
                revision=0,
                digest="sha256:" + "a" * 64,
                focus={"shot_id": "sh_001"},
                created_at="2026-08-05T00:00:00Z",
                updated_at="2026-08-05T00:00:00Z",
                metadata={
                    "code": "RUNTIME-01",
                    "title": "Runtime Story",
                    "subtitle": "Metadata subtitle",
                },
            ),
        )

    def create_project(self, spec):
        self.created = spec
        return ProjectCreateResult(
            project_id="prj_created",
            project_root="projects/story-01-acde123456",
            code=spec.code,
            title=spec.title,
            revision=0,
            digest="sha256:" + "b" * 64,
            focus={
                "episode_id": "ep_001",
                "scene_id": "sc_001",
                "shot_id": "sh_001",
                "prompt_id": "prompt_001",
            },
            metadata={
                "code": spec.code,
                "title": spec.title,
                "episode_title": spec.episode_title,
            },
        )


class FakeAssetService:
    def __init__(self) -> None:
        self.created = None

    def create_asset(self, **kwargs):
        self.created = kwargs
        spec = kwargs["spec"]
        upload: AssetUpload | None = kwargs["upload"]
        payload = {
            "kind": spec.category,
            "category": spec.category,
            "name": spec.name,
            "role": spec.role,
            "caption": spec.caption,
            "prompt_caption": spec.prompt_caption,
            "locked": spec.locked,
            "source": spec.source,
        }
        if upload is not None:
            payload.update(
                {
                    "media_kind": "image",
                    "relative_path": "assets/asset_server.png",
                    "mime_type": "image/png",
                    "byte_size": len(upload.content),
                    "sha256": "f" * 64,
                    "original_file_name": upload.file_name,
                }
            )
        return AssetCreateResult(
            asset_id="asset_server",
            revision=spec.expected_revision + 1,
            digest="sha256:" + "c" * 64,
            payload=payload,
        )


def json_post(api: StoryApi, path: str, payload: dict[str, object]):
    return api.dispatch(
        "POST",
        path,
        {MUTATION_HEADER: "1", "Content-Type": "application/json"},
        json.dumps(payload).encode("utf-8"),
    )


def multipart_body(
    fields: dict[str, str],
    *,
    file_name: str | None = None,
    mime_type: str = "image/png",
    content: bytes = b"",
    boundary: str = "h3story-boundary",
) -> tuple[str, bytes]:
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend(
            (
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                value.encode("utf-8"),
                b"\r\n",
            )
        )
    if file_name is not None:
        chunks.extend(
            (
                f"--{boundary}\r\n".encode(),
                (
                    'Content-Disposition: form-data; name="file"; '
                    f'filename="{file_name}"\r\n'
                ).encode(),
                f"Content-Type: {mime_type}\r\n\r\n".encode(),
                content,
                b"\r\n",
            )
        )
    chunks.append(f"--{boundary}--\r\n".encode())
    return f"multipart/form-data; boundary={boundary}", b"".join(chunks)


class AssetHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = FakeProjectRepository()
        self.assets = FakeAssetService()
        self.api = StoryApi(
            self.repository,
            None,
            asset_service=self.assets,
        )

    @staticmethod
    def asset_fields() -> dict[str, object]:
        return {
            "sessionId": "session_001",
            "expectedRevision": 4,
            "name": "Hero",
            "category": "character",
            "role": "Main character",
            "caption": "Human caption",
            "promptCaption": "H3 prompt caption",
            "locked": True,
            "source": "human",
        }

    def test_project_list_metadata_and_runtime_create_wire_contract(self) -> None:
        projects = self.api.dispatch("GET", "/api/projects")
        self.assertEqual(projects.status, 200)
        self.assertEqual(projects.body[0]["code"], "RUNTIME-01")
        self.assertEqual(projects.body[0]["title"], "Runtime Story")
        self.assertEqual(
            projects.body[0]["metadata"]["subtitle"], "Metadata subtitle"
        )

        created = json_post(
            self.api,
            "/api/projects",
            {
                "title": "New Story",
                "code": "STORY-01",
                "subtitle": "Subtitle",
                "episodeTitle": "Episode",
                "sceneTitle": "Scene",
                "shotTitle": "Shot",
                "logline": "Logline",
                "summary": "Summary",
            },
        )
        self.assertEqual(created.status, 201)
        self.assertEqual(created.body["projectId"], "prj_created")
        self.assertEqual(created.body["projectRoot"], "projects/story-01-acde123456")
        self.assertEqual(created.body["stateDigest"], "sha256:" + "b" * 64)
        self.assertNotIn("digest", created.body)
        self.assertEqual(self.repository.created.episode_title, "Episode")

    def test_project_create_validates_required_fields_and_mutation_guard(self) -> None:
        unguarded = self.api.dispatch(
            "POST",
            "/api/projects",
            {"Content-Type": "application/json"},
            b"{}",
        )
        self.assertEqual(unguarded.status, 403)
        self.assertEqual(unguarded.body["error"]["code"], "MUTATION_HEADER_REQUIRED")

        invalid = json_post(self.api, "/api/projects", {"title": "Only title"})
        self.assertEqual(invalid.status, 400)
        self.assertEqual(invalid.body["error"]["code"], "INVALID_REQUEST")

    def test_json_asset_creation_uses_exact_contract_and_camel_case_response(self) -> None:
        response = json_post(
            self.api,
            "/api/projects/prj_runtime/assets",
            self.asset_fields(),
        )
        self.assertEqual(response.status, 201)
        self.assertEqual(response.body["assetId"], "asset_server")
        self.assertEqual(response.body["revision"], 5)
        self.assertEqual(response.body["stateDigest"], "sha256:" + "c" * 64)
        self.assertEqual(response.body["asset"]["promptCaption"], "H3 prompt caption")
        self.assertNotIn("prompt_caption", response.body["asset"])
        self.assertEqual(self.assets.created["project_id"], "prj_runtime")
        self.assertIsNone(self.assets.created["upload"])

    def test_multipart_asset_creation_parses_utf8_fields_and_file(self) -> None:
        fields = {
            key: ("true" if value is True else str(value))
            for key, value in self.asset_fields().items()
        }
        content_type, body = multipart_body(
            fields,
            file_name="hero.png",
            content=b"\x89PNG\r\n\x1a\nfixture",
        )
        response = self.api.dispatch(
            "POST",
            "/api/projects/prj_runtime/assets",
            {MUTATION_HEADER: "1", "Content-Type": content_type},
            body,
        )
        self.assertEqual(response.status, 201)
        upload = self.assets.created["upload"]
        self.assertEqual(upload.file_name, "hero.png")
        self.assertEqual(upload.declared_mime_type, "image/png")
        self.assertTrue(upload.content.startswith(b"\x89PNG"))
        self.assertEqual(response.body["asset"]["mediaKind"], "image")
        self.assertEqual(response.body["asset"]["relativePath"], "assets/asset_server.png")

    def test_multipart_rejects_missing_boundary_duplicate_and_unknown_fields(self) -> None:
        missing_boundary = self.api.dispatch(
            "POST",
            "/api/projects/prj_runtime/assets",
            {MUTATION_HEADER: "1", "Content-Type": "multipart/form-data"},
            b"not multipart",
        )
        self.assertEqual(missing_boundary.status, 400)
        self.assertEqual(missing_boundary.body["error"]["code"], "INVALID_MULTIPART")

        boundary = "duplicate"
        duplicate = (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"name\"\r\n\r\nA\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"name\"\r\n\r\nB\r\n"
            f"--{boundary}--\r\n"
        ).encode()
        duplicated = self.api.dispatch(
            "POST",
            "/api/projects/prj_runtime/assets",
            {
                MUTATION_HEADER: "1",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
            duplicate,
        )
        self.assertEqual(duplicated.status, 400)
        self.assertIn("duplicate", duplicated.body["error"]["message"])

        content_type, body = multipart_body({"unexpected": "value"})
        unknown = self.api.dispatch(
            "POST",
            "/api/projects/prj_runtime/assets",
            {MUTATION_HEADER: "1", "Content-Type": content_type},
            body,
        )
        self.assertEqual(unknown.status, 400)
        self.assertIn("unsupported", unknown.body["error"]["message"])

    def test_http_handler_allows_asset_multipart_larger_than_json_limit(self) -> None:
        fields = {
            key: ("true" if value is True else str(value))
            for key, value in self.asset_fields().items()
        }
        large_png = b"\x89PNG\r\n\x1a\n" + b"x" * (2 * 1024 * 1024)
        content_type, body = multipart_body(
            fields,
            file_name="large.png",
            content=large_png,
        )
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.api))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        connection = http.client.HTTPConnection(
            "127.0.0.1", server.server_address[1], timeout=10
        )
        try:
            connection.request(
                "POST",
                "/api/projects/prj_runtime/assets",
                body=body,
                headers={
                    MUTATION_HEADER: "1",
                    "Content-Type": content_type,
                    "Origin": "http://localhost:3000",
                },
            )
            response = connection.getresponse()
            payload = json.loads(response.read())
            self.assertEqual(response.status, 201)
            self.assertEqual(payload["assetId"], "asset_server")
            self.assertEqual(
                response.getheader("Access-Control-Allow-Origin"),
                "http://localhost:3000",
            )
            self.assertEqual(len(self.assets.created["upload"].content), len(large_png))
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_real_repository_upload_and_preview_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            local_root = Path(directory)
            with ProjectRepository(local_root) as repository:
                project = repository.create_project(
                    ProjectCreateSpec(
                        title="Round Trip",
                        code="ROUND-TRIP",
                        subtitle="",
                        episode_title="Episode",
                        scene_title="Scene",
                        shot_title="Shot",
                        logline="",
                        summary="",
                    )
                )
                session = repository.activate_session(
                    project.project_id, "human:http-test"
                )
                api = StoryApi(
                    repository,
                    None,
                    asset_service=AssetService(repository, local_root),
                    local_root=local_root,
                )
                fields = {
                    **{
                        key: ("true" if value is True else str(value))
                        for key, value in self.asset_fields().items()
                    },
                    "sessionId": session.session_id,
                    "expectedRevision": "0",
                }
                image_bytes = b"\x89PNG\r\n\x1a\nround-trip"
                content_type, body = multipart_body(
                    fields,
                    file_name="hero.png",
                    content=image_bytes,
                )
                created = api.dispatch(
                    "POST",
                    f"/api/projects/{project.project_id}/assets",
                    {MUTATION_HEADER: "1", "Content-Type": content_type},
                    body,
                )
                self.assertEqual(created.status, 201)
                self.assertEqual(created.body["revision"], 1)
                asset_id = created.body["assetId"]
                preview = api.dispatch(
                    "GET",
                    (
                        f"/api/projects/{project.project_id}/assets/{asset_id}/content"
                        f"?sessionId={session.session_id}"
                    ),
                )
                self.assertEqual(preview.status, 200)
                self.assertEqual(preview.headers["Content-Type"], "image/png")
                self.assertIsInstance(preview.body, FileSlice)
                with preview.body.path.open("rb") as handle:
                    self.assertEqual(handle.read(), image_bytes)


if __name__ == "__main__":
    unittest.main()
