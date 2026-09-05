#!/usr/bin/env bash
# Build a relocatable Python env containing cyllama + the sidecar's deps,
# laid out at build/python-<arch>-<platform>/ for electron-builder to pick up.
#
# Must be run on a host matching the target triple (cyllama links native
# GPU libs at install time, so cross-builds are not supported).
set -euo pipefail

# cyllama >= 0.3.0 publishes cp312-abi3 wheels only, so PY_VERSION must
# stay >= 3.12.
PY_VERSION="${PY_VERSION:-3.12.7}"
PBS_RELEASE="${PBS_RELEASE:-20241016}"
# Pin cyllama so a re-bundle is reproducible. Bump deliberately when a
# new release exposes APIs we want; the renderer auto-adapts to whatever
# fields cyllama.GenerationConfig accepts (see _build_config and /info).
CYLLAMA_VERSION="${CYLLAMA_VERSION:-0.4.4}"

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

# Which cyllama distribution to install. cyllama ships the same import
# package under one distribution name per backend (cyllama,
# cyllama-cuda12, cyllama-vulkan, cyllama-rocm, cyllama-sycl), and
# python-sidecar/pyproject.toml is the single place that records which
# one this build wants -- set it with scripts/set-cyllama-variant.py.
# Reading it back from there rather than taking it as an argument keeps
# the wheel we install and the sidecar's own dependency metadata in
# agreement; if they disagreed, the "pip install ./python-sidecar" below
# would pull the *other* distribution in on top of this one, and two
# distributions owning the same cyllama/ directory is exactly the
# clobbering this avoids.
#
# Resolved up front, before the ~30 MB runtime download, so a bad
# combination fails in a second rather than a minute.
CYLLAMA_DIST="$(sed -nE 's/^[[:space:]]*"(cyllama(-[a-z0-9]+)?)[^"]*",[[:space:]]*$/\1/p' \
  python-sidecar/pyproject.toml | head -1)"
if [[ -z "$CYLLAMA_DIST" ]]; then
  echo "error: no cyllama dependency found in python-sidecar/pyproject.toml" >&2
  exit 1
fi

# A source build installs under the plain "cyllama" name whatever backend
# it was compiled with, so it cannot satisfy a variant pin.
if [[ -n "${CYLLAMA_SOURCE:-}" && "$CYLLAMA_DIST" != "cyllama" ]]; then
  echo "error: CYLLAMA_SOURCE builds install as 'cyllama', but" >&2
  echo "       python-sidecar/pyproject.toml pins '${CYLLAMA_DIST}'." >&2
  echo "       Run 'make variant-cpu' first to build from a local" >&2
  echo "       checkout (its own build flags choose the backend), or" >&2
  echo "       drop CYLLAMA_SOURCE to install the ${CYLLAMA_DIST} wheel." >&2
  exit 1
fi
echo "cyllama distribution: ${CYLLAMA_DIST}"


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


# Install cyllama. On macOS arm64 the default distribution ships with
# Metal enabled, so no variant is needed there.
# To install from a local checkout instead, set CYLLAMA_SOURCE=/path/to/cyllama.
if [[ -n "${CYLLAMA_SOURCE:-}" ]]; then
  echo "Installing cyllama from $CYLLAMA_SOURCE"
  "$PY" -m pip install "$CYLLAMA_SOURCE"
else
  echo "Installing ${CYLLAMA_DIST}==${CYLLAMA_VERSION} from PyPI"
  "$PY" -m pip install "${CYLLAMA_DIST}==${CYLLAMA_VERSION}"
fi

# Linux source builds: link libgomp into the extensions if the build
# host failed to.
#
# cyllama's CMake links OpenMP::OpenMP_CXX on Linux, but only when
# find_package(OpenMP) succeeds -- and it degrades silently when it does
# not. If the prebuilt ggml archives in thirdparty/*/lib were compiled
# with OpenMP while the extension's own configure could not detect it
# (the usual cause: CMake picks clang and libomp-dev is not installed,
# while the archives were built with gcc), the extensions end up with
# undefined omp_* / GOMP_* symbols and no matching DT_NEEDED entry, and
# every entry point dies at "import cyllama" with
# "undefined symbol: omp_get_thread_num".
#
# The published wheels are not affected -- they are built where OpenMP
# is detected, so they link libgomp themselves and auditwheel then
# vendors it as libgomp-<hash>.so.1 (auditwheel rewrites existing
# DT_NEEDED entries; it never adds a missing one, so it would not have
# saved such a build either).
#
# The real fix is to install OpenMP dev headers for the compiler CMake
# selects, or point CMake at one that has them, and rebuild cyllama.
# This is a backstop so a host in that state still produces an
# importable env: vendor its libgomp beside the package and point the
# affected extensions at it via DT_NEEDED + an $ORIGIN-relative RPATH,
# keeping the env self-contained rather than dependent on the build
# host at run time. No-op on a correctly linked build.
#
# Only source installs on Linux can land in this state; macOS/Windows
# link OpenMP differently.
if [[ -n "${CYLLAMA_SOURCE:-}" && "$PLAT_DIR" == "linux" ]]; then
  if ! command -v patchelf >/dev/null 2>&1; then
    echo "warn: patchelf not found -- skipping the libgomp backstop." >&2
    echo "      Harmless if cyllama linked OpenMP correctly. If the smoke" >&2
    echo "      test below fails with 'undefined symbol: omp_get_thread_num'," >&2
    echo "      install OpenMP dev headers for the compiler CMake picked" >&2
    echo "      (e.g. apt install libomp-dev for clang) and rebuild cyllama," >&2
    echo "      or install patchelf and re-run to apply the backstop." >&2
  else
    "$PY" - <<'PYREPAIR'
import os
import shutil
import subprocess
import sysconfig
from ctypes.util import find_library
from pathlib import Path

pkg = Path(sysconfig.get_paths()["purelib"]) / "cyllama"
if not pkg.is_dir():
    raise SystemExit("cyllama package not found; nothing to repair")


# Sonames that provide the OpenMP runtime. GNU's libgomp is one of
# three: clang links LLVM's libomp, and Intel's is libiomp5. All export
# the GOMP_* entry points, so any of them satisfies the references and
# only their total absence is a problem. Matching on "libgomp" alone
# would call a correctly linked clang build broken and then load a
# second OpenMP runtime into the same process alongside the first --
# a documented route to deadlocks and crashes, and strictly worse than
# doing nothing.
_OPENMP_SONAMES = ("libgomp", "libomp", "libiomp5")


def needs_gomp(so: Path) -> bool:
    """True if *so* references OpenMP symbols but links no OpenMP runtime."""
    try:
        undef = subprocess.run(
            ["nm", "-D", "--undefined-only", str(so)],
            capture_output=True, text=True, check=True,
        ).stdout
        needed = subprocess.run(
            ["patchelf", "--print-needed", str(so)],
            capture_output=True, text=True, check=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False
    uses_omp = any(
        line.split()[-1].startswith(("omp_", "GOMP_"))
        for line in undef.splitlines() if line.strip()
    )
    linked = any(
        Path(entry.strip()).name.startswith(_OPENMP_SONAMES)
        for entry in needed.splitlines() if entry.strip()
    )
    return uses_omp and not linked


targets = [so for so in pkg.rglob("*.so") if needs_gomp(so)]
if not targets:
    print("libgomp repair: nothing to do")
    raise SystemExit(0)

# Resolve the host libgomp. ctypes finds it via the same ldconfig cache
# the loader uses, so this matches what the extensions would have bound
# to anyway; ldconfig -p is the fallback for stripped-down images.
soname = find_library("gomp") or "libgomp.so.1"
src = None
if os.path.isabs(soname):
    src = Path(soname)
else:
    out = subprocess.run(
        ["ldconfig", "-p"], capture_output=True, text=True,
    ).stdout
    for line in out.splitlines():
        if soname in line and "=>" in line:
            src = Path(line.split("=>")[-1].strip())
            break
if src is None or not src.exists():
    raise SystemExit(f"could not locate {soname} on this host")

libdir = pkg / ".libs-local"
libdir.mkdir(exist_ok=True)
dst = libdir / "libgomp.so.1"
if not dst.exists():
    shutil.copy2(src.resolve(), dst)
    print(f"libgomp repair: vendored {src} -> {dst}")

for so in targets:
    rel = os.path.relpath(libdir, so.parent)
    rpath = f"$ORIGIN/{rel}" if rel != "." else "$ORIGIN"
    existing = subprocess.run(
        ["patchelf", "--print-rpath", str(so)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    # Prepend ours, keeping whatever the build already set.
    parts = [rpath] + [p for p in existing.split(":") if p and p != rpath]
    subprocess.run(
        ["patchelf", "--add-needed", "libgomp.so.1", str(so)], check=True)
    subprocess.run(
        ["patchelf", "--set-rpath", ":".join(parts), str(so)], check=True)
    print(f"libgomp repair: patched {so.relative_to(pkg)}")
PYREPAIR
  fi
fi

# Install remaining sidecar deps from python-sidecar/pyproject.toml so this
# script and the sidecar package stay in sync. cyllama is already installed
# above (pinned/optionally from source), so pip will treat that dep as
# satisfied and only resolve the rest.
"$PY" -m pip install ./python-sidecar

# Smoke test
"$PY" -c "import cyllama, fastapi, uvicorn, openai, anthropic, pypdf; print('cyllama', cyllama.__version__, '| pypdf', pypdf.__version__)"

# Prune to shrink the bundle.
PYLIB_GLOB="$OUT/lib/python${PY_VERSION%.*}"
find "$OUT" -type d -name "__pycache__" -prune -exec rm -rf {} +
find "$OUT" -type d \( -name "tests" -o -name "test" \) -prune -exec rm -rf {} + || true
find "$OUT" -type f -name "*.pyc" -delete || true
rm -rf "$PYLIB_GLOB/idlelib" "$PYLIB_GLOB/tkinter" "$PYLIB_GLOB/turtledemo" "$PYLIB_GLOB/ensurepip" 2>/dev/null || true

du -sh "$OUT"
echo "Done."
