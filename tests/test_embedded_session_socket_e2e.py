"""End-to-end POSIX listener/session handoff using an isolated deterministic ASGI child.

No model backend is loaded; this proves the exact socket/HTTP ownership path,
not MLX/Metal or an installed macOS application.
"""
from __future__ import annotations

import os
import secrets
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import ProxyHandler, build_opener

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sdk" / "python" / "src"))

from korgis_sdk import AppBindings, KorgisConnection  # noqa: E402
from korgis_sdk.errors import KorgisUnavailable  # noqa: E402


_CHILD = """
import socket
import sys
from fastapi import FastAPI
import uvicorn
from local_llm_server.embedded_session import read_session_secret, install_embedded_session_guard

fd, secret_fd, port = map(int, sys.argv[1:4])
secret = read_session_secret(secret_fd)
app = FastAPI()

@app.get('/health')
def health():
    return {'ok': True}

@app.get('/v1/models')
def models():
    return {'data': [{'key': 'fake-text', 'id': 'fake-text'}]}

@app.post('/v1/chat/completions')
def chat():
    return {'choices': [{'message': {'content': 'owned local reply'}}]}

install_embedded_session_guard(app, secret)
sock = socket.socket(fileno=fd)
try:
    uvicorn.Server(
        uvicorn.Config(app, host='127.0.0.1', port=port, log_level='error')
    ).run(sockets=[sock])
finally:
    sock.close()
"""


@pytest.mark.skipif(os.name != "posix", reason="POSIX pass_fds and process groups required")
def test_real_child_inherits_bound_listener_and_authenticates_before_inference():
    profile = AppBindings.from_dict({
        "schema_version": "korgis-client-bindings-v1",
        "app": "fixture",
        "roles": {"analysis": {"model": "fake-text", "task": "chat"}},
    })
    token = secrets.token_hex(32)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    process = None
    try:
        listener.bind(("127.0.0.1", 0))
        listener.listen(16)
        port = listener.getsockname()[1]
        read_fd, write_fd = os.pipe()
        try:
            try:
                os.write(write_fd, token.encode("ascii"))
            finally:
                os.close(write_fd)
            process = subprocess.Popen(
                [sys.executable, "-c", _CHILD, str(listener.fileno()), str(read_fd), str(port)],
                pass_fds=(listener.fileno(), read_fd),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        finally:
            os.close(read_fd)

        connection = KorgisConnection(
            profile,
            base_url="http://127.0.0.1:" + str(port),
            session_token=token,
            timeout=1.0,
        )
        deadline = time.monotonic() + 15
        while True:
            assert process.poll() is None, "child exited before readiness"
            try:
                connection.ensure_ready()
                break
            except KorgisUnavailable:
                if time.monotonic() >= deadline:
                    pytest.fail("child never became ready")
                time.sleep(0.1)
        with pytest.raises(HTTPError) as unauthorized:
            build_opener(ProxyHandler({})).open(
                "http://127.0.0.1:" + str(port) + "/health", timeout=2
            )
        assert unauthorized.value.code == 401
        assert connection.role("analysis").generate("private prompt") == "owned local reply"

        # The parent-held listening socket cannot be reclaimed by a different
        # local process even after the child process is terminated.
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=15)
        with socket.socket() as foreign:
            with pytest.raises(OSError):
                foreign.bind(("127.0.0.1", port))
    finally:
        if process is not None and process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=10)
        listener.close()
