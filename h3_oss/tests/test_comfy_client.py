from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
import urllib.error
import uuid
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from webui.comfy_client import (  # noqa: E402
    ComfyClient,
    ComfyHttpError,
    ComfyProtocolError,
)


PROMPT_ID = "11111111-2222-4333-8444-555555555555"


class FakeResponse:
    def __init__(self, body: bytes, url: str) -> None:
        self._body = io.BytesIO(body)
        self._url = url

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self) -> bytes:
        return self._body.read()

    def geturl(self) -> str:
        return self._url


class FakeOpener:
    def __init__(self) -> None:
        self.requests: list[Any] = []
        self.responses: list[tuple[int, bytes]] = []

    def add_json(self, payload: Any, status: int = 200) -> None:
        self.responses.append((status, json.dumps(payload).encode("utf-8")))

    def add_bytes(self, payload: bytes, status: int = 200) -> None:
        self.responses.append((status, payload))

    def open(self, request, timeout):  # noqa: ANN001
        self.requests.append(request)
        status, body = self.responses.pop(0)
        if status >= 400:
            raise urllib.error.HTTPError(
                request.full_url,
                status,
                "failure",
                {},
                io.BytesIO(body),
            )
        return FakeResponse(body, request.full_url)


class ComfyClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.opener = FakeOpener()
        self.client = ComfyClient(port=8188, opener=self.opener)

    def test_server_origin_is_numeric_loopback_and_not_user_configurable(self):
        self.assertEqual(self.client.base_url, "http://127.0.0.1:8188")
        with self.assertRaises(ValueError):
            ComfyClient(port=0)

    def test_health_and_object_info_use_official_routes(self):
        self.opener.add_json({"system": {"os": "nt"}})
        self.opener.add_json({"EasyCache": {"input": {}}})

        self.assertEqual(self.client.health()["system"]["os"], "nt")
        self.assertIn("EasyCache", self.client.object_info("EasyCache"))

        self.assertEqual(self.opener.requests[0].full_url, "http://127.0.0.1:8188/system_stats")
        self.assertEqual(self.opener.requests[1].full_url, "http://127.0.0.1:8188/object_info/EasyCache")

    def test_submit_prompt_posts_api_workflow_and_validates_returned_uuid(self):
        self.opener.add_json({"prompt_id": PROMPT_ID, "number": 3, "node_errors": {}})
        workflow = {"1": {"class_type": "KSampler", "inputs": {}}}

        result = self.client.submit_prompt(workflow, prompt_id=PROMPT_ID, client_id="h3-studio")

        request = self.opener.requests[0]
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.full_url, "http://127.0.0.1:8188/prompt")
        self.assertEqual(body["prompt"], workflow)
        self.assertEqual(body["prompt_id"], PROMPT_ID)
        self.assertEqual(result["prompt_id"], PROMPT_ID)

        self.opener.add_json({"prompt_id": "not-a-uuid"})
        with self.assertRaises(ValueError):
            self.client.submit_prompt(workflow)

    def test_upload_uses_multipart_input_and_never_sends_source_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "参照画像.png"
            source.write_bytes(b"\x89PNG\r\nreference")
            self.opener.add_json({"name": "reference.png", "subfolder": "h3/job", "type": "input"})

            response = self.client.upload_image(source, subfolder="h3\\job")

        request = self.opener.requests[0]
        body = request.data
        self.assertEqual(response["name"], "reference.png")
        self.assertEqual(request.full_url, "http://127.0.0.1:8188/upload/image")
        self.assertIn(b'name="image"', body)
        self.assertIn(b'name="type"\r\n\r\ninput', body)
        self.assertIn(b'name="subfolder"\r\n\r\nh3/job', body)
        self.assertIn(b"%E5%8F%82%E7%85%A7%E7%94%BB%E5%83%8F.png", body)
        self.assertNotIn(str(source.parent).encode(), body)

    def test_upload_rejects_wrong_media_type_size_and_traversal(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "payload.exe"
            source.write_bytes(b"MZ")
            with self.assertRaises(ValueError):
                self.client.upload_image(source)

            video = Path(temporary) / "clip.mp4"
            video.write_bytes(b"12345")
            limited = ComfyClient(opener=self.opener, max_upload_bytes=4)
            with self.assertRaises(ValueError):
                limited.upload_video(video)
            with self.assertRaises(ValueError):
                self.client.upload_video(video, subfolder="../escape")

    def test_poll_combines_queue_and_history_without_websocket(self):
        self.opener.add_json({})
        self.opener.add_json(
            {
                "queue_running": [],
                "queue_pending": [[2, str(uuid.uuid4())], [3, PROMPT_ID]],
            }
        )
        queued = self.client.poll_prompt(PROMPT_ID)
        self.assertEqual((queued.state, queued.progress, queued.queue_position), ("queued", 0, 1))

        history_entry = {
            "status": {"status_str": "success", "completed": True, "messages": []},
            "outputs": {"9": {"gifs": [{"filename": "h3.mp4", "subfolder": "", "type": "output"}]}},
        }
        self.opener.add_json({PROMPT_ID: history_entry})
        completed = self.client.poll_prompt(PROMPT_ID)
        self.assertEqual((completed.state, completed.progress), ("completed", 100))
        self.assertEqual(completed.outputs["9"]["gifs"][0]["filename"], "h3.mp4")

    def test_poll_extracts_execution_error_and_interruption(self):
        error_detail = {"node_id": "5", "exception_message": "CUDA out of memory"}
        self.opener.add_json(
            {
                PROMPT_ID: {
                    "status": {
                        "status_str": "error",
                        "completed": True,
                        "messages": [["execution_error", error_detail]],
                    },
                    "outputs": {},
                }
            }
        )
        failed = self.client.poll_prompt(PROMPT_ID)
        self.assertEqual(failed.state, "failed")
        self.assertEqual(failed.error, error_detail)

        self.opener.add_json(
            {
                PROMPT_ID: {
                    "status": {
                        "status_str": "error",
                        "completed": True,
                        "messages": [["execution_interrupted", {"prompt_id": PROMPT_ID}]],
                    }
                }
            }
        )
        self.assertEqual(self.client.poll_prompt(PROMPT_ID).state, "cancelled")

    def test_targeted_interrupt_and_queue_delete_have_exact_payloads(self):
        self.opener.add_bytes(b"")
        self.opener.add_bytes(b"")

        self.client.interrupt(PROMPT_ID)
        self.client.delete_queued(PROMPT_ID)

        interrupt_request, delete_request = self.opener.requests
        self.assertEqual(interrupt_request.full_url, "http://127.0.0.1:8188/interrupt")
        self.assertEqual(json.loads(interrupt_request.data), {"prompt_id": PROMPT_ID})
        self.assertEqual(delete_request.full_url, "http://127.0.0.1:8188/queue")
        self.assertEqual(json.loads(delete_request.data), {"delete": [PROMPT_ID]})

    def test_view_query_is_encoded_and_descriptor_cannot_traverse(self):
        self.opener.add_bytes(b"video-bytes")
        descriptor = {"filename": "生成 結果.mp4", "subfolder": "h3/run 1", "type": "output"}

        self.assertEqual(self.client.view_output(descriptor), b"video-bytes")
        url = self.opener.requests[0].full_url
        self.assertTrue(url.startswith("http://127.0.0.1:8188/view?"))
        query = urllib_parse(url)
        self.assertEqual(query["filename"], ["生成 結果.mp4"])
        self.assertEqual(query["subfolder"], ["h3/run 1"])

        for malicious in (
            {"filename": "../secret.txt", "subfolder": "", "type": "output"},
            {"filename": "secret.txt", "subfolder": "../../", "type": "output"},
            {"filename": "secret.txt", "subfolder": "/absolute", "type": "output"},
            {"filename": "secret.mp4:$DATA", "subfolder": "", "type": "output"},
            {"filename": "secret.txt", "subfolder": "", "type": "C:\\Windows"},
        ):
            with self.assertRaises(ValueError):
                self.client.view_output(malicious)

    def test_resolve_output_path_stays_inside_configured_root_and_blocks_symlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            output_root = base / "output"
            result_dir = output_root / "h3"
            result_dir.mkdir(parents=True)
            video = result_dir / "result.mp4"
            video.write_bytes(b"ok")
            client = ComfyClient(opener=self.opener, directory_roots={"output": output_root})

            resolved = client.resolve_output_path(
                {"filename": "result.mp4", "subfolder": "h3", "type": "output"}
            )
            self.assertEqual(resolved, video.resolve())

            outside = base / "outside"
            outside.mkdir()
            (outside / "stolen.mp4").write_bytes(b"no")
            link = output_root / "escape"
            try:
                link.symlink_to(outside, target_is_directory=True)
            except OSError:
                self.skipTest("directory symlinks are unavailable on this host")
            with self.assertRaises(ValueError):
                client.resolve_output_path(
                    {"filename": "stolen.mp4", "subfolder": "escape", "type": "output"}
                )

    def test_http_error_includes_comfy_validation_body(self):
        self.opener.add_bytes(b'{"error":"invalid prompt"}', status=400)
        with self.assertRaises(ComfyHttpError) as caught:
            self.client.submit_prompt({"1": {"class_type": "Missing", "inputs": {}}})
        self.assertEqual(caught.exception.status, 400)
        self.assertIn("invalid prompt", caught.exception.detail)

    def test_final_origin_is_revalidated(self):
        class EscapingOpener:
            def open(self, request, timeout):  # noqa: ANN001
                return FakeResponse(b"{}", "http://example.invalid/object_info")

        client = ComfyClient(opener=EscapingOpener())
        with self.assertRaises(ComfyProtocolError):
            client.object_info()


def urllib_parse(url: str) -> dict[str, list[str]]:
    from urllib.parse import parse_qs, urlsplit

    return parse_qs(urlsplit(url).query)


if __name__ == "__main__":
    unittest.main()
