"""Privacy-safe per-request CPU/RAM sampling for local inference.

The sampler deliberately reports process-tree observations, not request-exclusive
ownership. Concurrent requests may share the same measured process tree, so
attribution quality remains explicit instead of implying precision we do not have.
"""
from __future__ import annotations

import os
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Protocol

REQUEST_EVIDENCE_VERSION = "korgis-request-evidence-v1"
_DEFAULT_INTERVAL_MS = 100


@dataclass(frozen=True, slots=True)
class ResourceSample:
    monotonic_s: float
    rss_bytes: int | None
    cpu_percent: float | None
    cpu_interval_s: float | None = None


@dataclass(frozen=True, slots=True)
class RequestResourceEvidence:
    snapshot_id: str
    baseline_memory_bytes: int | None
    peak_memory_bytes: int | None
    end_memory_bytes: int | None
    peak_delta_bytes: int | None
    average_cpu_percent: float | None
    peak_cpu_percent: float | None
    interval_ms: int
    sample_count: int
    sample_errors: int
    cpu_observation_ms: float | None
    source: str
    attribution_scope: str = "korgis_process_tree"
    attribution_quality: str = "process_global"

    def to_public_dict(self) -> dict[str, object]:
        return {
            "snapshot_id": self.snapshot_id,
            "memory": {
                "baseline_bytes": self.baseline_memory_bytes,
                "peak_bytes": self.peak_memory_bytes,
                "end_bytes": self.end_memory_bytes,
                "peak_delta_bytes": self.peak_delta_bytes,
            },
            "cpu": {
                "average_percent": self.average_cpu_percent,
                "peak_percent": self.peak_cpu_percent,
            },
            "sampling": {
                "interval_ms": self.interval_ms,
                "sample_count": self.sample_count,
                "errors": self.sample_errors,
                "cpu_observation_ms": self.cpu_observation_ms,
            },
            "attribution": {
                "scope": self.attribution_scope,
                "quality": self.attribution_quality,
            },
            "sources": {
                "memory": self.source,
                "cpu": self.source,
            },
        }


class ResourceSampleSource(Protocol):
    source_name: str

    def sample(self) -> ResourceSample:
        ...


