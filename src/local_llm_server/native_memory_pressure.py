"""Supervised optional macOS Dispatch memory-pressure event bridge.

The helper is compiled explicitly from scripts/macos/memory_pressure.swift and
must be opted into by absolute executable path. No compilation, shell, service
or subprocess is started by default. The only protocol messages are "ready"
and "normal"/"warning"/"critical". EOF on stdin terminates the helper if the
parent dies, and stop() waits/kills *only* its attributable child.

Hardware behavior and packaged signed-binary distribution are not validated.
"""
from __future__ import annotations

import logging
import os
import platform
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path
from typing import TextIO

from .resources import ResourcePressure

_LOG = logging.getLogger(__name__)
_LEVELS = {"normal": ResourcePressure.NORMAL,
           "warning": ResourcePressure.ELEVATED,
           "critical": ResourcePressure.CRITICAL}


def configured_native_helper_path() -> str | None:
    return os.environ.get("LOCAL_LLM_MACOS_PRESSURE_HELPER") or None


class MacOSNativePressureEvents:
    def __init__(
        self,
        on_pressure: Callable[[ResourcePressure | None], None],
        *,
        executable: str | None = None,
        system_name: str | None = None,
    ) -> None:
        self._handler = on_pressure
        self._executable = executable if executable is not None else configured_native_helper_path()
        self._system = system_name if system_name is not None else platform.system()
        self._lock = threading.RLock()
        self._proc: subprocess.Popen[str] | None = None
        self._thread: threading.Thread | None = None
        self._state = "not_started"
        self._stopping = False

    def start(self) -> bool:
        """No-op unless an explicit, absolute, executable helper was configured."""
        with self._lock:
            if self._proc is not None:
                return True
            if self._system != "Darwin":
                self._state = "unsupported"
                return False
            if not self._executable:
                self._state = "not_configured"
                return False
            path = Path(self._executable)
            if not path.is_absolute() or not path.is_file() or not os.access(path, os.X_OK):
                self._state = "unavailable"
                return False
            try:
                proc = subprocess.Popen(
                    [str(path)],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    close_fds=True,
                )
            except OSError:
                self._state = "unavailable"
                return False
            self._proc = proc
            self._stopping = False
            self._state = "starting"
            self._thread = threading.Thread(
                target=self._read_events, name="korgis-native-pressure", daemon=True,
            )
            self._thread.start()
            return True

    def _read_events(self) -> None:
        proc = self._proc
        assert proc is not None and proc.stdout is not None
        try:
            # All protocol lines are short. A compromised helper must not
            # force unbounded in-process allocations through stdout.
            while True:
                raw = proc.stdout.readline(32)
                if not raw:
                    break
                message = raw.strip()
                if message == "ready":
                    with self._lock:
                        if not self._stopping:
                            self._state = "active"
                    continue
                level = _LEVELS.get(message)
                if level is None:
                    continue
                with self._lock:
                    active = self._state == "active" and not self._stopping
                if active:
                    try:
                        self._handler(level)
                    except Exception:
                        _LOG.exception("native pressure event handler failed")
        except (OSError, ValueError):
            _LOG.warning("macOS native pressure stream unavailable")
        finally:
            with self._lock:
                if not self._stopping:
                    self._state = "disconnected"
            try:
                self._handler(None)
            except Exception:
                _LOG.exception("failed to clear stale native pressure state")

    def stop(self) -> None:
        """Reap the helper before the app's memory ownership is released."""
        with self._lock:
            self._stopping = True
            proc, thread = self._proc, self._thread
            self._state = "stopped"
        if proc is not None:
            if proc.stdin is not None:
                try:
                    proc.stdin.close()  # EOF tells the Swift helper to exit.
                except OSError:
                    pass
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                proc.terminate()
                try:
                    proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2.0)
            if proc.stdout is not None:
                proc.stdout.close()
        if thread is not None:
            thread.join(timeout=2.0)
            if thread.is_alive():
                raise RuntimeError("native pressure reader failed to stop")
        with self._lock:
            self._proc = None
            self._thread = None
        self._handler(None)

    def public_status(self) -> dict[str, object]:
        with self._lock:
            return {
                "state": self._state,
                "kind": "dispatch_memorypressure" if self._state == "active" else "unavailable",
                "opted_in": bool(self._executable),
            }
