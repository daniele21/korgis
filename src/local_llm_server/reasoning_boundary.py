"""Chunk-safe reasoning/final-answer boundaries.

The parser owns only textual separation. It does not score, repair JSON or
invent missing final content. When reasoning is expected and hidden, ambiguous
text remains hidden until a closing reasoning delimiter proves that final-answer
content has begun.
"""
from __future__ import annotations

from dataclasses import dataclass, field

_OPEN = "<think>"
_CLOSE = "</think>"
_DELIMITERS = (_OPEN, _CLOSE)
_MAX_CARRY = max(len(value) for value in _DELIMITERS) - 1


def split_reasoning_content(
    content: str,
    *,
    expect_reasoning: bool,
) -> tuple[str, str]:
    """Return ``(reasoning, final)`` without guessing ambiguous hidden output."""
    close_only = _CLOSE in content and _OPEN not in content
    if not expect_reasoning and not close_only:
        if _OPEN not in content and _CLOSE not in content:
            return "", content.strip()
        parser = ReasoningStreamParser(expect_reasoning=False, collect_reasoning=True)
    else:
        parser = ReasoningStreamParser(expect_reasoning=True, collect_reasoning=True)

    final = parser.feed(content) + parser.finish()
    return parser.reasoning.strip(), final.strip()


@dataclass(slots=True)
class ReasoningStreamParser:
    """Incrementally expose final-answer text while retaining delimiter carry.

    ``expect_reasoning=True`` starts in the hidden reasoning state. This covers
    templates that emit ``</think>`` without a visible opening tag and prevents
    leakage when tags are absent or split. ``expect_reasoning=False`` starts in
    final-answer state but still consumes any explicit reasoning block that
    appears later.
    """

    expect_reasoning: bool
    collect_reasoning: bool = False
    _in_reasoning: bool = field(init=False)
    _carry: str = field(default="", init=False)
    _reasoning_parts: list[str] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        self._in_reasoning = bool(self.expect_reasoning)

    @property
    def reasoning(self) -> str:
        return "".join(self._reasoning_parts)

    @property
    def in_reasoning(self) -> bool:
        return self._in_reasoning

    @property
    def carry_size(self) -> int:
        return len(self._carry)

    def feed(self, chunk: str) -> str:
        if not chunk:
            return ""
        buffer = self._carry + chunk
        self._carry = ""
        output: list[str] = []

        while buffer:
            match = _next_delimiter(buffer)
            if match is not None:
                index, delimiter = match
                ordinary = buffer[:index]
                self._consume_ordinary(ordinary, output)
                buffer = buffer[index + len(delimiter):]
                if delimiter == _OPEN:
                    self._in_reasoning = True
                else:
                    self._in_reasoning = False
                continue

            safe_length = _safe_prefix_length(buffer)
            if safe_length == 0:
                self._carry = buffer
                break
            ordinary = buffer[:safe_length]
            self._consume_ordinary(ordinary, output)
            buffer = buffer[safe_length:]
            if buffer:
                self._carry = buffer
            break

        return "".join(output)

    def finish(self) -> str:
        """Flush final carry; ambiguous reasoning carry remains hidden."""
        carry = self._carry
        self._carry = ""
        if not carry:
            return ""
        if self._in_reasoning:
            self._record_reasoning(carry)
            return ""
        return carry

    def end_implicit_reasoning(self) -> None:
        """Exit initial implicit-reasoning state when backend separates reasoning."""
        if self._in_reasoning and not self._carry and not self._reasoning_parts:
            self._in_reasoning = False

    def _consume_ordinary(self, text: str, output: list[str]) -> None:
        if not text:
            return
        if self._in_reasoning:
            self._record_reasoning(text)
        else:
            output.append(text)

    def _record_reasoning(self, text: str) -> None:
        if self.collect_reasoning and text:
            self._reasoning_parts.append(text)


def _next_delimiter(value: str) -> tuple[int, str] | None:
    matches = [
        (index, delimiter)
        for delimiter in _DELIMITERS
        if (index := value.find(delimiter)) >= 0
    ]
    return min(matches, key=lambda item: item[0]) if matches else None


def _safe_prefix_length(value: str) -> int:
    """Return chars safe to classify while retaining possible delimiter prefix."""
    keep = 0
    max_suffix = min(len(value), _MAX_CARRY)
    for size in range(1, max_suffix + 1):
        suffix = value[-size:]
        if any(delimiter.startswith(suffix) for delimiter in _DELIMITERS):
            keep = size
    return len(value) - keep
