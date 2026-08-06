from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import tempfile
import threading
import unittest

from sidecar.h3_adapter import (
    AssetDescriptor,
    AssetResolver,
    H3AssetError,
    H3Client,
    H3JobStatus,
    H3UnsupportedOperation,
    H3_MUTATION_HEADER,
    H3_MUTATION_HEADER_VALUE,
    H3_ORIGIN,
    media_kind,
    TransportRequest,
    TransportResponse,
)


def json_response(payload: object, status: int = 200) -> TransportResponse:
    return TransportResponse(
        status=status,
        headers={"Content-Type": "application/json"},
        body=json.dumps(payload).encode("utf-8"),
    )


def job_payload(
    status: str = "queued", *, job_id: str = "abcdef123456", progress: float = 0
) -> dict[str, object]:
    terminal = status in {"completed", "failed", "cancelled", "interrupted"}
    return {
        "id": job_id,
        "status": status,
        "phase": "rendering" if status == "running" else status,
        "message": f"job is {status}",
        "progress": progress,
        "result": f"/api/jobs/{job_id}/result" if status == "completed" else None,
        "preview": f"/api/jobs/{job_id}/preview" if progress else None,
        "can_cancel": not terminal,
    }


class FakeTransport:
    def __init__(self, responses: list[TransportResponse]) -> None:
        self.responses = list(responses)
        self.requests: list[TransportRequest] = []

    def send(self, request: TransportRequest) -> TransportResponse:
        self.requests.append(request)
        if not self.responses:
            raise AssertionError(f"unexpected request: {request.method} {request.url}")
        return self.responses.pop(0)


def base_request(mode: str = "t2v") -> dict[str, object]:
    return {
        "shotId": "shot-42",
        "fields": {
            "mode": mode,
            "style": "cinematic",
            "prompt": "A crane shot over a rain-lit city.",
            "width": 768,
            "height": 448,
            "num_frames": 124,
            "steps": 20,
            "seed": 12345,
            "acceleration": "off",
            "ref_image_size": "match",
            "prompt_processing_mode": "community",
            "standalone_audio_policy": "dialogue_priority",
            "audio_preset": "auto",
            "dialogue": "",
            "soundscape": "distant traffic and rain",
            "music_policy": "subtle",
            "audio_gain_db": 0,
        },
        "uploads": {
            "first_image_asset_id": None,
            "last_image_asset_id": None,
            "reference_asset_ids": [],
        },
    }


