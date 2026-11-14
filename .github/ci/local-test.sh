#!/usr/bin/env bash
# Bounded deterministic validation for central Mac CI.
# This is NOT the complete risk-based Korgis CI (matrix, UI, security, release).
set -euo pipefail

python3 scripts/verify_repository.py --skip-specialists
python3 scripts/verify_operations.py
python3 scripts/verify_e2e.py

# Native v1 Mac CI lane. Existing Korgis services, models and global tools
# are never modified; builds are confined to runner-owned temporary storage.
[[ "$(uname -s)" == Darwin && "$(uname -m)" == arm64 ]] || {
  echo "::error::Real Apple Silicon macOS required"; exit 2;
}
[[ "$SOURCE_REPO" == daniele21/korgis &&
   "$SOURCE_SHA" =~ ^[a-f0-9]{40}$ &&
   "$(git rev-parse HEAD)" == "$SOURCE_SHA" ]] || {
  echo "::error::Untrusted or mismatched source"; exit 2;
}
[[ -n "$RUNNER_TEMP" && "$RUNNER_TEMP" == /* ]] || {
  echo "::error::Runner-owned scratch required"; exit 2;
}
for tool in git shasum xcrun curl tar; do
  command -v "$tool" >/dev/null || {
    echo "::error::Missing native tool: $tool"; exit 2;
  }
done
disk_kib="$(df -Pk "$RUNNER_TEMP" | awk 'NR==2 {print $4}')"
[[ "$disk_kib" =~ ^[0-9]+$ && "$disk_kib" -ge 8388608 ]] || {
  echo "::error::Need at least 8 GiB free scratch disk"; exit 2;
}
python=""
for candidate in python3.12 python3.11 python3.10 python3; do
  command -v "$candidate" >/dev/null 2>&1 || continue
  if "$candidate" -c 'import sys; assert sys.version_info >= (3, 10)' 2>/dev/null; then
    python="$candidate"
    break
  fi
done
[[ -n "$python" ]] || {
  echo "::error::Python >=3.10 missing"; exit 2;
}

work="$(mktemp -d "$RUNNER_TEMP/korgis-llama-v1.XXXXXXXX")"
cleanup() { rm -rf "$work"; }
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# Native installer requires CMake. Some shared Mac runner registrations omit
# Homebrew from PATH. Bootstrap a versioned portable CMake in scratch, after
# validating the upstream-published checksum; never use sudo/global installs.
if ! command -v cmake >/dev/null 2>&1; then
  cmake_version="3.31.6"
  cmake_dist="cmake-$cmake_version-macos-universal.tar.gz"
  cmake_url="https://github.com/Kitware/CMake/releases/download/v$cmake_version"
  mkdir -p "$work/cmake"
  if ! curl --fail --location --retry 2 --silent --show-error \
      "$cmake_url/$cmake_dist" -o "$work/$cmake_dist" ||
     ! curl --fail --location --retry 2 --silent --show-error \
      "$cmake_url/cmake-$cmake_version-SHA-256.txt" -o "$work/cmake-sha256.txt"; then
    echo "::error::Pinned portable CMake bootstrap unavailable"; exit 2
  fi
  expected="$(awk -v file="$cmake_dist" '$2 == file || $2 == "*" file {print $1}' "$work/cmake-sha256.txt")"
  actual="$(shasum -a 256 "$work/$cmake_dist" | awk '{print $1}')"
  [[ "$expected" =~ ^[0-9A-Fa-f]{64}$ && "$actual" == "$expected" ]] || {
    echo "::error::Pinned portable CMake SHA-256 verification failed"; exit 2;
  }
  tar -xzf "$work/$cmake_dist" -C "$work/cmake" --strip-components=1
  portable="$work/cmake/CMake.app/Contents/bin/cmake"
  [[ -x "$portable" ]] || {
    echo "::error::Portable CMake executable missing"; exit 2;
  }
  export PATH="$(dirname "$portable"):$PATH"
fi
cmake --version | head -1

# The source installer pins upstream commit, uses static Metal linking and
# validates the relocated CLI before promotion within this isolated root.
if ! bash scripts/install_llama_cpp_v1_macos.sh \
    --root "$work/engines" > "$work/build.log" 2>&1; then
  echo "::error::Pinned llama.cpp v1 Mac build failed (local log only)"
  exit 1
fi
binary="$work/engines/v1.0.0-2d8a325/bin/llama-server"
[[ -x "$binary" ]] || { echo "::error::Binary missing"; exit 1; }

# Force a native Metal Python binding build in a fresh, private env.
# Do not modify system Homebrew, PATH, user venvs or model artifacts.
if command -v uv >/dev/null 2>&1 && [[ "$(uv --version)" == "uv 0.8.13" ]]; then
  uv_bin="$(command -v uv)"
else
  "$python" -m venv "$work/bootstrap"
  if ! "$work/bootstrap/bin/python" -m pip -q install 'uv==0.8.13' \
      > "$work/bootstrap.log" 2>&1; then
    echo "::error::Pinned uv bootstrap failed"; exit 1
  fi
  uv_bin="$work/bootstrap/bin/uv"
fi
export UV_PROJECT_ENVIRONMENT="$work/venv"
export CMAKE_ARGS="-DGGML_METAL=on"
export FORCE_CMAKE=1
export UV_NO_PROGRESS=1
if ! "$uv_bin" sync --frozen --extra dev \
    --no-binary-package llama-cpp-python > "$work/sync.log" 2>&1; then
  echo "::error::Locked Metal llama-cpp-python build failed"; exit 1
fi

"$work/venv/bin/python" -m pytest -q \
  tests/test_llama_cpp_thinking.py \
  tests/test_engine_llama_cpp_lifecycle.py \
  tests/test_llama_server_compat.py \
  tests/test_llama_server_v1.py \
  tests/test_engine_llama_server_v1.py \
  tests/test_llama_cpp_v1_macos_installer.py

export KORGIS_PROBE_BINARY="$binary"
"$work/venv/bin/python" - <<'PY'
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
from local_llm_server.llama_server_compat import validate_llama_server_binary

binary = Path(os.environ["KORGIS_PROBE_BINARY"])
manifest = json.loads((binary.parent.parent / "build-identity.json").read_text())
digest = hashlib.sha256()
with binary.open("rb") as handle:
    for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
        digest.update(chunk)
assert manifest["binary_sha256"] == digest.hexdigest()
assert manifest["source_commit"] == "2d8a32502909ca065c41c4cf5f2fb1c427c047fc"
compat = validate_llama_server_binary(binary)
assert compat.exact_v1_release and compat.modern_runtime_options
import llama_cpp
assert hasattr(llama_cpp, "Llama")
binding = importlib.metadata.version("llama-cpp-python")
assert binding == "0.3.36"
print(json.dumps({
    "schema": "korgis-llama-v1-mac-native-smoke/v1",
    "source_sha": os.environ["SOURCE_SHA"],
    "llama_commit": manifest["source_commit"],
    "binary_sha256": digest.hexdigest(),
    "binding_version": binding,
    "backend_profile": compat.profile,
    "mac_native_build": "pass",
    "real_gguf_inference": "not_run",
    "swap_and_reclamation": "not_measured",
}, sort_keys=True))
PY
