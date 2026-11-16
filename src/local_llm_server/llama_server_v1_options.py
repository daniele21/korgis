"""Optional llama.cpp v1-specific CLI options; no runtime or process ownership.

The existing compatibility adapter selects and attributes binaries. This module
only verifies v1 opt-ins, without changing resource admission or defaults.
"""
from __future__ import annotations

import math
import subprocess
from pathlib import Path
from typing import Any, Callable, Mapping


def build_v1_option_args(
    cfg: Mapping[str, Any],
    *,
    exact_v1_release: bool,
    binary: Path | str,
    help_runner: Callable[[str], str] | None = None,
) -> list[str]:
    """Reject unsupported/unsafe v1 options before spawning llama-server."""
    cache_mib = cfg.get("llama_server_moe_cache_mib")
    cpu_share = cfg.get("llama_server_moe_cache_cpu_share")
    fp8 = any(
        str(cfg.get(key) or "").strip().lower() == "f8_e4m3"
        for key in ("llama_server_cache_type_k", "llama_server_cache_type_v")
    )
    if cache_mib is None and cpu_share is None and not fp8:
        return []
    if not exact_v1_release:
        raise ValueError("llama.cpp v1 features require an attributable v1.0.0 llama-server")

    if cache_mib is not None and cfg.get("llama_server_moe_stream_cache_gib") is not None:
        raise ValueError("v1 MoE host cache cannot be combined with experimental SSD streaming")

    args: list[str] = []
    required: list[str] = []
    if cache_mib is not None:
        if isinstance(cache_mib, bool) or not isinstance(cache_mib, int) or cache_mib <= 0:
            raise ValueError("llama_server_moe_cache_mib must be a positive integer")
        args.extend(["--moe-cache-mib", str(cache_mib)])
        required.append("--moe-cache-mib")

    if cpu_share is not None:
        if cache_mib is None:
            raise ValueError("llama_server_moe_cache_cpu_share requires a cache budget")
        if isinstance(cpu_share, bool) or not isinstance(cpu_share, (int, float)):
            raise ValueError("llama_server_moe_cache_cpu_share must be between 0 and 1")
        share = float(cpu_share)
        if not math.isfinite(share) or not 0 <= share <= 1:
            raise ValueError("llama_server_moe_cache_cpu_share must be between 0 and 1")
        args.extend(["--moe-cache-cpu-share", str(share)])
        required.append("--moe-cache-cpu-share")
    if fp8:
        required.extend(["--cache-type-k", "--cache-type-v", "f8_e4m3"])

    help_text = _read_help(binary, help_runner=help_runner)
    if not all(option in help_text for option in required):
        raise RuntimeError("Selected llama-server does not advertise required llama.cpp v1 options")
    return args


def _read_help(
    binary: Path | str,
    *,
    help_runner: Callable[[str], str] | None = None,
) -> str:
    if help_runner is not None:
        result = help_runner(str(binary))
        if not isinstance(result, str):
            raise RuntimeError("Cannot verify llama.cpp v1 options on selected binary")
        return result
    try:
        result = subprocess.run(
            [str(binary), "--help"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("Cannot verify llama.cpp v1 options on selected binary") from exc
    if result.returncode != 0:
        raise RuntimeError("Cannot verify llama.cpp v1 options on selected binary")
    return result.stdout + "\n" + result.stderr
