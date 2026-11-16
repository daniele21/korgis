"""Falsify the pinned external-binding patch without native build or model I/O."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "harden_llama_cpp_python_source.py"
spec = importlib.util.spec_from_file_location("harden_llama_cpp_python_source", SCRIPT)
assert spec is not None and spec.loader is not None
patcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patcher)


def _fixture(tmp_path: Path, *, version: str = "0.3.36") -> Path:
    root = tmp_path / "llama_cpp_python-0.3.36"
    package = root / "llama_cpp"
    package.mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        '[project]\ndependencies = [\n'
        '    "numpy>=1.20.0",\n'
        '    "diskcache>=5.6.1",\n'
        ']\n',
        encoding="utf-8",
    )
    (package / "__init__.py").write_text(
        f'from .llama import *\n__version__ = "{version}"\n', encoding="utf-8"
    )
    (package / "llama.py").write_text(
        "from .llama_cache import LlamaDiskCache, LlamaRAMCache\n", encoding="utf-8"
    )
    (package / "llama_cache.py").write_text(
        "import diskcache\n"
        "class LlamaRAMCache:\n"
        "    pass\n"
        "class LlamaDiskCache:\n"
        "    def __init__(self, cache_dir='cache'):\n"
        "        self.cache = diskcache.Cache(cache_dir)\n",
        encoding="utf-8",
    )
    return root


def test_patch_removes_vulnerable_dependency_but_preserves_ram_cache(tmp_path):
    root = _fixture(tmp_path)
    patcher.patch_source(root)

    metadata = (root / "pyproject.toml").read_text()
    init = (root / "llama_cpp" / "__init__.py").read_text()
    code = (root / "llama_cpp" / "llama_cache.py").read_text()

    assert "diskcache" not in metadata
    assert "0.3.36+korgis.1" in init
    assert "import diskcache" not in code
    assert "class LlamaRAMCache" in code

    namespace = {}
    exec(compile(code, "llama_cache.py", "exec"), namespace)
    assert namespace["LlamaRAMCache"] is not None
    with pytest.raises(RuntimeError, match="CVE-2025-69872"):
        namespace["LlamaDiskCache"]()


def test_patch_rejects_unrecognized_source_without_partial_write(tmp_path):
    root = _fixture(tmp_path, version="0.3.37")
    before = (root / "pyproject.toml").read_bytes()
    with pytest.raises(ValueError, match="binding version"):
        patcher.patch_source(root)
    assert (root / "pyproject.toml").read_bytes() == before


def test_patch_refuses_other_diskcache_imports_without_partial_write(tmp_path):
    root = _fixture(tmp_path)
    (root / "llama_cpp" / "unexpected.py").write_text("from diskcache import Cache\n")
    before = (root / "pyproject.toml").read_bytes()
    with pytest.raises(ValueError, match="unexpected diskcache import"):
        patcher.patch_source(root)
    assert (root / "pyproject.toml").read_bytes() == before


def test_patch_never_silently_repatches(tmp_path):
    root = _fixture(tmp_path)
    patcher.patch_source(root)
    with pytest.raises(ValueError, match="diskcache dependency metadata"):
        patcher.patch_source(root)


def test_pinned_upstream_sdist_identity():
    assert patcher.UPSTREAM_VERSION == "0.3.36"
    assert len(patcher.SDIST_SHA256) == 64
    assert patcher.PATCHED_VERSION == "0.3.36+korgis.1"
