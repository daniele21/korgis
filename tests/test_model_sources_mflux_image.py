from __future__ import annotations

import json
from pathlib import Path

import pytest

from local_llm_server.model_sources import (
    is_complete_mflux_image_model,
    resolve_mflux_image_runtime_path,
)


def _write_component(path: Path, *, include_shard: bool = True) -> None:
    path.mkdir(parents=True)
    shard = "0.safetensors"
    if include_shard:
        (path / shard).write_bytes(b"weights")
    (path / "model.safetensors.index.json").write_text(
        json.dumps(
            {
                "metadata": {
                    "quantization_level": "8",
                    "mflux_version": "0.20.0",
                },
                "weight_map": {"layer.weight": shard},
            }
        ),
        encoding="utf-8",
    )


def _write_snapshot(path: Path) -> None:
    for component in ("transformer", "text_encoder", "vae"):
        _write_component(path / component)
    processor = path / "processor"
    processor.mkdir()
    (processor / "tokenizer_config.json").write_text("{}", encoding="utf-8")


def test_complete_mflux_image_snapshot_requires_all_indexed_shards(tmp_path: Path):
    model = tmp_path / "model"
    _write_snapshot(model)

    assert is_complete_mflux_image_model(model)

    (model / "transformer" / "0.safetensors").unlink()
    assert not is_complete_mflux_image_model(model)


def test_complete_mflux_image_snapshot_requires_processor(tmp_path: Path):
    model = tmp_path / "model"
    _write_snapshot(model)
    (model / "processor" / "tokenizer_config.json").unlink()

    assert not is_complete_mflux_image_model(model)


def test_mflux_image_runtime_accepts_complete_local_snapshot(tmp_path: Path):
    model = tmp_path / "model"
    _write_snapshot(model)

    resolved = resolve_mflux_image_runtime_path(str(model), no_download=True)

    assert resolved == model.resolve()


def test_mflux_image_runtime_fails_closed_for_incomplete_snapshot(tmp_path: Path):
    model = tmp_path / "model"
    model.mkdir()

    with pytest.raises(FileNotFoundError, match="missing or incomplete"):
        resolve_mflux_image_runtime_path(str(model), no_download=True)
