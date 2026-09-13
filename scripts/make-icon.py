#!/usr/bin/env python3
"""Generate the app icon: a shaded Moebius strip on a macOS-style squircle.

Writes resources/icon.png (1024 px), icon.icns and icon.ico, which
electron-builder picks up from ``buildResources``. The outputs are committed,
so this only runs when the design changes. Needs macOS (``sips``,
``iconutil``) and ``rsvg-convert`` (``brew install librsvg``).

    python3 scripts/make-icon.py [outdir]
"""
from __future__ import annotations

import math
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

SIZE = 1024
# Apple's icon grid: an 824 px body inset 100 px, corner radius ~185 px.
BODY, INSET, RADIUS = 824, 100, 185

# The app accent (--accent in src/renderer/styles.css) and a cyan. The strip
# blends between them around the loop; cos(t) returns to its start, so the
# colour is continuous across the seam at t = 0.
COLOR_A = (0x4F, 0x46, 0xE5)
COLOR_B = (0x22, 0xD3, 0xEE)

R, HALF_WIDTH = 1.0, 0.45   # centre-circle radius, strip half-width
N_T, N_S = 300, 12          # facets around the loop and across the strip
TILT = math.radians(65)     # rotation about x: 0 looks down on the loop, 90 side-on
SPIN = math.radians(-45)    # rotation about z: puts the band edge-on at the front
FIT = 680                   # px the strip's larger extent spans

LIGHT = (-0.45, -0.6, 0.66)  # towards the viewer, from upper left


def _norm(v):
    m = math.sqrt(sum(c * c for c in v)) or 1.0
    return tuple(c / m for c in v)


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _point(t: float, s: float):
    """Moebius strip, rotated into view. Screen y grows downward."""
    k = R + s * math.cos(t / 2)
    x, y, z = k * math.cos(t), k * math.sin(t), s * math.sin(t / 2)
    x, y = x * math.cos(SPIN) - y * math.sin(SPIN), x * math.sin(SPIN) + y * math.cos(SPIN)
    y, z = y * math.cos(TILT) - z * math.sin(TILT), y * math.sin(TILT) + z * math.cos(TILT)
    return (x, -y, z)


def _facets():
    light = _norm(LIGHT)
    half = _norm((light[0], light[1], light[2] + 1.0))  # Blinn half-vector, viewer at +z
    out = []
    for i in range(N_T):
        t0, t1 = 2 * math.pi * i / N_T, 2 * math.pi * (i + 1) / N_T
        tm = (t0 + t1) / 2
        mix = (1 - math.cos(tm)) / 2
        base = [a + (b - a) * mix for a, b in zip(COLOR_A, COLOR_B)]
        for j in range(N_S):
            s0 = -HALF_WIDTH + 2 * HALF_WIDTH * j / N_S
            s1 = -HALF_WIDTH + 2 * HALF_WIDTH * (j + 1) / N_S
            quad = [_point(t0, s0), _point(t1, s0), _point(t1, s1), _point(t0, s1)]
            normal = _norm(_cross(
                tuple(q - p for p, q in zip(quad[0], quad[2])),
                tuple(q - p for p, q in zip(quad[1], quad[3])),
            ))
            # One-sided surface: light both faces alike.
            diffuse = abs(_dot(normal, light))
            specular = abs(_dot(normal, half)) ** 40
            shade = 0.32 + 0.78 * diffuse
            rgb = [min(255, c * shade + 255 * 0.55 * specular) for c in base]
            depth = sum(p[2] for p in quad) / 4
            out.append((depth, quad, rgb))
    out.sort(key=lambda f: f[0])  # far to near
    return out


