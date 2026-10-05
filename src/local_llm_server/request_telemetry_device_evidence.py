"""Representative-device evidence for per-request resource telemetry.

This module is intentionally a loopback-only client. It never serializes prompts,
assistant output, PIDs or private paths. It can capture a telemetry candidate,
capture a pre-telemetry latency baseline, and compare the two observationally.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import platform
from pathlib import Path
import re
import statistics
import subprocess
import time
from typing import Any, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
import uuid

from .resource_telemetry import REQUEST_EVIDENCE_VERSION

_PASS = "PASS"
_FAIL = "FAIL"
_OBSERVATIONAL = "OBSERVATIONAL"
_EXPECTED_MEMORY_SOURCE = "ps_process_tree_rss_excluding_sampler"
_EXPECTED_CPU_SOURCE = "ps_process_tree_cpu_time_delta_excluding_sampler"
_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")


class EvidenceError(RuntimeError):
    """Bounded runner failure that must not expose private machine paths."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, payload: Mapping[str, Any]) -> Path:
    target = path.expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return target


def _loopback_root(base_url: str) -> str:
    parsed = urlparse(base_url.rstrip("/"))
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise EvidenceError("RTE-1 accepts HTTP loopback URLs only")
    return base_url.rstrip("/")


def _request_json(
    url: str,
    *,
    payload: Mapping[str, Any] | None = None,
    timeout: float = 600.0,
) -> tuple[int, Mapping[str, Any], float]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST" if data is not None else "GET",
    )
    started = time.perf_counter()
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - loopback only
            status = int(getattr(response, "status", 200))
            raw = response.read()
    except HTTPError as exc:
        status = int(exc.code)
        raw = exc.read()
    except (URLError, TimeoutError, OSError) as exc:
        raise EvidenceError(f"loopback request failed: {type(exc).__name__}") from exc
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    try:
        body = json.loads(raw.decode("utf-8")) if raw else {}
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidenceError(f"loopback response was not JSON (status {status})") from exc
    if not isinstance(body, Mapping):
        raise EvidenceError(f"loopback response was not an object (status {status})")
    return status, body, elapsed_ms


def _chat_payload(
    model: str,
    prompt: str,
    *,
    max_tokens: int = 16,
    stream: bool = False,
) -> dict[str, Any]:
    return {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": max_tokens,
        "stream": stream,
    }


def _sanitize_completion(
    status: int,
    body: Mapping[str, Any],
    elapsed_ms: float,
) -> dict[str, Any]:
    usage = body.get("usage")
    usage = dict(usage) if isinstance(usage, Mapping) else None
    evidence = body.get("korgis")
    evidence = dict(evidence) if isinstance(evidence, Mapping) else None
    choices = body.get("choices")
    has_content = False
    if isinstance(choices, list) and choices and isinstance(choices[0], Mapping):
        message = choices[0].get("message")
        has_content = isinstance(message, Mapping) and isinstance(message.get("content"), str)
    return {
        "http_status": status,
        "wall_clock_ms": round(elapsed_ms, 3),
        "model": body.get("model"),
        "usage": usage,
        "has_assistant_content": has_content,
        "korgis": evidence,
        "assistant_content_retained": False,
    }


def _completion(
    root: str,
    model: str,
    prompt: str,
    *,
    max_tokens: int,
    timeout: float,
) -> dict[str, Any]:
    status, body, elapsed_ms = _request_json(
        f"{root}/v1/chat/completions",
        payload=_chat_payload(model, prompt, max_tokens=max_tokens),
        timeout=timeout,
    )
    return _sanitize_completion(status, body, elapsed_ms)


