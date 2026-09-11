"""Tests for scripts/release_notes.py (CHANGELOG section -> release body)."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "release_notes", ROOT / "scripts" / "release_notes.py"
)
release_notes = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release_notes)

CHANGELOG = """\
# Changelog

## [Unreleased]

- pending change

## [0.2.0] - 2026-09-11

### Added

- feature two

## [0.1.0]

- feature one
"""


def test_dated_heading_matches():
    section = release_notes.extract_section(CHANGELOG, "0.2.0")
    assert "feature two" in section
    assert "feature one" not in section
    assert "pending change" not in section


def test_bare_heading_matches():
    assert "feature one" in release_notes.extract_section(CHANGELOG, "0.1.0")


@pytest.mark.parametrize("version", ["0.2", "0.2.00", "0.1"])
def test_version_prefix_does_not_match_a_longer_version(version):
    assert release_notes.extract_section(CHANGELOG, version) is None


def _run(tmp_path, text, version):
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(text)
    out = tmp_path / "notes.md"
    code = release_notes.main([version, "--changelog", str(changelog), "-o", str(out)])
    return code, out


def test_main_writes_header_and_trimmed_body(tmp_path):
    code, out = _run(tmp_path, CHANGELOG, "0.2.0")
    assert code == 0
    assert out.read_text() == (
        "## Changes since the last Release\n\n### Added\n\n- feature two\n"
    )


def test_missing_version_falls_back_to_unreleased(tmp_path):
    code, out = _run(tmp_path, CHANGELOG, "9.9.9")
    assert code == 0
    assert "pending change" in out.read_text()


def test_empty_version_section_falls_back_to_unreleased(tmp_path):
    text = "## [Unreleased]\n\n- pending\n\n## [0.3.0] - 2026-10-01\n\n## [0.2.0]\n- old\n"
    code, out = _run(tmp_path, text, "0.3.0")
    assert code == 0
    assert "pending" in out.read_text()


def test_nothing_found_exits_2_without_writing(tmp_path):
    code, out = _run(tmp_path, "# Changelog\n", "0.1.0")
    assert code == 2
    assert not out.exists()
