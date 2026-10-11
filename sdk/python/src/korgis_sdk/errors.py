"""Public errors never echo server response bodies or user content."""

from __future__ import annotations


class KorgisError(Exception):
    def __init__(self, code: str, *, status: int | None = None):
        self.code = code
        self.status = status
        super().__init__(code)


class KorgisUnavailable(KorgisError):
    pass


class KorgisProtocolError(KorgisError):
    pass


class KorgisTaskError(KorgisError):
    pass
