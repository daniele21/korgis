"""Small, reusable lifecycle wrapper for backend subprocesses."""
from __future__ import annotations

import collections
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence


class ManagedProcess:
    def __init__(
        self,
        command: Sequence[str],
        *,
        name: str,
        logger: logging.Logger,
        env: Mapping[str, str] | None = None,
        tail_lines: int = 200,
        guard_parent_death: bool | None = None,
    ) -> None:
        self.command = list(command)
        self.name = name
        self.logger = logger
        self.env = dict(env) if env is not None else None
        self._tail: collections.deque[str] = collections.deque(maxlen=tail_lines)
        self._tail_lock = threading.Lock()
        self.process: subprocess.Popen[str] | None = None
        self._log_thread: threading.Thread | None = None
        # On macOS the watchdog owns backend termination if Korgis is killed
        # without running its normal shutdown handlers.
        self._guard_parent_death = (
            sys.platform == "darwin" if guard_parent_death is None else guard_parent_death
        )
        self._parent_watch_write_fd: int | None = None

    def start(self) -> None:
        if self.process is not None and self.process.poll() is None:
            raise RuntimeError(f"{self.name} is already running.")
        command = self.command
        read_fd: int | None = None
        write_fd: int | None = None
        if self._guard_parent_death:
            # Parent owns only the write end. EOF (including after SIGKILL)
            # tells the independent subprocess supervisor to stop its backend.
            read_fd, write_fd = os.pipe()
            command = [
                sys.executable, "-m", "local_llm_server.process_supervisor",
                str(read_fd), "--", *self.command,
            ]
        try:
            self.process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=self.env,
                start_new_session=True,
                pass_fds=(read_fd,) if read_fd is not None else (),
            )
            self._parent_watch_write_fd = write_fd
        except BaseException:
            if write_fd is not None:
                os.close(write_fd)
            raise
        finally:
            if read_fd is not None:
                os.close(read_fd)
        self._log_thread = threading.Thread(
            target=self._drain_logs,
            name=f"{self.name}-logs",
            daemon=True,
        )
        self._log_thread.start()

    def _drain_logs(self) -> None:
        process = self.process
        if process is None or process.stdout is None:
            return
        try:
            for line in process.stdout:
                cleaned = line.rstrip("\r\n")
                with self._tail_lock:
                    self._tail.append(cleaned)
                self.logger.info("[%s] %s", self.name, cleaned)
        except (OSError, ValueError):
            self.logger.debug("Log stream for %s closed.", self.name)

    def wait_ready(
        self,
        check_ready: Callable[[], bool],
        *,
        timeout: float,
        interval: float = 1.0,
    ) -> None:
        deadline = time.monotonic() + timeout
        last_error = ""
        while time.monotonic() < deadline:
            process = self.process
            if process is None:
                raise RuntimeError(f"{self.name} has not been started.")
            return_code = process.poll()
            if return_code is not None:
                self._join_log_thread()
                tail = "\n".join(self.tail_logs())
                raise RuntimeError(
                    f"{self.name} exited during startup with code {return_code}. {tail}"
                )
            try:
                if check_ready():
                    return
            except Exception as exc:
                last_error = str(exc)
            time.sleep(interval)
        self.close()
        raise TimeoutError(
            f"{self.name} did not become ready within {timeout:.0f}s. "
            f"Last error: {last_error or 'not ready'}"
        )

    def tail_logs(self) -> list[str]:
        with self._tail_lock:
            return list(self._tail)

    def close(self, *, timeout: float = 5.0) -> None:
        process = self.process
        try:
            if process is None:
                return
            if process.poll() is None:
                self.logger.info("Stopping %s", self.name)
                self._signal_process_group(process, signal.SIGTERM)
                try:
                    process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    self._signal_process_group(process, signal.SIGKILL)
                    process.wait(timeout=timeout)
            self._join_log_thread(timeout=timeout)
            self.process = None
        finally:
            write_fd = self._parent_watch_write_fd
            self._parent_watch_write_fd = None
            if write_fd is not None:
                os.close(write_fd)

    @staticmethod
    def _signal_process_group(process: subprocess.Popen[str], signum: int) -> None:
        """Signal the subprocess session, with a portable process fallback."""
        if os.name == "posix" and getattr(process, "pid", None):
            try:
                os.killpg(os.getpgid(process.pid), signum)
                return
            except (OSError, ProcessLookupError):
                pass
        if signum == signal.SIGKILL:
            process.kill()
        else:
            process.terminate()

    def _join_log_thread(self, *, timeout: float = 0.2) -> None:
        thread = self._log_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=timeout)
