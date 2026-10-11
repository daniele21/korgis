"""Deterministic managed session guard, without backend or hardware dependencies."""

from __future__ import annotations

import hashlib
import hmac
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from local_llm_server.embedded_session import (
    install_embedded_session_guard, read_session_secret,
)


def _app(secret):
    app = FastAPI()

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.post("/v1/chat/completions")
    def inference():
        return {"choices": [{"message": {"content": "local"}}]}

    install_embedded_session_guard(app, secret)
    return TestClient(app)


def test_managed_auth_covers_public_model_admin_and_docs_paths():
    token = "1" * 64
    c = _app(token)
    for path in ("/health", "/docs", "/api/v1/resources", "/v1/models", "/v1/chat/completions"):
        r = c.post(path) if path == "/v1/chat/completions" else c.get(path)
        assert r.status_code == 401
        assert "1" * 64 not in str(r.json())
    assert c.get("/health", headers={"Authorization": "Bearer " + "2" * 64}).status_code == 401
    assert c.get("/health", headers={"Authorization": "Bearer " + token}).json() == {"ok": True}
    assert c.post("/v1/chat/completions", headers={"Authorization": "Bearer " + token}).status_code == 200


def test_pre_auth_attestation_only_proves_possession_of_inherited_secret():
    token = "3" * 64
    c = _app(token)
    challenge = "f" * 64
    response = c.get("/_korgis/session/attest", headers={"X-Korgis-Challenge": challenge})
    assert response.status_code == 204
    assert response.headers["X-Korgis-Session-Proof"] == hmac.new(
        token.encode(), challenge.encode(), hashlib.sha256
    ).hexdigest()
    assert c.get("/_korgis/session/attest").status_code == 400
    assert c.post("/_korgis/session/attest").status_code == 401
    assert token not in str(response.headers)


def test_inherited_secret_is_read_once_and_fd_closed():
    token = "9" * 64
    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, token.encode("ascii"))
        os.close(write_fd)
        assert read_session_secret(read_fd) == token
        with pytest.raises(OSError):
            os.fstat(read_fd)
    finally:
        try:
            os.close(write_fd)
        except OSError:
            pass


def test_invalid_secret_is_rejected_before_server_startup():
    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, b"too-short")
        os.close(write_fd)
        with pytest.raises(ValueError, match="invalid managed session secret"):
            read_session_secret(read_fd)
        with pytest.raises(ValueError, match="invalid embedded session secret"):
            _app("short")
    finally:
        try:
            os.close(write_fd)
        except OSError:
            pass


def test_regular_http_application_does_not_inherit_embedded_auth_policy():
    app = FastAPI()

    @app.get("/health")
    def health():
        return {"ok": True}

    assert TestClient(app).get("/health").json() == {"ok": True}
