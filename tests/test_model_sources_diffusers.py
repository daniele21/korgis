from __future__ import annotations

import json
from pathlib import Path

import pytest

from local_llm_server.model_sources import (
    is_complete_diffusers_model,
    resolve_diffusers_runtime_path,
)


def _write_complete_diffusers_snapshot(path: Path) -> None:
    path.mkdir()
    (path / "model_index.json").write_text(
        json.dumps(
            {
                "_class_name": "QwenImage21Pipeline",
                "transformer": ["diffusers", "QwenImage21Transformer2DModel"],
                "vae": ["diffusers", "AutoencoderKLQwenImage21"],
            }
        ),
        encoding="utf-8",
    )
    for component in ("transformer", "vae"):
        component_path = path / component
        component_path.mkdir()
        (component_path / "model.safetensors").write_bytes(b"weights")


def test_complete_diffusers_snapshot_requires_components_and_weights(tmp_path: Path):
    model = tmp_path / "model"
    _write_complete_diffusers_snapshot(model)

    assert is_complete_diffusers_model(model)

    (model / "vae" / "model.safetensors").unlink()
    assert not is_complete_diffusers_model(model)


def test_diffusers_runtime_accepts_complete_local_snapshot(tmp_path: Path):
    model = tmp_path / "model"
    _write_complete_diffusers_snapshot(model)

    resolved = resolve_diffusers_runtime_path(str(model), no_download=True)

    assert resolved == model.resolve()


def test_diffusers_runtime_fails_closed_for_incomplete_local_snapshot(tmp_path: Path):
    model = tmp_path / "model"
    model.mkdir()
    (model / "model_index.json").write_text("{}", encoding="utf-8")

    with pytest.raises(FileNotFoundError, match="missing or incomplete"):
        resolve_diffusers_runtime_path(str(model), no_download=True)
