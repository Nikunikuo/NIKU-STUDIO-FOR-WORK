from __future__ import annotations

import io
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from starlette.datastructures import UploadFile


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from webui import server


class _Container:
    def __init__(self, *, has_audio: bool) -> None:
        self.streams = SimpleNamespace(audio=[object()] if has_audio else [])

    def __enter__(self) -> "_Container":
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class UploadAudioProbeTests(unittest.IsolatedAsyncioTestCase):
    async def _save(self, root: Path, *, has_audio: bool) -> dict[str, object]:
        upload = UploadFile(file=io.BytesIO(b"test-video"), filename="reference.mp4")
        with mock.patch.object(server.av, "open", return_value=_Container(has_audio=has_audio)):
            return await server._save_upload(upload, root, 0)

    async def test_video_upload_records_verified_audio_presence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with_audio = await self._save(root, has_audio=True)
            without_audio = await self._save(root, has_audio=False)

        self.assertIs(with_audio["has_audio"], True)
        self.assertIs(without_audio["has_audio"], False)

    async def test_failed_probe_stays_explicitly_unverified(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            upload = UploadFile(file=io.BytesIO(b"broken"), filename="reference.mp4")
            with mock.patch.object(server.av, "open", side_effect=server.av.error.InvalidDataError(0, "bad")):
                metadata = await server._save_upload(upload, Path(temporary), 0)

        self.assertNotIn("has_audio", metadata)


if __name__ == "__main__":
    unittest.main()
