from __future__ import annotations

import copy
import hashlib
import json
import os
import queue
import re
import subprocess
import threading
import time
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psutil

from .process_guard import ProcessJob


EVENT_PREFIX = "H3EVENT "
TERMINAL_STATES = {"completed", "failed", "cancelled", "interrupted"}
COMMUNITY_PLANNER_MODULE = "webui.community_prompt_worker"
COMMUNITY_PLANNER_TIMEOUT_SECONDS = 240
COMMUNITY_PLANNER_MODEL_ID = "Qwen/Qwen3-4B-Instruct-2507"
COMMUNITY_PLANNER_REVISION = "cdbee75f17c01a7cc42f958dc650907174af0554"
COMMUNITY_PLANNER_CONTRACT = "h3-community-plan-v1"
COMMUNITY_COMPILER_REVISION = "2026-08-05-native-clean-v3-camera-geometry"
COMMUNITY_CACHE_SCHEMA_VERSION = 1
COMMUNITY_PLANNER_LOCK = "prompt_planner.lock.json"
COMMUNITY_PLANNER_PROVENANCE_FILE = "h3-studio-provenance.json"
COMMUNITY_PLANNER_PROVENANCE_SCHEMA_VERSION = 1
COMMUNITY_PLANNER_MODEL_DIR = (
    Path("models") / "prompt_planner" / "Qwen3-4B-Instruct-2507"
)
COMMUNITY_PLANNER_REQUIRED_FILES = {
    "config.json": 727,
    "generation_config.json": 238,
    "LICENSE": 11_343,
    "model.safetensors.index.json": 32_819,
    "model-00001-of-00003.safetensors": 3_957_900_840,
    "model-00002-of-00003.safetensors": 3_987_450_520,
    "model-00003-of-00003.safetensors": 99_630_640,
    "tokenizer.json": 11_422_654,
    "tokenizer_config.json": 9_377,
}
_CJK_TEXT_RE = re.compile(
    "["
    "\u3040-\u30ff"  # Hiragana and Katakana.
    "\u31f0-\u31ff"  # Katakana phonetic extensions.
    "\u3400-\u4dbf"  # CJK Unified Ideographs Extension A.
    "\u4e00-\u9fff"  # CJK Unified Ideographs.
    "\uf900-\ufaff"  # CJK Compatibility Ideographs.
    "\uff66-\uff9f"  # Half-width Katakana.
    "]"
)
_ALL_REFERENCE_TAG_RE = re.compile(
    r"<(?P<kind>Picture|Video|Audio) (?P<index>[1-9][0-9]*)>",
)
_NONCANONICAL_CONTROL_TOKEN_RE = re.compile(
    r"<\s*(?:/?[A-Za-z]|\||<|>)[^>]*(?:>|$)"
)
_WINDOWS_ABSOLUTE_PATH_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?:[A-Z]:[\\/]|\\\\)[^\r\n]*"
)
_ORDINARY_QUOTED_TEXT_RE = re.compile(r'"[^"\r\n]+"|“[^”\r\n]+”')
_NATIVE_DIALOGUE_TAG_RE = re.compile(r"</?d(?:\s[^>]*)?>", re.IGNORECASE)
PUBLIC_ENGINE_DIAGNOSTIC_FIELDS = frozenset(
    {
        "comfyui_commit",
        "model_revision",
        "variant",
        "async_offload_streams",
        "attention_backend",
        "workflow_profile",
    }
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _redact_public_text(value: str) -> str:
    """Remove local absolute paths from browser-visible messages/logs."""

    return _WINDOWS_ABSOLUTE_PATH_RE.sub("[local path redacted]", value)


def community_planner_status(root: Path) -> dict[str, Any]:
    """Return a cheap readiness check for the pinned text-only Qwen planner.

    The nine runtime files are intentionally checked by byte size here so the
    frequently-polled capabilities endpoint remains cheap.  Authenticity and
    revision provenance are bound to the repository lock by a small marker
    written only after the normal setup command hashes all nine files.  Do not
    rely on Hugging Face's private ``.cache`` layout: users and cache cleaners
    may legitimately remove it after setup.
    """

    resolved_root = root.resolve()
    model_root = (resolved_root / COMMUNITY_PLANNER_MODEL_DIR).resolve()
    expected_parent = (resolved_root / "models" / "prompt_planner").resolve()
    public: dict[str, Any] = {
        "ready": False,
        "status": "model_incomplete",
        "model": COMMUNITY_PLANNER_MODEL_ID,
        "repo_id": COMMUNITY_PLANNER_MODEL_ID,
        "revision": COMMUNITY_PLANNER_REVISION,
        "local_only": True,
        "model_inference": True,
        "total_bytes": sum(COMMUNITY_PLANNER_REQUIRED_FILES.values()),
        "missing_files": [],
        "invalid_files": [],
    }
    try:
        model_root.relative_to(expected_parent)
    except ValueError:
        public["status"] = "model_path_invalid"
        return public
    missing: list[str] = []
    invalid: list[str] = []
    for name, size in COMMUNITY_PLANNER_REQUIRED_FILES.items():
        candidate = model_root / name
        if not candidate.is_file():
            missing.append(name)
            continue
        try:
            if candidate.stat().st_size != size:
                invalid.append(name)
        except OSError:
            missing.append(name)
    public["missing_files"] = missing
    public["invalid_files"] = invalid
    if invalid:
        public["status"] = "model_invalid"
    elif missing:
        public["status"] = "model_incomplete"
    else:
        lock_path = resolved_root / COMMUNITY_PLANNER_LOCK
        if not lock_path.is_file():
            public["status"] = "lock_missing"
            return public
        try:
            lock_sha256 = hashlib.sha256(lock_path.read_bytes()).hexdigest()
        except OSError:
            public["status"] = "lock_invalid"
            return public

        provenance_path = model_root / COMMUNITY_PLANNER_PROVENANCE_FILE
        if not provenance_path.is_file():
            public["status"] = "provenance_missing"
            return public
        try:
            provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            public["status"] = "provenance_invalid"
            return public

        expected_provenance = {
            "schema_version": COMMUNITY_PLANNER_PROVENANCE_SCHEMA_VERSION,
            "model_id": COMMUNITY_PLANNER_MODEL_ID,
            "revision": COMMUNITY_PLANNER_REVISION,
            "lock_sha256": lock_sha256,
            "file_count": len(COMMUNITY_PLANNER_REQUIRED_FILES),
            "total_bytes": sum(COMMUNITY_PLANNER_REQUIRED_FILES.values()),
        }
        provenance_valid = (
            isinstance(provenance, Mapping)
            and set(provenance) == set(expected_provenance)
            and type(provenance.get("schema_version")) is int
            and type(provenance.get("file_count")) is int
            and type(provenance.get("total_bytes")) is int
            and all(provenance.get(key) == value for key, value in expected_provenance.items())
        )
        if not provenance_valid:
            public["status"] = "provenance_invalid"
            return public
        public.update(ready=True, status="ready")
    return public


def _reference_manifest(references: list[Any]) -> list[dict[str, Any]]:
    """Expose only stable reference ordinals; never send upload paths/names."""

    ordinals = {"image": 0, "video": 0, "audio": 0}
    tag_kind = {"image": "Picture", "video": "Video", "audio": "Audio"}
    manifest: list[dict[str, Any]] = []
    for source_index, reference in enumerate(references):
        if not isinstance(reference, Mapping):
            continue
        kind = str(reference.get("kind") or "").casefold()
        if kind not in ordinals:
            continue
        ordinals[kind] += 1
        item: dict[str, Any] = {
            "kind": kind,
            "index": ordinals[kind],
            "source_index": source_index,
            "tag": f"<{tag_kind[kind]} {ordinals[kind]}>",
        }
        if kind == "video" and isinstance(reference.get("has_audio"), bool):
            item["has_audio"] = reference["has_audio"]
        manifest.append(item)
    return manifest


def _validate_reference_tags(
    prompt: str,
    *,
    mode: str,
    references: list[Any],
) -> list[dict[str, Any]]:
    """Read-only allowlist check for reference labels in a current prompt."""

    scrubbed = _ALL_REFERENCE_TAG_RE.sub("", prompt)
    scrubbed = scrubbed.replace("<d>", "").replace("</d>", "")
    if _NONCANONICAL_CONTROL_TOKEN_RE.search(scrubbed):
        return [
            {
                "severity": "error",
                "code": "PROMPT_INVALID_CONTROL_TAG",
                "message": (
                    "H3制御タグの綴り・大文字小文字・空白が公式形式ではないか、"
                    "未対応のangle tagが含まれています。"
                ),
                "fatal": True,
            }
        ]

    observed = {
        (match.group("kind").casefold(), int(match.group("index")))
        for match in _ALL_REFERENCE_TAG_RE.finditer(prompt)
    }
    if mode == "i2v":
        allowed: set[tuple[str, int]] = {("picture", 1)}
    elif mode == "first_last":
        allowed = {("picture", 1), ("picture", 2)}
    elif mode == "omni":
        allowed = {
            (str(item["kind"]).casefold(), int(item["index"]))
            for item in _reference_manifest(references)
        }
        # Manifest kind names are upload kinds while H3 uses Picture labels.
        allowed = {
            ({"image": "picture", "video": "video", "audio": "audio"}[kind], index)
            for kind, index in allowed
        }
    else:
        allowed = set()

    if observed <= allowed:
        return []
    return [
        {
            "severity": "error",
            "code": "PROMPT_REFERENCE_OUT_OF_RANGE",
            "message": "プロンプトに、添付素材または生成モードへ対応しない参照タグがあります。",
            "fatal": True,
        }
    ]


def _validate_native_clean_prompt(
    prompt: str,
    *,
    mode: str,
    references: list[Any],
) -> list[dict[str, Any]]:
    """Validate the public Comfy prompt contract without rewriting a byte."""

    diagnostics: list[dict[str, Any]] = []

    def fail(code: str, message: str) -> None:
        diagnostics.append(
            {
                "severity": "error",
                "code": code,
                "message": message,
                "fatal": True,
            }
        )

    if not isinstance(prompt, str) or not prompt.strip():
        fail("NATIVE_CLEAN_EMPTY_PROMPT", "H3へ送る英語プロンプトが空です。")
        return diagnostics
    if _NATIVE_DIALOGUE_TAG_RE.search(prompt):
        fail(
            "NATIVE_CLEAN_DIALOGUE_TAG",
            "公開Comfy互換経路では<d>を使いません。実台詞は普通の二重引用符で囲んでください。",
        )
    if "<|" in prompt or "|>" in prompt:
        fail(
            "NATIVE_CLEAN_RESERVED_TOKEN",
            "公開Comfy互換経路へ予約tokenを入力できません。",
        )
    outside_quotes = _ORDINARY_QUOTED_TEXT_RE.sub("", prompt)
    if _CJK_TEXT_RE.search(outside_quotes):
        fail(
            "NATIVE_CLEAN_CJK_CONTROL_PROSE",
            "H3へ送る映像・カメラ・音響の制御文に日本語が残っています。"
            "日本語は普通の二重引用符内の実台詞だけにしてください。",
        )
    diagnostics.extend(
        _validate_reference_tags(
            prompt,
            mode=mode,
            references=references,
        )
    )
    return diagnostics


def _validated_community_cache_response(
    envelope: Any,
    *,
    cache_key: str,
    mode: str,
    references: list[Any],
) -> Mapping[str, Any] | None:
    """Return a fully validated planner response from one versioned cache envelope.

    Cache files are optimization hints, not trusted compiler output.  Old,
    partial, manually edited, or previously rejected entries are cache misses.
    """

    if not isinstance(envelope, Mapping):
        return None
    if envelope.get("schema_version") != COMMUNITY_CACHE_SCHEMA_VERSION:
        return None
    if envelope.get("cache_key") != cache_key:
        return None
    if envelope.get("compiler_revision") != COMMUNITY_COMPILER_REVISION:
        return None
    if envelope.get("planner_contract") != COMMUNITY_PLANNER_CONTRACT:
        return None
    if envelope.get("planner_model") != COMMUNITY_PLANNER_MODEL_ID:
        return None
    if envelope.get("planner_revision") != COMMUNITY_PLANNER_REVISION:
        return None

    response = envelope.get("response")
    if not isinstance(response, Mapping) or response.get("ok") is not True:
        return None
    compiled_prompt = response.get("compiled_prompt")
    if not isinstance(compiled_prompt, str) or not compiled_prompt.strip():
        return None
    if envelope.get("compiled_sha256") != hashlib.sha256(
        compiled_prompt.encode("utf-8")
    ).hexdigest():
        return None
    if not isinstance(response.get("plan"), Mapping):
        return None
    metadata = response.get("planner_metadata")
    if not isinstance(metadata, Mapping):
        return None
    if metadata.get("contract") != COMMUNITY_PLANNER_CONTRACT:
        return None
    if metadata.get("model_id") != COMMUNITY_PLANNER_MODEL_ID:
        return None
    if metadata.get("model_revision") != COMMUNITY_PLANNER_REVISION:
        return None
    diagnostics = response.get("diagnostics")
    if not isinstance(diagnostics, list):
        return None
    if any(
        isinstance(item, Mapping)
        and (
            bool(item.get("fatal"))
            or str(item.get("severity") or "").casefold() in {"error", "fatal"}
        )
        for item in diagnostics
    ):
        return None
    if _validate_native_clean_prompt(
        compiled_prompt,
        mode=mode,
        references=references,
    ):
        return None
    return response


class JobManager:
    """Runs one GPU generation job at a time and persists its visible state."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.data_dir = self.root / "webui_data"
        self.jobs_dir = self.data_dir / "jobs"
        self.outputs_dir = self.root / "outputs" / "webui"
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.outputs_dir.mkdir(parents=True, exist_ok=True)

        self._jobs: dict[str, dict[str, Any]] = {}
        self._queue: queue.Queue[str] = queue.Queue()
        self._lock = threading.RLock()
        self._runner: threading.Thread | None = None
        self._stop = threading.Event()
        self._current_job_id: str | None = None
        self._current_process: subprocess.Popen[str] | None = None
        self._current_process_job: ProcessJob | None = None
        self._engine_variant: str | None = None
        self._current_prompt_process: subprocess.Popen[str] | None = None
        self._current_prompt_process_job: ProcessJob | None = None
        self._current_prompt_job_id: str | None = None
        self._load_existing_jobs()

    def _load_existing_jobs(self) -> None:
        for metadata_path in self.jobs_dir.glob("*/job.json"):
            try:
                job = json.loads(metadata_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if job.get("status") not in TERMINAL_STATES:
                job.update(
                    status="interrupted",
                    phase="停止しました",
                    message="Web UIの前回終了により処理が中断されました。もう一度生成してください。",
                    finished_at=utc_now(),
                    progress_updated_at=utc_now(),
                )
                self._save_job(job)
            self._jobs[job["id"]] = job

    def start(self) -> None:
        with self._lock:
            if self._runner and self._runner.is_alive():
                return
            self._stop.clear()
            self._runner = threading.Thread(target=self._run_loop, name="h3-job-runner", daemon=True)
            self._runner.start()

    def stop(self) -> None:
        self._stop.set()
        self._queue.put("")
        with self._lock:
            owned_prompt = self._claim_current_prompt_locked()
            owned_engine = self._claim_current_process_locked()
        if owned_prompt != (None, None):
            self._cleanup_owned_process(*owned_prompt)
        if owned_engine != (None, None):
            self._cleanup_owned_process(*owned_engine)

    def submit(self, job: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._jobs[job["id"]] = job
            self._save_job(job)
            self._queue.put(job["id"])
            return copy.deepcopy(job)

    def list_jobs(self) -> list[dict[str, Any]]:
        with self._lock:
            jobs = [copy.deepcopy(job) for job in self._jobs.values()]
        return sorted(jobs, key=lambda item: item.get("created_at", ""), reverse=True)

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return copy.deepcopy(job) if job else None

    def current_job_id(self) -> str | None:
        with self._lock:
            return self._current_job_id

    def cancel(self, job_id: str) -> dict[str, Any] | None:
        owned_process: tuple[subprocess.Popen[str] | None, ProcessJob | None] = (None, None)
        owned_prompt: tuple[subprocess.Popen[str] | None, ProcessJob | None] = (
            None,
            None,
        )
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or job.get("status") in TERMINAL_STATES:
                return copy.deepcopy(job) if job else None
            job.update(
                status="cancelled",
                phase="キャンセルしました",
                message="生成をキャンセルしました。モデルや重みは変更されていません。",
                finished_at=utc_now(),
                progress_updated_at=utc_now(),
            )
            self._save_job(job)
            is_current = self._current_job_id == job_id
            if is_current:
                # Claim this job's exact engine/prompt worker in the same critical
                # section as the job-id comparison. A completed old cancel must
                # never kill work installed for the next queued job.
                owned_process = self._claim_current_process_locked()
                owned_prompt = self._claim_current_prompt_locked(job_id=job_id)
        if owned_prompt != (None, None):
            self._cleanup_owned_process(*owned_prompt)
        if owned_process != (None, None):
            self._cleanup_owned_process(*owned_process)
        return self.get_job(job_id)

    def _run_loop(self) -> None:
        while not self._stop.is_set():
            job_id = self._queue.get()
            if not job_id or self._stop.is_set():
                continue
            with self._lock:
                job = self._jobs.get(job_id)
                if not job or job.get("status") == "cancelled":
                    continue
                self._current_job_id = job_id
                job.update(
                    status="running",
                    phase="生成を開始しています",
                    message="ローカル生成エンジンを起動しています。",
                    progress=1,
                    started_at=utc_now(),
                    progress_updated_at=utc_now(),
                )
                self._save_job(job)

            request_path = self.jobs_dir / job_id / "request.json"
            try:
                execution_request_path = self._prepare_effective_prompt(job_id, request_path)
                if execution_request_path is None:
                    continue
                with self._lock:
                    current = self._jobs.get(job_id)
                    if not current or current.get("status") == "cancelled":
                        continue
                requested_variant = "ref2va" if job.get("mode") == "omni" else "fl2va"
                process = self._ensure_engine(requested_variant)
                assert process.stdin is not None and process.stdout is not None
                process.stdin.write(
                    json.dumps(
                        {"request": os.fspath(execution_request_path)},
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                process.stdin.flush()
                terminal_status = None
                while not self._stop.is_set():
                    raw_line = process.stdout.readline()
                    if not raw_line:
                        return_code = process.poll()
                        raise RuntimeError(f"生成エンジンが終了コード {return_code} で停止しました。")
                    line = raw_line.rstrip("\r\n")
                    if line.startswith(EVENT_PREFIX):
                        payload = line[len(EVENT_PREFIX) :]
                        self._handle_event(job_id, payload)
                        try:
                            event = json.loads(payload)
                            if event.get("status") in TERMINAL_STATES:
                                terminal_status = event["status"]
                                break
                        except json.JSONDecodeError:
                            pass
                    elif line.strip():
                        self._append_log(job_id, line.strip())
                if terminal_status != "completed":
                    self._terminate_current_process()
            except Exception as exc:
                with self._lock:
                    current = self._jobs.get(job_id)
                    if current and current.get("status") not in TERMINAL_STATES:
                        current.update(
                            status="failed",
                            phase="生成に失敗しました",
                            message=f"生成エンジンを起動できませんでした: {exc}",
                            finished_at=utc_now(),
                            progress_updated_at=utc_now(),
                        )
                        self._save_job(current)
                self._terminate_current_process()
            finally:
                owned_process: tuple[subprocess.Popen[str] | None, ProcessJob | None] = (None, None)
                with self._lock:
                    if self._current_process and self._current_process.poll() is not None:
                        owned_process = self._claim_current_process_locked()
                    self._current_job_id = None
                self._cleanup_owned_process(*owned_process)

    def _update_prompt_progress(
        self,
        job_id: str,
        *,
        progress: float,
        phase: str,
        message: str,
    ) -> bool:
        """Publish a monotonic pre-generation update without reviving a job."""

        with self._lock:
            current = self._jobs.get(job_id)
            if not current or current.get("status") in TERMINAL_STATES:
                return False
            previous = float(current.get("progress") or 0)
            current.update(
                phase=phase,
                message=message,
                progress=max(previous, progress),
                progress_updated_at=utc_now(),
            )
            self._save_job(current)
            return True

    @staticmethod
    def _worker_json(stdout: str) -> Mapping[str, Any]:
        payload = stdout.strip()
        if not payload:
            raise ValueError("prompt worker returned no JSON")
        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError as whole_error:
            # The contract is one JSON document on stdout, but accepting a
            # final compact JSON line makes the boundary robust to a dependency
            # that writes a harmless startup notice before the result.
            decoded = None
            for line in reversed(payload.splitlines()):
                try:
                    decoded = json.loads(line)
                except json.JSONDecodeError:
                    continue
                break
            if decoded is None:
                raise ValueError("prompt worker returned malformed JSON") from whole_error
        if not isinstance(decoded, Mapping):
            raise ValueError("prompt worker JSON must be an object")
        return decoded

    @staticmethod
    def _public_worker_metadata(value: Any, *, depth: int = 0) -> Any:
        """Keep useful provenance while excluding local paths and debug dumps."""

        if depth > 3:
            return None
        if isinstance(value, str):
            return _redact_public_text(value)
        if value is None or isinstance(value, (int, float, bool)):
            return value
        if isinstance(value, Mapping):
            public: dict[str, Any] = {}
            for raw_key, raw_value in value.items():
                key = str(raw_key)
                folded = key.casefold()
                if any(
                    token in folded
                    for token in ("path", "stdout", "stderr", "traceback", "exception")
                ):
                    continue
                converted = JobManager._public_worker_metadata(raw_value, depth=depth + 1)
                if converted is not None:
                    public[key] = converted
            return public
        if isinstance(value, (list, tuple)):
            return [
                converted
                for item in value
                if (converted := JobManager._public_worker_metadata(item, depth=depth + 1))
                is not None
            ]
        return str(value)

    def _write_prompt_artifacts(
        self,
        job_dir: Path,
        *,
        original_prompt: str,
        source_prompt: str,
        final_prompt: str | None,
        report: Mapping[str, Any],
    ) -> None:
        artifact_dir = job_dir / "prompt_processing"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        (artifact_dir / "original_prompt.txt").write_text(original_prompt, encoding="utf-8")
        (artifact_dir / "source_prompt.txt").write_text(source_prompt, encoding="utf-8")
        final_path = artifact_dir / "final_prompt.txt"
        if final_prompt is None:
            final_path.unlink(missing_ok=True)
        else:
            final_path.write_text(final_prompt, encoding="utf-8")
        self._write_json_atomic(artifact_dir / "report.json", report)

    def _fail_prompt_processing(
        self,
        job_id: str,
        request: Mapping[str, Any],
        job_dir: Path,
        source_prompt: str,
        processing: Mapping[str, Any],
        *,
        code: str,
        detail: str,
        started: float,
        extra_diagnostics: list[Any] | None = None,
        component: Mapping[str, Any] | None = None,
        model_inference: bool = True,
    ) -> None:
        """Persist an auditable failure and guarantee no stale execution copy."""

        execution_path = job_dir / "execution_request.json"
        execution_path.unlink(missing_ok=True)
        private_dir = job_dir / "prompt_processing"
        private_dir.mkdir(parents=True, exist_ok=True)
        self._write_json_atomic(
            private_dir / "failure.private.json",
            {
                "code": code,
                "detail": detail,
                "diagnostics": list(extra_diagnostics or []),
                "component": dict(component or {}),
            },
        )
        public_detail = _redact_public_text(detail)
        diagnostics = list(processing.get("diagnostics") or [])
        public_extra = self._public_worker_metadata(extra_diagnostics or [])
        if isinstance(public_extra, list):
            diagnostics.extend(public_extra)
        diagnostics.append(
            {
                "severity": "error",
                "code": code,
                "message": public_detail,
                "fatal": True,
            }
        )
        model_status = dict(component) if isinstance(component, Mapping) else {}
        report = dict(processing)
        report.update(
            status="prompt_processing_failed",
            local_only=True,
            model_inference=model_inference,
            model_repo=model_status.get("repo_id"),
            model_revision=model_status.get("revision"),
            source_sha256=hashlib.sha256(source_prompt.encode("utf-8")).hexdigest(),
            request_source_sha256=hashlib.sha256(
                str(request.get("prompt") or "").encode("utf-8")
            ).hexdigest(),
            output_sha256=None,
            elapsed_ms=round((time.monotonic() - started) * 1000, 2),
            error_code=code,
            diagnostics=diagnostics,
            artifacts=[
                "prompt_processing/original_prompt.txt",
                "prompt_processing/source_prompt.txt",
                "prompt_processing/report.json",
                "prompt_processing/failure.private.json",
            ],
        )
        if component:
            report["component"] = self._public_worker_metadata(component)
        self._write_prompt_artifacts(
            job_dir,
            original_prompt=str(request.get("prompt") or ""),
            source_prompt=source_prompt,
            final_prompt=None,
            report=report,
        )
        public_message = (
            "プロンプトを現行のH3生成形式へ準備できなかったため、"
            f"生成を開始しませんでした（{code}）。"
        )
        with self._lock:
            current = self._jobs.get(job_id)
            if current and current.get("status") not in TERMINAL_STATES:
                current.update(
                    status="failed",
                    phase="プロンプト処理に失敗しました",
                    message=public_message,
                    compiler=report,
                    prompt_processing=report,
                    auto_adjustments=report.get("auto_adjustments", []),
                    diagnostics=diagnostics,
                    finished_at=utc_now(),
                    progress_updated_at=utc_now(),
                )
                self._save_job(current)
        self._append_log(
            job_id,
            f"Prompt processing blocked generation: {code}: {public_detail}",
        )

    def _run_tracked_prompt_worker(
        self,
        job_id: str,
        command: list[str],
        input_text: str,
        env: Mapping[str, str],
        *,
        timeout: float,
    ) -> tuple[int, str, str] | None:
        """Run one cancellable prompt worker process owned by ``job_id``.

        ``Popen.communicate`` runs in a short-lived reader thread so stdout and
        stderr are drained without deadlocking while the runner remains able to
        observe cancellation, manager shutdown, and the wall-clock timeout.
        Ownership is installed and claimed under ``_lock``; a late cancel for
        an old job can therefore never terminate a worker belonging to the
        next queued job.
        """

        creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        process_job = ProcessJob()
        process: subprocess.Popen[str] | None = None
        try:
            process = subprocess.Popen(
                command,
                cwd=self.root,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=dict(env),
                creationflags=creation_flags,
            )
            process_job.attach(process)
        except Exception:
            process_job.terminate()
            self._cleanup_owned_process(process, None)
            raise

        installed = False
        with self._lock:
            current = self._jobs.get(job_id)
            eligible = (
                not self._stop.is_set()
                and current is not None
                and current.get("status") not in TERMINAL_STATES
                and self._current_job_id == job_id
            )
            if eligible and self._current_prompt_process is None:
                self._current_prompt_process = process
                self._current_prompt_process_job = process_job
                self._current_prompt_job_id = job_id
                installed = True
        if not installed:
            self._cleanup_owned_process(process, process_job)
            return None

        communication: dict[str, Any] = {}
        communication_done = threading.Event()

        def communicate() -> None:
            try:
                stdout, stderr = process.communicate(input=input_text)
                communication["stdout"] = stdout or ""
                communication["stderr"] = stderr or ""
            except Exception as exc:  # pragma: no cover - platform pipe failures vary
                communication["error"] = exc
            finally:
                communication_done.set()

        reader = threading.Thread(
            target=communicate,
            name=f"h3-prompt-worker-{job_id}",
            daemon=True,
        )
        reader.start()
        deadline = time.monotonic() + timeout
        while not communication_done.wait(0.05):
            with self._lock:
                current = self._jobs.get(job_id)
                still_owned = (
                    self._current_prompt_process is process
                    and self._current_prompt_job_id == job_id
                )
                abort_requested = (
                    self._stop.is_set()
                    or not current
                    or current.get("status") in TERMINAL_STATES
                    or self._current_job_id != job_id
                    or not still_owned
                )
            if abort_requested:
                with self._lock:
                    owned = self._claim_current_prompt_locked(
                        job_id=job_id,
                        process=process,
                    )
                self._cleanup_owned_process(*owned)
                reader.join(timeout=6)
                return None
            if time.monotonic() >= deadline:
                with self._lock:
                    owned = self._claim_current_prompt_locked(
                        job_id=job_id,
                        process=process,
                    )
                self._cleanup_owned_process(*owned)
                reader.join(timeout=6)
                with self._lock:
                    current = self._jobs.get(job_id)
                    cancelled = (
                        self._stop.is_set()
                        or not current
                        or current.get("status") in TERMINAL_STATES
                        or self._current_job_id != job_id
                    )
                if cancelled:
                    return None
                raise subprocess.TimeoutExpired(command, timeout)

        with self._lock:
            owned = self._claim_current_prompt_locked(
                job_id=job_id,
                process=process,
            )
            current = self._jobs.get(job_id)
            cancelled = (
                self._stop.is_set()
                or not current
                or current.get("status") in TERMINAL_STATES
                or self._current_job_id != job_id
            )
        if owned[0] is None:
            # cancel()/stop() already owns and reaps this exact process.
            reader.join(timeout=6)
            return None
        try:
            if cancelled:
                return None
            error = communication.get("error")
            if error is not None:
                raise OSError(
                    "ローカルプロンプトワーカーとの入出力に失敗しました: "
                    f"{error.__class__.__name__}"
                ) from error
            return_code = process.returncode
            if return_code is None:
                return_code = process.poll()
            return (
                int(return_code if return_code is not None else -1),
                str(communication.get("stdout") or ""),
                str(communication.get("stderr") or ""),
            )
        finally:
            self._cleanup_owned_process(*owned)

    def _plan_community_prompt(
        self,
        job_id: str,
        request: Mapping[str, Any],
        job_dir: Path,
        source_prompt: str,
        processing: Mapping[str, Any],
        *,
        started: float,
    ) -> tuple[str, dict[str, Any]] | None:
        """Run the text-only Qwen planner, then enforce the native-clean contract."""

        planner_status = community_planner_status(self.root)
        python = self.root / ".comfy-venv" / "Scripts" / "python.exe"
        if not python.is_file():
            self._fail_prompt_processing(
                job_id,
                request,
                job_dir,
                source_prompt,
                processing,
                code="PLANNER_RUNTIME_MISSING",
                detail="ローカルQwenプロンプト用の専用Python環境が見つかりません。",
                started=started,
                component=planner_status,
                model_inference=True,
            )
            return None

        raw_references = list(request.get("references") or [])
        payload: dict[str, Any] = {
            "prompt": str(request.get("prompt") or source_prompt),
            "references": _reference_manifest(raw_references),
            "dialogue_texts": [
                str(request.get("dialogue") or "")
            ] if str(request.get("dialogue") or "").strip() else [],
            "duration_seconds": float(request.get("num_frames") or 0) / 24.0,
            "mode": str(request.get("mode") or "t2v"),
            "style_direction": str(request.get("style_direction") or ""),
            "soundscape": str(request.get("soundscape") or ""),
            "audio_preset": str(request.get("audio_preset") or "auto"),
            "music_policy": str(request.get("music_policy") or "auto"),
            "expected_revision": COMMUNITY_PLANNER_REVISION,
            "max_attempts": 2,
        }
        cache_payload = dict(payload)
        cache_payload.update(
            planner_model=COMMUNITY_PLANNER_MODEL_ID,
            planner_revision=COMMUNITY_PLANNER_REVISION,
            planner_contract=COMMUNITY_PLANNER_CONTRACT,
            compiler_revision=COMMUNITY_COMPILER_REVISION,
        )
        cache_key = hashlib.sha256(
            json.dumps(
                cache_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        cache_dir = self.data_dir / "prompt_cache"
        cache_path = cache_dir / f"{cache_key}.json"
        response: Mapping[str, Any] | None = None
        cache_hit = False
        if cache_path.is_file():
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                validated_cached = _validated_community_cache_response(
                    cached,
                    cache_key=cache_key,
                    mode=str(request.get("mode") or "t2v"),
                    references=raw_references,
                )
                if validated_cached is not None:
                    response = validated_cached
                    cache_hit = True
            except (OSError, json.JSONDecodeError):
                response = None

        worker_elapsed_ms = 0.0
        private_dir = job_dir / "prompt_processing"
        private_dir.mkdir(parents=True, exist_ok=True)
        if response is None:
            if not planner_status["ready"]:
                self._fail_prompt_processing(
                    job_id,
                    request,
                    job_dir,
                    source_prompt,
                    processing,
                    code="PLANNER_MODEL_NOT_READY",
                    detail=(
                        "固定revisionのローカルQwenプロンプトモデルが未配置、"
                        "不完全、またはサイズ不一致です。"
                    ),
                    started=started,
                    component=planner_status,
                    model_inference=True,
                )
                return None
            if not self._update_prompt_progress(
                job_id,
                progress=1.25,
                phase="プロンプトを整理しています",
                message="日本語の意図を公開成功例と同じ英語Storyboardへ変換しています。",
            ):
                return None
            payload["model_path"] = os.fspath(
                (self.root / COMMUNITY_PLANNER_MODEL_DIR).resolve()
            )
            env = os.environ.copy()
            env.update(
                PYTHONIOENCODING="utf-8",
                PYTHONUNBUFFERED="1",
                HF_HUB_OFFLINE="1",
                TRANSFORMERS_OFFLINE="1",
                TOKENIZERS_PARALLELISM="false",
            )
            worker_started = time.monotonic()
            try:
                process_result = self._run_tracked_prompt_worker(
                    job_id,
                    [os.fspath(python), "-m", COMMUNITY_PLANNER_MODULE],
                    json.dumps(payload, ensure_ascii=False),
                    env,
                    timeout=COMMUNITY_PLANNER_TIMEOUT_SECONDS,
                )
            except subprocess.TimeoutExpired:
                self._fail_prompt_processing(
                    job_id,
                    request,
                    job_dir,
                    source_prompt,
                    processing,
                    code="PLANNER_TIMEOUT",
                    detail=(
                        f"ローカルQwenプロンプト整理が"
                        f"{COMMUNITY_PLANNER_TIMEOUT_SECONDS}秒以内に完了しませんでした。"
                    ),
                    started=started,
                    component=planner_status,
                    model_inference=True,
                )
                return None
            except OSError as exc:
                self._fail_prompt_processing(
                    job_id,
                    request,
                    job_dir,
                    source_prompt,
                    processing,
                    code="PLANNER_PROCESS_FAILED",
                    detail=f"ローカルQwenプロンプトプロセスを開始できませんでした（{exc.__class__.__name__}）。",
                    started=started,
                    component=planner_status,
                    model_inference=True,
                )
                return None
            if process_result is None:
                return None
            worker_elapsed_ms = round((time.monotonic() - worker_started) * 1000, 2)
            return_code, worker_stdout, worker_stderr = process_result
            if worker_stderr:
                (private_dir / "planner.stderr.log").write_text(
                    worker_stderr,
                    encoding="utf-8",
                )
            try:
                response = self._worker_json(worker_stdout)
            except ValueError as exc:
                if worker_stdout:
                    (private_dir / "planner.stdout.log").write_text(
                        worker_stdout,
                        encoding="utf-8",
                    )
                self._fail_prompt_processing(
                    job_id,
                    request,
                    job_dir,
                    source_prompt,
                    processing,
                    code="PLANNER_RESPONSE_INVALID",
                    detail=str(exc),
                    started=started,
                    component={**planner_status, "worker_elapsed_ms": worker_elapsed_ms},
                    model_inference=True,
                )
                return None
            if return_code != 0 or response.get("ok") is not True:
                self._fail_prompt_processing(
                    job_id,
                    request,
                    job_dir,
                    source_prompt,
                    processing,
                    code=str(response.get("code") or "PLANNER_PROCESS_FAILED"),
                    detail=str(response.get("error") or "Qwenが有効なH3プロンプトを作成できませんでした。"),
                    started=started,
                    extra_diagnostics=list(response.get("diagnostics") or []),
                    component={**planner_status, "worker_elapsed_ms": worker_elapsed_ms},
                    model_inference=True,
                )
                return None
        assert response is not None
        self._write_json_atomic(private_dir / "planner_response.json", response)
        compiled_prompt = response.get("compiled_prompt")
        if not isinstance(compiled_prompt, str) or not compiled_prompt.strip():
            self._fail_prompt_processing(
                job_id,
                request,
                job_dir,
                source_prompt,
                processing,
                code="PLANNER_COMPILED_PROMPT_MISSING",
                detail="Qwen plannerが完全なcompiled_promptを返しませんでした。",
                started=started,
                component=planner_status,
                model_inference=True,
            )
            return None
        if not self._update_prompt_progress(
            job_id,
            progress=1.65,
            phase="プロンプトを検証しています",
            message="日本語台詞・参照番号・英語制御文を検証しています。",
        ):
            return None
        validation = _validate_native_clean_prompt(
            compiled_prompt,
            mode=str(request.get("mode") or "t2v"),
            references=raw_references,
        )
        worker_diagnostics = list(response.get("diagnostics") or [])
        if validation or any(
            isinstance(item, Mapping)
            and (bool(item.get("fatal")) or str(item.get("severity") or "").casefold() in {"error", "fatal"})
            for item in worker_diagnostics
        ):
            self._fail_prompt_processing(
                job_id,
                request,
                job_dir,
                source_prompt,
                processing,
                code="PLANNER_VALIDATION_FAILED",
                detail="公開Comfy用プロンプトの安全検証に失敗しました。",
                started=started,
                extra_diagnostics=[*worker_diagnostics, *validation],
                component=planner_status,
                model_inference=True,
            )
            return None

        if not cache_hit:
            cache_dir.mkdir(parents=True, exist_ok=True)
            self._write_json_atomic(
                cache_path,
                {
                    "schema_version": COMMUNITY_CACHE_SCHEMA_VERSION,
                    "cache_key": cache_key,
                    "compiler_revision": COMMUNITY_COMPILER_REVISION,
                    "planner_contract": COMMUNITY_PLANNER_CONTRACT,
                    "planner_model": COMMUNITY_PLANNER_MODEL_ID,
                    "planner_revision": COMMUNITY_PLANNER_REVISION,
                    "compiled_sha256": hashlib.sha256(
                        compiled_prompt.encode("utf-8")
                    ).hexdigest(),
                    "response": response,
                },
            )

        planner_metadata = response.get("planner_metadata")
        public_metadata = self._public_worker_metadata(
            planner_metadata if isinstance(planner_metadata, Mapping) else {}
        )
        diagnostics = list(processing.get("diagnostics") or [])
        public_worker_diagnostics = self._public_worker_metadata(worker_diagnostics)
        if isinstance(public_worker_diagnostics, list):
            diagnostics.extend(public_worker_diagnostics)
        adjustments = list(processing.get("auto_adjustments") or [])
        adjustments.append(
            {
                "code": "COMMUNITY_PROMPT_CACHE_REUSED" if cache_hit else "COMMUNITY_PROMPT_PLANNED",
                "message": (
                    "同一入力の検証済み英語Storyboardを再利用しました。"
                    if cache_hit
                    else "日本語の意図を公開成功例準拠の英語Storyboardへ整理しました。"
                ),
            }
        )
        processing_public = dict(processing)
        processing_public.update(
            status="community_planned",
            mode="community_planned",
            translation_required=True,
            translation_status="cache_hit" if cache_hit else "completed",
            local_only=True,
            model_inference=not cache_hit,
            model_repo=COMMUNITY_PLANNER_MODEL_ID,
            model_revision=COMMUNITY_PLANNER_REVISION,
            workflow_profile="native_clean",
            prompt_cache_key=cache_key,
            prompt_cache_hit=cache_hit,
            source_sha256=hashlib.sha256(source_prompt.encode("utf-8")).hexdigest(),
            request_source_sha256=hashlib.sha256(
                str(request.get("prompt") or "").encode("utf-8")
            ).hexdigest(),
            output_sha256=hashlib.sha256(compiled_prompt.encode("utf-8")).hexdigest(),
            elapsed_ms=round((time.monotonic() - started) * 1000, 2),
            worker_elapsed_ms=worker_elapsed_ms,
            compiler_metadata=public_metadata,
            auto_adjustments=adjustments,
            diagnostics=diagnostics,
            artifacts=[
                "prompt_processing/original_prompt.txt",
                "prompt_processing/source_prompt.txt",
                "prompt_processing/final_prompt.txt",
                "prompt_processing/report.json",
                "prompt_processing/planner_response.json",
            ],
            provenance={
                "compiler": "h3-studio-community-prompt-planner",
                "contract": COMMUNITY_PLANNER_CONTRACT,
                "compiler_revision": COMMUNITY_COMPILER_REVISION,
                "model_repo": COMMUNITY_PLANNER_MODEL_ID,
                "model_revision": COMMUNITY_PLANNER_REVISION,
                "local_only": True,
                "model_inference": not cache_hit,
            },
        )
        self._write_prompt_artifacts(
            job_dir,
            original_prompt=str(request.get("prompt") or ""),
            source_prompt=source_prompt,
            final_prompt=compiled_prompt,
            report=processing_public,
        )
        if not self._update_prompt_progress(
            job_id,
            progress=1.9,
            phase="生成を準備しています",
            message="公開Comfy用プロンプトの変換と検証が完了しました。",
        ):
            return None
        return compiled_prompt, processing_public

    def _finalize_prompt_execution(
        self,
        job_id: str,
        request_path: Path,
        request: Mapping[str, Any],
        *,
        effective_prompt: str,
        compiler_public: Mapping[str, Any],
        compiler_source: str,
    ) -> Path | None:
        """Persist one derived execution request without mutating request.json."""

        execution_request = dict(request)
        execution_request["effective_prompt"] = effective_prompt
        execution_request["effective_prompt_source"] = compiler_source
        execution_request["compiler"] = dict(compiler_public)
        execution_request["prompt_processing"] = dict(compiler_public)
        execution_request["workflow_profile"] = "native_clean"
        execution_request["embedded_video_audio_policy"] = str(
            compiler_public.get("embedded_video_audio_policy") or "ignore"
        )
        execution_request["embedded_video_audio_indices"] = list(
            compiler_public.get("embedded_video_audio_indices") or []
        )
        execution_request_path = request_path.parent / "execution_request.json"
        self._write_json_atomic(execution_request_path, execution_request)
        with self._lock:
            current = self._jobs.get(job_id)
            if not current or current.get("status") == "cancelled":
                execution_request_path.unlink(missing_ok=True)
                return None
            current.update(
                effective_prompt=effective_prompt,
                effective_prompt_source=compiler_source,
                workflow_profile="native_clean",
                compiler=dict(compiler_public),
                prompt_processing=dict(compiler_public),
                auto_adjustments=list(compiler_public.get("auto_adjustments", [])),
                diagnostics=list(compiler_public.get("diagnostics", [])),
            )
            self._save_job(current)
        return execution_request_path

    def _prepare_effective_prompt(self, job_id: str, request_path: Path) -> Path | None:
        """Prepare a current NIKU H STUDIO request for native-clean execution.

        Public requests have exactly two prompt paths: community runs the
        pinned local Qwen planner, while raw_en validates and preserves an
        English H3 prompt byte-for-byte. Removed legacy modes fail closed.
        """

        request = json.loads(request_path.read_text(encoding="utf-8"))
        job_dir = request_path.parent
        started = time.monotonic()
        effective_prompt = str(
            request.get("effective_prompt") or request.get("prompt") or ""
        )
        processing = request.get("prompt_processing")
        processing_public = (
            dict(processing) if isinstance(processing, Mapping) else {}
        )
        requested_mode = str(
            request.get("prompt_processing_mode")
            or processing_public.get("processing_mode_requested")
            or ""
        ).casefold()
        legacy_requested = (
            requested_mode in {"direct", "official_en", "context_ir"}
            or str(processing_public.get("mode") or "").casefold() == "context_ir"
        )

        if legacy_requested:
            self._fail_prompt_processing(
                job_id,
                request,
                job_dir,
                effective_prompt,
                processing_public,
                code="LEGACY_PROMPT_MODE_REMOVED",
                detail=(
                    "このjobが指定する旧プロンプト実装は現行NIKU H STUDIOから撤去されています。"
                    "communityまたはraw_enで新しく生成してください。"
                ),
                started=started,
                model_inference=False,
            )
            return None
        if requested_mode not in {"community", "raw_en"}:
            self._fail_prompt_processing(
                job_id,
                request,
                job_dir,
                effective_prompt,
                processing_public,
                code="INVALID_PROMPT_PROCESSING_MODE",
                detail="プロンプト処理方式はcommunityまたはraw_enで指定してください。",
                started=started,
                model_inference=False,
            )
            return None
        if not effective_prompt.strip():
            self._fail_prompt_processing(
                job_id,
                request,
                job_dir,
                effective_prompt,
                processing_public,
                code="PROMPT_EMPTY",
                detail="H3へ送るプロンプトが空です。",
                started=started,
                model_inference=False,
            )
            return None

        mode = str(request.get("mode") or "t2v").casefold()
        processing_public.update(
            processing_mode_requested=requested_mode,
            processing_mode_effective=requested_mode,
            workflow_profile="native_clean",
        )
        if requested_mode == "community":
            planned = self._plan_community_prompt(
                job_id,
                request,
                job_dir,
                effective_prompt,
                processing_public,
                started=started,
            )
            if planned is None:
                return None
            compiled_prompt, compiler_public = planned
            return self._finalize_prompt_execution(
                job_id,
                request_path,
                request,
                effective_prompt=compiled_prompt,
                compiler_public=compiler_public,
                compiler_source="community_planned",
            )

        native_diagnostics = _validate_native_clean_prompt(
            effective_prompt,
            mode=mode,
            references=list(request.get("references") or []),
        )
        if native_diagnostics:
            first = native_diagnostics[0]
            self._fail_prompt_processing(
                job_id,
                request,
                job_dir,
                effective_prompt,
                processing_public,
                code=str(
                    first.get("code") or "NATIVE_CLEAN_VALIDATION_FAILED"
                ),
                detail=str(
                    first.get("message")
                    or "英語H3プロンプトの検証に失敗しました。"
                ),
                started=started,
                extra_diagnostics=native_diagnostics[1:],
                model_inference=False,
            )
            return None

        elapsed_ms = round((time.monotonic() - started) * 1000, 2)
        processing_public.update(
            status="native_raw",
            mode="raw_en",
            translation_required=False,
            translation_status="not_required",
            local_only=True,
            model_inference=False,
            workflow_profile="native_clean",
            source_sha256=hashlib.sha256(
                effective_prompt.encode("utf-8")
            ).hexdigest(),
            request_source_sha256=hashlib.sha256(
                str(request.get("prompt") or "").encode("utf-8")
            ).hexdigest(),
            output_sha256=hashlib.sha256(
                effective_prompt.encode("utf-8")
            ).hexdigest(),
            elapsed_ms=elapsed_ms,
            artifacts=[
                "prompt_processing/original_prompt.txt",
                "prompt_processing/source_prompt.txt",
                "prompt_processing/final_prompt.txt",
                "prompt_processing/report.json",
            ],
            provenance={
                "compiler": "public-comfy-raw-prompt",
                "local_only": True,
                "model_inference": False,
            },
        )
        self._write_prompt_artifacts(
            job_dir,
            original_prompt=str(request.get("prompt") or ""),
            source_prompt=effective_prompt,
            final_prompt=effective_prompt,
            report=processing_public,
        )
        if not self._update_prompt_progress(
            job_id,
            progress=1.9,
            phase="生成を準備しています",
            message="英語H3プロンプトの検証が完了しました。",
        ):
            return None
        return self._finalize_prompt_execution(
            job_id,
            request_path,
            request,
            effective_prompt=effective_prompt,
            compiler_public=processing_public,
            compiler_source="native_raw",
        )

    @staticmethod
    def _write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)

    def _ensure_engine(self, requested_variant: str) -> subprocess.Popen[str]:
        with self._lock:
            process = self._current_process
            if process and process.poll() is None and self._engine_variant == requested_variant:
                if self._current_process_job is not None:
                    self._current_process_job.check()
                return process
            if process and process.poll() is not None:
                # The kernel Job may still own a redirector child or helper
                # even though the root Popen has already exited.
                self._terminate_current_process()
                process = None
            if process and process.poll() is None:
                # A fresh process is intentional here. PyTorch's CPU allocator
                # can retain tens of GiB after deleting a 33B model, so an
                # in-process FL2VA/Ref2VA swap risks paging both checkpoints.
                self._terminate_current_process()
                process = None
            if process:
                if process.stdin:
                    process.stdin.close()
                if process.stdout:
                    process.stdout.close()
            python = self.root / ".comfy-venv" / "Scripts" / "python.exe"
            if not python.is_file():
                raise FileNotFoundError(f"ComfyUI用Python環境が見つかりません: {python}")
            command = [os.fspath(python), "-m", "webui.comfy_engine_worker", "--serve"]
            env = os.environ.copy()
            env.update(PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
            creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            process_job = ProcessJob()
            process: subprocess.Popen[str] | None = None
            try:
                process = subprocess.Popen(
                    command,
                    cwd=self.root,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    env=env,
                    creationflags=creation_flags,
                )
                process_job.attach(process)
            except Exception:
                process_job.terminate()
                self._cleanup_owned_process(process, None)
                raise
            self._current_process = process
            self._current_process_job = process_job
            self._engine_variant = requested_variant
            return process

    def _handle_event(self, job_id: str, payload: str) -> None:
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            self._append_log(job_id, payload)
            return
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or job.get("status") == "cancelled":
                return
            for key in (
                "status",
                "phase",
                "message",
                "progress",
                "step",
                "total_steps",
                "result",
                "preview",
                "backend",
                "workflow_profile",
                "attention_backend",
                "acceleration",
                "media",
                "scheduler",
            ):
                if key in event:
                    job[key] = event[key]
            for key in ("cache", "timings"):
                if key not in event:
                    continue
                value = event[key]
                current = job.get(key)
                if isinstance(current, Mapping) and isinstance(value, Mapping):
                    job[key] = {**current, **value}
                else:
                    job[key] = value
            engine_diagnostics = event.get("diagnostics")
            if isinstance(engine_diagnostics, Mapping):
                # Keep local filesystem paths and process identifiers in the
                # on-disk Comfy log, not in the browser-facing job object.
                safe_diagnostics = {
                    key: value
                    for key, value in engine_diagnostics.items()
                    if key in PUBLIC_ENGINE_DIAGNOSTIC_FIELDS
                }
                technical = job.get("technical")
                if not isinstance(technical, Mapping):
                    technical = {}
                job["technical"] = {**technical, "engine": safe_diagnostics}
            if event.get("status") in TERMINAL_STATES:
                job["finished_at"] = utc_now()
            if any(key in event for key in ("progress", "phase", "message")):
                job["progress_updated_at"] = utc_now()
            self._save_job(job)

    def _append_log(self, job_id: str, line: str) -> None:
        public_line = _redact_public_text(line)
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            logs = job.setdefault("logs", [])
            logs.append({"time": utc_now(), "text": public_line[-2000:]})
            del logs[:-160]
            self._save_job(job)

    def _save_job(self, job: dict[str, Any]) -> None:
        job_dir = self.jobs_dir / job["id"]
        job_dir.mkdir(parents=True, exist_ok=True)
        target = job_dir / "job.json"
        temporary = job_dir / "job.json.tmp"
        temporary.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(target)

    def _claim_current_process_locked(
        self,
    ) -> tuple[subprocess.Popen[str] | None, ProcessJob | None]:
        """Atomically detach the current engine; caller must hold `_lock`."""

        process = self._current_process
        process_job = self._current_process_job
        self._current_process = None
        self._current_process_job = None
        self._engine_variant = None
        return process, process_job

    def _claim_current_prompt_locked(
        self,
        *,
        job_id: str | None = None,
        process: subprocess.Popen[str] | None = None,
    ) -> tuple[subprocess.Popen[str] | None, ProcessJob | None]:
        """Atomically detach one exact prompt worker; caller must hold ``_lock``."""

        current = self._current_prompt_process
        if job_id is not None and self._current_prompt_job_id != job_id:
            return None, None
        if process is not None and current is not process:
            return None, None
        process_job = self._current_prompt_process_job
        self._current_prompt_process = None
        self._current_prompt_process_job = None
        self._current_prompt_job_id = None
        return current, process_job

    def _terminate_current_process(self) -> None:
        with self._lock:
            owned_process = self._claim_current_process_locked()
        self._cleanup_owned_process(*owned_process)

    @staticmethod
    def _cleanup_owned_process(
        process: subprocess.Popen[str] | None,
        process_job: ProcessJob | None,
    ) -> None:
        if not process and process_job is None:
            return
        try:
            if process is not None:
                try:
                    parent = psutil.Process(process.pid)
                    children = parent.children(recursive=True)
                    for child in reversed(children):
                        child.terminate()
                    parent.terminate()
                    _, alive = psutil.wait_procs([*children, parent], timeout=5)
                    for remaining in alive:
                        remaining.kill()
                except (psutil.Error, OSError):
                    try:
                        process.terminate()
                    except OSError:
                        pass
        finally:
            # The kernel Job is authoritative even if the root Popen exited
            # before psutil could enumerate an orphaned redirector/helper.
            if process_job is not None:
                process_job.terminate()
            if process is not None:
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    try:
                        process.kill()
                        process.wait(timeout=2)
                    except (OSError, subprocess.SubprocessError):
                        pass
                except (OSError, subprocess.SubprocessError):
                    pass
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream is not None:
                        try:
                            stream.close()
                        except OSError:
                            pass
