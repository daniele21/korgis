#!/usr/bin/env python3
"""Patch a verified llama-cpp-python 0.3.36 source tree for a no-disk-cache build.

Source archive authenticity must be checked against the pinned PyPI SHA-256
*before* extraction. This command modifies only an isolated, temporary copy;
it never patches a user environment or changes Korgis' dependency lock.
"""
from __future__ import annotations

import argparse
import ast
from pathlib import Path

UPSTREAM_VERSION = "0.3.36"
PATCHED_VERSION = "0.3.36+korgis.1"
SDIST_SHA256 = "832db0699007f1be95a7e41ef12e88926b02ba836461e36a36372db2760c1a2e"


def _replace_once(text: str, before: str, after: str, description: str) -> str:
    if text.count(before) != 1:
        raise ValueError(f"unexpected upstream {description}; refusing to patch")
    return text.replace(before, after, 1)


def patch_source(source_root: Path) -> None:
    """Fail closed on unknown upstream layout; preserve inference/cache-RAM API."""
    source_root = source_root.resolve(strict=True)
    metadata = source_root / "pyproject.toml"
    init = source_root / "llama_cpp" / "__init__.py"
    cache = source_root / "llama_cpp" / "llama_cache.py"

    originals = [path.read_text(encoding="utf-8") for path in (metadata, init, cache)]
    project = _replace_once(
        originals[0], '    "diskcache>=5.6.1",\n', "",
        "diskcache dependency metadata",
    )
    version = _replace_once(
        originals[1], f'__version__ = "{UPSTREAM_VERSION}"',
        f'__version__ = "{PATCHED_VERSION}"', "binding version",
    )
    source = _replace_once(originals[2], "import diskcache\n", "", "diskcache import")
    source = _replace_once(
        source,
        "        self.cache = diskcache.Cache(cache_dir)",
        '        raise RuntimeError(\n'
        '            "LlamaDiskCache is disabled in the Korgis hardened binding '\
        '(CVE-2025-69872)"\n'
        '        )',
        "disk-backed cache constructor",
    )

    # Guard against importing the vulnerable package elsewhere in the same
    # upstream distribution (not just the currently known llama_cache module).
    for path in sorted((source_root / "llama_cpp").rglob("*.py")):
        data = source if path == cache else path.read_text(encoding="utf-8")
        tree = ast.parse(data, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(
                entry.name == "diskcache" or entry.name.startswith("diskcache.")
                for entry in node.names
            ):
                raise ValueError(f"unexpected diskcache import in {path}")
            if isinstance(node, ast.ImportFrom) and (
                node.module == "diskcache" or (node.module or "").startswith("diskcache.")
            ):
                raise ValueError(f"unexpected diskcache import in {path}")

    # Validate all changes before touching any source file.
    for path, contents in zip((metadata, init, cache), (project, version, source)):
        path.write_text(contents, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="isolated, SHA-verified unpacked sdist root")
    args = parser.parse_args()
    patch_source(args.source)
    print(f"PATCHED llama-cpp-python {PATCHED_VERSION}; disk-cache disabled")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
