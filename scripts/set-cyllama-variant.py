#!/usr/bin/env python3
"""Point the bundled sidecar at one of cyllama's per-backend distributions.

cyllama publishes the same import package under several distribution
names, one per GPU backend: ``cyllama`` (CPU everywhere, and Metal on
macOS arm64 -- the released wheel has ``metal: true`` built in),
``cyllama-cuda12``, ``cyllama-vulkan``, ``cyllama-rocm`` and
``cyllama-sycl``. They all install as ``import cyllama``, so nothing in
``sidecar.py`` changes between variants -- only which wheel gets
installed into the bundled Python env, and therefore which backend the
shipped app can drive.

They also *conflict*: two of them own the same ``cyllama/`` directory, so
exactly one may be installed. The dependency line in
``python-sidecar/pyproject.toml`` is the single place that decides which,
and this script rewrites it. ``scripts/build-python-env.sh`` reads the
distribution name back out of that file, so the pin and the sidecar's own
metadata cannot drift apart.

The variant also renames the installer (``electron-builder.yml``'s
``artifactName``) so a CUDA build and a CPU build don't overwrite each
other in ``dist/``. ``appId`` and ``productName`` stay put: these are the
same application with a different accelerator, not competing apps, so
they share their config and user data.

Usage:
    python3 scripts/set-cyllama-variant.py cuda
    python3 scripts/set-cyllama-variant.py --show
    python3 scripts/set-cyllama-variant.py cuda --platform win
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "python-sidecar" / "pyproject.toml"
BUILDER_YML = ROOT / "electron-builder.yml"

# backend -> (distribution name, platforms with a published wheel).
#
# Platform sets are what cyllama actually ships (verified against PyPI for
# 0.4.4), not what the backend could theoretically target: rocm and sycl
# are Linux-only, cuda12 has no macOS build (Apple dropped NVIDIA long
# ago), and vulkan's macOS wheel is x86_64 only -- on Apple silicon the
# base distribution's Metal build is both faster and native, so there is
# nothing to gain there anyway.
VARIANTS: dict[str, tuple[str, frozenset[str]]] = {
    "cpu": ("cyllama", frozenset({"mac", "linux", "win"})),
    "cuda": ("cyllama-cuda12", frozenset({"linux", "win"})),
    "vulkan": ("cyllama-vulkan", frozenset({"mac", "linux", "win"})),
    "rocm": ("cyllama-rocm", frozenset({"linux"})),
    "sycl": ("cyllama-sycl", frozenset({"linux"})),
}

# Distribution name -> backend, for reading the current state back.
_DIST_TO_BACKEND = {dist: name for name, (dist, _) in VARIANTS.items()}

# The sidecar's cyllama dependency line. Captures indent, distribution
# name, whatever version specifier follows, and the trailing comma, so a
# rewrite preserves the floor and the file's formatting.
_DEP_RE = re.compile(
    r'^(?P<indent>\s*)"(?P<dist>cyllama(?:-[a-z0-9]+)?)(?P<spec>[^"]*)",\s*$',
    re.MULTILINE,
)

# artifactName is managed by this script; it is rewritten wholesale each
# time rather than patched, so switching variants can't accumulate suffixes.
_ARTIFACT_RE = re.compile(r"^artifactName:.*$\n?", re.MULTILINE)


def host_platform() -> str:
    if sys.platform == "darwin":
        return "mac"
    if sys.platform.startswith("win"):
        return "win"
    return "linux"


def read_dependency() -> tuple[str, str]:
    """Return ``(dist, spec)`` for the sidecar's current cyllama pin."""
    text = PYPROJECT.read_text()
    match = _DEP_RE.search(text)
    if match is None:
        raise SystemExit(
            f"no cyllama dependency line found in {PYPROJECT}.\n"
            'Expected a line like:  "cyllama>=0.4.4",'
        )
    return match.group("dist"), match.group("spec")


def current_backend() -> str:
    dist, _ = read_dependency()
    backend = _DIST_TO_BACKEND.get(dist)
    if backend is None:
        raise SystemExit(
            f"{PYPROJECT} pins an unrecognised distribution: {dist}\n"
            f"Known: {', '.join(sorted(_DIST_TO_BACKEND))}"
        )
    return backend


def set_backend(backend: str, platform: str) -> bool:
    """Rewrite the pin and the artifact name. True if anything changed."""
    dist, platforms = VARIANTS[backend]
    if platform not in platforms:
        raise SystemExit(
            f"cyllama publishes no {backend} wheel for {platform}.\n"
            f"{backend} is available on: {', '.join(sorted(platforms))}.\n"
            + (
                "On Apple silicon the default 'cpu' variant already ships "
                "Metal (metal: true in the released wheel), so it is the "
                "GPU build for that platform.\n"
                if platform == "mac"
                else ""
            )
            + "Pass --platform to target a host other than this one."
        )

    changed = False

    text = PYPROJECT.read_text()
    old_dist, spec = read_dependency()
    if old_dist != dist:
        text = _DEP_RE.sub(
            lambda m: f'{m.group("indent")}"{dist}{m.group("spec")}",',
            text,
            count=1,
        )
        PYPROJECT.write_text(text)
        changed = True

    # Installer filename. electron-builder substitutes ${productName},
    # ${version}, ${arch} and ${ext} itself; the backend is baked in here
    # because it is fixed for the duration of a build.
    yml = BUILDER_YML.read_text()
    line = (
        "artifactName: "
        "${productName}-${version}-" + backend + "-${arch}.${ext}\n"
    )
    if _ARTIFACT_RE.search(yml):
        new_yml = _ARTIFACT_RE.sub(line, yml, count=1)
    else:
        # Insert after productName so the identity fields stay together.
        new_yml, n = re.subn(
            r"^(productName:.*\n)", r"\1" + line, yml, count=1, flags=re.MULTILINE
        )
        if not n:
            raise SystemExit(f"could not find productName: in {BUILDER_YML}")
    if new_yml != yml:
        BUILDER_YML.write_text(new_yml)
        changed = True

    return changed


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Select which cyllama backend distribution the app bundles.",
    )
    parser.add_argument(
        "backend",
        nargs="?",
        choices=sorted(VARIANTS),
        help="backend to build against (omit with --show to query)",
    )
    parser.add_argument(
        "--show",
        action="store_true",
        help="print the current variant and exit",
    )
    parser.add_argument(
        "--platform",
        choices=("mac", "linux", "win"),
        default=host_platform(),
        help="platform to validate availability against (default: this host)",
    )
    args = parser.parse_args()

    if args.show or args.backend is None:
        backend = current_backend()
        dist, spec = read_dependency()
        print(f"variant : {backend}")
        print(f"dist    : {dist}{spec}")
        if args.backend is None and not args.show:
            print()
            print("Available: " + ", ".join(sorted(VARIANTS)))
        return 0

    changed = set_backend(args.backend, args.platform)
    dist, spec = read_dependency()
    state = "set" if changed else "already"
    print(f"variant {state}: {args.backend} ({dist}{spec})")
    if changed:
        print("Rebuild the bundled env for it to take effect: make python")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
