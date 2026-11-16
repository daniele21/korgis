"""Explicit application bindings for the independent Korgis Python SDK."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

PROFILE_VERSION = "korgis-client-bindings-v1"
_ALLOWED_TASKS = frozenset({"chat", "vision", "transcription"})
_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")


class ProfileError(ValueError):
    """An invalid or ambiguous role binding."""


@dataclass(frozen=True)
class RoleBinding:
    model: str
    task: str


@dataclass(frozen=True)
class AppBindings:
    app: str
    roles: Mapping[str, RoleBinding]

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AppBindings":
        if not isinstance(value, Mapping) or set(value) != {"schema_version", "app", "roles"}:
            raise ProfileError("bindings require exactly schema_version, app and roles")
        if value["schema_version"] != PROFILE_VERSION:
            raise ProfileError("unsupported bindings schema_version")
        app = value["app"]
        if not isinstance(app, str) or not _NAME.fullmatch(app):
            raise ProfileError("app must be a non-empty safe identifier")
        raw_roles = value["roles"]
        if not isinstance(raw_roles, dict) or not raw_roles or len(raw_roles) > 32:
            raise ProfileError("roles must be a non-empty object of at most 32 entries")
        roles: dict[str, RoleBinding] = {}
        for name, entry in raw_roles.items():
            if not isinstance(name, str) or not _NAME.fullmatch(name):
                raise ProfileError("invalid role name")
            if not isinstance(entry, dict) or set(entry) != {"model", "task"}:
                raise ProfileError("each role requires exactly model and task")
            model, task = entry["model"], entry["task"]
            if not isinstance(model, str) or not model.strip() or model != model.strip() or len(model) > 256 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./:-]*", model):
                raise ProfileError("role.model must be an explicit model key")
            if not isinstance(task, str) or task not in _ALLOWED_TASKS:
                raise ProfileError("unsupported role.task")
            roles[name] = RoleBinding(model=model, task=task)
        return cls(app=app, roles=MappingProxyType(roles))

    @classmethod
    def load(cls, value: "AppBindings | Mapping[str, Any] | str | Path") -> "AppBindings":
        if isinstance(value, cls):
            return value
        if isinstance(value, (str, Path)):
            path = Path(value)
            if path.stat().st_size > 64 * 1024:
                raise ProfileError("bindings file exceeds 64 KiB")
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise ProfileError("bindings file is not valid UTF-8 JSON") from exc
            return cls.from_dict(raw)
        return cls.from_dict(value)

    @property
    def models(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(binding.model for binding in self.roles.values()))
