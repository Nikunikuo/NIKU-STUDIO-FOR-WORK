#!/usr/bin/env python3
"""Audit Project Core selected takes and optionally assemble a derived video.

The script is intentionally independent of the Sidecar HTTP client. Feed it the
JSON emitted by ``storyctl.py context`` and an exact project root. It refuses to
combine missing, duplicate, unselected, or path-escaping takes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context-file", required=True, help="storyctl context JSON, or '-' for stdin")
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--concat-output", type=Path, help="optional derived MP4 output")
    parser.add_argument("--ffmpeg", type=Path, help="ffmpeg executable; auto-detected when omitted")
    parser.add_argument("--ffprobe", type=Path, help="optional ffprobe executable")
    parser.add_argument("--width", type=int)
    parser.add_argument("--height", type=int)
    parser.add_argument("--expected-duration", type=float)
    parser.add_argument("--duration-tolerance", type=float, default=0.08)
    return parser.parse_args()


def read_json(path: str) -> dict[str, Any]:
    raw = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("context JSON must be an object")
    return value


def payload(entity: dict[str, Any]) -> dict[str, Any]:
    value = entity.get("payload")
    return value if isinstance(value, dict) else {}


def find_executable(explicit: Path | None, name: str) -> str | None:
    if explicit:
        resolved = explicit.expanduser().resolve()
        if not resolved.is_file():
            raise FileNotFoundError(f"{name} executable not found: {resolved}")
        return str(resolved)
    found = shutil.which(name)
    if found:
        return found
    if name == "ffmpeg":
        try:
            import imageio_ffmpeg  # type: ignore

            return imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            return None
    return None


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def probe_ffprobe(ffprobe: str, path: Path) -> dict[str, Any] | None:
    completed = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration:stream=codec_type,codec_name,width,height,nb_frames,sample_rate,channels", "-of", "json", str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        return None
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def probe_ffmpeg_text(ffmpeg: str, path: Path) -> dict[str, Any]:
    completed = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    text = completed.stderr
    duration_match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
    video_match = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", text)
    video_codec_match = re.search(r"Video:\s*([a-zA-Z0-9_]+)", text)
    audio_match = re.search(r"Audio:\s*([a-zA-Z0-9_]+).*?(\d+) Hz,\s*([^,\n]+)", text)
    return {
        "duration": (int(duration_match.group(1)) * 3600 + int(duration_match.group(2)) * 60 + float(duration_match.group(3))) if duration_match else None,
        "width": int(video_match.group(1)) if video_match else None,
        "height": int(video_match.group(2)) if video_match else None,
        "videoCodec": video_codec_match.group(1) if video_codec_match else None,
        "audioCodec": audio_match.group(1) if audio_match else None,
        "sampleRate": audio_match.group(2) if audio_match else None,
        "channels": audio_match.group(3).strip() if audio_match else None,
    }


def inspect_media(path: Path, ffmpeg: str | None, ffprobe: str | None) -> dict[str, Any]:
    if ffprobe:
        value = probe_ffprobe(ffprobe, path)
        if value is not None:
            streams = value.get("streams") if isinstance(value.get("streams"), list) else []
            video = next((item for item in streams if isinstance(item, dict) and item.get("codec_type") == "video"), {})
            audio = next((item for item in streams if isinstance(item, dict) and item.get("codec_type") == "audio"), {})
            duration_raw = (value.get("format") or {}).get("duration") if isinstance(value.get("format"), dict) else None
            return {
                "duration": float(duration_raw) if duration_raw is not None else None,
                "width": video.get("width"),
                "height": video.get("height"),
                "frames": video.get("nb_frames"),
                "videoCodec": video.get("codec_name"),
                "audioCodec": audio.get("codec_name"),
                "sampleRate": audio.get("sample_rate"),
                "channels": audio.get("channels"),
                "probe": "ffprobe",
            }
    if ffmpeg:
        return {**probe_ffmpeg_text(ffmpeg, path), "probe": "ffmpeg-text"}
    return {"probe": "unavailable"}


def safe_media_path(root: Path, relative: str) -> Path:
    if not relative or Path(relative).is_absolute():
        raise ValueError(f"artifact path must be project-relative: {relative!r}")
    candidate = (root / Path(relative.replace("/", os.sep))).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise ValueError(f"artifact escapes project root: {relative!r}") from error
    return candidate


def selected_manifest(context: dict[str, Any], root: Path) -> tuple[list[dict[str, Any]], list[str]]:
    raw_entities = context.get("entities")
    entities = [item for item in raw_entities if isinstance(item, dict)] if isinstance(raw_entities, list) else []
    shots = [item for item in entities if item.get("entityType") == "shot"]
    takes = [item for item in entities if item.get("entityType") == "take" and payload(item).get("selection") == "selected"]
    shot_order = {str(item.get("entityId")): int(payload(item).get("order", 0) or 0) for item in shots}
    shot_titles = {str(item.get("entityId")): str(payload(item).get("title", item.get("entityId"))) for item in shots}
    grouped: dict[str, list[dict[str, Any]]] = {}
    for take in takes:
        shot_id = str(payload(take).get("shotId", ""))
        grouped.setdefault(shot_id, []).append(take)

    errors: list[str] = []
    manifest: list[dict[str, Any]] = []
    for shot in sorted(shots, key=lambda item: (shot_order.get(str(item.get("entityId")), 0), str(item.get("entityId")))):
        shot_id = str(shot.get("entityId"))
        candidates = grouped.get(shot_id, [])
        if len(candidates) != 1:
            errors.append(f"shot {shot_id} has {len(candidates)} selected takes; expected exactly one")
            continue
        take = candidates[0]
        take_payload = payload(take)
        relative = str(take_payload.get("artifactRelativePath", ""))
        try:
            path = safe_media_path(root, relative)
        except ValueError as error:
            errors.append(f"{shot_id}: {error}")
            continue
        if not path.is_file():
            errors.append(f"{shot_id}: artifact does not exist: {path}")
            continue
        expected_hash = str(take_payload.get("artifactSha256", "")).lower()
        actual_hash = sha256(path)
        if expected_hash and expected_hash != actual_hash:
            errors.append(f"{shot_id}: SHA-256 mismatch for {path.name}")
        manifest.append({
            "shotId": shot_id,
            "shotTitle": shot_titles.get(shot_id, shot_id),
            "takeId": str(take.get("entityId")),
            "path": str(path),
            "relativePath": relative,
            "sha256": actual_hash,
        })

    return manifest, errors


def concat(ffmpeg: str, manifest: list[dict[str, Any]], output: Path) -> None:
    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".txt", delete=False, dir=output.parent) as handle:
        list_path = Path(handle.name)
        for item in manifest:
            escaped = item["path"].replace("'", "'\\''")
            handle.write(f"file '{escaped}'\n")
    try:
        completed = subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(list_path), "-c", "copy", str(output)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or "ffmpeg concat failed")
    finally:
        list_path.unlink(missing_ok=True)


def main() -> int:
    args = parse_args()
    root = args.project_root.expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"project root not found: {root}")
    context = read_json(args.context_file)
    manifest, errors = selected_manifest(context, root)
    ffmpeg = find_executable(args.ffmpeg, "ffmpeg")
    ffprobe = find_executable(args.ffprobe, "ffprobe")
    warnings: list[str] = []
    for item in manifest:
        item["media"] = inspect_media(Path(item["path"]), ffmpeg, ffprobe)
        media = item["media"]
        if media.get("probe") == "unavailable":
            warnings.append("ffprobe/ffmpeg unavailable; media stream metadata was not inspected")
        if args.width is not None and media.get("width") not in (None, args.width):
            errors.append(f"{item['shotId']}: width {media['width']} != {args.width}")
        if args.height is not None and media.get("height") not in (None, args.height):
            errors.append(f"{item['shotId']}: height {media['height']} != {args.height}")
        duration = media.get("duration")
        if args.expected_duration is not None and isinstance(duration, (int, float)) and abs(float(duration) - args.expected_duration) > args.duration_tolerance:
            errors.append(f"{item['shotId']}: duration {duration:.3f}s != {args.expected_duration:.3f}s")

    output_info: dict[str, Any] | None = None
    if args.concat_output:
        if errors:
            raise RuntimeError("refusing to concatenate an invalid selection: " + "; ".join(errors))
        if not ffmpeg:
            raise RuntimeError("ffmpeg is required for --concat-output")
        concat(ffmpeg, manifest, args.concat_output)
        output = args.concat_output.expanduser().resolve()
        output_info = {"path": str(output), "sha256": sha256(output), "bytes": output.stat().st_size}

    report = {
        "projectId": context.get("projectId"),
        "projectRevision": context.get("projectRevision", context.get("revision")),
        "stateDigest": context.get("stateDigest", context.get("digest")),
        "selectedCount": len(manifest),
        "selected": manifest,
        "errors": errors,
        "warnings": sorted(set(warnings)),
        "derivedOutput": output_info,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as error:
        print(f"audit_selected_takes: {error}", file=sys.stderr)
        raise SystemExit(2)
