#!/usr/bin/env bash
# Build a pinned llama.cpp v1 Metal backend; never switch an active runtime.
set -euo pipefail

UPSTREAM="https://github.com/ggml-org/llama.cpp.git"
SOURCE_COMMIT="2d8a32502909ca065c41c4cf5f2fb1c427c047fc"
RELEASE="v1.0.0"
SHORT_COMMIT="2d8a325"
ROOT="$HOME/.local-llm/engines/llama-cpp"

usage() {
  cat <<'EOF'
Usage: bash scripts/install_llama_cpp_v1_macos.sh [--root ABSOLUTE_DIRECTORY]
Build pinned llama.cpp v1.0.0 with Metal on Apple Silicon into a versioned directory.
Requires Xcode Command Line Tools, git, and CMake.
Never overwrite an existing binary, activate it, change PATH or restart Korgis.
Select LOCAL_LLM_SERVER_BIN explicitly; rollback to the previous verified binary.
EOF
}
while [[ $# -gt 0 ]]; do
  case "$1" in
    --root)
      if [[ $# -lt 2 || "$2" != /* ]]; then
        echo "Error: --root requires an absolute directory." >&2
        exit 2
      fi
      ROOT="$2"
      shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Error: unexpected argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
  echo "Error: the Metal build requires Apple Silicon macOS." >&2
  exit 1
fi
for tool in git cmake shasum; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "Error: missing required tool: $tool" >&2
    exit 1
  fi
done

DESTINATION="$ROOT/$RELEASE-$SHORT_COMMIT"
mkdir -p "$ROOT"
LOCK="$ROOT/.llama-v1-install.lock"
if ! mkdir "$LOCK" 2>/dev/null; then
  echo "Error: another installation owns this destination root; refusing to proceed." >&2
  exit 1
fi
SCRATCH=""
STAGE=""
cleanup() {
  if [[ -n "$SCRATCH" ]]; then rm -rf "$SCRATCH"; fi
  if [[ -n "$STAGE" ]]; then rm -rf "$STAGE"; fi
  rmdir "$LOCK" 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
if [[ -e "$DESTINATION" || -L "$DESTINATION" ]]; then
  echo "Error: destination already exists; refusing to overwrite: $DESTINATION" >&2
  exit 1
fi
SCRATCH="$(mktemp -d)"
STAGE="$(mktemp -d "$ROOT/.llama-v1-staging.XXXXXXXX")"

SOURCE="$SCRATCH/upstream"
BUILD="$SCRATCH/build"
git init --quiet "$SOURCE"
git -C "$SOURCE" remote add origin "$UPSTREAM"
git -C "$SOURCE" fetch --quiet --depth 1 origin "$SOURCE_COMMIT"
git -C "$SOURCE" checkout --quiet --detach FETCH_HEAD
test "$(git -C "$SOURCE" rev-parse HEAD)" = "$SOURCE_COMMIT"
git -C "$SOURCE" submodule update --init --recursive --depth 1

cmake -S "$SOURCE" -B "$BUILD" \
  -DCMAKE_BUILD_TYPE=Release \
  -DLLAMA_BUILD_TESTS=OFF \
  -DLLAMA_BUILD_SERVER=ON \
  -DLLAMA_BUILD_IS_DEV=OFF \
  -DGGML_METAL=ON \
  -DGGML_METAL_EMBED_LIBRARY=ON
cmake --build "$BUILD" --config Release --target llama-server --parallel 3

# Keep adjacent native libraries/resources: an isolated executable alone may
# fail @rpath resolution on macOS.
cp -R "$BUILD/bin" "$STAGE/bin"
BINARY="$STAGE/bin/llama-server"
test -x "$BINARY"
VERSION="$("$BINARY" --version 2>&1)"
HELP="$("$BINARY" --help 2>&1)"
if [[ "$VERSION" != *1.0.0* || "$VERSION" != *"$SHORT_COMMIT"* ]]; then
  echo "Error: built binary lacks exact v1.0.0 source attribution." >&2
  exit 1
fi
for flag in --parallel --gpu-layers --cache-type-k --cache-type-v \
  --moe-cache-mib --moe-cache-cpu-share; do
  if [[ "$HELP" != *"$flag"* ]]; then
    echo "Error: built binary is missing expected CLI flag $flag." >&2
    exit 1
  fi
done
if [[ "$HELP" != *f8_e4m3* ]]; then
  echo "Error: built binary does not advertise FP8 KV." >&2
  exit 1
fi

HASH="$(shasum -a 256 "$BINARY" | awk '{print $1}')"
printf '{"upstream":"ggml-org/llama.cpp","tag":"%s","source_commit":"%s","binary_sha256":"%s","backend":"metal"}\n' \
  "$RELEASE" "$SOURCE_COMMIT" "$HASH" > "$STAGE/build-identity.json"

# Same-filesystem rename: atomic promotion, no PATH/config/runtime changes.
mv "$STAGE" "$DESTINATION"
echo "Installed attributable llama.cpp $RELEASE ($SHORT_COMMIT)."
echo "Binary SHA-256: $HASH"
printf 'Select explicitly: export LOCAL_LLM_SERVER_BIN="%s/bin/llama-server"\n' "$DESTINATION"
echo "Native build/CLI proof is not real GGUF inference or memory evidence."