def _mac_hardware_profile() -> dict[str, Any]:
    profile: dict[str, Any] = {
        "system": platform.system(),
        "machine": platform.machine(),
        "macos_version": platform.mac_ver()[0] or None,
        "hardware_model": None,
        "chip": None,
        "memory_bytes": None,
    }
    if profile["system"] != "Darwin":
        return profile
    for key, field, cast in (
        ("hw.model", "hardware_model", str),
        ("machdep.cpu.brand_string", "chip", str),
        ("hw.memsize", "memory_bytes", int),
    ):
        try:
            completed = subprocess.run(
                ["sysctl", "-n", key],
                capture_output=True,
                text=True,
                timeout=3,
                check=True,
            )
            value = completed.stdout.strip()
            profile[field] = cast(value) if value else None
        except (OSError, ValueError, subprocess.SubprocessError):
            profile[field] = None
    return profile


def _runtime_idle(status: Mapping[str, Any], model: str) -> bool:
    models = status.get("models")
    candidate: Mapping[str, Any] = status
    if isinstance(models, Mapping):
        selected = models.get(model)
        if isinstance(selected, Mapping):
            candidate = selected
    active = candidate.get("active_requests")
    phase = str(candidate.get("phase") or "").lower()
    state = str(candidate.get("state") or "").lower()
    active_ok = active in {None, 0}
    phase_ok = phase in {"", "idle", "ready"}
    state_ok = state not in {"running", "generating", "loading"}
    return active_ok and phase_ok and state_ok


