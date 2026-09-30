"""Binary resolution and command construction for stable-diffusion.cpp sd-server."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable, Mapping

WhichResolver = Callable[[str], str | None]
CommandRunner = Callable[[Path], str]

_VERSION_PATTERNS = (
    re.compile(r"(?i)stable[- ]diffusion\.cpp[^\r\n]*?(?P<version>v?\d+(?:\.\d+){1,3}[^\r\n]*)"),
    re.compile(r"(?i)version[:\s]+(?P<version>[^\r\n]+)"),
)


def resolve_sd_server_binary(
    cfg: Mapping[str, Any],
    *,
    which_resolver: WhichResolver | None = None,
) -> Path:
    """Resolve one explicit/discovered sd-server executable without fallback magic."""
    explicit = cfg.get("sd_server_bin") or os.getenv("LOCAL_LLM_SD_SERVER_BIN")
    if explicit:
        path = Path(str(explicit)).expanduser()
        _require_executable(path)
        return path

    resolver = which_resolver or shutil.which
    discovered = resolver("sd-server")
    if discovered:
        path = Path(discovered).expanduser()
        _require_executable(path)
        return path

    raise FileNotFoundError(
        "sd-server binary not found. Build/install stable-diffusion.cpp and set "
        "LOCAL_LLM_SD_SERVER_BIN or --sd-server-bin."
    )


def probe_sd_server_version(
    binary: Path | str,
    *,
    run_command: CommandRunner | None = None,
) -> str | None:
    """Return an attributable sd-server version string when the binary exposes one."""
    path = Path(str(binary)).expanduser()
    runner = run_command or _default_runner
    try:
        output = runner(path)
    except (OSError, subprocess.SubprocessError):
        return None

    for pattern in _VERSION_PATTERNS:
        match = pattern.search(output)
        if match is not None:
            value = " ".join(match.group("version").strip().split())
            return value[:160] or None
    return None


def build_sd_server_command(
    *,
    binary: Path | str,
    artifacts: Mapping[str, str | Path],
    host: str,
    port: int,
    cfg: Mapping[str, Any],
) -> list[str]:
    """Build the resident sd-server command for a Qwen Image artifact bundle."""
    required = ("diffusion_model", "text_encoder", "vae")
    missing = [name for name in required if name not in artifacts]
    if missing:
        raise ValueError(
            "stable-diffusion.cpp artifact bundle is missing: "
            + ", ".join(missing)
        )

    cmd = [
        str(binary),
        "--diffusion-model",
        str(artifacts["diffusion_model"]),
        "--vae",
        str(artifacts["vae"]),
        "--llm",
        str(artifacts["text_encoder"]),
        "--listen-ip",
        str(host),
        "--listen-port",
        str(_positive_int(port, "sd_server_port")),
    ]
    if bool(cfg.get("image_diffusion_flash_attention", True)):
        cmd.append("--diffusion-fa")
    if bool(cfg.get("image_offload_to_cpu", True)):
        cmd.append("--offload-to-cpu")
    return cmd


def _default_runner(binary: Path) -> str:
    completed = subprocess.run(
        [str(binary), "--version"],
        capture_output=True,
        text=True,
        timeout=2.0,
        check=False,
    )
    return "\n".join(
        part for part in (completed.stdout, completed.stderr) if part
    )


def _require_executable(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"sd-server binary does not exist: {path}")
    if not os.access(path, os.X_OK):
        raise PermissionError(f"sd-server binary is not executable: {path}")


def _positive_int(value: Any, name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if parsed <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return parsed