class PsProcessTreeSampleSource:
    """Sample the current Korgis process tree through the local POSIX `ps` tool."""

    source_name = "ps_process_tree_cpu_time_delta_excluding_sampler"

    def __init__(self, root_pid: int | None = None) -> None:
        self.root_pid = root_pid or os.getpid()
        self._previous_cpu_seconds: dict[int, float] | None = None
        self._previous_monotonic_s: float | None = None

    def sample(self) -> ResourceSample:
        process = subprocess.Popen(
            ["ps", "-axo", "pid=,ppid=,rss=,time="],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        sampler_pid = process.pid
        try:
            stdout, stderr = process.communicate(timeout=1.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            raise RuntimeError("ps sampling timed out") from None
        if process.returncode != 0:
            detail = stderr.strip()[:200]
            raise RuntimeError(
                f"ps failed with exit code {process.returncode}: {detail}"
            )

        rows: dict[int, tuple[int, int, float]] = {}
        for line in stdout.splitlines():
            parts = line.split()
            if len(parts) < 4:
                continue
            try:
                pid = int(parts[0])
                ppid = int(parts[1])
                rss_kib = max(0, int(parts[2]))
                cpu_seconds = _parse_cpu_time_seconds(parts[3])
            except (TypeError, ValueError):
                continue
            rows[pid] = (ppid, rss_kib, cpu_seconds)

        selected = {self.root_pid}
        changed = True
        while changed:
            changed = False
            for pid, (ppid, _, _) in rows.items():
                if pid not in selected and ppid in selected:
                    selected.add(pid)
                    changed = True

        selected.discard(sampler_pid)
        present = [rows[pid] for pid in selected if pid in rows]
        if not present:
            raise RuntimeError("root process was not present in ps output")

        sampled_at = time.monotonic()
        rss_bytes = sum(row[1] for row in present) * 1024
        current_cpu_seconds = {
            pid: rows[pid][2]
            for pid in selected
            if pid in rows
        }

        cpu_percent = None
        cpu_interval_s = None
        if (
            self._previous_cpu_seconds is not None
            and self._previous_monotonic_s is not None
        ):
            elapsed_s = sampled_at - self._previous_monotonic_s
            if elapsed_s > 0:
                cpu_delta_s = sum(
                    max(
                        0.0,
                        cpu_seconds
                        - self._previous_cpu_seconds.get(pid, 0.0),
                    )
                    for pid, cpu_seconds in current_cpu_seconds.items()
                )
                cpu_percent = cpu_delta_s / elapsed_s * 100.0
                cpu_interval_s = elapsed_s

        self._previous_cpu_seconds = current_cpu_seconds
        self._previous_monotonic_s = sampled_at
        return ResourceSample(
            monotonic_s=sampled_at,
            rss_bytes=rss_bytes,
            cpu_percent=cpu_percent,
            cpu_interval_s=cpu_interval_s,
        )


def _parse_cpu_time_seconds(raw: str) -> float:
    """Parse POSIX ps TIME values without treating lifetime %CPU as request CPU."""
    value = raw.strip()
    if not value:
        raise ValueError("empty CPU time")

    days = 0
    if "-" in value:
        day_text, value = value.split("-", 1)
        days = int(day_text)

    parts = value.split(":")
    if not 1 <= len(parts) <= 3:
        raise ValueError(f"unsupported CPU time: {raw}")

    seconds = float(parts[-1].replace(",", "."))
    minutes = int(parts[-2]) if len(parts) >= 2 else 0
    hours = int(parts[-3]) if len(parts) == 3 else 0
    return days * 86400.0 + hours * 3600.0 + minutes * 60.0 + seconds


def aggregate_resource_samples(
    samples: list[ResourceSample],
    *,
    interval_ms: int,
    sample_errors: int,
    source: str,
    snapshot_id: str,
) -> RequestResourceEvidence:
    memory_values = [item.rss_bytes for item in samples if item.rss_bytes is not None]
    cpu_values = [
        (item.cpu_percent, item.cpu_interval_s)
        for item in samples
        if (
            item.cpu_percent is not None
            and item.cpu_interval_s is not None
            and item.cpu_interval_s > 0
        )
    ]

    baseline_memory = memory_values[0] if memory_values else None
    peak_memory = max(memory_values) if memory_values else None
    end_memory = memory_values[-1] if memory_values else None
    peak_delta = (
        max(0, peak_memory - baseline_memory)
        if peak_memory is not None and baseline_memory is not None
        else None
    )

    cpu_observation_s = sum(interval for _, interval in cpu_values)
    average_cpu = (
        sum(percent * interval for percent, interval in cpu_values)
        / cpu_observation_s
        if cpu_observation_s > 0
        else None
    )
    peak_cpu = max((percent for percent, _ in cpu_values), default=None)

    return RequestResourceEvidence(
        snapshot_id=snapshot_id,
        baseline_memory_bytes=baseline_memory,
        peak_memory_bytes=peak_memory,
        end_memory_bytes=end_memory,
        peak_delta_bytes=peak_delta,
        average_cpu_percent=average_cpu,
        peak_cpu_percent=peak_cpu,
        interval_ms=interval_ms,
        sample_count=len(samples),
        sample_errors=sample_errors,
        cpu_observation_ms=(
            cpu_observation_s * 1000.0 if cpu_observation_s > 0 else None
        ),
        source=source,
    )


class RequestResourceSampler:
    """Bounded background sampler whose failures never fail inference."""

    def __init__(
        self,
        *,
        interval_ms: int = _DEFAULT_INTERVAL_MS,
        sample_source: ResourceSampleSource | None = None,
    ) -> None:
        if interval_ms < 20:
            raise ValueError("interval_ms must be >= 20")
        self.interval_ms = interval_ms
        self.sample_source = sample_source or PsProcessTreeSampleSource()
        self.snapshot_id = f"resource-{uuid.uuid4().hex}"
        self._samples: list[ResourceSample] = []
        self._errors = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._started = False

    def start(self) -> "RequestResourceSampler":
        if self._started:
            raise RuntimeError("resource sampler already started")
        self._started = True
        self._capture()
        self._thread = threading.Thread(
            target=self._run,
            name="korgis-resource-sampler",
            daemon=True,
        )
        self._thread.start()
        return self

    def stop(self) -> RequestResourceEvidence:
        if not self._started:
            raise RuntimeError("resource sampler was not started")
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, self.interval_ms / 1000.0 * 3))
        self._capture()
        self._started = False
        return aggregate_resource_samples(
            self._samples,
            interval_ms=self.interval_ms,
            sample_errors=self._errors,
            source=self.sample_source.source_name,
            snapshot_id=self.snapshot_id,
        )

    def _run(self) -> None:
        interval_s = self.interval_ms / 1000.0
        while not self._stop.wait(interval_s):
            self._capture()

    def _capture(self) -> None:
        try:
            self._samples.append(self.sample_source.sample())
        except Exception:
            self._errors += 1


def request_evidence_payload(
    *,
    execution_source: str,
    resources: RequestResourceEvidence | None = None,
    request_id: str | None = None,
) -> dict[str, object]:
    return {
        "evidence_version": REQUEST_EVIDENCE_VERSION,
        "request_id": request_id or f"req-{uuid.uuid4().hex}",
        "execution_source": execution_source,
        "resources": resources.to_public_dict() if resources is not None else None,
    }
