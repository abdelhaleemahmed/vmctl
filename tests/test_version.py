"""The package version is defined once and matches pyproject/CLI."""
import pathlib
import re

import vmctl


def test_version_is_a_sane_semver():
    assert re.fullmatch(r"\d+\.\d+\.\d+", vmctl.__version__)


def test_version_matches_pyproject():
    root = pathlib.Path(__file__).resolve().parent.parent
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    assert m, "no version in pyproject.toml"
    assert m.group(1) == vmctl.__version__
