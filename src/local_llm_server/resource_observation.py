"""Privacy-safe observed resource snapshots for control-plane consumers."""
from __future__ import annotations

import os
import platform
import resource
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .resources import (
    ResourceObserver,
    ResourceValue,
    ResourceValueSource,
    StandardLibraryResourceObserver,
)
from .resources_macos import MacOSResourceObserver, read_process_rss as read_macos_process_rss


def _value_payload(value: ResourceValue) -> dict[str, object]:
    return {
        "value": value.value,
        "source": value.source.value,
        "unit": value.unit,
    }


def _observer_for_platform() -> ResourceObserver:
    if platform.system().lower() == "darwin":
        return MacOSResourceObserver()
    return StandardLibraryResourceObserver()


def _linux_process_rss(pid: int) -> ResourceValue:
    try:
        resident_pages = int(
            Path(f"/proc/{pid}/statm").read_text(encoding="utf-8").split()[1]
        )
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
        return ResourceValue(
            resident_pages * page_size,
            ResourceValueSource.MEASURED,
            "bytes",
        )
    except (OSError, ValueError, IndexError):
        return ResourceValue.unavailable("bytes")


def read_process_rss(pid: int) -> ResourceValue:
    if isinstance(pid, bool) or pid <= 0:
        return ResourceValue.unavailable("bytes")
    system = platform.system().lower()
    if system == "darwin":
        return read_macos_process_rss(pid)
    if system == "linux":
        return _linux_process_rss(pid)
    return ResourceValue.unavailable("bytes")


def _parse_ps_cpu_time(raw: str) -> float | None:
    value = raw.strip()
    if not value:
        return None
    days = 0
    if "-" in value:
        day_text, value = value.split("-", 1)
        try:
            days = int(day_text)
        except ValueError:
            return None
    parts = value.split(":")
    try:
        if len(parts) == 3:
            hours, minutes, seconds = parts
        elif len(parts) == 2:
            hours = "0"
            minutes, seconds = parts
        else:
            return None
        return (
            days * 86400
            + int(hours) * 3600
            + int(minutes) * 60
            + float(seconds)
        )
    except ValueError:
        return None


def _linux_process_cpu_seconds(pid: int) -> ResourceValue:
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        closing = raw.rfind(")")
        if closing < 0:
            return ResourceValue.unavailable("seconds")
        fields = raw[closing + 2 :].split()
        user_ticks = int(fields[11])
        system_ticks = int(fields[12])
        ticks_per_second = int(os.sysconf("SC_CLK_TCK"))
        return ResourceValue(
            (user_ticks + system_ticks) / ticks_per_second,
            ResourceValueSource.MEASURED,
            "seconds",
        )
    except (OSError, ValueError, IndexError):
        return ResourceValue.unavailable("seconds")


def read_process_cpu_seconds(pid: int) -> ResourceValue:
    if isinstance(pid, bool) or pid <= 0:
        return ResourceValue.unavailable("seconds")
    if pid == os.getpid():
        usage = resource.getrusage(resource.RUSAGE_SELF)
        return ResourceValue(
            float(usage.ru_utime + usage.ru_stime),
            ResourceValueSource.MEASURED,
            "seconds",
        )
    system = platform.system().lower()
    if system == "linux":
        return _linux_process_cpu_seconds(pid)
    if system == "darwin":
        try:
            completed = subprocess.run(
                ("ps", "-o", "time=", "-p", str(pid)),
                capture_output=True,
                text=True,
                check=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return ResourceValue.unavailable("seconds")
        seconds = _parse_ps_cpu_time(completed.stdout)
        if seconds is None:
            return ResourceValue.unavailable("seconds")
        return ResourceValue(
            seconds,
            ResourceValueSource.MEASURED,
            "seconds",
        )
    return ResourceValue.unavailable("seconds")


def _runtime_process(runtime: Any) -> tuple[int | None, str]:
    engine = runtime.engine
    isolation = str(getattr(engine, "execution_isolation", "unknown"))
    if isolation == "in_process":
        return os.getpid(), "server_process"
    if isolation == "subprocess":
        managed = getattr(engine, "process", None)
        process = getattr(managed, "process", None)
        pid = getattr(process, "pid", None)
        if isinstance(pid, int) and pid > 0:
            return pid, "owned_backend_process"
    return None, "unavailable"


def resource_observation_payload(
    manager: Any,
    *,
    observer: ResourceObserver | None = None,
    clock: Any = time.monotonic,
    utc_now: Any = lambda: datetime.now(timezone.utc),
) -> dict[str, object]:
    """Return measured resource evidence without paths, prompts or process IDs."""
    selected_observer = observer or _observer_for_platform()
    system = selected_observer.snapshot()
    runtimes: list[dict[str, object]] = []

    for runtime in manager.list():
        pid, scope = _runtime_process(runtime)
        rss = read_process_rss(pid) if pid is not None else ResourceValue.unavailable("bytes")
        cpu = (
            read_process_cpu_seconds(pid)
            if pid is not None
            else ResourceValue.unavailable("seconds")
        )
        runtimes.append(
            {
                "runtime_key": runtime.key,
                "model_id": runtime.model_id,
                "backend": str(
                    getattr(runtime.engine, "backend", runtime.cfg.get("backend", "unknown"))
                ),
                "execution_isolation": str(
                    getattr(runtime.engine, "execution_isolation", "unknown")
                ),
                "scope": scope,
                "process_rss_bytes": _value_payload(rss),
                "process_cpu_seconds": _value_payload(cpu),
            }
        )

    return {
        "schema_version": "1",
        "captured_at_utc": utc_now().isoformat(),
        "captured_at_monotonic": float(clock()),
        "platform": system.platform,
        "system": {
            "total_memory_bytes": _value_payload(system.total_memory_bytes),
            "available_memory_bytes": _value_payload(system.available_memory_bytes),
            "server_process_rss_bytes": _value_payload(system.process_rss_bytes),
            "accelerator_memory_bytes": _value_payload(system.accelerator_memory_bytes),
            "thermal_pressure": _value_payload(system.thermal_pressure),
        },
        "runtimes": runtimes,
    }
