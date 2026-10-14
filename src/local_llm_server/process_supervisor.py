"""Supervise a backend with an EOF-based parent-death contract.

Only the Korgis parent holds the pipe's write end. If it disappears due to
SIGKILL, crash, or OOM, this process closes its own backend before exiting.
The backend shares this supervisor's process group for ordinary graceful stop.
This is a subprocess transport implementation detail, not a new runtime owner.
"""
from __future__ import annotations

import os
import select
import signal
import subprocess
import sys
import threading


def _relay_output(stream) -> None:
    try:
        while True:
            chunk = stream.read(8192)
            if not chunk:
                break
            try:
                os.write(1, chunk)
            except OSError:
                # The parent's log reader died. Continue draining the backend
                # without allowing a broken stdout pipe to disable supervision.
                pass
    finally:
        stream.close()


def _stop_backend(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 3 or args[1] != "--":
        raise SystemExit("expected: process_supervisor PARENT_READ_FD -- BACKEND [ARGS...]")
    parent_fd = int(args[0])
    if parent_fd < 0:
        raise SystemExit("invalid parent pipe fd")

    backend: subprocess.Popen[bytes] | None = None
    try:
        # The backend inherits our process group, but not the owner-watch fd.
        backend = subprocess.Popen(
            args[2:],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            close_fds=True,
        )
        assert backend.stdout is not None
        thread = threading.Thread(target=_relay_output, args=(backend.stdout,), daemon=True)
        thread.start()
        while backend.poll() is None:
            readable, _, _ = select.select([parent_fd], [], [], 0.2)
            if readable and os.read(parent_fd, 1) == b"":
                # The Korgis owner is gone; this is not ordinary request idle.
                _stop_backend(backend)
                return 0
        thread.join(timeout=1)
        return int(backend.returncode or 0)
    finally:
        if backend is not None and backend.poll() is None:
            _stop_backend(backend)
        os.close(parent_fd)


if __name__ == "__main__":
    raise SystemExit(main())
