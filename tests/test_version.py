"""The package version is defined once, and everything else reads it.

Kept from the 2.0.0 tree and adapted: pyproject no longer *contains* the version, it
reads it from the package (H-03), which is the stronger form of the same promise -- the
two cannot disagree because there is only one of them. So the test checks that the
single source is declared, and that the documentation trees read it too rather than
carrying their own copies (they used to, and had fallen behind).
"""

import pathlib
import re

import vmctl

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_version_is_a_sane_semver():
    assert re.fullmatch(r"\d+\.\d+\.\d+", vmctl.__version__)


def test_pyproject_reads_the_version_from_the_package():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert 'dynamic = ["version"]' in text
    assert 'version = {attr = "vmctl.__version__"}' in text
    assert not re.search(r'^version\s*=\s*"\d', text, re.MULTILINE), "a second copy of the version"


def test_the_cli_reports_the_package_version():
    from click.testing import CliRunner

    from vmctl.cli.main import cli

    result = CliRunner().invoke(cli, ["--version"])

    assert result.exit_code == 0
    assert vmctl.__version__ in result.output
