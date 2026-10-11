"""Standalone Python consumer SDK contract tests; no inference runtime is started."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sdk" / "python" / "src"))

from korgis_sdk import (  # noqa: E402
    AppBindings, Korgis, KorgisConnection, KorgisError, KorgisProtocolError,
    KorgisTaskError, KorgisUnavailable, ProfileError,
)


def _profile(task="chat", model="nemotron-nano-4b"):
    return {
        "schema_version": "korgis-client-bindings-v1",
        "app": "example",
        "roles": {"analysis": {"model": model, "task": task}},
    }


class _Response:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, _limit):
        return self.payload


class _Opener:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request)
        outcome = next(self.responses)
        if isinstance(outcome, Exception):
            raise outcome
        return _Response(outcome)


def _client(task="chat", responses=(), model="nemotron-nano-4b"):
    client = KorgisConnection(
        AppBindings.from_dict(_profile(task=task, model=model)),
        base_url="http://127.0.0.1:1235",
    )
    opener = _Opener(responses)
    client._opener = opener
    return client, opener


def test_profile_is_explicit_and_fail_closed(tmp_path):
    p = AppBindings.from_dict(_profile())
    assert p.models == ("nemotron-nano-4b",)
    file = tmp_path / "bindings.json"
    file.write_text(json.dumps(_profile()), encoding="utf-8")
    assert AppBindings.load(file).roles["analysis"].model == "nemotron-nano-4b"
    with pytest.raises(TypeError):
        p.roles["other"] = p.roles["analysis"]
    for invalid in (
        {**_profile(), "fallback": "cloud"},
        {**_profile(), "schema_version": "unknown"},
        {**_profile(), "roles": {"analysis": {"model": "m", "task": "inference"}}},
        {**_profile(), "roles": {"analysis": {"model": "", "task": "chat"}}},
        {**_profile(), "roles": {}},
        {**_profile(), "roles": {"analysis": {"model": "m", "task": "chat", "residency": "warm"}}},
    ):
        with pytest.raises(ProfileError):
            AppBindings.from_dict(invalid)


def test_http_loopback_required_and_redirects_disabled():
    for url in ("http://192.168.1.2:1235", "https://example.com", "http://user:pw@localhost:1235"):
        with pytest.raises(ValueError):
            KorgisConnection(AppBindings.from_dict(_profile()), base_url=url)
    client = KorgisConnection(AppBindings.from_dict(_profile()), base_url="http://127.0.0.1:1235")
    assert any(type(handler).__name__ == "_RejectRedirects" for handler in client._opener.handlers)


def test_connection_verifies_exact_resident_model_and_connect_does_not_own_server(monkeypatch):
    client, opener = _client(responses=[
        {"ok": True},
        {"object": "list", "data": [{"key": "nemotron-nano-4b", "id": "vendor/nemotron"}]},
    ])
    client.ensure_ready()
    client.close()
    assert [x.full_url for x in opener.requests] == [
        "http://127.0.0.1:1235/health",
        "http://127.0.0.1:1235/v1/models",
    ]
    missing, _ = _client(responses=[
        {"ok": True}, {"object": "list", "data": [{"id": "another-model"}]},
    ])
    with pytest.raises(KorgisUnavailable, match="requested_model_not_resident"):
        missing.ensure_ready()
    monkeypatch.setattr(KorgisConnection, "ensure_ready", lambda self: None)
    with Korgis.connect(_profile()) as attached:
        assert attached.role("analysis") is not None


def test_chat_routes_exact_model_and_disallows_model_override():
    client, opener = _client(responses=[{
        "choices": [{"message": {"role": "assistant", "content": "done"}}]
    }])
    assert client.role("analysis").generate("Say done", temperature=0) == "done"
    body = json.loads(opener.requests[0].data)
    assert body == {
        "model": "nemotron-nano-4b", "messages": [{"role": "user", "content": "Say done"}],
        "stream": False, "temperature": 0,
    }
    with pytest.raises(KorgisTaskError, match="reserved_generation_field"):
        client.role("analysis").generate("bad", model="different")


def test_transcription_uses_first_class_audio_endpoint_and_hides_original_filename(tmp_path):
    client, opener = _client(task="transcription", responses=[{"text": "hello"}])
    private = tmp_path / "sensitive-employee-name.wav"
    private.write_bytes(b"audio-data")
    assert client.role("analysis").transcribe(private) == "hello"
    request = opener.requests[0]
    assert request.full_url.endswith("/v1/audio/transcriptions")
    assert b'name="model"' in request.data
    assert b"nemotron-nano-4b" in request.data
    assert b"audio-data" in request.data
    assert b"sensitive-employee-name" not in request.data
    with pytest.raises(KorgisTaskError, match="role_does_not_support_chat"):
        client.role("analysis").generate("not an asr call")


def test_errors_do_not_echo_sensitive_server_payload():
    client, opener = _client(responses=[HTTPError(
        "http://127.0.0.1:1235/v1/chat/completions", 500, "secret:private transcript",
        {}, None,
    )])
    with pytest.raises(KorgisError) as err:
        client.role("analysis").generate("secret prompt")
    assert err.value.status == 500
    assert "secret" not in str(err.value)
    client2, _ = _client(responses=[URLError("connection refused")])
    with pytest.raises(KorgisUnavailable, match="server_unreachable"):
        client2.ensure_ready()


def test_malformed_inference_responses_fail_instead_of_repairing():
    client, _ = _client(responses=[{"output": "{maybe json}"}])
    with pytest.raises(KorgisProtocolError, match="missing_choices"):
        client.role("analysis").generate("prompt")


def test_managed_launches_only_declared_models_and_stops_owned_process(monkeypatch):
    import korgis_sdk.managed as managed

    starts = []
    signals = []

    class Process:
        pid = 321
        returncode = None

        def __init__(self, args, **kwargs):
            starts.append((args, kwargs))

        def poll(self):
            return self.returncode

        def wait(self, timeout):
            self.returncode = 0
            return 0

    monkeypatch.setattr(managed.shutil, "which", lambda value: "/trusted/local-llm")
    monkeypatch.setattr(managed, "_ephemeral_port", lambda: 49152)
    monkeypatch.setattr(managed.subprocess, "Popen", Process)
    monkeypatch.setattr(managed.os, "killpg", lambda pid, sig: signals.append((pid, sig)))
    monkeypatch.setattr(KorgisConnection, "ensure_ready", lambda self: None)
    with Korgis.managed(_profile()) as ai:
        assert ai.role("analysis") is not None
    argv, opts = starts[0]
    assert argv == [
        "/trusted/local-llm", "serve", "--host", "127.0.0.1",
        "--port", "49152", "--no-download", "--model", "nemotron-nano-4b",
    ]
    assert opts["start_new_session"] is True
    assert signals and signals[0][0] == 321


def test_managed_startup_failure_does_not_leave_owned_process(monkeypatch):
    import korgis_sdk.managed as managed

    class FailedProcess:
        pid = 321

        def __init__(self, *args, **kwargs):
            pass

        def poll(self):
            return 2

    monkeypatch.setattr(managed.shutil, "which", lambda x: "/trusted/local-llm")
    monkeypatch.setattr(managed, "_ephemeral_port", lambda: 49152)
    monkeypatch.setattr(managed.subprocess, "Popen", FailedProcess)
    with pytest.raises(KorgisUnavailable, match="managed_server_exited"):
        with Korgis.managed(_profile()):
            pass


def test_sdk_builds_as_a_standalone_wheel(tmp_path):
    """The consumer must remain installable without the heavyweight server package."""
    import subprocess
    import zipfile

    root = Path(__file__).resolve().parents[1]
    output = tmp_path / "wheels"
    subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--no-isolation",
         "--outdir", str(output), str(root / "sdk" / "python")],
        check=True, capture_output=True, text=True, timeout=60,
    )
    wheels = list(output.glob("korgis_client-*.whl"))
    assert len(wheels) == 1
    with zipfile.ZipFile(wheels[0]) as archive:
        files = set(archive.namelist())
        metadata = next(name for name in files if name.endswith(".dist-info/METADATA"))
        body = archive.read(metadata).decode("utf-8")
        assert "Requires-Dist: llama-cpp-python" not in body
        assert "Requires-Dist: fastapi" not in body
        assert "korgis_sdk/client.py" in files
        assert "korgis_sdk/managed.py" in files