def _wait_until_idle(
    root: str,
    model: str,
    timeout: float = 15.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    latest: Mapping[str, Any] = {}
    while time.monotonic() < deadline:
        status, latest, _ = _request_json(f"{root}/status", timeout=3)
        if status == 200 and _runtime_idle(latest, model):
            return {"idle": True, "status": _sanitize_status(latest, model)}
        time.sleep(0.25)
    return {"idle": False, "status": _sanitize_status(latest, model)}


def _sanitize_status(status: Mapping[str, Any], model: str) -> dict[str, Any]:
    models = status.get("models")
    candidate: Mapping[str, Any] = status
    if isinstance(models, Mapping):
        selected = models.get(model)
        if isinstance(selected, Mapping):
            candidate = selected
    return {
        "state": candidate.get("state"),
        "phase": candidate.get("phase"),
        "active_requests": candidate.get("active_requests"),
        "output_chunks": candidate.get("output_chunks"),
    }


def _stream_probe(
    root: str,
    model: str,
    prompt: str,
    *,
    cancel_after_first_frame: bool,
    timeout: float,
) -> dict[str, Any]:
    request = Request(
        f"{root}/v1/chat/completions",
        data=json.dumps(
            _chat_payload(model, prompt, max_tokens=64, stream=True)
        ).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    frames = 0
    done = False
    started = time.perf_counter()
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - loopback only
            http_status = int(getattr(response, "status", 200))
            for raw in response:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                frames += 1
                if line == "data: [DONE]":
                    done = True
                    break
                if cancel_after_first_frame:
                    break
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise EvidenceError(f"stream probe failed: {type(exc).__name__}") from exc
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    idle = _wait_until_idle(root, model)
    evidence_status, evidence, _ = _request_json(
        f"{root}/api/v1/evidence",
        timeout=3,
    )
    runtime_items = evidence.get("runtimes") if isinstance(evidence, Mapping) else None
    latest_resource = None
    if isinstance(runtime_items, list):
        for item in runtime_items:
            if not isinstance(item, Mapping):
                continue
            runtime = item.get("runtime")
            runtime = runtime if isinstance(runtime, Mapping) else {}
            if runtime.get("key") == model or runtime.get("model_id") == model:
                latest_resource = item.get("request_resources")
                break
    return {
        "http_status": http_status,
        "frames_observed": frames,
        "done_observed": done,
        "cancelled_by_client": cancel_after_first_frame,
        "wall_clock_ms": round(elapsed_ms, 3),
        "runtime_idle_after_close": bool(idle["idle"]),
        "status_after_close": idle["status"],
        "latest_resource_snapshot_present": (
            evidence_status == 200 and isinstance(latest_resource, Mapping)
        ),
        "stream_content_retained": False,
    }


def _identity_summary(payload: Mapping[str, Any], model: str) -> dict[str, Any]:
    models = payload.get("models")
    selected = models.get(model) if isinstance(models, Mapping) else None
    selected = selected if isinstance(selected, Mapping) else {}
    runtime = selected.get("runtime")
    runtime = runtime if isinstance(runtime, Mapping) else {}
    model_identity = selected.get("model")
    model_identity = model_identity if isinstance(model_identity, Mapping) else {}
    return {
        "protocol_version": payload.get("protocol_version"),
        "model": model,
        "runtime_fingerprint": runtime.get("fingerprint"),
        "model_id": model_identity.get("id"),
        "backend": runtime.get("name"),
        "runtime_version": runtime.get("version"),
        "config_digest": runtime.get("config_digest"),
        "identity_content_retained": False,
    }


def _resource_snapshot_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    observation = payload.get("observation")
    observation = observation if isinstance(observation, Mapping) else {}
    runtimes = observation.get("runtimes")
    return {
        "policy_state": payload.get("policy_state"),
        "observation_schema_version": observation.get("schema_version"),
        "platform": observation.get("platform"),
        "runtime_count": len(runtimes) if isinstance(runtimes, list) else None,
    }


def _request_errors(
    item: Mapping[str, Any],
    *,
    expected_source: str,
    concurrent: bool = False,
) -> list[str]:
    errors: list[str] = []
    if item.get("http_status") != 200:
        errors.append("http_status_not_200")
    if item.get("assistant_content_retained") is not False:
        errors.append("assistant_content_retention_not_disabled")
    evidence = item.get("korgis")
    if not isinstance(evidence, Mapping):
        errors.append("missing_korgis_evidence")
        return errors
    if evidence.get("evidence_version") != REQUEST_EVIDENCE_VERSION:
        errors.append("unexpected_evidence_version")
    if evidence.get("execution_source") != expected_source:
        errors.append("unexpected_execution_source")
    resources = evidence.get("resources")
    if expected_source == "cache":
        if resources is not None:
            errors.append("cache_has_fresh_resource_evidence")
        return errors
    if not isinstance(resources, Mapping):
        errors.append("missing_resource_evidence")
        return errors

    memory = resources.get("memory")
    memory = memory if isinstance(memory, Mapping) else {}
    baseline = memory.get("baseline_bytes")
    peak = memory.get("peak_bytes")
    end = memory.get("end_bytes")
    delta = memory.get("peak_delta_bytes")
    values = [baseline, peak, end, delta]
    if any(
        value is not None
        and (not isinstance(value, int) or isinstance(value, bool) or value < 0)
        for value in values
    ):
        errors.append("invalid_memory_value")
    if isinstance(baseline, int) and isinstance(peak, int) and peak < baseline:
        errors.append("peak_memory_below_baseline")

    cpu = resources.get("cpu")
    cpu = cpu if isinstance(cpu, Mapping) else {}
    avg_cpu = cpu.get("average_percent")
    peak_cpu = cpu.get("peak_percent")
    sampling = resources.get("sampling")
    sampling = sampling if isinstance(sampling, Mapping) else {}
    cpu_observation_ms = sampling.get("cpu_observation_ms")
    if (avg_cpu is not None or peak_cpu is not None) and not (
        isinstance(cpu_observation_ms, (int, float))
        and not isinstance(cpu_observation_ms, bool)
        and cpu_observation_ms > 0
    ):
        errors.append("cpu_missing_positive_observation_window")

    sources = resources.get("sources")
    sources = sources if isinstance(sources, Mapping) else {}
    if (
        any(value is not None for value in (baseline, peak, end, delta))
        and sources.get("memory") != _EXPECTED_MEMORY_SOURCE
    ):
        errors.append("unexpected_memory_source")
    if (
        (avg_cpu is not None or peak_cpu is not None)
        and sources.get("cpu") != _EXPECTED_CPU_SOURCE
    ):
        errors.append("unexpected_cpu_source")

    attribution = resources.get("attribution")
    attribution = attribution if isinstance(attribution, Mapping) else {}
    if attribution.get("scope") != "korgis_process_tree":
        errors.append("unexpected_attribution_scope")
    if concurrent and attribution.get("quality") != "process_global":
        errors.append("concurrent_request_not_process_global")
    return errors


def validate_candidate_summary(summary: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    hardware = summary.get("hardware")
    if not isinstance(hardware, Mapping) or hardware.get("system") != "Darwin":
        errors.append("representative_device_is_not_macos")
    if (
        summary.get("source_commit") is None
        or not _FULL_SHA.fullmatch(str(summary.get("source_commit")))
    ):
        errors.append("invalid_source_commit")

    workloads = summary.get("workloads")
    if not isinstance(workloads, Mapping):
        return errors + ["missing_workloads"]
    for group in ("sequential_short", "sequential_long"):
        items = workloads.get(group)
        if not isinstance(items, list) or not items:
            errors.append(f"missing_{group}")
            continue
        for item in items:
            errors.extend(
                f"{group}:{error}"
                for error in _request_errors(item, expected_source="inference")
            )

    cache = workloads.get("cache")
    if not isinstance(cache, Mapping):
        errors.append("missing_cache_probe")
    else:
        first = cache.get("first")
        second = cache.get("second")
        if not isinstance(first, Mapping) or not isinstance(second, Mapping):
            errors.append("invalid_cache_probe")
        else:
            errors.extend(
                f"cache_first:{error}"
                for error in _request_errors(first, expected_source="inference")
            )
            errors.extend(
                f"cache_second:{error}"
                for error in _request_errors(second, expected_source="cache")
            )

    concurrent = workloads.get("concurrent")
    if not isinstance(concurrent, list) or len(concurrent) != 2:
        errors.append("invalid_concurrent_probe")
    else:
        for item in concurrent:
            errors.extend(
                f"concurrent:{error}"
                for error in _request_errors(
                    item,
                    expected_source="inference",
                    concurrent=True,
                )
            )

    streaming = workloads.get("streaming")
    if not isinstance(streaming, Mapping):
        errors.append("missing_streaming_probe")
    else:
        completed = streaming.get("completed")
        cancelled = streaming.get("cancelled")
        for label, item in (("completed", completed), ("cancelled", cancelled)):
            if not isinstance(item, Mapping):
                errors.append(f"missing_stream_{label}")
                continue
            if item.get("http_status") != 200:
                errors.append(f"stream_{label}_http_status_not_200")
            if item.get("runtime_idle_after_close") is not True:
                errors.append(f"stream_{label}_runtime_not_idle")
            if item.get("latest_resource_snapshot_present") is not True:
                errors.append(f"stream_{label}_missing_latest_resource_snapshot")
            if item.get("stream_content_retained") is not False:
                errors.append(f"stream_{label}_content_retained")
    return errors


def _latency_stats(items: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    values = [
        float(item["wall_clock_ms"])
        for item in items
        if isinstance(item.get("wall_clock_ms"), (int, float))
    ]
    if not values:
        return {
            "count": 0,
            "mean_ms": None,
            "median_ms": None,
            "min_ms": None,
            "max_ms": None,
        }
    return {
        "count": len(values),
        "mean_ms": round(statistics.fmean(values), 3),
        "median_ms": round(statistics.median(values), 3),
        "min_ms": round(min(values), 3),
        "max_ms": round(max(values), 3),
    }


def compare_summaries(
    candidate: Mapping[str, Any],
    baseline: Mapping[str, Any],
) -> dict[str, Any]:
    candidate_workloads = candidate.get("workloads")
    baseline_workloads = baseline.get("workloads")
    candidate_workloads = (
        candidate_workloads if isinstance(candidate_workloads, Mapping) else {}
    )
    baseline_workloads = (
        baseline_workloads if isinstance(baseline_workloads, Mapping) else {}
    )
    same_model = candidate.get("model") == baseline.get("model")
    same_fingerprint = (
        candidate.get("identity", {}).get("runtime_fingerprint")
        == baseline.get("identity", {}).get("runtime_fingerprint")
        if isinstance(candidate.get("identity"), Mapping)
        and isinstance(baseline.get("identity"), Mapping)
        else False
    )
    comparisons: dict[str, Any] = {}
    for name in ("sequential_short", "sequential_long"):
        cand_items = candidate_workloads.get(name)
        base_items = baseline_workloads.get(name)
        cand_items = cand_items if isinstance(cand_items, list) else []
        base_items = base_items if isinstance(base_items, list) else []
        cand_stats = _latency_stats(cand_items)
        base_stats = _latency_stats(base_items)
        delta = None
        pct = None
        if (
            cand_stats["median_ms"] is not None
            and base_stats["median_ms"] not in {None, 0}
        ):
            delta = round(
                cand_stats["median_ms"] - base_stats["median_ms"],
                3,
            )
            pct = round(delta / base_stats["median_ms"] * 100.0, 3)
        comparisons[name] = {
            "candidate": cand_stats,
            "baseline": base_stats,
            "median_delta_ms": delta,
            "median_delta_percent": pct,
        }
    return {
        "schema_version": 1,
        "procedure": "rte1_sampler_overhead_comparison_v1",
        "status": _OBSERVATIONAL,
        "comparable": same_model and same_fingerprint,
        "same_model": same_model,
        "same_runtime_fingerprint": same_fingerprint,
        "candidate_source_commit": candidate.get("source_commit"),
        "baseline_source_commit": baseline.get("source_commit"),
        "workloads": comparisons,
        "claim": (
            "single-device observational latency delta only; "
            "no cross-device guarantee"
        ),
    }


def capture(
    *,
    base_url: str,
    model: str,
    source_commit: str,
    mode: str,
    sequential_runs: int,
    timeout: float,
) -> dict[str, Any]:
    if not _FULL_SHA.fullmatch(source_commit):
        raise EvidenceError("source commit must be a full lowercase Git SHA")
    if mode not in {"candidate", "baseline"}:
        raise EvidenceError("mode must be candidate or baseline")
    root = _loopback_root(base_url)
    identity_status, identity_payload, _ = _request_json(
        f"{root}/v1/runtime/identity",
        timeout=5,
    )
    if identity_status != 200:
        raise EvidenceError("runtime identity endpoint is not ready")
    resource_status, resource_payload, _ = _request_json(
        f"{root}/api/v1/resources",
        timeout=5,
    )
    if resource_status != 200:
        raise EvidenceError(
            "resource endpoint is not ready; start Korgis with --enable-admin-api"
        )

    short_prompt = "Return exactly the word OK."
    long_prompt = (
        "Read this synthetic context and return exactly the word OK. "
        + ("synthetic-token " * 2048)
    )
    short = [
        _completion(
            root,
            model,
            short_prompt + f" Run {index}.",
            max_tokens=8,
            timeout=timeout,
        )
        for index in range(sequential_runs)
    ]
    long = [
        _completion(
            root,
            model,
            long_prompt + f" Run {index}.",
            max_tokens=8,
            timeout=timeout,
        )
        for index in range(sequential_runs)
    ]

    workloads: dict[str, Any] = {
        "sequential_short": short,
        "sequential_long": long,
    }
    if mode == "candidate":
        nonce = uuid.uuid4().hex
        cache_prompt = f"Return exactly OK. Synthetic cache probe {nonce}."
        cache_first = _completion(
            root,
            model,
            cache_prompt,
            max_tokens=8,
            timeout=timeout,
        )
        cache_second = _completion(
            root,
            model,
            cache_prompt,
            max_tokens=8,
            timeout=timeout,
        )
        concurrent_prompts = [
            (
                "Return exactly OK after reading this synthetic context. "
                + (f"lane-{lane} " * 4096)
            )
            for lane in (1, 2)
        ]
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(
                    _completion,
                    root,
                    model,
                    prompt,
                    max_tokens=32,
                    timeout=timeout,
                )
                for prompt in concurrent_prompts
            ]
            concurrent = [future.result() for future in futures]
        workloads.update(
            {
                "cache": {
                    "first": cache_first,
                    "second": cache_second,
                },
                "concurrent": concurrent,
                "streaming": {
                    "completed": _stream_probe(
                        root,
                        model,
                        (
                            "Return exactly OK after this synthetic streaming probe. "
                            + ("stream " * 512)
                        ),
                        cancel_after_first_frame=False,
                        timeout=timeout,
                    ),
                    "cancelled": _stream_probe(
                        root,
                        model,
                        "Generate a synthetic numbered list from one to one hundred.",
                        cancel_after_first_frame=True,
                        timeout=timeout,
                    ),
                },
            }
        )

    summary: dict[str, Any] = {
        "schema_version": 1,
        "procedure": "rte1_request_resource_telemetry_v1",
        "captured_at": _utc_now(),
        "mode": mode,
        "source_commit": source_commit,
        "model": model,
        "identity": _identity_summary(identity_payload, model),
        "hardware": _mac_hardware_profile(),
        "resource_endpoint": _resource_snapshot_summary(resource_payload),
        "workloads": workloads,
        "privacy": {
            "prompts_retained": False,
            "assistant_content_retained": False,
            "private_paths_retained": False,
            "process_ids_retained": False,
        },
    }
    if mode == "candidate":
        errors = validate_candidate_summary(summary)
    else:
        errors = []
        if summary["hardware"].get("system") != "Darwin":
            errors.append("representative_device_is_not_macos")
        for name in ("sequential_short", "sequential_long"):
            if not all(
                item.get("http_status") == 200
                for item in workloads[name]
            ):
                errors.append(f"{name}_contains_failed_request")
    summary["validation_errors"] = errors
    summary["status"] = _PASS if not errors else _FAIL
    return summary


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Capture or compare RTE-1 representative-device evidence"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    capture_parser = sub.add_parser("capture")
    capture_parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:1235",
    )
    capture_parser.add_argument("--model", required=True)
    capture_parser.add_argument("--source-commit", required=True)
    capture_parser.add_argument(
        "--mode",
        choices=("candidate", "baseline"),
        required=True,
    )
    capture_parser.add_argument(
        "--sequential-runs",
        type=int,
        default=3,
    )
    capture_parser.add_argument("--timeout", type=float, default=600.0)
    capture_parser.add_argument("--output", type=Path, required=True)

    compare_parser = sub.add_parser("compare")
    compare_parser.add_argument("--candidate", type=Path, required=True)
    compare_parser.add_argument("--baseline", type=Path, required=True)
    compare_parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "capture":
        if args.sequential_runs < 2:
            raise SystemExit("--sequential-runs must be >= 2")
        try:
            summary = capture(
                base_url=args.base_url,
                model=args.model,
                source_commit=args.source_commit,
                mode=args.mode,
                sequential_runs=args.sequential_runs,
                timeout=args.timeout,
            )
        except EvidenceError as exc:
            raise SystemExit(str(exc)) from exc
        _write_json(args.output, summary)
        print(
            json.dumps(
                {
                    "status": summary["status"],
                    "output": str(args.output),
                }
            )
        )
        return 0 if summary["status"] == _PASS else 1

    candidate = json.loads(
        args.candidate.read_text(encoding="utf-8")
    )
    baseline = json.loads(
        args.baseline.read_text(encoding="utf-8")
    )
    comparison = compare_summaries(candidate, baseline)
    _write_json(args.output, comparison)
    print(
        json.dumps(
            {
                "status": comparison["status"],
                "comparable": comparison["comparable"],
                "output": str(args.output),
            }
        )
    )
    return 0 if comparison["comparable"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
