import assert from "node:assert/strict";
import { access, readFile, readdir } from "node:fs/promises";
import test from "node:test";

const developmentPreviewMeta =
  /<meta(?=[^>]*\bname=["']codex-preview["'])(?=[^>]*\bcontent=["']development["'])[^>]*>/i;
const projectRoot = new URL("../", import.meta.url);
const previewRoot = new URL("../app/_sites-preview/", import.meta.url);

async function render() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);

  return worker.fetch(
    new Request("http://localhost/", {
      headers: { accept: "text/html" },
    }),
    {
      ASSETS: {
        fetch: async () => new Response("Not found", { status: 404 }),
      },
    },
    {
      waitUntil() {},
      passThroughOnException() {},
    },
  );
}

test("server-renders the H3 Story Studio project workspace", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);

  const html = await response.text();
  assert.match(html, /<html[^>]*lang="ja"/i);
  assert.match(html, /<title>NIKU STDUIO FOR WORK \| Local AI Film Production<\/title>/i);
  assert.match(html, /NIKU STDUIO FOR WORK/);
  assert.match(html, /Projectを読み込み中/);
  assert.match(html, /Project Coreから物語を読み込んでいます。/);
  assert.match(html, /Codex Director/);
  assert.match(html, /Codexで続ける/);
  assert.match(html, /World Bible/);
  assert.match(html, /動画制作/);
  assert.doesNotMatch(html, developmentPreviewMeta);
  assert.doesNotMatch(html, /Your site is taking shape|Building your site|react-loading-skeleton/i);
});

test("removes the disposable starter surface and keeps runtime authoring wired", async () => {
  const [page, layout, packageJson, readme, previewFiles] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/layout.tsx", import.meta.url), "utf8"),
    readFile(new URL("../package.json", import.meta.url), "utf8"),
    readFile(new URL("../README.md", import.meta.url), "utf8"),
    readdir(previewRoot),
  ]);

  assert.deepEqual(previewFiles, []);
  assert.match(page, /useState<Project\[\]>\(\[\]\)/);
  assert.match(page, /STORY_SIDECAR\.listProjects\(\)/);
  assert.match(page, /STORY_SIDECAR\.createProject\(request\)/);
  assert.match(page, /STORY_SIDECAR\.uploadAsset/);
  assert.match(page, /async function saveScript/);
  assert.match(page, /H3STORY_HANDOFF/);
  assert.match(page, /selectedTakesByShot/);
  assert.match(page, /function TakeThumbnail/);
  assert.match(page, /async function downloadMediaToBrowser/);
  assert.match(page, /className="take-download"/);
  assert.match(page, /className="timeline-download"/);
  assert.match(layout, /title:\s*"NIKU STDUIO FOR WORK \| Local AI Film Production"/);
  assert.match(packageJson, /"name": "h3-story-studio"/);
  assert.match(readme, /^# NIKU STDUIO FOR WORK — Drama Studio/m);
  assert.doesNotMatch(page, /SkeletonPreview|codex-preview|react-loading-skeleton/);
  assert.doesNotMatch(page, /新規Projectフローは次の実装ゲート|startDemoRender/);
  assert.doesNotMatch(packageJson, /react-loading-skeleton|site-creator-vinext-starter/);
  assert.doesNotMatch(readme, /vinext-starter|loading skeleton/i);
  assert.match(page, /acceleration: "community"/);
  assert.match(page, /高速化なし（比較用）/);
  assert.match(page, /おすすめ：控えめ（通常）/);
  assert.match(page, /より慎重（品質優先）/);
  assert.match(page, /速度優先（品質を確認）/);
  assert.match(page, /Draft（12 steps未満）ではH3が自動的に高速化なし/);

  await Promise.all([
    "public/favicon.svg",
    "public/file.svg",
    "public/globe.svg",
    "public/window.svg",
  ].map((path) => assert.rejects(access(new URL(path, projectRoot)))));
});
