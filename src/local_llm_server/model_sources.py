"""Resolve model artifacts consistently across config, CLI, downloads, and engines."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal


SourceType = Literal["explicit", "lmstudio", "managed", "huggingface", "unresolved"]
_MLX_BACKENDS = {"mlx", "mlx_vlm_server"}
_DIFFUSERS_BACKENDS = {"diffusers_image"}
_MFLUX_IMAGE_BACKENDS = {"mflux_image"}
logger = logging.getLogger("local-llm.model_sources")


@dataclass(frozen=True)
class ResolvedModel:
    """One model source resolved without performing network access."""

    model_path: str
    local_path: Path | None
    source_type: SourceType
    downloaded: bool
    mmproj_path: Path | None = None


def is_complete_mlx_model(path: Path, *, multimodal: bool = False) -> bool:
    """Return whether *path* contains a complete, loadable MLX model snapshot."""
    if not path.is_dir() or not (path / "config.json").is_file():
        return False
    if not (path / "tokenizer_config.json").is_file():
        return False
    if multimodal and not any(
        (path / name).is_file()
        for name in ("preprocessor_config.json", "processor_config.json")
    ):
        return False

    # LM Studio can consolidate a sharded repository into model.safetensors
    # while retaining the upstream index, so the consolidated file wins.
    if (path / "model.safetensors").is_file():
        return True

    index_path = path / "model.safetensors.index.json"
    if not index_path.is_file():
        return False
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
        shards = set((index.get("weight_map") or {}).values())
    except (OSError, ValueError, TypeError):
        return False
    return bool(shards) and all((path / str(shard)).is_file() for shard in shards)


def is_complete_diffusers_model(path: Path) -> bool:
    """Return whether *path* looks like a complete local Diffusers snapshot."""
    if not path.is_dir() or not (path / "model_index.json").is_file():
        return False
    try:
        index = json.loads((path / "model_index.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return False

    component_names = [
        str(name)
        for name, value in index.items()
        if not str(name).startswith("_")
        and isinstance(value, list)
        and len(value) >= 2
    ]
    if not component_names:
        return False
    if any(not (path / name).exists() for name in component_names):
        return False

    weight_class_markers = (
        "Model",
        "Transformer",
        "Autoencoder",
        "UNet",
        "ForConditionalGeneration",
    )
    weighted_components = [
        str(name)
        for name, value in index.items()
        if not str(name).startswith("_")
        and isinstance(value, list)
        and len(value) >= 2
        and any(marker in str(value[1]) for marker in weight_class_markers)
    ]
    if not weighted_components:
        return False
    for name in weighted_components:
        component = path / name
        if not any(component.rglob("*.safetensors")) and not any(
            component.rglob("*.bin")
        ):
            return False
    return True


def _indexed_weight_component_metadata(path: Path) -> dict[str, Any] | None:
    index_path = path / "model.safetensors.index.json"
    if not index_path.is_file():
        return None
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
        weight_map = index.get("weight_map")
        shards = set(weight_map.values()) if isinstance(weight_map, dict) else set()
    except (OSError, ValueError, TypeError):
        return None
    if not shards or not all(
        isinstance(shard, str)
        and Path(shard).name == shard
        and (path / shard).is_file()
        for shard in shards
    ):
        return None
    metadata = index.get("metadata")
    return dict(metadata) if isinstance(metadata, dict) else {}


def mflux_image_quantization_bits(path: Path) -> int | None:
    """Return one consistent stored MFlux quantization level, or None."""
    levels: set[int] = set()
    for component in ("transformer", "text_encoder", "vae"):
        metadata = _indexed_weight_component_metadata(path / component)
        if metadata is None:
            return None
        raw = metadata.get("quantization_level")
        try:
            levels.add(int(raw))
        except (TypeError, ValueError):
            return None
    return next(iter(levels)) if len(levels) == 1 else None


def is_complete_mflux_image_model(
    path: Path,
    *,
    expected_quantization_bits: int | None = None,
) -> bool:
    """Return whether *path* is a complete MFlux-saved Qwen image checkpoint."""
    if not path.is_dir():
        return False
    stored_bits = mflux_image_quantization_bits(path)
    if stored_bits is None:
        return False
    if (
        expected_quantization_bits is not None
        and stored_bits != expected_quantization_bits
    ):
        return False

    processor = path / "processor"
    if not processor.is_dir():
        return False
    return any(
        (processor / name).is_file()
        for name in (
            "tokenizer.json",
            "tokenizer_config.json",
            "vocab.json",
        )
    )


def _looks_like_local_path(reference: str) -> bool:
    expanded = Path(reference).expanduser()
    return (
        expanded.exists()
        or reference.startswith(("/", "./", "../", "~"))
        or expanded.suffix.lower() in {".gguf", ".safetensors"}
    )


def _is_complete_local(path: Path, backend: str, *, multimodal: bool) -> bool:
    if backend in _MLX_BACKENDS:
        return is_complete_mlx_model(path, multimodal=multimodal)
    if backend in _DIFFUSERS_BACKENDS:
        return is_complete_diffusers_model(path)
    if backend in _MFLUX_IMAGE_BACKENDS:
        return is_complete_mflux_image_model(path)
    return path.is_file()


def _cached_huggingface_snapshot(
    repo_id: str,
    *,
    backend: str,
    multimodal: bool,
) -> Path | None:
    try:
        from huggingface_hub import snapshot_download
        snapshot = Path(snapshot_download(repo_id=repo_id, local_files_only=True))
    except Exception:
        # Cache inspection is deliberately best-effort and offline. Missing
        # optional dependencies and incomplete cache entries both mean absent.
        return None
    return snapshot if _is_complete_local(snapshot, backend, multimodal=multimodal) else None


def resolve_registry_model(
    key: str,
    entry: dict[str, Any],
    models_dir: Path,
    *,
    backend: str | None = None,
    explicit_path: str | None = None,
) -> ResolvedModel:
    """Resolve a registry entry locally without downloading or contacting the network."""
    resolved_backend = str(backend or entry.get("backend") or "llama_cpp")
    multimodal = bool(entry.get("multimodal", False))

    if explicit_path is not None:
        reference = str(explicit_path)
        if _looks_like_local_path(reference):
            path = Path(reference).expanduser().resolve()
            return ResolvedModel(
                str(path), path, "explicit",
                _is_complete_local(path, resolved_backend, multimodal=multimodal),
            )
        cached = _cached_huggingface_snapshot(
            reference, backend=resolved_backend, multimodal=multimodal
        )
        return ResolvedModel(
            str(cached) if cached else reference,
            cached,
            "huggingface",
            cached is not None,
        )

    configured_path = entry.get("path")
    if configured_path:
        path = Path(str(configured_path)).expanduser().resolve()
        return ResolvedModel(
            str(path), path, "explicit",
            _is_complete_local(path, resolved_backend, multimodal=multimodal),
        )

    lmstudio_key = entry.get("lmstudio_path")
    if lmstudio_key:
        root = Path.home() / ".lmstudio" / "models" / str(lmstudio_key)
        candidate = root / str(entry["filename"]) if entry.get("filename") else root
        if _is_complete_local(candidate, resolved_backend, multimodal=multimodal):
            mmproj = root / str(entry["mmproj_filename"]) if entry.get("mmproj_filename") else None
            mmproj_ready = mmproj is None or mmproj.is_file()
            if mmproj_ready:
                return ResolvedModel(str(candidate), candidate, "lmstudio", True, mmproj)

    filename = entry.get("filename")
    if filename:
        candidate = models_dir / str(filename)
        mmproj = models_dir / str(entry["mmproj_filename"]) if entry.get("mmproj_filename") else None
        downloaded = _is_complete_local(
            candidate, resolved_backend, multimodal=multimodal
        ) and (mmproj is None or mmproj.is_file())
        return ResolvedModel(str(candidate), candidate, "managed", downloaded, mmproj)

    reference = str(entry.get("model_id") or key)
    cached = _cached_huggingface_snapshot(
        reference, backend=resolved_backend, multimodal=multimodal
    )
    return ResolvedModel(
        str(cached) if cached else reference,
        cached,
        "huggingface" if entry.get("model_id") else "unresolved",
        cached is not None,
    )


def resolve_mlx_runtime_path(
    reference: str,
    *,
    no_download: bool,
    multimodal: bool,
) -> Path:
    """Resolve/download an MLX reference before starting its backend process."""
    if _looks_like_local_path(reference):
        path = Path(reference).expanduser().resolve()
        if is_complete_mlx_model(path, multimodal=multimodal):
            return path
        raise FileNotFoundError(f"MLX model directory is missing or incomplete: {path}")

    cached = _cached_huggingface_snapshot(
        reference,
        backend="mlx_vlm_server" if multimodal else "mlx",
        multimodal=multimodal,
    )
    if cached is not None:
        logger.info("Using complete Hugging Face cache for %s: %s", reference, cached)
        return cached
    if no_download:
        raise FileNotFoundError(
            f"Model '{reference}' is not fully cached and --no-download is set. "
            "Run 'local-llm download <model>' first."
        )

    try:
        from huggingface_hub import snapshot_download
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Hugging Face downloads require the vision/MLX dependencies. "
            'Install with: pip install "local-llm-server[vision]"'
        ) from exc
    logger.info("Downloading Hugging Face model before backend startup: %s", reference)
    try:
        path = Path(snapshot_download(repo_id=reference))
    except Exception as exc:
        raise RuntimeError(
            f"Failed to download Hugging Face model '{reference}': {exc}"
        ) from exc
    if not is_complete_mlx_model(path, multimodal=multimodal):
        raise RuntimeError(f"Downloaded MLX snapshot is incomplete: {path}")
    return path


def resolve_diffusers_runtime_path(
    reference: str,
    *,
    no_download: bool,
) -> Path:
    """Resolve/download a Diffusers repository before image backend startup."""
    if _looks_like_local_path(reference):
        path = Path(reference).expanduser().resolve()
        if is_complete_diffusers_model(path):
            return path
        raise FileNotFoundError(
            f"Diffusers model directory is missing or incomplete: {path}"
        )

    cached = _cached_huggingface_snapshot(
        reference,
        backend="diffusers_image",
        multimodal=False,
    )
    if cached is not None:
        logger.info("Using complete Hugging Face cache for %s: %s", reference, cached)
        return cached
    if no_download:
        raise FileNotFoundError(
            f"Model '{reference}' is not fully cached and --no-download is set. "
            "Run 'local-llm download <model>' first."
        )

    try:
        from huggingface_hub import snapshot_download
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Diffusers image model downloads require the image-generation dependencies. "
            'Install with: python -m pip install -r requirements/image.txt'
        ) from exc

    logger.info("Downloading Hugging Face image model before backend startup: %s", reference)
    try:
        path = Path(snapshot_download(repo_id=reference))
    except Exception as exc:
        raise RuntimeError(
            f"Failed to download Hugging Face image model '{reference}': {exc}"
        ) from exc
    if not is_complete_diffusers_model(path):
        raise RuntimeError(f"Downloaded Diffusers snapshot is incomplete: {path}")
    return path



def resolve_mflux_image_runtime_path(
    reference: str,
    *,
    no_download: bool,
    expected_quantization_bits: int | None = None,
) -> Path:
    """Resolve/download a complete MFlux image checkpoint before runtime startup."""
    if _looks_like_local_path(reference):
        path = Path(reference).expanduser().resolve()
        if is_complete_mflux_image_model(
            path,
            expected_quantization_bits=expected_quantization_bits,
        ):
            return path
        raise FileNotFoundError(
            f"MFlux image model directory is missing or incomplete: {path}"
        )

    cached = _cached_huggingface_snapshot(
        reference,
        backend="mflux_image",
        multimodal=False,
    )
    if cached is not None:
        logger.info("Using complete Hugging Face MFlux cache for %s: %s", reference, cached)
        return cached
    if no_download:
        raise FileNotFoundError(
            f"Model '{reference}' is not fully cached and --no-download is set. "
            "Run 'local-llm download <model>' first."
        )

    try:
        from huggingface_hub import snapshot_download
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "MFlux image model downloads require the optional MLX image dependencies. "
            "Install with: python -m pip install -r requirements/image-mlx.txt"
        ) from exc

    logger.info("Downloading Hugging Face MFlux image model before backend startup: %s", reference)
    try:
        path = Path(snapshot_download(repo_id=reference))
    except Exception as exc:
        raise RuntimeError(
            f"Failed to download Hugging Face MFlux image model '{reference}': {exc}"
        ) from exc
    if not is_complete_mflux_image_model(
        path,
        expected_quantization_bits=expected_quantization_bits,
    ):
        expected = (
            f" with Q{expected_quantization_bits} metadata"
            if expected_quantization_bits is not None
            else ""
        )
        raise RuntimeError(
            f"Downloaded MFlux image snapshot is incomplete or mismatched{expected}: {path}"
        )
    return path
