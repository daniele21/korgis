"""Authenticated, explicitly owned loopback sessions for embedded Korgis.

Opt-in only; regular CLI serving and OpenAI-compatible clients are unchanged.
The secret arrives over an inherited pipe, never via arguments or environment.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re

from fastapi import FastAPI
from starlette.responses import JSONResponse, Response

_ATTEST_PATH = "/_korgis/session/attest"
_CHALLENGE = re.compile(r"^[0-9a-f]{64}$")
_TOKEN = re.compile(r"^[0-9a-f]{64}$")


def read_session_secret(fd: int) -> str:
    """Consume a single 256-bit hex secret from a parent-owned pipe."""
    if not isinstance(fd, int) or fd < 3:
        raise ValueError("session secret must be provided using an inherited fd")
    try:
        payload = os.read(fd, 129)
    finally:
        os.close(fd)
    try:
        secret = payload.decode("ascii")
    except UnicodeDecodeError:
        raise ValueError("invalid managed session secret") from None
    if not _TOKEN.fullmatch(secret):
        raise ValueError("invalid managed session secret")
    return secret


class _EmbeddedSessionMiddleware:
    """ASGI auth gate: all HTTP routes including docs, admin and media."""

    def __init__(self, app, *, secret: str):
        if not _TOKEN.fullmatch(secret):
            raise ValueError("invalid embedded session secret")
        self.app = app
        self.secret = secret

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return

        headers = dict(scope.get("headers", ()))
        if scope["path"] == _ATTEST_PATH and scope["method"] == "GET":
            challenge = headers.get(b"x-korgis-challenge", b"")
            try:
                nonce = challenge.decode("ascii")
            except UnicodeDecodeError:
                nonce = ""
            if not _CHALLENGE.fullmatch(nonce):
                response = JSONResponse({"code": "invalid_session_challenge"}, status_code=400)
            else:
                proof = hmac.new(
                    self.secret.encode("ascii"), nonce.encode("ascii"), hashlib.sha256
                ).hexdigest()
                response = Response(status_code=204, headers={"X-Korgis-Session-Proof": proof})
            await response(scope, receive, send)
            return

        expected = ("Bearer " + self.secret).encode("ascii")
        authorization = headers.get(b"authorization", b"")
        if not hmac.compare_digest(authorization, expected):
            response = JSONResponse({"code": "session_authorization_required"}, status_code=401)
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


def install_embedded_session_guard(application: FastAPI, secret: str) -> FastAPI:
    """Register last, outside product middleware; no unauthenticated bypasses."""
    application.add_middleware(_EmbeddedSessionMiddleware, secret=secret)
    return application
