"""Prove crash cleanup with real POSIX processes, not fake Popen objects."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

import pytest


@pytest.mark.skipif(os.name != "posix", reason="POSIX parent-death supervision")
def test_sigkill_owner_stops_backend(tmp_path):
    backend_pid_path = tmp_path / "backend.pid"
    child_code = (
        "import os,time; from pathlib import Path; "
        "Path(%r).write_text(str(os.getpid())); "
        "time.sleep(120)"
    ) % str(backend_pid_path)
    owner_code = (
        "import logging,sys,time; "
        "from local_llm_server.process import ManagedProcess; "
        "p=ManagedProcess([sys.executable,'-c',%r],name='probe', "
        "logger=logging.getLogger('probe'),guard_parent_death=True); "
        "p.start(); time.sleep(120)"
    ) % child_code
    owner = subprocess.Popen(
        [sys.executable, "-c", owner_code],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    backend_pid = None
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if backend_pid_path.exists():
                backend_pid = int(backend_pid_path.read_text())
                break
            assert owner.poll() is None, "owner exited before backend startup"
            time.sleep(0.05)
        assert backend_pid is not None, "backend never started"
        os.kill(owner.pid, signal.SIGKILL)
        owner.wait(timeout=5)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                os.kill(backend_pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
        else:
            pytest.fail("supervisor did not reap backend after owner SIGKILL")
    finally:
        if owner.poll() is None:
            owner.kill()
            owner.wait(timeout=5)
        if backend_pid is not None:
            try:
                os.kill(backend_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
