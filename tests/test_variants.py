"""Tests for the GPU-variant selector (scripts/set-cyllama-variant.py).

The script edits two files that decide what a release actually contains --
the sidecar's cyllama pin and the installer filename -- so the risks worth
covering are: rewriting the wrong line, losing the version floor,
accumulating suffixes across switches, and letting a build be configured
for a wheel cyllama does not publish.

Every test works on a copy of the real files in a tmp_path, so nothing
here mutates the checkout.
"""

import importlib.util
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "set-cyllama-variant.py"
BUILD_SCRIPT = ROOT / "scripts" / "build-python-env.sh"

# Read at import time so the make-target check can parametrize over them.
VARIANT_BACKENDS = ("cpu", "cuda", "vulkan", "rocm", "sycl")


def _load(tmp_path: Path):
    """Import the script with ROOT redirected at a throwaway copy."""
    (tmp_path / "python-sidecar").mkdir(parents=True, exist_ok=True)
    shutil.copy(
        ROOT / "python-sidecar" / "pyproject.toml",
        tmp_path / "python-sidecar" / "pyproject.toml",
    )
    shutil.copy(ROOT / "electron-builder.yml", tmp_path / "electron-builder.yml")

    spec = importlib.util.spec_from_file_location(f"variant_{tmp_path.name}", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.ROOT = tmp_path
    mod.PYPROJECT = tmp_path / "python-sidecar" / "pyproject.toml"
    mod.BUILDER_YML = tmp_path / "electron-builder.yml"
    return mod


@pytest.fixture()
def variant(tmp_path):
    return _load(tmp_path)


def test_checkout_ships_the_cpu_variant(variant):
    # The committed state must be the portable one: it is what a plain
    # `make python` builds and what CI exercises.
    assert variant.current_backend() == "cpu"
    dist, _ = variant.read_dependency()
    assert dist == "cyllama"


def test_every_backend_round_trips(variant):
    for backend, (dist, platforms) in variant.VARIANTS.items():
        variant.set_backend(backend, sorted(platforms)[0])
        assert variant.current_backend() == backend
        assert variant.read_dependency()[0] == dist


def test_version_floor_survives_a_switch(variant):
    _, floor = variant.read_dependency()
    assert floor.startswith(">=")
    variant.set_backend("cuda", "linux")
    assert variant.read_dependency()[1] == floor
    variant.set_backend("cpu", "linux")
    assert variant.read_dependency()[1] == floor


def test_only_the_dependency_line_is_touched(variant):
    before = variant.PYPROJECT.read_text().splitlines()
    variant.set_backend("vulkan", "linux")
    after = variant.PYPROJECT.read_text().splitlines()
    assert len(before) == len(after)
    differing = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
    assert len(differing) == 1
    # ...and not the project's own name, which also starts with "cyllama".
    assert 'name = "cyllama-sidecar"' in variant.PYPROJECT.read_text()


def test_artifact_name_carries_the_backend_and_does_not_accumulate(variant):
    for backend in ("cuda", "rocm", "sycl", "cpu"):
        variant.set_backend(backend, "linux")
        names = [
            line
            for line in variant.BUILDER_YML.read_text().splitlines()
            if line.startswith("artifactName:")
        ]
        assert len(names) == 1, "artifactName must stay a single line"
        assert f"-{backend}-" in names[0]
        # No other backend's name may linger in it.
        others = set(variant.VARIANTS) - {backend}
        assert not [o for o in others if f"-{o}-" in names[0]]


def test_appid_and_product_name_are_left_alone(variant):
    before = variant.BUILDER_YML.read_text()
    variant.set_backend("cuda", "linux")
    after = variant.BUILDER_YML.read_text()
    for key in ("appId:", "productName:"):
        line_before = next(l for l in before.splitlines() if l.startswith(key))
        line_after = next(l for l in after.splitlines() if l.startswith(key))
        assert line_before == line_after


@pytest.mark.parametrize(
    "backend,platform",
    [("cuda", "mac"), ("rocm", "win"), ("rocm", "mac"), ("sycl", "win")],
)
def test_unpublished_combinations_are_refused(variant, backend, platform):
    with pytest.raises(SystemExit) as exc:
        variant.set_backend(backend, platform)
    assert backend in str(exc.value)


def test_refusal_leaves_both_files_untouched(variant):
    py_before = variant.PYPROJECT.read_text()
    yml_before = variant.BUILDER_YML.read_text()
    with pytest.raises(SystemExit):
        variant.set_backend("rocm", "mac")
    assert variant.PYPROJECT.read_text() == py_before
    assert variant.BUILDER_YML.read_text() == yml_before


def test_switching_is_idempotent(variant):
    assert variant.set_backend("cuda", "linux") is True
    assert variant.set_backend("cuda", "linux") is False


def test_cli_show_reports_the_current_variant(tmp_path):
    _load(tmp_path)  # not used directly; exercises the real checkout below
    out = subprocess.run(
        [sys.executable, str(SCRIPT), "--show"],
        capture_output=True, text=True, check=True, cwd=ROOT,
    ).stdout
    assert "variant : cpu" in out


def test_build_script_reads_the_same_line_the_selector_writes(variant):
    """The sed in build-python-env.sh must agree with the script's regex.

    These are two independent parsers of one line; if they drift, the env
    gets one distribution while the sidecar's metadata asks for another,
    and pip installs both over the same cyllama/ directory.
    """
    sed_expr = None
    for line in BUILD_SCRIPT.read_text().splitlines():
        if line.startswith("CYLLAMA_DIST=") and "sed -nE" in line:
            sed_expr = line.split("'")[1]
            break
    assert sed_expr, "could not find the dist-extraction sed in build-python-env.sh"

    for backend, (dist, platforms) in variant.VARIANTS.items():
        variant.set_backend(backend, sorted(platforms)[0])
        got = subprocess.run(
            ["sed", "-nE", sed_expr, str(variant.PYPROJECT)],
            capture_output=True, text=True, check=True,
        ).stdout.splitlines()
        assert got and got[0] == dist, f"sed disagreed for {backend}"


def test_variant_names_match_the_makefile_targets(variant):
    """`make variant-cuda` etc. must exist for every backend we support."""
    makefile = (ROOT / "Makefile").read_text()
    phony = re.search(r"^\.PHONY:(.*?)(?=^\w|\Z)", makefile, re.MULTILINE | re.DOTALL)
    assert phony
    declared = phony.group(1).replace("\\", " ").split()
    for backend in variant.VARIANTS:
        assert f"variant-{backend}" in declared
        assert f"app-{backend}" in declared


@pytest.mark.parametrize("backend", sorted(VARIANT_BACKENDS))
def test_makefile_targets_actually_have_a_recipe(backend):
    """Being listed in .PHONY is not the same as being buildable.

    A ``variant-%`` pattern rule looks right and passes the name check
    above, but GNU make does not apply implicit rules to phony targets,
    so every such target reports "Nothing to be done" and silently does
    nothing. Ask make itself what it would run.
    """
    for target in (f"variant-{backend}", f"app-{backend}"):
        out = subprocess.run(
            ["make", "--dry-run", target],
            capture_output=True, text=True, cwd=ROOT,
        )
        assert out.returncode == 0, f"make -n {target} failed: {out.stderr}"
        assert "Nothing to be done" not in out.stdout, (
            f"{target} matches no rule with a recipe"
        )
        assert "set-cyllama-variant.py" in out.stdout, (
            f"{target} does not run the variant selector"
        )
