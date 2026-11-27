"""ASGI reasoning/final-answer boundary for interactive chat responses.

The middleware keeps raw model output inside the product boundary, applies the
same chunk-safe reasoning parser used by Evaluation, and exposes only the final
application answer when structured output is requested. JSON validation happens
strictly after reasoning separation; malformed final JSON is never repaired.
"""
from __future__ import annotations

import codecs
import json
from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI

from .application_output import normalize_application_output, request_expects_reasoning
from .core.contracts import ErrorCode, InferenceError, OutputConstraints
from .reasoning_boundary import ReasoningStreamParser
from .structured_output import parse_structured_output

_INFERENCE_PATHS = frozenset({"/v1/chat/completions", "/api/v1/chat"})
_STRUCTURED_FORMATS = frozenset({"json_object", "json_schema"})


class ReasoningBoundaryMiddleware:
    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http" or scope.get("method", "").upper() != "POST":
            await self.app(scope, receive, send)
            return
        if scope.get("path") not in _INFERENCE_PATHS:
            await self.app(scope, receive, send)
            return

        body = await _read_request_body(receive)
        try:
            payload = json.loads(body.decode("utf-8")) if body else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            await self.app(scope, _replay_body(body, receive), send)
            return
        if not isinstance(payload, dict):
            await self.app(scope, _replay_body(body, receive), send)
            return

        application = scope.get("app")
        manager = getattr(getattr(application, "state", None), "runtime_manager", None)
        runtime = None
        if manager is not None:
            try:
                runtime = manager.resolve(payload.get("model"))
            except (LookupError, RuntimeError):
                runtime = None
        cfg = getattr(runtime, "cfg", {}) if runtime is not None else {}

        constraints = _output_constraints(payload)
        if constraints.format is None and bool(cfg.get("force_json", False)):
            constraints = OutputConstraints(format="json_object")
        structured = constraints.format in _STRUCTURED_FORMATS
        is_stream = bool(payload.get("stream", False))

        show_thinking = _effective_bool(
            payload,
            primary="show_thinking",
            alias="show_reasoning",
            fallback=cfg.get("show_thinking", False),
        )
        enable_thinking_value = _optional_bool(
            payload,
            primary="enable_thinking",
            alias="enable_reasoning",
        )
        expect_reasoning = request_expects_reasoning(enable_thinking_value, cfg)

        # SO-2 owns the structured application boundary. Do not needlessly
        # re-normalize ordinary non-stream responses already handled by the
        # historical response builder; this keeps the migration surface small.
        if not is_stream and not structured:
            await self.app(scope, _replay_body(body, receive), send)
            return

        # Intentional reasoning exposure remains valid only for unstructured
        # streams. Structured application content must never contain reasoning.
        if is_stream and show_thinking and not structured:
            await self.app(scope, _replay_body(body, receive), send)
            return

        downstream = dict(payload)
        downstream["show_thinking"] = True
        downstream.pop("show_reasoning", None)
        rewritten = json.dumps(
            downstream,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        rewritten_scope = dict(scope)
        rewritten_scope["headers"] = _replace_content_length(
            scope.get("headers", []), len(rewritten)
        )

        if is_stream:
            redactor = SSEReasoningRedactor(
                expect_reasoning=expect_reasoning,
                constraints=constraints,
            )

            async def filtered_send(message: dict[str, Any]) -> None:
                if message.get("type") != "http.response.body":
                    await send(message)
                    return

                chunk = message.get("body", b"") or b""
                more_body = bool(message.get("more_body", False))
                output = redactor.feed(chunk)
                if not more_body:
                    output += redactor.finish()
                if output or not more_body:
                    forwarded = dict(message)
                    forwarded["body"] = output
                    await send(forwarded)

            await self.app(
                rewritten_scope,
                _replay_body(rewritten, receive),
                filtered_send,
            )
            return

        await self._handle_nonstream(
            rewritten_scope,
            rewritten,
            receive,
            send,
            expect_reasoning=expect_reasoning,
            constraints=constraints,
            expose_reasoning=show_thinking,
        )

    async def _handle_nonstream(
        self,
        scope: dict[str, Any],
        body: bytes,
        original_receive: Any,
        send: Any,
        *,
        expect_reasoning: bool,
        constraints: OutputConstraints,
        expose_reasoning: bool,
    ) -> None:
        start_message: dict[str, Any] | None = None
        body_parts: list[bytes] = []

        async def capture_send(message: dict[str, Any]) -> None:
            nonlocal start_message
            if message.get("type") == "http.response.start":
                start_message = dict(message)
                return
            if message.get("type") == "http.response.body":
                body_parts.append(message.get("body", b"") or b"")

        await self.app(scope, _replay_body(body, original_receive), capture_send)
        if start_message is None:
            return

        status = int(start_message.get("status", 500))
        raw_body = b"".join(body_parts)
        if status >= 400:
            await _send_complete_response(send, start_message, raw_body)
            return

        try:
            response_payload = json.loads(raw_body.decode("utf-8"))
            if not isinstance(response_payload, dict):
                raise ValueError("non-object JSON response")
            raw_content = _response_raw_content(response_payload)
            pre_separated_reasoning = _response_reasoning_content(response_payload)
            normalized = normalize_application_output(
                raw_content,
                expect_reasoning=(
                    False if pre_separated_reasoning else expect_reasoning
                ),
                constraints=constraints,
            )
            _apply_normalized_response(
                response_payload,
                normalized.final_content,
                normalized.reasoning or pre_separated_reasoning,
                normalized.structured_output,
                expose_reasoning=expose_reasoning,
            )
            rendered = json.dumps(
                response_payload,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        except InferenceError as exc:
            await _send_model_output_error(send, exc)
            return
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            # This boundary only owns valid JSON completion responses. Preserve
            # unexpected non-JSON transport shapes instead of inventing content.
            await _send_complete_response(send, start_message, raw_body)
            return

        await _send_complete_response(send, start_message, rendered)


class SSEReasoningRedactor:
    """Remove reasoning from SSE and validate structured final content at EOF."""

    def __init__(
        self,
        *,
        expect_reasoning: bool,
        constraints: OutputConstraints | None = None,
    ) -> None:
        self.parser = ReasoningStreamParser(expect_reasoning=expect_reasoning)
        self.constraints = constraints or OutputConstraints()
        self._structured = self.constraints.format in _STRUCTURED_FORMATS
        self._structured_parts: list[str] = []
        self._structured_finish_reason: str | None = None
        self._decoder = codecs.getincrementaldecoder("utf-8")()
        self._buffer = ""
        self._last_metadata: dict[str, Any] = {}
        self._done_seen = False

    def feed(self, chunk: bytes | str) -> bytes:
        if isinstance(chunk, bytes):
            text = self._decoder.decode(chunk, final=False)
        else:
            text = str(chunk)
        self._buffer += text
        output: list[str] = []

        while "\n\n" in self._buffer:
            event, self._buffer = self._buffer.split("\n\n", 1)
            rendered = self._filter_event(event)
            if rendered:
                output.append(rendered + "\n\n")
        return "".join(output).encode("utf-8")

    def finish(self) -> bytes:
        self._buffer += self._decoder.decode(b"", final=True)
        output: list[str] = []
        if self._buffer:
            rendered = self._filter_event(self._buffer.rstrip("\n"))
            if rendered:
                output.append(rendered + "\n\n")
            self._buffer = ""
        if not self._done_seen:
            output.extend(self._finish_application_content(include_done=False))
        return "\n\n".join(output).encode("utf-8") + (b"\n\n" if output else b"")

    def _filter_event(self, event: str) -> str:
        stripped = event.strip()
        if not stripped or not stripped.startswith("data:"):
            return event
        data = stripped[5:].strip()
        if data == "[DONE]":
            self._done_seen = True
            return "\n\n".join(self._finish_application_content(include_done=True))

        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            return event
        if not isinstance(payload, dict):
            return event

        self._remember_metadata(payload)
        choices = payload.get("choices")
        if isinstance(choices, list):
            normalized_choices: list[Any] = []
            for choice in choices:
                if not isinstance(choice, dict):
                    normalized_choices.append(choice)
                    continue
                normalized = dict(choice)
                if self._structured and normalized.get("finish_reason") is not None:
                    self._structured_finish_reason = str(normalized["finish_reason"])
                    normalized["finish_reason"] = None

                delta = normalized.get("delta")
                if isinstance(delta, dict):
                    normalized_delta = dict(delta)
                    if isinstance(normalized_delta.get("reasoning_content"), str) and normalized_delta["reasoning_content"]:
                        self.parser.end_implicit_reasoning()
                    content = normalized_delta.get("content")
                    if isinstance(content, str) and content:
                        exposed = self.parser.feed(content)
                        if exposed:
                            if self._structured:
                                self._structured_parts.append(exposed)
                                normalized_delta.pop("content", None)
                            else:
                                normalized_delta["content"] = exposed
                        else:
                            normalized_delta.pop("content", None)
                    normalized["delta"] = normalized_delta
                normalized_choices.append(normalized)
            payload["choices"] = normalized_choices

        # Structured content is intentionally buffered until the complete final
        # object can be validated. Preserve only real side-channel evidence
        # (usage/timings/role/etc.) before that point; suppress empty transport
        # events and finish-only events whose reason is attached to final JSON.
        if not _has_transport_signal(payload):
            return ""
        return "data: " + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    def _finish_application_content(self, *, include_done: bool) -> list[str]:
        tail = self.parser.finish()
        output: list[str] = []
        if self._structured:
            if tail:
                self._structured_parts.append(tail)
            final = "".join(self._structured_parts).strip()
            try:
                parse_structured_output(final, self.constraints)
            except InferenceError as exc:
                output.append(_sse_error_event(exc))
            else:
                output.append(
                    self._synthetic_content_event(
                        final,
                        finish_reason=self._structured_finish_reason,
                    )
                )
        elif tail:
            output.append(self._synthetic_content_event(tail))
        if include_done:
            output.append("data: [DONE]")
        return output

    def _remember_metadata(self, payload: Mapping[str, Any]) -> None:
        for key in ("id", "object", "created", "model"):
            if key in payload:
                self._last_metadata[key] = payload[key]

    def _synthetic_content_event(
        self,
        content: str,
        *,
        finish_reason: str | None = None,
    ) -> str:
        payload = dict(self._last_metadata)
        payload.setdefault("object", "chat.completion.chunk")
        payload["choices"] = [
            {
                "index": 0,
                "delta": {"content": content},
                "finish_reason": finish_reason,
            }
        ]
        return "data: " + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def install_reasoning_boundary(application: FastAPI) -> FastAPI:
    if getattr(application.state, "reasoning_boundary_installed", False):
        return application
    application.state.reasoning_boundary_installed = True
    application.add_middleware(ReasoningBoundaryMiddleware)
    return application


async def _read_request_body(receive: Any) -> bytes:
    chunks: list[bytes] = []
    more_body = True
    while more_body:
        message = await receive()
        if message.get("type") != "http.request":
            continue
        chunks.append(message.get("body", b"") or b"")
        more_body = bool(message.get("more_body", False))
    return b"".join(chunks)


def _replay_body(body: bytes, original_receive: Any):
    """Replay the consumed body once, then preserve the real disconnect channel."""
    sent = False

    async def receive() -> dict[str, Any]:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return await original_receive()

    return receive


def _replace_content_length(headers: Any, length: int) -> list[tuple[bytes, bytes]]:
    output: list[tuple[bytes, bytes]] = []
    for key, value in headers:
        if bytes(key).lower() == b"content-length":
            continue
        output.append((bytes(key), bytes(value)))
    output.append((b"content-length", str(length).encode("ascii")))
    return output


def _effective_bool(
    payload: Mapping[str, Any],
    *,
    primary: str,
    alias: str,
    fallback: Any,
) -> bool:
    value = payload.get(primary)
    if value is None:
        value = payload.get(alias)
    if value is None:
        value = fallback
    return bool(value)


def _optional_bool(
    payload: Mapping[str, Any],
    *,
    primary: str,
    alias: str,
) -> bool | None:
    value = payload.get(primary)
    if value is None:
        value = payload.get(alias)
    return None if value is None else bool(value)


def _output_constraints(payload: Mapping[str, Any]) -> OutputConstraints:
    response_format = payload.get("response_format")
    if not isinstance(response_format, Mapping):
        return OutputConstraints()
    format_name = response_format.get("type")
    if format_name is None:
        return OutputConstraints()
    schema = response_format.get("json_schema")
    return OutputConstraints(
        format=str(format_name),
        json_schema=dict(schema) if isinstance(schema, Mapping) else None,
    )


def _response_raw_content(payload: Mapping[str, Any]) -> str:
    raw = payload.get("raw_output")
    if isinstance(raw, str):
        return raw
    choices = payload.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], Mapping):
        message = choices[0].get("message")
        if isinstance(message, Mapping) and isinstance(message.get("content"), str):
            return str(message["content"])
    return str(payload.get("content") or "")


