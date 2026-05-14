#!/usr/bin/env bash
# Build a relocatable Python env containing cyllama + the sidecar's deps,
# laid out at build/python-<arch>-<platform>/ for electron-builder to pick up.
#
# Must be run on a host matching the target triple (cyllama links native
# GPU libs at install time, so cross-builds are not supported).
set -euo pipefail

PY_VERSION="${PY_VERSION:-3.12.7}"
PBS_RELEASE="${PBS_RELEASE:-20241016}"
# Pin cyllama so a re-bundle is reproducible. Bump deliberately when a
# new release exposes APIs we want; the renderer auto-adapts to whatever
# fields cyllama.GenerationConfig accepts (see _build_config and /info).
CYLLAMA_VERSION="${CYLLAMA_VERSION:-0.2.15}"

# Detect target triple if not provided.
detect_triple() {
  local os arch
  case "$(uname -s)" in
    Darwin) os="apple-darwin" ;;
    Linux)  os="unknown-linux-gnu" ;;
    MINGW*|MSYS*|CYGWIN*) os="pc-windows-msvc" ;;
    *) echo "Unsupported OS: $(uname -s)" >&2; exit 1 ;;
  esac
  case "$(uname -m)" in
    arm64|aarch64) arch="aarch64" ;;
    x86_64|amd64)  arch="x86_64" ;;
    *) echo "Unsupported arch: $(uname -m)" >&2; exit 1 ;;
  esac
  echo "${arch}-${os}"
}

TRIPLE="${1:-$(detect_triple)}"

# electron-builder uses node-style arch+platform names. Map to our dir.
node_arch() {
  case "$1" in
    aarch64-*) echo "arm64" ;;
    x86_64-*)  echo "x64" ;;
    *) echo "unknown" ;;
  esac
}
node_platform() {
  # Match electron-builder's ${os} token (mac/win/linux), not Node's process.platform.
  case "$1" in
    *-apple-darwin)       echo "mac"   ;;
    *-unknown-linux-gnu)  echo "linux" ;;
    *-pc-windows-msvc)    echo "win"   ;;
    *) echo "unknown" ;;
  esac
}

ARCH_DIR="$(node_arch "$TRIPLE")"
PLAT_DIR="$(node_platform "$TRIPLE")"
OUT="build/python-${PLAT_DIR}-${ARCH_DIR}"

echo "Target triple : $TRIPLE"
echo "Output dir    : $OUT"

rm -rf "$OUT"
mkdir -p "$OUT"

URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_RELEASE}/cpython-${PY_VERSION}+${PBS_RELEASE}-${TRIPLE}-install_only.tar.gz"
echo "Fetching $URL"
curl -fL --retry 3 "$URL" | tar -xz -C "$OUT" --strip-components=1

if [[ "$PLAT_DIR" == "win32" ]]; then
  PY="$OUT/python.exe"
else
  PY="$OUT/bin/python3"
fi

"$PY" -V
"$PY" -m pip install --upgrade pip wheel

# Install cyllama from PyPI. On macOS arm64 this ships with Metal as default.
# To install from a local checkout instead, set CYLLAMA_SOURCE=/path/to/cyllama.
if [[ -n "${CYLLAMA_SOURCE:-}" ]]; then
  echo "Installing cyllama from $CYLLAMA_SOURCE"
  "$PY" -m pip install "$CYLLAMA_SOURCE"
else
  echo "Installing cyllama==${CYLLAMA_VERSION} from PyPI"
  "$PY" -m pip install "cyllama==${CYLLAMA_VERSION}"
fi

# Install remaining sidecar deps from python-sidecar/pyproject.toml so this
# script and the sidecar package stay in sync. cyllama is already installed
# above (pinned/optionally from source), so pip will treat that dep as
# satisfied and only resolve the rest.
"$PY" -m pip install ./python-sidecar

# Smoke test
"$PY" -c "import cyllama, fastapi, uvicorn, openai, anthropic, pypdf, numpy; print('cyllama', cyllama.__version__, '| pypdf', pypdf.__version__, '| numpy', numpy.__version__)"

# Prune to shrink the bundle.
PYLIB_GLOB="$OUT/lib/python${PY_VERSION%.*}"
find "$OUT" -type d -name "__pycache__" -prune -exec rm -rf {} +
find "$OUT" -type d \( -name "tests" -o -name "test" \) -prune -exec rm -rf {} + || true
find "$OUT" -type f -name "*.pyc" -delete || true
rm -rf "$PYLIB_GLOB/idlelib" "$PYLIB_GLOB/tkinter" "$PYLIB_GLOB/turtledemo" "$PYLIB_GLOB/ensurepip" 2>/dev/null || true

du -sh "$OUT"
echo "Done."
