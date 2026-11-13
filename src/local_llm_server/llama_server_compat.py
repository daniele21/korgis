"""Version attribution, binary selection and CLI policy for external ``llama-server``.

Local LLM Server does not download or silently replace the specialist runtime.
It owns which executable is started, proves that executable's build identity,
and only enables the modern command profile when the executable satisfies the
repository's llama.cpp v0.3.0 / b10621 feature floor.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

LLAMA_CPP_VALIDATED_RELEASE = "v0.3.0"
LLAMA_CPP_VALIDATED_MIN_BUILD = 10621
LLAMA_CPP_VALIDATED_RELEASE_COMMIT = "c1d0e7a004015f23bc0233470b747b596f29b264"
LLAMA_CPP_V1_RELEASE_BUILD = 12360
LLAMA_CPP_V1_RELEASE_COMMIT = "2d8a32502909ca065c41c4cf5f2fb1c427c047fc"

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

    @property
    def matches_v1_release(self) -> bool:
        """Identify the exact v1.0.0 tag rather than any later build."""
        return bool(
            self.build == LLAMA_CPP_V1_RELEASE_BUILD
            and LLAMA_CPP_V1_RELEASE_COMMIT.startswith(self.commit)
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
    def exact_v1_release(self) -> bool:
        return bool(self.supported and self.identity and self.identity.matches_v1_release)

    @property
    def profile(self) -> str:
        if self.exact_v1_release:
            return "attributed-v1.0.0"
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
    run_command: CommandRunner | None = None,
) -> LlamaServerCompatibility:
    """Validate an external server against the repository's v0.3 feature floor.

    Future attributable builds are accepted as forward-compatible with the
    stable v0.3 feature floor but retain their exact build/commit identity.
    ``allow_unvalidated`` is a deliberate compatibility escape hatch for older
    or unparseable runtimes. Those runtimes never receive v0.3-only options.
    """
    identity = probe_llama_server_version(binary, run_command=run_command)
    if identity is None:
        if allow_unvalidated:
            return LlamaServerCompatibility(identity=None, supported=False)
        raise RuntimeError(
            "Cannot verify llama-server version. Local LLM Server requires an "
            f"attributable llama.cpp {LLAMA_CPP_VALIDATED_RELEASE}+ executable "
            "for the managed llama_server backend. Set LOCAL_LLM_SERVER_BIN to "
            "that binary, or explicitly allow an unvalidated legacy runtime."
        )

    if identity.build < LLAMA_CPP_VALIDATED_MIN_BUILD:
        if allow_unvalidated:
            return LlamaServerCompatibility(identity=identity, supported=False)
        raise RuntimeError(
            "llama-server is older than the validated runtime floor: "
            f"found build {identity.build}, require build "
            f"{LLAMA_CPP_VALIDATED_MIN_BUILD}+ "
            f"({LLAMA_CPP_VALIDATED_RELEASE} or newer)."
        )

    return LlamaServerCompatibility(identity=identity, supported=True)


def resolve_llama_server_binary(
    cfg: Mapping[str, Any],
    *,
    run_command: CommandRunner | None = None,
    which_resolver: WhichResolver | None = None,
    home: Path | None = None,
) -> tuple[Path, LlamaServerCompatibility]:
    """Resolve one executable and its compatibility without hidden fallback.

    An explicit config/environment path is authoritative and is never silently
    replaced. Automatic discovery prefers the first attributable v0.3-capable
    candidate. A legacy candidate is considered only when the caller explicitly
    enables ``llama_server_allow_unvalidated``.
    """
    explicit = cfg.get("llama_server_bin") or os.getenv("LOCAL_LLM_SERVER_BIN")
    allow_unvalidated = bool(cfg.get("llama_server_allow_unvalidated", False))

    if explicit:
        path = Path(str(explicit)).expanduser()
        _require_executable(path)
        compatibility = validate_llama_server_binary(
            path,
            allow_unvalidated=allow_unvalidated,
            run_command=run_command,
        )
        return path, compatibility

    root = home or Path.home()
    candidates: list[Path] = []
    lmstudio_backends = root / ".lmstudio" / "extensions" / "backends"
    candidates.extend(
        sorted(
            lmstudio_backends.glob("llama.cpp-*/llama-server"),
            key=lambda path: path.parent.name,
            reverse=True,
        )
    )
    resolver = which_resolver or shutil.which
    discovered = resolver("llama-server")
    if discovered:
        candidates.append(Path(discovered))

    first_legacy: tuple[Path, LlamaServerCompatibility] | None = None
    executable_candidates: list[Path] = []
    for candidate in _dedupe_paths(candidates):
        if not candidate.exists() or not os.access(candidate, os.X_OK):
            continue
        executable_candidates.append(candidate)
        compatibility = validate_llama_server_binary(
            candidate,
            allow_unvalidated=True,
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
            f"{LLAMA_CPP_VALIDATED_MIN_BUILD}+ runtime floor. Set "
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
    moe_stream_help_runner: Callable[[str], str] | None = None,
    v1_help_runner: Callable[[str], str] | None = None,
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
        if cfg.get("llama_server_moe_stream_cache_gib") is not None:
            raise ValueError("MoE expert streaming requires an attributable modern llama-server")
        if any(cfg.get(key) is not None for key in (
            "llama_server_moe_cache_mib", "llama_server_moe_cache_cpu_share",
        )):
            raise ValueError("llama.cpp v1 features require an attributable v1.0.0 llama-server")
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

    stream_cache = cfg.get("llama_server_moe_stream_cache_gib")
    stream_threads = cfg.get("llama_server_moe_stream_io_threads")
    if stream_cache is None and stream_threads is not None:
        raise ValueError("MoE streaming I/O threads require a cache budget")
    if stream_cache is not None and cfg.get("llama_server_moe_cache_mib") is not None:
        raise ValueError("v1 MoE host cache cannot be combined with experimental SSD streaming")
    if stream_cache is not None:
        if load_mode not in {"auto", "none"}:
            raise ValueError("MoE expert streaming cannot be combined with mmap/mlock/direct load mode")
        cache_gib = _strict_positive_int(stream_cache, "llama_server_moe_stream_cache_gib")
        required = ["--moe-stream-cache"]
        if stream_threads is not None:
            threads = _strict_positive_int(stream_threads, "llama_server_moe_stream_io_threads")
            required.append("--moe-stream-io-threads")
        _verify_moe_stream_options(binary, required, help_runner=moe_stream_help_runner)
        # Experimental llama.cpp CLI: --moe-stream-cache implies --moe-stream.
        # The engine disables mmap; Korgis deliberately retains conservative accounting.
        cmd.extend(["--moe-stream-cache", f"{cache_gib}G"])
        if stream_threads is not None:
            cmd.extend(["--moe-stream-io-threads", str(threads)])
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
        if cache_type not in _ALLOWED_CACHE_TYPES and not (
            cache_type == "f8_e4m3" and compatibility.exact_v1_release
        ):
            raise ValueError(
                f"{cfg_key} must be one of: "
                + ", ".join(sorted(_ALLOWED_CACHE_TYPES))
                + (" or f8_e4m3 for v1.0.0" if compatibility.exact_v1_release else "")
            )
        cmd.extend([flag, cache_type])

    if cfg.get("llama_server_cache_ram_mib") is not None:
        cache_ram = _non_negative_int(
            cfg["llama_server_cache_ram_mib"],
            "llama_server_cache_ram_mib",
        )
        cmd.extend(["--cache-ram", str(cache_ram)])

    # Keep v1-only options out of the shared v0.3-compatible CLI owner.
    from .llama_server_v1_options import build_v1_option_args

    cmd.extend(build_v1_option_args(
        cfg,
        exact_v1_release=compatibility.exact_v1_release,
        binary=binary,
        help_runner=v1_help_runner,
    ))
    return cmd


def _strict_positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not (isinstance(value, int) or (
        isinstance(value, str) and value.isascii() and value.isdecimal()
    )):
        raise ValueError(f"{name} must be a positive integer")
    parsed = int(value)
    if parsed <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return parsed


def _verify_moe_stream_options(
    binary: Path | str,
    required: list[str],
    *,
    help_runner: Callable[[str], str] | None = None,
) -> None:
    # Newer build numbers do not establish experimental feature support.
    if help_runner is not None:
        help_text = help_runner(str(binary))
    else:
        try:
            result = subprocess.run(
                [str(binary), "--help"], capture_output=True, text=True,
                timeout=10, check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError("Cannot verify MoE streaming options on selected llama-server") from exc
        help_text = result.stdout + "\n" + result.stderr
    if not all(option in help_text for option in required):
        raise RuntimeError(
            "Selected llama-server does not advertise required experimental MoE streaming options"
        )


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
        except (OSError, subprocess.SubprocessError):
            return None
        identity = parse_llama_server_version(output)
        if identity is None:
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