def svg() -> str:
    facets = _facets()
    xs = [p[0] for _, quad, _ in facets for p in quad]
    ys = [p[1] for _, quad, _ in facets for p in quad]
    scale = FIT / max(max(xs) - min(xs), max(ys) - min(ys))
    cx = SIZE / 2 - scale * (max(xs) + min(xs)) / 2
    cy = SIZE / 2 - scale * (max(ys) + min(ys)) / 2
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{SIZE}" height="{SIZE}" '
        f'viewBox="0 0 {SIZE} {SIZE}">',
        "<defs>",
        '<linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">'
        '<stop offset="0" stop-color="#2a2a33"/><stop offset="1" stop-color="#101015"/>'
        "</linearGradient>",
        '<radialGradient id="glow" cx="0.5" cy="0.45" r="0.5">'
        '<stop offset="0" stop-color="#4f46e5" stop-opacity="0.35"/>'
        '<stop offset="1" stop-color="#4f46e5" stop-opacity="0"/>'
        "</radialGradient>",
        # 8-bit gradients step visibly across 824 px; faint noise hides the steps.
        '<filter id="grain"><feTurbulence type="fractalNoise" baseFrequency="0.9" '
        'numOctaves="2" stitchTiles="stitch"/><feColorMatrix type="saturate" values="0"/>'
        "</filter>",
        f'<clipPath id="body"><rect x="{INSET}" y="{INSET}" width="{BODY}" height="{BODY}" '
        f'rx="{RADIUS}"/></clipPath>',
        '<filter id="shadow" x="-10%" y="-10%" width="120%" height="130%">'
        '<feGaussianBlur in="SourceAlpha" stdDeviation="12"/>'
        '<feOffset dy="10"/><feComponentTransfer><feFuncA type="linear" slope="0.35"/>'
        "</feComponentTransfer><feMerge><feMergeNode/><feMergeNode in=\"SourceGraphic\"/>"
        "</feMerge></filter>",
        "</defs>",
        f'<rect x="{INSET}" y="{INSET}" width="{BODY}" height="{BODY}" rx="{RADIUS}" '
        'fill="url(#bg)" filter="url(#shadow)"/>',
        f'<rect x="{INSET}" y="{INSET}" width="{BODY}" height="{BODY}" rx="{RADIUS}" '
        'fill="url(#glow)"/>',
        f'<rect x="{INSET}" y="{INSET}" width="{BODY}" height="{BODY}" filter="url(#grain)" '
        'clip-path="url(#body)" opacity="0.05"/>',
        '<g stroke-linejoin="round" stroke-width="0.8">',
    ]
    for _, quad, rgb in facets:
        pts = " ".join(f"{cx + p[0] * scale:.1f},{cy + p[1] * scale:.1f}" for p in quad)
        color = "#%02x%02x%02x" % tuple(round(c) for c in rgb)
        parts.append(f'<polygon points="{pts}" fill="{color}" stroke="{color}"/>')
    parts += ["</g>", "</svg>"]
    return "\n".join(parts)


def _ico(pngs: dict[int, bytes]) -> bytes:
    """ICO with PNG-compressed entries, which Windows Vista and later read."""
    sizes = sorted(pngs)
    header = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    entries, blobs = b"", b""
    for size in sizes:
        data = pngs[size]
        dim = 0 if size >= 256 else size  # 0 means 256
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        blobs += data
        offset += len(data)
    return header + entries + blobs


def _resize(src: Path, size: int, dst: Path) -> None:
    subprocess.run(["sips", "-z", str(size), str(size), str(src), "--out", str(dst)],
                   check=True, capture_output=True)


def main(outdir: Path) -> None:
    for tool in ("rsvg-convert", "sips", "iconutil"):
        if not shutil.which(tool):
            sys.exit(f"{tool} not found; see the module docstring")
    outdir.mkdir(parents=True, exist_ok=True)
    png_path = outdir / "icon.png"
    with tempfile.TemporaryDirectory() as tmp:
        svg_path = Path(tmp) / "icon.svg"
        svg_path.write_text(svg())
        subprocess.run(["rsvg-convert", "-w", str(SIZE), "-h", str(SIZE), str(svg_path),
                        "-o", str(png_path)], check=True)

        iconset = Path(tmp) / "icon.iconset"
        iconset.mkdir()
        for base in (16, 32, 128, 256, 512):
            _resize(png_path, base, iconset / f"icon_{base}x{base}.png")
            _resize(png_path, base * 2, iconset / f"icon_{base}x{base}@2x.png")
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(outdir / "icon.icns")],
                       check=True)

        pngs = {}
        for size in (16, 24, 32, 48, 64, 128, 256):
            dst = Path(tmp) / f"ico_{size}.png"
            _resize(png_path, size, dst)
            pngs[size] = dst.read_bytes()
        (outdir / "icon.ico").write_bytes(_ico(pngs))


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "resources")