def _response_reasoning_content(payload: Mapping[str, Any]) -> str:
    choices = payload.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], Mapping):
        message = choices[0].get("message")
        if isinstance(message, Mapping):
            reasoning = message.get("reasoning_content")
            if isinstance(reasoning, str) and reasoning:
                return reasoning
    return ""


def _apply_normalized_response(
    payload: dict[str, Any],
    final: str,
    reasoning: str,
    structured: Mapping[str, Any] | None,
    *,
    expose_reasoning: bool,
) -> None:
    choices = payload.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        message = choices[0].get("message")
        if isinstance(message, dict):
            message["content"] = final
    payload["output"] = final
    payload["response"] = final
    payload["content"] = final
    payload["final_answer"] = final
    payload["thinking"] = reasoning if expose_reasoning else ""
    if structured is not None:
        payload["structured_output"] = dict(structured)


def _has_transport_signal(payload: Mapping[str, Any]) -> bool:
    if isinstance(payload.get("usage"), Mapping) and payload.get("usage"):
        return True
    if isinstance(payload.get("timings"), Mapping) and payload.get("timings"):
        return True
    choices = payload.get("choices")
    if not isinstance(choices, list):
        return False
    for choice in choices:
        if not isinstance(choice, Mapping):
            continue
        if choice.get("finish_reason") is not None:
            return True
        delta = choice.get("delta")
        if isinstance(delta, Mapping) and bool(delta):
            return True
        text = choice.get("text")
        if isinstance(text, str) and text:
            return True
    return False


async def _send_complete_response(
    send: Any,
    start: Mapping[str, Any],
    body: bytes,
) -> None:
    headers = _replace_response_content_length(start.get("headers", []), len(body))
    await send({**dict(start), "headers": headers})
    await send({"type": "http.response.body", "body": body, "more_body": False})


async def _send_model_output_error(send: Any, error: InferenceError) -> None:
    body = json.dumps(
        {
            "detail": {
                "code": error.code.value,
                "message": error.message,
                "retryable": error.retryable,
                "details": dict(error.details),
            }
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": 502 if error.code is ErrorCode.INVALID_MODEL_OUTPUT else 400,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body, "more_body": False})


def _replace_response_content_length(headers: Any, length: int) -> list[tuple[bytes, bytes]]:
    output: list[tuple[bytes, bytes]] = []
    for key, value in headers:
        if bytes(key).lower() == b"content-length":
            continue
        output.append((bytes(key), bytes(value)))
    output.append((b"content-length", str(length).encode("ascii")))
    return output


def _sse_error_event(error: InferenceError) -> str:
    payload = {
        "error": {
            "code": error.code.value,
            "message": error.message,
            "retryable": error.retryable,
            "details": dict(error.details),
        }
    }
    return "data: " + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
