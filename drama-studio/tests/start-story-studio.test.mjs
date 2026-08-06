import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const launcherPath = path.join(root, "scripts", "start-story-studio.ps1");
const cmdPath = path.join(root, "Start-H3-Story-Studio.cmd");

test("launcher keeps the existing H3 repository read-only and uses its launcher", async () => {
  const source = await readFile(launcherPath, "utf8");

  assert.match(source, /動画生成モデルH3_OSS/);
  assert.match(source, /scripts\\start_webui\.ps1/);
  assert.doesNotMatch(source, /Remove-Item|Stop-Process|taskkill|kill\s/i);
});

test("launcher checks all fixed loopback services and avoids duplicate starts", async () => {
  const source = await readFile(launcherPath, "utf8");

  assert.match(source, /http:\/\/127\.0\.0\.1:7863/);
  assert.match(source, /http:\/\/127\.0\.0\.1:7864/);
  assert.match(source, /http:\/\/localhost:3000/);
  assert.match(source, /Test-H3Studio/);
  assert.match(source, /Test-StorySidecar/);
  assert.match(source, /Test-StoryWebUi/);
  assert.match(source, /Assert-PortAvailableForStart/);
});

test("launcher recognizes the current product title after the NIKU rename", async () => {
  const source = await readFile(launcherPath, "utf8");

  assert.match(source, /NIKU STDUIO FOR WORK\|H3 Story Studio/);
});

test("launcher hides child processes, logs locally, and supports NoBrowser", async () => {
  const source = await readFile(launcherPath, "utf8");
  const cmd = await readFile(cmdPath, "utf8");

  assert.match(source, /work\\logs/);
  assert.equal((source.match(/-WindowStyle Hidden/g) ?? []).length, 3);
  assert.match(source, /\[switch\]\$NoBrowser/);
  assert.match(source, /if \(-not \$NoBrowser\)/);
  assert.match(source, /\$env:BROWSER = Join-Path \$env:SystemRoot "System32\\where\.exe"/);
  assert.match(cmd, /start-story-studio\.ps1/);
  assert.match(cmd, /%\*/);
});
