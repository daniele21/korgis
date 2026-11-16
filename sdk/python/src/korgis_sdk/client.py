"""Dependency-free, fail-closed HTTP consumer for an existing Korgis control plane."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import secrets
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from .errors import KorgisError, KorgisProtocolError, KorgisTaskError, KorgisUnavailable
from .profile import AppBindings, RoleBinding

_MAX_RESPONSE_BYTES = 16 * 1024 * 1024
_MAX_AUDIO_BYTES = 32 * 1024 * 1024


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise KorgisProtocolError("redirect_forbidden", status=code)


def _validate_url(base_url: str) -> str:
    parsed = urllib.parse.urlsplit(base_url)
    if parsed.scheme != "http" or parsed.username or parsed.password or not parsed.hostname:
        raise ValueError("the v1 SDK accepts HTTP loopback URLs only")
    try:
        loopback = ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        loopback = parsed.hostname == "localhost"
    if not loopback or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError("the v1 SDK accepts HTTP loopback URLs only")
    if parsed.port is not None and not 1 <= parsed.port <= 65535:
        raise ValueError("invalid port")
    return base_url.rstrip("/")


class KorgisConnection:
    """Connect to a running control plane; the caller retains process ownership."""

    def __init__(self, profile: AppBindings, *, base_url: str, timeout: float = 120.0,
                 session_token: str | None = None):
        self.profile = profile
        self.base_url = _validate_url(base_url)
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self.timeout = timeout
        if session_token is not None and (len(session_token) != 64 or
                                          any(c not in "0123456789abcdef" for c in session_token)):
            raise ValueError("invalid managed session credential")
        self._session_token = session_token
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _RejectRedirects())

    def __enter__(self) -> "KorgisConnection":
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    def close(self) -> None:
        """Never stop a server attached from another application."""

    def role(self, name: str) -> "RoleClient":
        binding = self.profile.roles.get(name)
        if binding is None:
            raise KorgisTaskError("unknown_role")
        return RoleClient(self, binding)

    def ensure_ready(self) -> None:
        health = self._request("GET", "/health")
        if health.get("ok") is not True:
            raise KorgisUnavailable("server_not_ready")
        models = self._request("GET", "/v1/models")
        data = models.get("data")
        if not isinstance(data, list):
            raise KorgisProtocolError("invalid_model_list")
        resident = {
            key for item in data if isinstance(item, dict)
            for key in (item.get("key"), item.get("id")) if isinstance(key, str)
        }
        if not set(self.profile.models).issubset(resident):
            raise KorgisUnavailable("requested_model_not_resident")

    def _attest(self) -> None:
        """Prove server identity BEFORE sending a credential or sensitive prompt."""
        if self._session_token is None:
            return
        challenge = secrets.token_hex(32)
        request = urllib.request.Request(
            self.base_url + "/_korgis/session/attest",
            headers={"X-Korgis-Challenge": challenge},
            method="GET",
        )
        try:
            with self._opener.open(request, timeout=min(2.0, self.timeout)) as response:
                proof = response.headers.get("X-Korgis-Session-Proof", "")
                if response.status != 204:
                    raise KorgisProtocolError("managed_session_identity_mismatch")
        except urllib.error.HTTPError:
            raise KorgisProtocolError("managed_session_identity_mismatch") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise KorgisUnavailable("server_unreachable") from None
        expected = hmac.new(
            self._session_token.encode("ascii"), challenge.encode("ascii"), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(proof, expected):
            raise KorgisProtocolError("managed_session_identity_mismatch")

    def _request(self, method: str, path: str, data: bytes | None = None,
                 content_type: str = "application/json") -> dict[str, Any]:
        self._attest()
        headers = {"Content-Type": content_type} if data is not None else {}
        if self._session_token is not None:
            headers["Authorization"] = "Bearer " + self._session_token
        req = urllib.request.Request(
            self.base_url + path, data=data, headers=headers, method=method,
        )
        try:
            with self._opener.open(req, timeout=self.timeout) as response:
                # Reject unexpected large responses rather than unbounded allocation.
                blob = response.read(_MAX_RESPONSE_BYTES + 1)
                if len(blob) > _MAX_RESPONSE_BYTES:
                    raise KorgisProtocolError("response_too_large")
        except urllib.error.HTTPError as exc:
            # Inference server errors can contain user text; do not echo their bodies.
            raise KorgisError("http_error", status=exc.code) from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise KorgisUnavailable("server_unreachable") from None
        try:
            result = json.loads(blob)
        except (ValueError, UnicodeError):
            raise KorgisProtocolError("invalid_json_response") from None
        if not isinstance(result, dict):
            raise KorgisProtocolError("invalid_response_shape")
        return result


class RoleClient:
    def __init__(self, connection: KorgisConnection, binding: RoleBinding):
        self._connection = connection
        self._binding = binding

    def chat(self, messages: list[dict[str, Any]], **generation: Any) -> dict[str, Any]:
        if self._binding.task not in {"chat", "vision"}:
            raise KorgisTaskError("role_does_not_support_chat")
        if not isinstance(messages, list) or not messages:
            raise KorgisTaskError("messages_required")
        if {"model", "messages", "stream"} & generation.keys():
            raise KorgisTaskError("reserved_generation_field")
        payload = {"model": self._binding.model, "messages": messages,
                   "stream": False, **generation}
        return self._connection._request(
            "POST", "/v1/chat/completions", json.dumps(payload).encode("utf-8")
        )

    def generate(self, prompt: str, **generation: Any) -> str:
        if not isinstance(prompt, str):
            raise KorgisTaskError("prompt_must_be_text")
        result = self.chat([{"role": "user", "content": prompt}], **generation)
        choices = result.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise KorgisProtocolError("missing_choices")
        message = choices[0].get("message")
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            raise KorgisProtocolError("missing_final_content")
        return message["content"]

    def transcribe(self, audio: bytes | str | Path) -> str:
        if self._binding.task != "transcription":
            raise KorgisTaskError("role_does_not_support_transcription")
        if isinstance(audio, (str, Path)):
            path = Path(audio)
            if path.stat().st_size > _MAX_AUDIO_BYTES:
                raise KorgisTaskError("audio_too_large")
            audio = path.read_bytes()
        if not isinstance(audio, bytes) or not audio:
            raise KorgisTaskError("audio_required")
        if len(audio) > _MAX_AUDIO_BYTES:
            raise KorgisTaskError("audio_too_large")
        boundary = "korgis-" + uuid.uuid4().hex
        body = (
            ("--" + boundary + "\r\n"
             'Content-Disposition: form-data; name="model"\r\n\r\n'
             + self._binding.model + "\r\n"
             + "--" + boundary + "\r\n"
             'Content-Disposition: form-data; name="file"; filename="audio.bin"\r\n'
             "Content-Type: application/octet-stream\r\n\r\n").encode("ascii")
            + audio + ("\r\n--" + boundary + "--\r\n").encode("ascii")
        )
        result = self._connection._request(
            "POST", "/v1/audio/transcriptions", body,
            "multipart/form-data; boundary=" + boundary,
        )
        if not isinstance(result.get("text"), str):
            raise KorgisProtocolError("missing_transcription_text")
        return result["text"]


class Korgis:
    """Application-facing entrypoint; does not implement server runtime policy."""

    @staticmethod
    def connect(profile: AppBindings | dict[str, Any] | str | Path,
                *, base_url: str = "http://127.0.0.1:1235",
                timeout: float = 120.0,
                session_token: str | None = None) -> KorgisConnection:
        connection = KorgisConnection(AppBindings.load(profile), base_url=base_url,
                                      timeout=timeout, session_token=session_token)
        connection.ensure_ready()
        return connection

    @staticmethod
    def managed(profile: AppBindings | dict[str, Any] | str | Path,
                *, executable: str = "local-llm", startup_timeout: float = 120.0):
        from .managed import ManagedKorgis
        return ManagedKorgis(AppBindings.load(profile), executable=executable,
                             startup_timeout=startup_timeout)
