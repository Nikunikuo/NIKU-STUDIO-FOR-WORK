"""One-shot local Qwen worker for the strict H3 community prompt planner.

The process reads one JSON object from stdin and writes one JSON object to
stdout.  It is intentionally one-shot: the caller can wait for this process to
exit before starting H3, ensuring Qwen releases its VRAM first.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from webui.community_prompt_planner import (
    MODEL_ID,
    MODEL_RELATIVE_PATH,
    MODEL_REVISION,
    CommunityPromptPlannerError,
    build_model_messages,
    compile_model_result,
    prepare_planner_input,
    require_verified_model_checkout,
)


LOGGER = logging.getLogger("h3-studio-community-prompt-worker")
DEFAULT_MAX_NEW_TOKENS = 1280
MIN_MAX_NEW_TOKENS = 768
MAX_MAX_NEW_TOKENS = 2048


class QwenCommunityPromptPlanner:
    """Offline greedy inference wrapper for Qwen3-4B-Instruct-2507."""

    def __init__(
        self,
        model_path: str | Path,
        *,
        device: str = "cuda",
        max_new_tokens: int = DEFAULT_MAX_NEW_TOKENS,
    ) -> None:
        load_started = time.perf_counter()
        import_started = load_started
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer
        except Exception as exc:  # pragma: no cover - integration-only dependency path
            raise CommunityPromptPlannerError(
                f"Unable to import the local Qwen runtime: {exc}",
                code="PLANNER_RUNTIME_IMPORT_FAILED",
            ) from exc
        import_finished = time.perf_counter()

        self.model_path = str(Path(model_path).resolve())
        self.device = device
        self.max_new_tokens = int(max_new_tokens)
        if not MIN_MAX_NEW_TOKENS <= self.max_new_tokens <= MAX_MAX_NEW_TOKENS:
            raise CommunityPromptPlannerError(
                f"max_new_tokens must be from {MIN_MAX_NEW_TOKENS} to "
                f"{MAX_MAX_NEW_TOKENS}.",
                code="INVALID_MAX_NEW_TOKENS",
            )
        self._torch = torch
        try:
            tokenizer_started = time.perf_counter()
            self._tokenizer = AutoTokenizer.from_pretrained(
                self.model_path,
                local_files_only=True,
                trust_remote_code=False,
            )
            tokenizer_finished = time.perf_counter()
            dtype = torch.bfloat16 if device.startswith("cuda") else torch.float32
            model_kwargs: dict[str, Any] = {
                "local_files_only": True,
                "trust_remote_code": False,
                "dtype": dtype,
                "low_cpu_mem_usage": True,
            }
            if device.startswith("cuda"):
                model_kwargs["device_map"] = {"": device}
            model_started = time.perf_counter()
            self._model = AutoModelForCausalLM.from_pretrained(
                self.model_path,
                **model_kwargs,
            )
            if not device.startswith("cuda"):
                self._model.to(device)
            self._model.eval()
            if device.startswith("cuda"):
                torch.cuda.synchronize()
            model_finished = time.perf_counter()
        except Exception as exc:  # pragma: no cover - exercised by real model smoke tests
            raise CommunityPromptPlannerError(
                f"Unable to load the local Qwen planner: {exc}",
                code="PLANNER_MODEL_LOAD_FAILED",
            ) from exc
        self.load_metadata = {
            "runtime_import_ms": round((import_finished - import_started) * 1000, 2),
            "tokenizer_load_ms": round(
                (tokenizer_finished - tokenizer_started) * 1000, 2
            ),
            "model_load_ms": round((model_finished - model_started) * 1000, 2),
            "total_load_ms": round((model_finished - load_started) * 1000, 2),
        }
        self.last_generation_metadata: dict[str, Any] | None = None

    def generate(self, messages: Sequence[Mapping[str, str]]) -> str:
        try:
            encode_started = time.perf_counter()
            template_kwargs: dict[str, Any] = {
                "tokenize": True,
                "add_generation_prompt": True,
                "return_tensors": "pt",
                "return_dict": True,
                "enable_thinking": False,
            }
            try:
                encoded = self._tokenizer.apply_chat_template(
                    list(messages), **template_kwargs
                )
            except TypeError:
                # Older tokenizer implementations may not expose the Qwen
                # enable_thinking switch. Instruct-2507 itself is non-thinking.
                template_kwargs.pop("enable_thinking", None)
                encoded = self._tokenizer.apply_chat_template(
                    list(messages), **template_kwargs
                )
            encoded = {
                key: value.to(self.device) if hasattr(value, "to") else value
                for key, value in encoded.items()
            }
            input_length = int(encoded["input_ids"].shape[-1])
            encode_finished = time.perf_counter()
            pad_token_id = self._tokenizer.pad_token_id
            if pad_token_id is None:
                pad_token_id = self._tokenizer.eos_token_id
            if self.device.startswith("cuda"):
                self._torch.cuda.synchronize()
            generation_started = time.perf_counter()
            with self._torch.inference_mode():
                generated = self._model.generate(
                    **encoded,
                    do_sample=False,
                    max_new_tokens=self.max_new_tokens,
                    pad_token_id=pad_token_id,
                    eos_token_id=self._tokenizer.eos_token_id,
                    use_cache=True,
                )
            if self.device.startswith("cuda"):
                self._torch.cuda.synchronize()
            generation_finished = time.perf_counter()
            new_tokens = generated[0, input_length:]
            output_tokens = int(new_tokens.shape[-1])
            eos_value = self._tokenizer.eos_token_id
            eos_ids = (
                {int(value) for value in eos_value}
                if isinstance(eos_value, (list, tuple, set))
                else ({int(eos_value)} if eos_value is not None else set())
            )
            last_token = int(new_tokens[-1].item()) if output_tokens else None
            eos_reached = last_token in eos_ids if last_token is not None else False
            content_tokens_before_eos = output_tokens - 1 if eos_reached else output_tokens
            decode_started = time.perf_counter()
            decoded = self._tokenizer.decode(new_tokens, skip_special_tokens=True).strip()
            decode_finished = time.perf_counter()
            generation_seconds = generation_finished - generation_started
            self.last_generation_metadata = {
                "input_tokens": input_length,
                "output_tokens_including_eos": output_tokens,
                "content_tokens_before_eos": content_tokens_before_eos,
                "eos_reached": eos_reached,
                "hit_max_new_tokens": output_tokens >= self.max_new_tokens and not eos_reached,
                "max_new_tokens": self.max_new_tokens,
                "prompt_encoding_ms": round((encode_finished - encode_started) * 1000, 2),
                "generation_ms": round(generation_seconds * 1000, 2),
                "decode_ms": round((decode_finished - decode_started) * 1000, 2),
                "content_tokens_per_second": (
                    round(content_tokens_before_eos / generation_seconds, 3)
                    if generation_seconds > 0
                    else None
                ),
            }
            return decoded
        except CommunityPromptPlannerError:
            raise
        except Exception as exc:  # pragma: no cover - exercised by real model smoke tests
            raise CommunityPromptPlannerError(
                f"Local Qwen prompt inference failed: {exc}",
                code="PLANNER_INFERENCE_FAILED",
            ) from exc


def _payload_dialogues(payload: Mapping[str, Any]) -> list[str]:
    value = payload.get("dialogue_texts", [])
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise CommunityPromptPlannerError(
            "dialogue_texts must be an array of strings.",
            code="INVALID_WORKER_REQUEST",
        )
    return list(value)


def _payload_references(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    value = payload.get("references", [])
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, Mapping) for item in value):
        raise CommunityPromptPlannerError(
            "references must be an array of objects.",
            code="INVALID_WORKER_REQUEST",
        )
    return list(value)


def _generate(planner: Any, messages: Sequence[Mapping[str, str]]) -> str:
    if hasattr(planner, "generate"):
        result = planner.generate(messages)
    elif callable(planner):
        result = planner(messages)
    else:
        raise CommunityPromptPlannerError(
            "Planner object must be callable or expose generate(messages).",
            code="INVALID_PLANNER_OBJECT",
        )
    if not isinstance(result, str):
        raise CommunityPromptPlannerError(
            "Planner inference must return text.", code="PLANNER_RESULT_TYPE_ERROR"
        )
    return result


def _parse_max_new_tokens(value: Any) -> int:
    if value is None:
        return DEFAULT_MAX_NEW_TOKENS
    if isinstance(value, bool):
        raise CommunityPromptPlannerError(
            "max_new_tokens must be an integer.", code="INVALID_WORKER_REQUEST"
        )
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise CommunityPromptPlannerError(
            "max_new_tokens must be an integer.", code="INVALID_WORKER_REQUEST"
        ) from exc
    if not MIN_MAX_NEW_TOKENS <= result <= MAX_MAX_NEW_TOKENS:
        raise CommunityPromptPlannerError(
            f"max_new_tokens must be from {MIN_MAX_NEW_TOKENS} to "
            f"{MAX_MAX_NEW_TOKENS}.",
            code="INVALID_WORKER_REQUEST",
        )
    return result


def process_request(
    payload: Mapping[str, Any],
    *,
    planner: Any | None = None,
    cli_model_path: str | None = None,
    cli_device: str | None = None,
    cli_max_new_tokens: int | None = None,
) -> dict[str, Any]:
    """Compile one request; injectable ``planner`` keeps unit tests model-free."""

    if not isinstance(payload, Mapping):
        raise CommunityPromptPlannerError(
            "Worker input must be a JSON object.", code="INVALID_WORKER_REQUEST"
        )
    prompt = payload.get("prompt")
    if not isinstance(prompt, str):
        raise CommunityPromptPlannerError(
            "prompt must be a string.", code="INVALID_WORKER_REQUEST"
        )
    expected_revision = str(payload.get("expected_revision") or MODEL_REVISION)
    if expected_revision != MODEL_REVISION:
        raise CommunityPromptPlannerError(
            f"Only the pinned planner revision {MODEL_REVISION} is allowed.",
            code="MODEL_REVISION_NOT_PINNED",
        )
    max_attempts_value = payload.get("max_attempts", 2)
    if isinstance(max_attempts_value, bool):
        raise CommunityPromptPlannerError(
            "max_attempts must be an integer from 1 to 3.",
            code="INVALID_WORKER_REQUEST",
        )
    try:
        max_attempts = int(max_attempts_value)
    except (TypeError, ValueError) as exc:
        raise CommunityPromptPlannerError(
            "max_attempts must be an integer from 1 to 3.",
            code="INVALID_WORKER_REQUEST",
        ) from exc
    if max_attempts < 1 or max_attempts > 3:
        raise CommunityPromptPlannerError(
            "max_attempts must be an integer from 1 to 3.",
            code="INVALID_WORKER_REQUEST",
        )
    max_new_tokens = _parse_max_new_tokens(
        cli_max_new_tokens
        if cli_max_new_tokens is not None
        else payload.get("max_new_tokens")
    )

    started = time.monotonic()
    prepared = prepare_planner_input(
        prompt,
        reference_inventory=_payload_references(payload),
        dialogue_texts=_payload_dialogues(payload),
        duration_seconds=payload.get("duration_seconds"),
        style_direction=str(payload.get("style_direction") or ""),
        soundscape=str(payload.get("soundscape") or ""),
        audio_preset=str(payload.get("audio_preset") or "auto"),
        music_policy=str(payload.get("music_policy") or "auto"),
    )
    model_path = Path(
        cli_model_path
        or str(payload.get("model_path") or "")
        or Path(__file__).resolve().parents[1] / MODEL_RELATIVE_PATH
    ).resolve()
    checkout_metadata: dict[str, Any]
    selected_planner = planner
    if selected_planner is None:
        checkout = require_verified_model_checkout(
            model_path, expected_revision=expected_revision
        )
        checkout_metadata = asdict(checkout)
        selected_planner = QwenCommunityPromptPlanner(
            model_path,
            device=cli_device or str(payload.get("device") or "cuda"),
            max_new_tokens=max_new_tokens,
        )
    else:
        # Tests and embedding callers may inject an already-loaded engine. The
        # production CLI never takes this path.
        checkout_metadata = {
            "model_id": MODEL_ID,
            "expected_revision": MODEL_REVISION,
            "detected_revision": None,
            "verified": False,
            "path": str(model_path),
            "injected_planner": True,
        }

    messages: list[dict[str, str]] = build_model_messages(prepared)
    last_error: CommunityPromptPlannerError | None = None
    compiled = None
    raw = ""
    attempts = 0
    generation_measurements: list[dict[str, Any]] = []
    for attempts in range(1, max_attempts + 1):
        raw = _generate(selected_planner, messages)
        measurement = getattr(selected_planner, "last_generation_metadata", None)
        if isinstance(measurement, Mapping):
            generation_measurements.append(
                {"attempt": attempts, **dict(measurement)}
            )
        try:
            compiled = compile_model_result(raw, prepared)
            break
        except CommunityPromptPlannerError as exc:
            last_error = exc
            if attempts >= max_attempts:
                break
            # The rejected result contains no literal dialogue because the
            # model never received it. Return only the machine-readable reason
            # and ask Qwen to regenerate the entire JSON object.
            messages.extend(
                [
                    {"role": "assistant", "content": raw},
                    {
                        "role": "user",
                        "content": (
                            f"REJECTED ({exc.code}): {exc}. Return a corrected complete "
                            "raw JSON object only. Keep all original constraints."
                        ),
                    },
                ]
            )
    if compiled is None:
        assert last_error is not None
        raise CommunityPromptPlannerError(
            f"Planner output remained invalid after {attempts} attempt(s): {last_error}",
            code=last_error.code,
        )

    metadata = compiled.metadata()
    metadata.update(
        {
            "checkout": checkout_metadata,
            "elapsed_ms": round((time.monotonic() - started) * 1000, 2),
            "attempts": attempts,
            "device": cli_device or str(payload.get("device") or "cuda"),
            "offline": True,
            "decoding": "greedy",
            "max_new_tokens": max_new_tokens,
            "runtime_timing": {
                "model_load": getattr(selected_planner, "load_metadata", None),
                "generation_attempts": generation_measurements,
                "measured_generation_ms_total": round(
                    sum(
                        float(item.get("generation_ms") or 0)
                        for item in generation_measurements
                    ),
                    2,
                ),
            },
        }
    )
    return {
        "ok": True,
        "compiled_prompt": compiled.prompt,
        "plan": compiled.plan.to_dict(),
        "planner_metadata": metadata,
        "diagnostics": [],
    }


def _error_response(exc: Exception) -> dict[str, Any]:
    code = (
        exc.code
        if isinstance(exc, CommunityPromptPlannerError)
        else "COMMUNITY_PROMPT_WORKER_FAILED"
    )
    return {
        "ok": False,
        "code": code,
        "error": str(exc),
        "diagnostics": [
            {
                "severity": "error",
                "code": code,
                "message": str(exc),
                "fatal": True,
            }
        ],
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-path", help="Local pinned Qwen3-4B-Instruct-2507 directory")
    parser.add_argument("--device", default="cuda", help="Inference device (default: cuda)")
    parser.add_argument(
        "--max-new-tokens",
        type=int,
        help=f"Generation cap ({MIN_MAX_NEW_TOKENS}-{MAX_MAX_NEW_TOKENS})",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    try:
        raw_request = sys.stdin.read()
        if not raw_request.strip():
            raise CommunityPromptPlannerError(
                "Worker stdin is empty.", code="INVALID_WORKER_REQUEST"
            )
        payload = json.loads(raw_request)
        response = process_request(
            payload,
            cli_model_path=args.model_path,
            cli_device=args.device,
            cli_max_new_tokens=args.max_new_tokens,
        )
        exit_code = 0
    except Exception as exc:
        LOGGER.error("Community prompt worker failed: %s: %s", exc.__class__.__name__, exc)
        response = _error_response(exc)
        exit_code = 1
    sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["QwenCommunityPromptPlanner", "main", "process_request"]
