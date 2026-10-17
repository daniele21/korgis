"""Lifecycle-owned, observation-only host pressure monitor.

This adapter samples the canonical ResourceManager observer periodically, records
bounded public-safe alert transitions and uses existing residency policy only
to PREVIEW eligible candidates. It never unloads models, clears caches or
claims native macOS pressure notification support. Native events have a separate
evidence gate and MUST NOT be inferred from these measurements.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Any, Mapping

from .residency_pressure import PressureEvictionPolicy
from .resource_manager import ResourceManager
from .resources import ResourcePressure

_LOG = logging.getLogger(__name__)


class HostPressureMonitor:
    """Bounded sampler owned by an application's ASGI lifespan.

    Sampling uses the ResourceManager's single host observer, and makes no
    background decision that changes live runtime ownership or model residency.
    """

    def __init__(
        self,
        resource_manager: ResourceManager,
        *,
        residency_snapshot: Callable[[], Mapping[str, Any]] | None = None,
        clear_response_cache: Callable[[], None] | None = None,
        interval_seconds: float = 5.0,
        history_limit: int = 16,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not resource_manager.host_protection_enabled:
            raise ValueError("host monitor requires an active host observer")
        if interval_seconds < 0.1:
            raise ValueError("interval_seconds must be >= 0.1")
        if history_limit < 1 or history_limit > 128:
            raise ValueError("history_limit must be between 1 and 128")
        self._manager = resource_manager
        self._residency_snapshot = residency_snapshot
        self._clear_response_cache = clear_response_cache
        self._interval_seconds = interval_seconds
        self._clock = clock
        self._policy = PressureEvictionPolicy()
        self._history: deque[dict[str, object]] = deque(maxlen=history_limit)
        self._lock = threading.RLock()
        self._sample_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_host: dict[str, object] | None = None
        self._last_at: float | None = None
        self._sample_count = 0
        self._state = "normal"
        self._candidate_count = 0
        self._cache_trim_count = 0

    def start(self) -> None:
        """Start exactly one daemon thread; never start during import."""
        with self._lock:
            if self._thread is not None:
                if self._thread.is_alive():
                    return
                if not self._stop.is_set():
                    raise RuntimeError("host pressure monitor thread ended unexpectedly")
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="korgis-host-pressure",
                daemon=True,
            )
            self._thread.start()

    def stop(self, *, timeout_seconds: float = 10.0) -> None:
        """Stop sampling before the owning server releases its runtimes.

        The macOS observer command runner bounds each syscall invocation.
        A blocked sampler never leads to unbounded shutdown waiting.
        """
        self._stop.set()
        with self._lock:
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=timeout_seconds)
            if thread.is_alive():
                raise RuntimeError("host pressure monitor did not stop within the bound")

    def sample_once(self) -> dict[str, object]:
        """Perform one immediate sample, suitable for deterministic tests."""
        with self._sample_lock:
            host = self._manager.host_memory_status()
            if host is None:
                host = {
                    "pressure": "unknown", "alert": "unknown", "source": "unavailable",
                    "available_bytes": None, "total_bytes": None,
                    "swap_used_bytes": None, "automatic_pressure_eviction_enabled": False,
                }
            try:
                pressure = ResourcePressure(str(host.get("pressure", "unknown")))
            except ValueError:
                pressure = ResourcePressure.UNKNOWN
            try:
                residency = self._residency_snapshot() if self._residency_snapshot else {}
            except Exception:
                _LOG.warning("residency snapshot unavailable during pressure observation")
                residency = {}
            evaluation = self._policy.observe(pressure, residency)
            sampled_at = self._clock()
            alert = str(host.get("alert", "unknown"))
            cache_trimmed = False
            # This only drops Korgis-owned, completed response-cache entries.
            # Do not touch model/KV memory or infer host RSS was reclaimed.
            if (
                evaluation.transition == "triggered"
                and pressure is ResourcePressure.CRITICAL
                and self._clear_response_cache is not None
                and not self._stop.is_set()
            ):
                try:
                    self._clear_response_cache()
                    cache_trimmed = True
                except Exception:
                    _LOG.exception("Korgis response-cache trim failed")
            with self._lock:
                previous_alert = (
                    str(self._last_host.get("alert")) if self._last_host is not None else None
                )
                if alert != previous_alert or evaluation.transition is not None:
                    self._history.append({
                        "at_monotonic": sampled_at,
                        "alert": alert,
                        "policy_transition": evaluation.transition,
                        "automatic_action": cache_trimmed,
                        "action": "response_cache_cleared" if cache_trimmed else None,
                    })
                if cache_trimmed:
                    self._cache_trim_count += 1
                self._last_host = dict(host)
                self._last_at = sampled_at
                self._sample_count += 1
                self._state = evaluation.state.value
                self._candidate_count = len(evaluation.candidates)
            return self.snapshot()

    def snapshot(self) -> dict[str, object]:
        """Return aggregate status without a fresh blocking OS invocation."""
        with self._lock:
            running = bool(
                self._thread is not None and self._thread.is_alive()
                and not self._stop.is_set()
            )
            return {
                "running": running,
                "sample_interval_seconds": self._interval_seconds,
                "sample_count": self._sample_count,
                "sampled_at_monotonic": self._last_at,
                "host_memory": dict(self._last_host) if self._last_host is not None else None,
                "pressure_policy_state": self._state,
                "eviction_candidate_count": self._candidate_count,
                "response_cache_trim_count": self._cache_trim_count,
                "reclamation_claim": False,
                "automatic_pressure_eviction_enabled": False,
                "events": [dict(item) for item in self._history],
            }

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.sample_once()
            except Exception:
                _LOG.exception("host pressure sampler failed (no automatic action)")
            if self._stop.wait(self._interval_seconds):
                break
