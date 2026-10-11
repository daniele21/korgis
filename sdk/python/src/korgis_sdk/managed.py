"""Owned local Korgis session: inherited listener, isolated credential, exact readiness."""

from __future__ import annotations

import os
import secrets
import shutil
import signal
import socket
import subprocess
import time

from .client import KorgisConnection
from .errors import KorgisUnavailable
from .profile import AppBindings


def _bound_listener() -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        sock.listen(16)
        return sock
    except BaseException:
        sock.close()
        raise


class ManagedKorgis:
    """Owns the child process and its exclusive local socket, never an attached daemon."""

    def __init__(self, profile: AppBindings, *, executable: str = "local-llm",
                 startup_timeout: float = 120.0):
        if startup_timeout <= 0:
            raise ValueError("startup_timeout must be positive")
        self.profile = profile
        self.executable = executable
        self.startup_timeout = startup_timeout
        self._process: subprocess.Popen | None = None
        self._listener: socket.socket | None = None
        self._connection: KorgisConnection | None = None

    def __enter__(self) -> KorgisConnection:
        if self._process is not None:
            raise RuntimeError("managed context is already running")
        if os.name != "posix":
            raise KorgisUnavailable("managed_socket_handoff_unsupported")
        binary = shutil.which(self.executable)
        if binary is None:
            raise KorgisUnavailable("local_llm_cli_not_installed")
        models = self.profile.models
        token = secrets.token_hex(32)
        try:
            listener = _bound_listener()
            # Keep the parent's listener open for the entire lease. If the
            # child crashes no foreign process can bind/reuse its endpoint.
            self._listener = listener
            port = listener.getsockname()[1]
            read_fd, write_fd = os.pipe()
            try:
                try:
                    os.write(write_fd, token.encode("ascii"))
                finally:
                    os.close(write_fd)
                args = [
                    binary, "serve", "--host", "127.0.0.1", "--port", str(port),
                    "--no-download",
                ]
                if len(models) == 1:
                    args.extend(["--model", models[0]])
                else:
                    args.extend(["--models", *models, "--default-model", models[0]])
                args.extend([
                    "--listen-fd", str(listener.fileno()), "--session-fd", str(read_fd),
                ])
                self._process = subprocess.Popen(
                    args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, close_fds=True, start_new_session=True,
                    pass_fds=(listener.fileno(), read_fd),
                )
            finally:
                os.close(read_fd)
            candidate = KorgisConnection(
                self.profile, base_url="http://127.0.0.1:" + str(port),
                timeout=1.0, session_token=token,
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
        connection = self._connection
        self._connection = None
        if connection is not None:
            connection.close()
        process = self._process
        self._process = None
        listener = self._listener
        self._listener = None
        try:
            if process is None or process.poll() is not None:
                return
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
        finally:
            if listener is not None:
                listener.close()
