from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import BytesIO, StringIO
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "scripts" / "storyctl.py"
SPEC = importlib.util.spec_from_file_location("storyctl_under_test", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
storyctl = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = storyctl
SPEC.loader.exec_module(storyctl)


class FakeClient:
    def __init__(self, result=None) -> None:
        self.result = {"ok": True} if result is None else result
        self.calls: list[tuple[str, str, object]] = []

    def request(self, method, path, payload=None):
        self.calls.append((method, path, payload))
        return self.result

    def request_multipart(self, path, fields, file_name):
        self.calls.append(("MULTIPART", path, {"fields": fields, "file": file_name}))
        return self.result


class FakeResponse:
    def __init__(self, body: object) -> None:
        self.body = json.dumps(body).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.body


class StoryCtlTests(unittest.TestCase):
    def parse(self, *argv: str):
        return storyctl.build_parser().parse_args(argv)

    def test_read_only_commands_and_context_paths(self) -> None:
        client = FakeClient([{"projectId": "night"}])
        self.assertEqual(
            storyctl.execute(self.parse("projects"), client),
            [{"projectId": "night"}],
        )
        storyctl.execute(self.parse("health"), client)
        storyctl.execute(self.parse("h3-state"), client)
        storyctl.execute(
            self.parse(
                "context",
                "--project-id",
                "prj_night",
                "--session-id",
                "session_1",
            ),
            client,
        )
        self.assertEqual(
            client.calls,
            [
                ("GET", "/api/projects", None),
                ("GET", "/api/health", None),
                ("GET", "/api/h3/state", None),
                (
                    "GET",
                    "/api/projects/prj_night/context?sessionId=session_1",
                    None,
                ),
            ],
        )

    def test_activate_and_handoff_emit_session_ready_payloads(self) -> None:
        client = FakeClient({"sessionId": "session_new", "boundRevision": 7})
        result = storyctl.execute(
            self.parse("activate", "--project-id", "prj_night"), client
        )
        self.assertEqual(result["sessionId"], "session_new")
        self.assertEqual(
            client.calls[-1],
            (
                "POST",
                "/api/projects/prj_night/activate",
                {"agentId": "codex-storyctl"},
            ),
        )
        storyctl.execute(
            self.parse(
                "handoff",
                "--project-id",
                "prj_night",
                "--session-id",
                "session_new",
            ),
            client,
        )
        self.assertEqual(
            client.calls[-1],
            (
                "POST",
                "/api/projects/prj_night/handoffs",
                {"sessionId": "session_new"},
            ),
        )

    def test_create_project_and_asset_commands_forward_public_contracts(self) -> None:
        client = FakeClient({"projectId": "prj_generated", "revision": 0})
        project_spec = {
            "title": "Zero Start",
            "code": "ZERO-START",
            "subtitle": "Local",
            "episodeTitle": "Episode 1",
            "sceneTitle": "Scene 1",
            "shotTitle": "Shot 1",
            "logline": "Start from nothing.",
            "summary": "Created by a fresh Codex.",
        }
        storyctl.execute(
            self.parse("create-project", "--spec-json", json.dumps(project_spec)),
            client,
        )
        self.assertEqual(client.calls[-1], ("POST", "/api/projects", project_spec))

        asset_spec = {
            "name": "主人公",
            "category": "character",
            "role": "主人公",
            "caption": "黒髪の整備士。",
            "promptCaption": "short black hair, mechanic workwear",
            "locked": True,
            "source": "codex_gpt_image_2",
        }
        base = (
            "add-asset", "--project-id", "prj_generated", "--session-id", "session_1",
            "--expected-revision", "0", "--spec-json", json.dumps(asset_spec),
        )
        storyctl.execute(self.parse(*base), client)
        self.assertEqual(client.calls[-1][0:2], ("POST", "/api/projects/prj_generated/assets"))
        self.assertEqual(client.calls[-1][2]["expectedRevision"], 0)
        self.assertEqual(client.calls[-1][2]["source"], "codex_gpt_image_2")

        storyctl.execute(self.parse(*base, "--file", "C:/tmp/hero.png"), client)
        self.assertEqual(client.calls[-1][0], "MULTIPART")
        self.assertEqual(client.calls[-1][2]["file"], "C:/tmp/hero.png")
        self.assertEqual(client.calls[-1][2]["fields"]["locked"], True)

    def test_patch_accepts_inline_or_file_array_and_forwards_cas(self) -> None:
        operations = [{"op": "set_focus", "payload": {"shotId": "sh_012"}}]
        client = FakeClient({"revision": 8})
        result = storyctl.execute(
            self.parse(
                "patch",
                "--project-id",
                "prj_night",
                "--session-id",
                "session_1",
                "--expected-revision",
                "7",
                "--message",
                "  focus Shot 012  ",
                "--operations-json",
                json.dumps(operations),
            ),
            client,
        )
        self.assertEqual(result["revision"], 8)
        self.assertEqual(
            client.calls[-1][2],
            {
                "sessionId": "session_1",
                "expectedRevision": 7,
                "message": "focus Shot 012",
                "operations": operations,
            },
        )

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "operations.json")
            path.write_text(json.dumps(operations), encoding="utf-8")
            storyctl.execute(
                self.parse(
                    "patch",
                    "--project-id",
                    "prj_night",
                    "--session-id",
                    "session_1",
                    "--expected-revision",
                    "8",
                    "--message",
                    "same operations from file",
                    "--operations-file",
                    str(path),
                ),
                client,
            )
        self.assertEqual(client.calls[-1][2]["operations"], operations)

    def test_patch_rejects_non_array_or_malformed_json_without_http_call(self) -> None:
        client = FakeClient()
        base = (
            "patch",
            "--project-id",
            "prj_night",
            "--session-id",
            "session_1",
            "--expected-revision",
            "7",
            "--message",
            "edit",
            "--operations-json",
        )
        with self.assertRaises(storyctl.CliError) as not_array:
            storyctl.execute(self.parse(*base, "{}"), client)
        self.assertEqual(not_array.exception.code, "INVALID_OPERATIONS")
        with self.assertRaises(storyctl.CliError) as malformed:
            storyctl.execute(self.parse(*base, "[oops"), client)
        self.assertEqual(malformed.exception.code, "INVALID_JSON")
        self.assertEqual(client.calls, [])

    def test_render_job_and_sync_contracts(self) -> None:
        client = FakeClient()
        request = {"fields": {"mode": "t2v"}, "uploads": {}}
        storyctl.execute(
            self.parse(
                "render",
                "--project-id",
                "prj_night",
                "--shot-id",
                "sh_012",
                "--session-id",
                "session_1",
                "--expected-revision",
                "7",
                "--request-json",
                json.dumps(request),
            ),
            client,
        )
        self.assertEqual(
            client.calls[-1],
            (
                "POST",
                "/api/projects/prj_night/shots/sh_012/renders",
                {
                    "sessionId": "session_1",
                    "expectedRevision": 7,
                    "request": request,
                },
            ),
        )

        storyctl.execute(self.parse("job", "--job-id", "abc123def456"), client)
        self.assertEqual(client.calls[-1], ("GET", "/api/h3/jobs/abc123def456", None))

        storyctl.execute(
            self.parse(
                "sync",
                "--project-id",
                "prj_night",
                "--local-job-id",
                "render_1",
                "--session-id",
                "session_1",
                "--expected-revision",
                "8",
                "--no-download",
            ),
            client,
        )
        self.assertEqual(client.calls[-1][2]["downloadResult"], False)

    def test_dangerous_ids_and_invalid_h3_job_are_rejected_before_http(self) -> None:
        client = FakeClient()
        with self.assertRaises(storyctl.CliError) as unsafe:
            storyctl.execute(
                self.parse("activate", "--project-id", "../escape"), client
            )
        self.assertEqual(unsafe.exception.code, "INVALID_IDENTIFIER")
        with self.assertRaises(storyctl.CliError) as invalid_job:
            storyctl.execute(self.parse("job", "--job-id", "ABC123"), client)
        self.assertEqual(invalid_job.exception.code, "INVALID_H3_JOB_ID")
        self.assertEqual(client.calls, [])

    def test_http_client_pins_origin_and_adds_guard_only_to_mutations(self) -> None:
        captured = []

        def fake_urlopen(request, timeout):
            captured.append((request, timeout))
            return FakeResponse({"ok": True})

        client = storyctl.SidecarClient(timeout=3)
        with patch.object(storyctl, "urlopen", side_effect=fake_urlopen):
            client.request("GET", "/api/health")
            client.request("POST", "/api/projects/prj/activate", {"agentId": "codex"})
            with tempfile.TemporaryDirectory() as directory:
                image = Path(directory, "asset.png")
                image.write_bytes(b"\x89PNG\r\n\x1a\nTEST")
                client.request_multipart(
                    "/api/projects/prj/assets",
                    {"sessionId": "session_1", "expectedRevision": 1, "locked": True},
                    image,
                )

        get_request, get_timeout = captured[0]
        post_request, post_timeout = captured[1]
        self.assertEqual(get_request.full_url, "http://127.0.0.1:7864/api/health")
        self.assertIsNone(get_request.get_header(storyctl.MUTATION_HEADER))
        post_headers = {name.casefold(): value for name, value in post_request.header_items()}
        self.assertEqual(
            post_headers[storyctl.MUTATION_HEADER.casefold()],
            storyctl.MUTATION_HEADER_VALUE,
        )
        self.assertEqual(json.loads(post_request.data), {"agentId": "codex"})
        multipart_request, multipart_timeout = captured[2]
        multipart_headers = {
            name.casefold(): value for name, value in multipart_request.header_items()
        }
        self.assertIn("multipart/form-data; boundary=h3storyctl-", multipart_headers["content-type"])
        self.assertIn(b'name="sessionId"', multipart_request.data)
        self.assertIn(b'filename="asset.png"', multipart_request.data)
        self.assertIn(b"\x89PNG\r\n\x1a\nTEST", multipart_request.data)
        self.assertEqual((get_timeout, post_timeout, multipart_timeout), (3, 3, 3))

    def test_main_writes_json_stdout_and_structured_http_error_stderr(self) -> None:
        stdout = StringIO()
        stderr = StringIO()
        with patch.object(storyctl, "urlopen", return_value=FakeResponse({"状態": "正常"})):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = storyctl.main(["health"])
        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(stdout.getvalue()), {"状態": "正常"})
        self.assertEqual(stderr.getvalue(), "")

        http_error = HTTPError(
            "http://127.0.0.1:7864/api/projects",
            503,
            "Unavailable",
            {},
            BytesIO(
                json.dumps(
                    {"error": {"code": "PROJECT_CORE_UNAVAILABLE", "message": "offline"}}
                ).encode("utf-8")
            ),
        )
        stdout = StringIO()
        stderr = StringIO()
        with patch.object(storyctl, "urlopen", side_effect=http_error):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = storyctl.main(["projects"])
        self.assertEqual(exit_code, 3)
        self.assertEqual(stdout.getvalue(), "")
        error_wire = json.loads(stderr.getvalue())
        self.assertEqual(error_wire["error"]["code"], "PROJECT_CORE_UNAVAILABLE")
        self.assertEqual(error_wire["error"]["details"]["httpStatus"], 503)


if __name__ == "__main__":
    unittest.main()
