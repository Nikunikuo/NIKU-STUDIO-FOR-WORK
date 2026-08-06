import assert from "node:assert/strict";
import test from "node:test";

import {
  StorySidecarClient,
  StorySidecarError,
  STORY_MUTATION_HEADER,
} from "../lib/story/sidecar-client";

test("sidecar client is pinned to the numeric loopback boundary", () => {
  assert.throws(
    () => new StorySidecarClient({ origin: "http://localhost:7864" }),
    /exactly http:\/\/127\.0\.0\.1:7864/,
  );
  assert.throws(
    () => new StorySidecarClient({ origin: "https://127.0.0.1:7864" }),
    /exactly http:\/\/127\.0\.0\.1:7864/,
  );
});

test("activation sends JSON and the local mutation marker", async () => {
  const calls: Array<{ url: string; init: RequestInit }> = [];
  const client = new StorySidecarClient({
    fetchImpl: async (input, init = {}) => {
      calls.push({ url: String(input), init });
      return Response.json({
        sessionId: "ses_001",
        projectId: "prj_001",
        agentId: "codex",
        boundRevision: 7,
        stateDigest: "sha256:demo",
        focus: {},
        activatedAt: "2026-08-05T00:00:00Z",
      });
    },
  });

  const binding = await client.activateProject("prj_001", "codex");
  assert.equal(binding.boundRevision, 7);
  assert.equal(calls[0].url, "http://127.0.0.1:7864/api/projects/prj_001/activate");
  assert.equal(calls[0].init.method, "POST");
  const headers = new Headers(calls[0].init.headers);
  assert.equal(headers.get(STORY_MUTATION_HEADER), "1");
  assert.equal(headers.get("content-type"), "application/json");
  assert.deepEqual(JSON.parse(String(calls[0].init.body)), { agentId: "codex" });
});

test("read calls omit the mutation marker and unsafe IDs never reach fetch", async () => {
  let calls = 0;
  const client = new StorySidecarClient({
    fetchImpl: async (_input, init = {}) => {
      calls += 1;
      assert.equal(new Headers(init.headers).has(STORY_MUTATION_HEADER), false);
      return Response.json({ status: "ok", version: "0.1.0", projectCore: "ready", h3: "offline" });
    },
  });

  assert.equal((await client.health()).status, "ok");
  await assert.rejects(() => client.activateProject("../other", "codex"), /unsafe characters/);
  assert.equal(calls, 1);
});

test("structured sidecar failures preserve revision conflict details", async () => {
  const client = new StorySidecarClient({
    fetchImpl: async () => Response.json(
      {
        error: {
          code: "REVISION_CONFLICT",
          message: "expected revision 7, actual revision 8",
          details: { expected: 7, actual: 8 },
        },
      },
      { status: 409 },
    ),
  });

  await assert.rejects(
    () => client.commitPatch("prj_001", {
      sessionId: "ses_001",
      expectedRevision: 7,
      message: "caption update",
      operations: [],
    }),
    (error: unknown) => {
      assert.ok(error instanceof StorySidecarError);
      assert.equal(error.status, 409);
      assert.equal(error.code, "REVISION_CONFLICT");
      assert.deepEqual(error.details, { expected: 7, actual: 8 });
      return true;
    },
  );
});

test("runtime project creation sends the full scaffold request", async () => {
  const calls: Array<{ url: string; init: RequestInit }> = [];
  const client = new StorySidecarClient({
    fetchImpl: async (input, init = {}) => {
      calls.push({ url: String(input), init });
      return Response.json({
        projectId: "prj_generated",
        projectRoot: "projects/browser-test-123",
        code: "BROWSER-TEST",
        title: "Browser Test",
        revision: 0,
        stateDigest: "sha256:created",
        focus: { episodeId: "ep_1", sceneId: "sc_1", shotId: "sh_1", promptId: "prompt_1" },
      }, { status: 201 });
    },
  });

  const request = {
    title: "Browser Test",
    code: "BROWSER-TEST",
    subtitle: "Local",
    episodeTitle: "Episode 1",
    sceneTitle: "Scene 1",
    shotTitle: "Shot 1",
    logline: "A clean-room browser test.",
    summary: "Created through the public Story Sidecar contract.",
  };
  const result = await client.createProject(request);
  assert.equal(result.revision, 0);
  assert.equal(calls[0].url, "http://127.0.0.1:7864/api/projects");
  assert.equal(calls[0].init.method, "POST");
  const headers = new Headers(calls[0].init.headers);
  assert.equal(headers.get(STORY_MUTATION_HEADER), "1");
  assert.equal(headers.get("content-type"), "application/json");
  assert.deepEqual(JSON.parse(String(calls[0].init.body)), request);
});

test("metadata asset creation sends CAS fields as JSON", async () => {
  const calls: Array<{ url: string; init: RequestInit }> = [];
  const client = new StorySidecarClient({
    fetchImpl: async (input, init = {}) => {
      calls.push({ url: String(input), init });
      return Response.json({
        projectId: "prj_001",
        assetId: "asset_generated",
        revision: 8,
        stateDigest: "sha256:asset",
        asset: { name: "Hero", category: "character", mediaKind: "none" },
      }, { status: 201 });
    },
  });
  await client.createAsset("prj_001", "ses_001", 7, {
    name: "Hero",
    category: "character",
    role: "lead",
    caption: "human caption",
    promptCaption: "reference metadata",
    locked: true,
    source: "human",
  });
  const body = JSON.parse(String(calls[0].init.body));
  assert.equal(calls[0].url, "http://127.0.0.1:7864/api/projects/prj_001/assets");
  assert.equal(body.sessionId, "ses_001");
  assert.equal(body.expectedRevision, 7);
  assert.equal(body.promptCaption, "reference metadata");
});

test("binary asset upload preserves browser multipart boundaries", async () => {
  const calls: Array<{ url: string; init: RequestInit }> = [];
  const client = new StorySidecarClient({
    fetchImpl: async (input, init = {}) => {
      calls.push({ url: String(input), init });
      return Response.json({
        projectId: "prj_001",
        assetId: "asset_image",
        revision: 9,
        stateDigest: "sha256:image",
        asset: { name: "Hero", category: "character", mediaKind: "image", relativePath: "assets/asset_image.png" },
      }, { status: 201 });
    },
  });
  const file = new File([new Uint8Array([137, 80, 78, 71])], "hero.png", { type: "image/png" });
  await client.uploadAsset("prj_001", "ses_001", 8, {
    name: "Hero",
    category: "character",
    role: "lead",
    caption: "caption",
    promptCaption: "metadata",
    locked: false,
    source: "codex_gpt_image_2",
  }, file);
  const headers = new Headers(calls[0].init.headers);
  assert.equal(headers.get(STORY_MUTATION_HEADER), "1");
  assert.equal(headers.has("content-type"), false);
  assert.ok(calls[0].init.body instanceof FormData);
  const form = calls[0].init.body as FormData;
  assert.equal(form.get("sessionId"), "ses_001");
  assert.equal(form.get("expectedRevision"), "8");
  assert.equal(form.get("source"), "codex_gpt_image_2");
  assert.ok(form.get("file") instanceof File);
});

test("asset content URL is project/session scoped and rejects unsafe IDs", () => {
  const client = new StorySidecarClient();
  assert.equal(
    client.assetContentUrl("prj_001", "asset_001", "ses_001"),
    "http://127.0.0.1:7864/api/projects/prj_001/assets/asset_001/content?sessionId=ses_001",
  );
  assert.throws(() => client.assetContentUrl("../escape", "asset_001", "ses_001"), /unsafe characters/);
});
