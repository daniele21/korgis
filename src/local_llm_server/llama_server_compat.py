"""Version attribution, binary selection and CLI policy for external ``llama-server``.

Local LLM Server does not download or silently replace the specialist runtime.
It owns which executable is started, proves that executable's build identity,
and only enables the modern command profile when the executable satisfies the
repository's llama.cpp v0.3.0 / b10621 feature floor.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

logger = logging.getLogger("local-llm.llama_server_compat")

DEFAULT_PROBE_TIMEOUT_SECONDS = float(
    os.getenv("LOCAL_LLM_PROBE_TIMEOUT_SECONDS", "60.0")
)

LLAMA_CPP_VALIDATED_RELEASE = "v0.3.0"
LLAMA_CPP_VALIDATED_MIN_BUILD = 10621
LLAMA_CPP_VALIDATED_RELEASE_COMMIT = "c1d0e7a004015f23bc0233470b747b596f29b264"

_VERSION_PATTERNS = (
    re.compile(
        r"version:\s*(?P<build>\d+)\s*\(`?(?P<commit>[0-9a-fA-F]{7,40})`?\)"
    ),
    re.compile(
        r"version:\s*[^\r\n]*?\(build\s+(?P<build>\d+),\s*commit\s+`?(?P<commit>[0-9a-fA-F]{7,40})`?\)"
    ),
)
_ALLOWED_LOAD_MODES = {"auto", "none", "mmap", "mlock", "mmap+mlock", "dio"}
_ALLOWED_CACHE_TYPES = {
    "f32",
    "f16",
    "bf16",
    "q8_0",
    "q4_0",
    "q4_1",
    "iq4_nl",
    "q5_0",
    "q5_1",
}

CommandRunner = Callable[[Path], str]
WhichResolver = Callable[[str], str | None]
_ExecutableCacheKey = tuple[str, int, int, int, int, int]
_VERSION_IDENTITY_CACHE: dict[_ExecutableCacheKey, "LlamaServerBuildIdentity"] = {}
_VERSION_IDENTITY_CACHE_LOCK = threading.RLock()


@dataclass(frozen=True, slots=True)
class LlamaServerBuildIdentity:
    build: int
    commit: str

    @property
    def backend_version(self) -> str:
        return f"build-{self.build}@{self.commit}"

    @property
    def matches_validated_release(self) -> bool:
        return bool(
            self.build == LLAMA_CPP_VALIDATED_MIN_BUILD
            and LLAMA_CPP_VALIDATED_RELEASE_COMMIT.startswith(self.commit)
        )


@dataclass(frozen=True, slots=True)
class LlamaServerCompatibility:
    identity: LlamaServerBuildIdentity | None
    supported: bool
    validated_release: str = LLAMA_CPP_VALIDATED_RELEASE
    minimum_build: int = LLAMA_CPP_VALIDATED_MIN_BUILD

    @property
    def backend_version(self) -> str | None:
        if self.identity is None:
            return None
        return self.identity.backend_version

    @property
    def modern_runtime_options(self) -> bool:
        return bool(
            self.supported
            and self.identity is not None
            and self.identity.build >= self.minimum_build
        )

    @property
    def exact_validated_release(self) -> bool:
        return bool(self.identity and self.identity.matches_validated_release)

    @property
    def profile(self) -> str:
        if self.exact_validated_release:
            return "validated-v0.3.0"
        if self.modern_runtime_options:
            return "forward-compatible-v0.3"
        return "legacy-unvalidated"


def parse_llama_server_version(text: str) -> LlamaServerBuildIdentity | None:
    """Parse attributable ``llama-server --version`` build/commit identity."""
    match = next(
        (candidate.search(text) for candidate in _VERSION_PATTERNS if candidate.search(text)),
        None,
    )
    if match is None:
        return None
    build = int(match.group("build"))
    if build <= 0:
        return None
    return LlamaServerBuildIdentity(
        build=build,
        commit=match.group("commit").lower(),
    )


def probe_llama_server_version(
    binary: Path | str,
    *,
    run_command: CommandRunner | None = None,
) -> LlamaServerBuildIdentity | None:
    """Return attributable build identity, or ``None`` when it cannot be proven.

    Production probing caches only a positive build+commit identity for the exact
    executable file identity. Repeated resident loads therefore do not launch a
    second ``llama-server --version`` process while another backend is active.
    Replacing/upgrading the executable changes the cache key and forces a fresh
    attribution probe. Failed or unattributable probes are never cached.

    Injected runners intentionally bypass the process-wide cache so deterministic
    tests and explicit diagnostic callers retain complete control over probing.
    """
    path = Path(str(binary)).expanduser()
    if run_command is None:
        return _probe_default_runner_cached(path)

    try:
        output = run_command(path)
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_llama_server_version(output)


def validate_llama_server_binary(
    binary: Path | str,
    *,
    allow_unvalidated: bool = False,
    minimum_build: int = LLAMA_CPP_VALIDATED_MIN_BUILD,
    run_command: CommandRunner | None = None,
) -> LlamaServerCompatibility:
    """Validate an external server against the repository's v0.3 feature floor.

    Future attributable builds are accepted as forward-compatible with the
    stable v0.3 feature floor but retain their exact build/commit identity.
    ``allow_unvalidated`` is a deliberate compatibility escape hatch for older
    or unparseable runtimes. Those runtimes never receive v0.3-only options.
    """
    if isinstance(minimum_build, bool) or minimum_build <= 0:
        raise ValueError("minimum_build must be a positive integer")

    identity = probe_llama_server_version(binary, run_command=run_command)
    if identity is None:
        if allow_unvalidated:
            return LlamaServerCompatibility(
                identity=None,
                supported=False,
                minimum_build=minimum_build,
            )
        raise RuntimeError(
            "Cannot verify llama-server version. Local LLM Server requires an "
            f"attributable llama.cpp {LLAMA_CPP_VALIDATED_RELEASE}+ executable "
            "for the managed llama_server backend. Set LOCAL_LLM_SERVER_BIN to "
            "that binary, or explicitly allow an unvalidated legacy runtime."
        )

    if identity.build < minimum_build:
        if allow_unvalidated:
            return LlamaServerCompatibility(
                identity=identity,
                supported=False,
                minimum_build=minimum_build,
            )
        raise RuntimeError(
            "llama-server is older than the validated runtime floor: "
            f"found build {identity.build}, require build "
            f"{minimum_build}+ "
            f"({LLAMA_CPP_VALIDATED_RELEASE} or newer)."
        )

    return LlamaServerCompatibility(
        identity=identity,
        supported=True,
        minimum_build=minimum_build,
    )


def resolve_llama_server_binary(
    cfg: Mapping[str, Any],
    *,
    run_command: CommandRunner | None = None,
    which_resolver: WhichResolver | None = None,
    home: Path | None = None,
    system_dirs: tuple[Path, ...] | None = None,
) -> tuple[Path, LlamaServerCompatibility]:
    """Resolve one executable and its compatibility without hidden fallback.

    An explicit config/environment path is authoritative and is never silently
    replaced. Automatic discovery prefers PATH and standard installation
    locations, followed by specialist runtime directories. A legacy candidate is
    considered only when the caller explicitly enables
    ``llama_server_allow_unvalidated``.
    """
    explicit = cfg.get("llama_server_bin") or os.getenv("LOCAL_LLM_SERVER_BIN")
    allow_unvalidated = bool(cfg.get("llama_server_allow_unvalidated", False))
    configured_minimum_build = cfg.get("llama_server_min_build")
    minimum_build = (
        LLAMA_CPP_VALIDATED_MIN_BUILD
        if configured_minimum_build is None
        else int(configured_minimum_build)
    )
    if minimum_build <= 0:
        raise ValueError("llama_server_min_build must be a positive integer")

    if explicit:
        path = Path(str(explicit)).expanduser()
        _require_executable(path)
        compatibility = validate_llama_server_binary(
            path,
            allow_unvalidated=allow_unvalidated,
            minimum_build=minimum_build,
            run_command=run_command,
        )
        return path, compatibility

    root = home or Path.home()
    candidates: list[Path] = []
    resolver = which_resolver or shutil.which
    discovered = resolver("llama-server")
    if discovered:
        candidates.append(Path(discovered))

    dirs_to_check = (
        system_dirs
        if system_dirs is not None
        else (
            (Path("/opt/homebrew/bin"), Path("/usr/local/bin"), root / ".local" / "bin")
            if home is None
            else (root / ".local" / "bin",)
        )
    )
    for standard_dir in dirs_to_check:
        candidates.append(standard_dir / "llama-server")

    lmstudio_backends = root / ".lmstudio" / "extensions" / "backends"
    candidates.extend(
        sorted(
            lmstudio_backends.glob("llama.cpp-*/llama-server"),
            key=lambda path: path.parent.name,
            reverse=True,
        )
    )

    first_legacy: tuple[Path, LlamaServerCompatibility] | None = None
    executable_candidates: list[Path] = []
    for candidate in _dedupe_paths(candidates):
        if not candidate.exists() or not os.access(candidate, os.X_OK):
            continue
        executable_candidates.append(candidate)
        compatibility = validate_llama_server_binary(
            candidate,
            allow_unvalidated=True,
            minimum_build=minimum_build,
            run_command=run_command,
        )
        if compatibility.modern_runtime_options:
            return candidate, compatibility
        if first_legacy is None:
            first_legacy = (candidate, compatibility)

    if allow_unvalidated and first_legacy is not None:
        return first_legacy

    if executable_candidates:
        raise RuntimeError(
            "No discovered llama-server satisfies the llama.cpp "
            f"{LLAMA_CPP_VALIDATED_RELEASE} / build "
            f"{minimum_build}+ runtime floor. Set "
            "LOCAL_LLM_SERVER_BIN to a supported executable, or explicitly "
            "allow an unvalidated legacy runtime."
        )

    raise FileNotFoundError(
        "llama-server binary not found. Set LOCAL_LLM_SERVER_BIN or "
        "llama_server_bin to an executable path."
    )


def build_llama_server_command(
    *,
    binary: Path | str,
    model_path: Path | str,
    mmproj_path: Path | str | None,
    host: str,
    port: int,
    cfg: Mapping[str, Any],
    compatibility: LlamaServerCompatibility,
) -> list[str]:
    """Build the owned subprocess command from one compatibility decision."""
    cmd = [
        str(binary),
        "-m",
        str(model_path),
        "--port",
        str(int(port)),
        "--host",
        str(host),
        "-c",
        str(_positive_int(cfg.get("ctx_size", 4096), "ctx_size")),
    ]
    if mmproj_path is not None:
        cmd.extend(["--mmproj", str(mmproj_path)])

    if not compatibility.modern_runtime_options:
        return cmd

    parallel = _positive_int(
        cfg.get("max_concurrent_requests", 1),
        "max_concurrent_requests",
    )
    cmd.extend(["--parallel", str(parallel)])
    cmd.append(
        "--cont-batching"
        if bool(cfg.get("llama_server_cont_batching", True))
        else "--no-cont-batching"
    )
    cmd.append(
        "--kv-unified"
        if bool(cfg.get("llama_server_kv_unified", True))
        else "--no-kv-unified"
    )

    gpu_layers = str(cfg.get("llama_server_gpu_layers") or "auto").strip().lower()
    if gpu_layers not in {"auto", "all"}:
        try:
            if int(gpu_layers) < 0:
                raise ValueError
        except ValueError as exc:
            raise ValueError(
                "llama_server_gpu_layers must be 'auto', 'all', or a non-negative integer"
            ) from exc
    cmd.extend(["--gpu-layers", gpu_layers])

    load_mode = str(cfg.get("llama_server_load_mode") or "auto").strip().lower()
    if load_mode not in _ALLOWED_LOAD_MODES:
        raise ValueError(
            "llama_server_load_mode must be one of: "
            + ", ".join(sorted(_ALLOWED_LOAD_MODES))
        )
    cmd.extend(["--load-mode", load_mode])
    cmd.extend(
        [
            "--fit",
            "on" if bool(cfg.get("llama_server_fit", True)) else "off",
        ]
    )

    if cfg.get("n_threads") is not None:
        cmd.extend(["--threads", str(_positive_int(cfg["n_threads"], "n_threads"))])
    if cfg.get("n_batch") is not None:
        cmd.extend(["--batch-size", str(_positive_int(cfg["n_batch"], "n_batch"))])
    if cfg.get("n_ubatch") is not None:
        cmd.extend(["--ubatch-size", str(_positive_int(cfg["n_ubatch"], "n_ubatch"))])
    if cfg.get("flash_attn") is not None:
        cmd.extend(["--flash-attn", "on" if bool(cfg["flash_attn"]) else "off"])

    if cfg.get("llama_server_fit_target_mib") is not None:
        target = _non_negative_int(
            cfg["llama_server_fit_target_mib"],
            "llama_server_fit_target_mib",
        )
        cmd.extend(["--fit-target", str(target)])
    if cfg.get("llama_server_fit_ctx") is not None:
        fit_ctx = _positive_int(cfg["llama_server_fit_ctx"], "llama_server_fit_ctx")
        cmd.extend(["--fit-ctx", str(fit_ctx)])

    for cfg_key, flag in (
        ("llama_server_cache_type_k", "--cache-type-k"),
        ("llama_server_cache_type_v", "--cache-type-v"),
    ):
        value = cfg.get(cfg_key)
        if value is None:
            continue
        cache_type = str(value).strip().lower()
        if cache_type not in _ALLOWED_CACHE_TYPES:
            raise ValueError(
                f"{cfg_key} must be one of: "
                + ", ".join(sorted(_ALLOWED_CACHE_TYPES))
            )
        cmd.extend([flag, cache_type])

    if cfg.get("llama_server_cache_ram_mib") is not None:
        cache_ram = _non_negative_int(
            cfg["llama_server_cache_ram_mib"],
            "llama_server_cache_ram_mib",
        )
        cmd.extend(["--cache-ram", str(cache_ram)])

    return cmd


def _probe_default_runner_cached(path: Path) -> LlamaServerBuildIdentity | None:
    """Probe one executable at most once per unchanged file identity."""
    try:
        cache_key = _executable_cache_key(path)
    except OSError:
        return None

    # Hold the lock through the bounded version subprocess so concurrent loads
    # of the same executable cannot both initialize another native probe.
    with _VERSION_IDENTITY_CACHE_LOCK:
        cached = _VERSION_IDENTITY_CACHE.get(cache_key)
        if cached is not None:
            return cached
        try:
            output = _default_runner(path)
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("Probing llama-server version for %s failed: %s", path, exc)
            return None
        identity = parse_llama_server_version(output)
        if identity is None:
            logger.warning(
                "Could not parse llama-server version from output for %s: %r",
                path,
                output,
            )
            return None

        resolved_path = cache_key[0]
        for stale_key in tuple(_VERSION_IDENTITY_CACHE):
            if stale_key[0] == resolved_path and stale_key != cache_key:
                _VERSION_IDENTITY_CACHE.pop(stale_key, None)
        _VERSION_IDENTITY_CACHE[cache_key] = identity
        return identity


def _executable_cache_key(path: Path) -> _ExecutableCacheKey:
    resolved = path.resolve()
    metadata = resolved.stat()
    return (
        str(resolved),
        int(metadata.st_dev),
        int(metadata.st_ino),
        int(metadata.st_size),
        int(metadata.st_mtime_ns),
        int(metadata.st_ctime_ns),
    )


def _default_runner(binary: Path, timeout: float | None = None) -> str:
    completed = subprocess.run(
        [str(binary), "--version"],
        capture_output=True,
        text=True,
        timeout=timeout if timeout is not None else DEFAULT_PROBE_TIMEOUT_SECONDS,
        check=False,
    )
    return "\n".join(
        part for part in (completed.stdout, completed.stderr) if part
    )


def _require_executable(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"llama-server binary does not exist: {path}")
    if not os.access(path, os.X_OK):
        raise PermissionError(f"llama-server binary is not executable: {path}")


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    result: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path.expanduser())
        if key in seen:
            continue
        seen.add(key)
        result.append(path.expanduser())
    return result


def _positive_int(value: Any, name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if parsed <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return parsed


def _non_negative_int(value: Any, name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a non-negative integer") from exc
    if parsed < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return parsed