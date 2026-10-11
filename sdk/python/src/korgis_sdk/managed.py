"""Managed development sidecar. The Korgis CLI retains runtime and resource policy."""

from __future__ import annotations

import os
import shutil
import signal
import socket
import subprocess
import time

from .client import KorgisConnection
from .errors import KorgisUnavailable
from .profile import AppBindings


def _ephemeral_port() -> int:
    # The CLI owns the final bind; a race here must fail startup, not attach
    # to an unknown external service. This helper is a developer preview.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class ManagedKorgis:
    """Owns one launched CLI process; never kills an attached external daemon."""

    def __init__(self, profile: AppBindings, *, executable: str = "local-llm",
                 startup_timeout: float = 120.0):
        if startup_timeout <= 0:
            raise ValueError("startup_timeout must be positive")
        self.profile = profile
        self.executable = executable
        self.startup_timeout = startup_timeout
        self._process: subprocess.Popen | None = None
        self._connection: KorgisConnection | None = None

    def __enter__(self) -> KorgisConnection:
        if self._process is not None:
            raise RuntimeError("managed context is already running")
        binary = shutil.which(self.executable)
        if binary is None:
            raise KorgisUnavailable("local_llm_cli_not_installed")
        models = self.profile.models
        port = _ephemeral_port()
        args = [binary, "serve", "--host", "127.0.0.1", "--port", str(port),
                "--no-download"]
        if len(models) == 1:
            args.extend(["--model", models[0]])
        else:
            args.extend(["--models", *models, "--default-model", models[0]])
        try:
            self._process = subprocess.Popen(
                args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, close_fds=True, start_new_session=True,
            )
            candidate = KorgisConnection(
                self.profile, base_url="http://127.0.0.1:" + str(port),
                timeout=1.0,
            )
            deadline = time.monotonic() + self.startup_timeout
            while time.monotonic() < deadline:
                if self._process.poll() is not None:
                    raise KorgisUnavailable("managed_server_exited")
                try:
                    candidate.ensure_ready()
                    candidate.timeout = 120.0
                    self._connection = candidate
                    return candidate
                except KorgisUnavailable:
                    time.sleep(min(0.25, max(0, deadline - time.monotonic())))
            raise KorgisUnavailable("managed_server_start_timeout")
        except BaseException:
            self.close()
            raise

    def __exit__(self, *_args) -> None:
        self.close()

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None
        process = self._process
        self._process = None
        if process is None or process.poll() is not None:
            return
        # Only the process group created by start_new_session is signalled.
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=5)
