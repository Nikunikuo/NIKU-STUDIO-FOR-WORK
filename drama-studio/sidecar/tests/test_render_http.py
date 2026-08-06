from __future__ import annotations

import json
import unittest

from sidecar.http_server import MUTATION_HEADER, StoryApi
from sidecar.render_service import RenderSubmission, RenderSyncResult


class FakeRenderService:
    def __init__(self) -> None:
        self.submitted = None
        self.synced = None

    def submit_render(self, **kwargs):
        self.submitted = kwargs
        return RenderSubmission(
            project_id=kwargs["project_id"],
            shot_id=kwargs["shot_id"],
            take_id="take_demo",
            local_job_id="job_demo",
            h3_job_id="a" * 12,
            revision=3,
            status="queued",
        )

    def sync_render(self, **kwargs):
        self.synced = kwargs
        return RenderSyncResult(
            project_id=kwargs["project_id"],
            shot_id="sh_012",
            take_id="take_demo",
            local_job_id=kwargs["local_job_id"],
            h3_job_id="a" * 12,
            revision=4,
            status="running",
            progress=0.42,
        )


class FakeH3:
    def get_job(self, job_id: str):
        return {"id": job_id, "status": "running", "progress": 0.25}


def post(api: StoryApi, path: str, payload: dict[str, object]):
    return api.dispatch(
        "POST",
        path,
        {MUTATION_HEADER: "1", "Content-Type": "application/json"},
        json.dumps(payload).encode("utf-8"),
    )


class RenderHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.render = FakeRenderService()
        self.api = StoryApi(None, None, self.render)

    def test_submit_render_forwards_bound_revision_and_request(self) -> None:
        request = {"shotId": "sh_012", "fields": {}, "uploads": {}}
        response = post(
            self.api,
            "/api/projects/prj_001/shots/sh_012/renders",
            {
                "sessionId": "session_001",
                "expectedRevision": 1,
                "request": request,
            },
        )

        self.assertEqual(response.status, 202)
        self.assertEqual(response.body["localJobId"], "job_demo")
        self.assertEqual(response.body["h3JobId"], "a" * 12)
        self.assertEqual(self.render.submitted["project_id"], "prj_001")
        self.assertEqual(self.render.submitted["shot_id"], "sh_012")
        self.assertEqual(self.render.submitted["expected_revision"], 1)
        self.assertEqual(self.render.submitted["request"], request)

    def test_sync_render_is_a_guarded_mutation_and_can_skip_download(self) -> None:
        response = post(
            self.api,
            "/api/projects/prj_001/renders/job_demo/sync",
            {
                "sessionId": "session_001",
                "expectedRevision": 3,
                "downloadResult": False,
            },
        )

        self.assertEqual(response.status, 200)
        self.assertEqual(response.body["status"], "running")
        self.assertEqual(response.body["progress"], 0.42)
        self.assertEqual(self.render.synced["local_job_id"], "job_demo")
        self.assertFalse(self.render.synced["download_result"])

        unguarded = self.api.dispatch(
            "POST",
            "/api/projects/prj_001/renders/job_demo/sync",
            {"Content-Type": "application/json"},
            b"{}",
        )
        self.assertEqual(unguarded.status, 403)
        self.assertEqual(unguarded.body["error"]["code"], "MUTATION_HEADER_REQUIRED")

    def test_render_routes_validate_ids_revision_and_dependency(self) -> None:
        unsafe = post(
            self.api,
            "/api/projects/prj_001/shots/..%2Fescape/renders",
            {
                "sessionId": "session_001",
                "expectedRevision": 1,
                "request": {},
            },
        )
        self.assertEqual(unsafe.status, 400)

        invalid_revision = post(
            self.api,
            "/api/projects/prj_001/shots/sh_012/renders",
            {
                "sessionId": "session_001",
                "expectedRevision": -1,
                "request": {},
            },
        )
        self.assertEqual(invalid_revision.status, 400)

        unavailable = StoryApi(None, None, None)
        missing = post(
            unavailable,
            "/api/projects/prj_001/shots/sh_012/renders",
            {
                "sessionId": "session_001",
                "expectedRevision": 1,
                "request": {},
            },
        )
        self.assertEqual(missing.status, 503)
        self.assertEqual(missing.body["error"]["code"], "RENDER_SERVICE_UNAVAILABLE")

    def test_h3_job_poll_is_read_only_and_validates_the_pinned_id(self) -> None:
        api = StoryApi(None, FakeH3(), self.render)
        response = api.dispatch("GET", "/api/h3/jobs/aaaaaaaaaaaa")
        self.assertEqual(response.status, 200)
        self.assertEqual(response.body["status"], "running")
        self.assertEqual(response.body["progress"], 0.25)

        unsafe = api.dispatch("GET", "/api/h3/jobs/..%2Fescape")
        self.assertEqual(unsafe.status, 404)


if __name__ == "__main__":
    unittest.main()