class H3AdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary.name)
        self.assets = self.workspace / "assets"
        self.assets.mkdir()
        (self.assets / "character.png").write_bytes(b"PNG_CHARACTER")
        (self.assets / "motion.mp4").write_bytes(b"MP4_MOTION")
        (self.assets / "voice.wav").write_bytes(b"WAV_VOICE")
        self.asset_index = {
            "image-asset": "character.png",
            "video-asset": "motion.mp4",
            "audio-asset": "voice.wav",
        }
        self.resolver = AssetResolver([self.assets], self.asset_index)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_submit_uses_fixed_loopback_mutation_header_and_streaming_multipart(self) -> None:
        transport = FakeTransport([json_response(job_payload())])
        client = H3Client(
            transport,
            self.resolver,
            boundary_factory=lambda: "H3BoundaryForTest",
        )
        request = base_request("omni")
        uploads = request["uploads"]
        assert isinstance(uploads, dict)
        uploads["reference_asset_ids"] = [
            "image-asset",
            "video-asset",
            "audio-asset",
        ]

        job = client.submit_job(request)

        self.assertEqual(job.id, "abcdef123456")
        self.assertEqual(job.status, H3JobStatus.QUEUED)
        self.assertEqual(len(transport.requests), 1)
        sent = transport.requests[0]
        self.assertEqual(sent.method, "POST")
        self.assertEqual(sent.url, f"{H3_ORIGIN}/api/jobs")
        self.assertEqual(sent.headers[H3_MUTATION_HEADER], H3_MUTATION_HEADER_VALUE)
        self.assertFalse(any(name.casefold() == "origin" for name in sent.headers))
        self.assertEqual(
            sent.headers["Content-Type"],
            "multipart/form-data; boundary=H3BoundaryForTest",
        )
        self.assertIsNotNone(sent.body)
        assert sent.body is not None and not isinstance(sent.body, bytes)
        wire = b"".join(sent.body.iter_bytes(chunk_size=3))
        self.assertEqual(int(sent.headers["Content-Length"]), len(wire))
        self.assertEqual(sent.body.content_length, len(wire))
        self.assertIn(b'name="mode"\r\n\r\nomni\r\n', wire)
        self.assertIn(b'name="num_frames"\r\n\r\n124\r\n', wire)
        self.assertEqual(wire.count(b'name="references"; filename='), 3)
        image_at = wire.index(b'filename="character.png"')
        video_at = wire.index(b'filename="motion.mp4"')
        audio_at = wire.index(b'filename="voice.wav"')
        self.assertLess(image_at, video_at)
        self.assertLess(video_at, audio_at)
        self.assertIn(b"PNG_CHARACTER", wire)
        self.assertIn(b"MP4_MOTION", wire)
        self.assertIn(b"WAV_VOICE", wire)
        self.assertNotIn(str(self.assets).encode("utf-8"), wire)
        self.assertTrue(wire.endswith(b"--H3BoundaryForTest--\r\n"))

    def test_per_submission_resolver_is_project_scoped_and_does_not_mutate_default(self) -> None:
        scoped_root = self.workspace / "project-two"
        scoped_root.mkdir()
        scoped_file = scoped_root / "scoped.png"
        scoped_file.write_bytes(b"PROJECT_TWO_IMAGE")
        scoped_resolver = AssetResolver(
            [scoped_root],
            {"image-asset": "scoped.png"},
        )
        transport = FakeTransport(
            [json_response(job_payload()), json_response(job_payload())]
        )
        client = H3Client(
            transport,
            self.resolver,
            boundary_factory=lambda: "ProjectScopedBoundary",
        )
        request = base_request("omni")
        uploads = request["uploads"]
        assert isinstance(uploads, dict)
        uploads["reference_asset_ids"] = ["image-asset"]

        client.submit_job(request, asset_resolver=scoped_resolver)
        client.submit_job(request)

        scoped_body = transport.requests[0].body
        default_body = transport.requests[1].body
        assert scoped_body is not None and not isinstance(scoped_body, bytes)
        assert default_body is not None and not isinstance(default_body, bytes)
        scoped_wire = b"".join(scoped_body.iter_bytes())
        default_wire = b"".join(default_body.iter_bytes())
        self.assertIn(b"PROJECT_TWO_IMAGE", scoped_wire)
        self.assertNotIn(b"PNG_CHARACTER", scoped_wire)
        self.assertIn(b"PNG_CHARACTER", default_wire)
        self.assertNotIn(b"PROJECT_TWO_IMAGE", default_wire)
        self.assertEqual(scoped_resolver.resolve("image-asset"), scoped_file)
        self.assertEqual(
            self.resolver.resolve("image-asset"),
            self.assets / "character.png",
        )

    def test_immutable_descriptor_hash_is_checked_over_exact_streamed_bytes(self) -> None:
        original = b"PNG_CHARACTER"
        descriptor_resolver = AssetResolver(
            [self.assets],
            {
                "image-asset": AssetDescriptor(
                    path="character.png",
                    mime_type="image/png",
                    byte_size=len(original),
                    sha256=sha256(original).hexdigest(),
                    source_path=self.assets / "character.png",
                )
            },
        )
        transport = FakeTransport([json_response(job_payload())])
        client = H3Client(
            transport,
            descriptor_resolver,
            boundary_factory=lambda: "ImmutableSnapshotBoundary",
        )
        request = base_request("omni")
        uploads = request["uploads"]
        assert isinstance(uploads, dict)
        uploads["reference_asset_ids"] = ["image-asset"]

        client.submit_job(request)
        # Mutate after request construction, using the same size so only the
        # descriptor digest can catch the drift in the exact yielded stream.
        (self.assets / "character.png").write_bytes(b"X" * len(original))
        body = transport.requests[0].body
        assert body is not None and not isinstance(body, bytes)
        yielded: list[bytes] = []
        with self.assertRaisesRegex(H3AssetError, "changed content during upload"):
            for chunk in body.iter_bytes(chunk_size=3):
                yielded.append(chunk)
        self.assertFalse(
            b"".join(yielded).endswith(b"--ImmutableSnapshotBoundary--\r\n")
        )

    def test_registered_mime_must_match_the_exact_upload_extension(self) -> None:
        with self.assertRaisesRegex(H3AssetError, "does not match"):
            media_kind("frame.png", "image/jpeg")

    def test_state_is_converted_and_get_never_sends_mutation_or_origin_headers(self) -> None:
        state_payload = {
            "capabilities": {
                "backend": "comfy",
                "ready": True,
                "local_only": True,
                "modes": {
                    "t2v": True,
                    "i2v": True,
                    "first_last": True,
                    "omni": True,
                },
                "model_files": {"fl2va": True, "ref2va": True},
            },
            "active_job_id": "abcdef123456",
            "jobs": [job_payload("running", progress=37.5)],
            "system": {"gpu": {"name": "RTX 5090"}},
        }
        transport = FakeTransport([json_response(state_payload)])
        client = H3Client(transport, self.resolver)

        state = client.get_state()

        self.assertTrue(state.capabilities.ready)
        self.assertTrue(state.capabilities.local_only)
        self.assertEqual(state.capabilities.backend, "comfy")
        self.assertTrue(state.capabilities.modes["omni"])
        self.assertEqual(state.active_job_id, "abcdef123456")
        self.assertEqual(state.jobs[0].status, H3JobStatus.RUNNING)
        self.assertEqual(state.jobs[0].progress, 37.5)
        self.assertEqual(state.system["gpu"], {"name": "RTX 5090"})
        sent = transport.requests[0]
        self.assertEqual(sent.url, f"{H3_ORIGIN}/api/state")
        self.assertFalse(any(name.casefold() == "origin" for name in sent.headers))
        self.assertNotIn(H3_MUTATION_HEADER, sent.headers)

    def test_asset_ids_cannot_traverse_or_name_an_outside_absolute_path(self) -> None:
        outside = self.workspace / "outside.png"
        outside.write_bytes(b"outside")
        resolver = AssetResolver(
            [self.assets],
            {
                "traversal": "../outside.png",
                "absolute-outside": outside,
            },
        )

        with self.assertRaises(H3AssetError):
            resolver.resolve("traversal")
        with self.assertRaises(H3AssetError):
            resolver.resolve("absolute-outside")
        # IDs remain opaque: even a real absolute path is not accepted unless
        # that exact string is an allowlisted key.
        with self.assertRaises(H3AssetError):
            resolver.resolve(str(self.assets / "character.png"))

        transport = FakeTransport([])
        client = H3Client(transport, resolver)
        request = base_request("i2v")
        uploads = request["uploads"]
        assert isinstance(uploads, dict)
        uploads["first_image_asset_id"] = "traversal"
        with self.assertRaises(H3AssetError):
            client.submit_job(request)
        self.assertEqual(transport.requests, [])

    def test_cancel_result_and_delete_semantics_match_the_pinned_api(self) -> None:
        transport = FakeTransport(
            [
                json_response(job_payload("cancelled")),
                TransportResponse(
                    status=200,
                    headers={
                        "Content-Type": "video/mp4",
                        "Content-Disposition": 'attachment; filename="MiniMax-H3-abcdef123456.mp4"',
                    },
                    body=b"MP4_RESULT",
                ),
            ]
        )
        client = H3Client(transport, self.resolver)

        cancelled = client.cancel_job("abcdef123456")
        result = client.get_result("abcdef123456")

        self.assertEqual(cancelled.status, H3JobStatus.CANCELLED)
        cancel_request = transport.requests[0]
        self.assertEqual(
            cancel_request.url, f"{H3_ORIGIN}/api/jobs/abcdef123456/cancel"
        )
        self.assertEqual(
            cancel_request.headers[H3_MUTATION_HEADER], H3_MUTATION_HEADER_VALUE
        )
        self.assertEqual(cancel_request.headers["Content-Length"], "0")
        self.assertFalse(
            any(name.casefold() == "origin" for name in cancel_request.headers)
        )
        self.assertEqual(result.job_id, "abcdef123456")
        self.assertEqual(result.content, b"MP4_RESULT")
        self.assertEqual(result.content_type, "video/mp4")
        result_request = transport.requests[1]
        self.assertEqual(
            result_request.url, f"{H3_ORIGIN}/api/jobs/abcdef123456/result"
        )
        self.assertNotIn(H3_MUTATION_HEADER, result_request.headers)

        before_delete = len(transport.requests)
        with self.assertRaises(H3UnsupportedOperation):
            client.delete_job("abcdef123456")
        self.assertEqual(len(transport.requests), before_delete)

    def test_polling_converts_status_updates_until_terminal(self) -> None:
        transport = FakeTransport(
            [
                json_response(job_payload("queued", progress=0)),
                json_response(job_payload("running", progress=48)),
                json_response(job_payload("completed", progress=100)),
            ]
        )
        client = H3Client(transport, self.resolver)
        now = [10.0]
        updates: list[H3JobStatus] = []

        def fake_sleep(seconds: float) -> None:
            now[0] += seconds

        completed = client.poll_job(
            "abcdef123456",
            interval_seconds=0.25,
            timeout_seconds=5,
            on_update=lambda job: updates.append(job.status),
            sleep=fake_sleep,
            clock=lambda: now[0],
        )

        self.assertEqual(completed.status, H3JobStatus.COMPLETED)
        self.assertEqual(
            updates,
            [H3JobStatus.QUEUED, H3JobStatus.RUNNING, H3JobStatus.COMPLETED],
        )
        self.assertEqual(now[0], 10.5)
        self.assertEqual(
            [request.url for request in transport.requests],
            [f"{H3_ORIGIN}/api/jobs/abcdef123456"] * 3,
        )

    def test_submit_and_wait_serializes_the_single_h3_renderer(self) -> None:
        class BlockingTransport:
            def __init__(self) -> None:
                self.requests: list[TransportRequest] = []
                self.first_entered = threading.Event()
                self.second_entered = threading.Event()
                self.release_first = threading.Event()
                self._lock = threading.Lock()

            def send(self, request: TransportRequest) -> TransportResponse:
                with self._lock:
                    self.requests.append(request)
                    call_number = len(self.requests)
                if call_number == 1:
                    self.first_entered.set()
                    if not self.release_first.wait(timeout=2):
                        raise AssertionError("test did not release first render")
                else:
                    self.second_entered.set()
                job_id = "111111111111" if call_number == 1 else "222222222222"
                return json_response(job_payload("completed", job_id=job_id, progress=100))

        transport = BlockingTransport()
        client = H3Client(
            transport,
            self.resolver,
            boundary_factory=lambda: "SerialBoundary",
        )
        results: list[str] = []

        def render() -> None:
            results.append(client.submit_and_wait(base_request()).id)

        first = threading.Thread(target=render)
        second = threading.Thread(target=render)
        first.start()
        self.assertTrue(transport.first_entered.wait(timeout=1))
        second.start()
        # The second invocation must not reach transport while the first owns
        # the serial render section.
        self.assertFalse(transport.second_entered.wait(timeout=0.1))
        transport.release_first.set()
        first.join(timeout=2)
        second.join(timeout=2)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertTrue(transport.second_entered.is_set())
        self.assertCountEqual(results, ["111111111111", "222222222222"])
        self.assertEqual(len(transport.requests), 2)


if __name__ == "__main__":
    unittest.main()
