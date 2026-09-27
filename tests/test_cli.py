"""CLI tests — exit codes and output contracts, with VBoxManage mocked.

Every test here mocks ``subprocess.run``, so they carry ``allow_subprocess`` to
opt out of the hermeticity guard while still never needing a hypervisor.
"""
import subprocess

import pytest
from click.testing import CliRunner

from vmctl.cli.main import cli

from conftest import read_fixture

pytestmark = pytest.mark.allow_subprocess


class FakeCompleted:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def vbox(monkeypatch):
    """Mock VBoxManage, dispatching on the subcommand."""
    calls = []

    def fake_run(cmd, *args, **kwargs):
        calls.append(cmd)
        verb = cmd[1] if len(cmd) > 1 else ""
        if verb == "list":
            return FakeCompleted(stdout=read_fixture("list_vms.txt"))
        if verb == "showvminfo":
            label = {"bios-minimal": "bios_minimal",
                     "multi-disk": "multidisk"}.get(cmd[2], "bios_minimal")
            return FakeCompleted(stdout=read_fixture(f"showvminfo_{label}.txt"))
        if verb == "showmediuminfo":
            return FakeCompleted(stdout=read_fixture("showmediuminfo_bios_minimal_0.txt"))
        return FakeCompleted()

    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


# ---------------------------------------------------------------------------
# Read-only commands
# ---------------------------------------------------------------------------

def test_version(runner):
    result = runner.invoke(cli, ["--version"])
    assert result.exit_code == 0
    from vmctl import __version__
    assert __version__ in result.output


def test_help_lists_every_command(runner):
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    for command in ("list", "status", "start", "stop", "read", "export",
                    "import", "create", "edit", "delete", "validate", "batch",
                    "completion"):
        assert command in result.output


def test_list_simple(runner, vbox):
    result = runner.invoke(cli, ["list", "--format", "simple"])
    assert result.exit_code == 0
    assert "bios-minimal" in result.output
    assert "multi-disk" in result.output


def test_list_table_has_a_header(runner, vbox):
    result = runner.invoke(cli, ["list"])
    assert result.exit_code == 0
    assert "NAME" in result.output and "STATUS" in result.output


def test_read_outputs_yaml(runner, vbox):
    result = runner.invoke(cli, ["read", "bios-minimal"])
    assert result.exit_code == 0
    import yaml
    assert yaml.safe_load(result.output)["name"] == "bios-minimal"


def test_read_outputs_json(runner, vbox):
    result = runner.invoke(cli, ["read", "bios-minimal", "--format", "json"])
    assert result.exit_code == 0
    import json
    assert json.loads(result.output)["cpu"]["count"] == 2


def test_export_writes_a_file(runner, vbox, tmp_path):
    out = tmp_path / "vm.yaml"
    result = runner.invoke(cli, ["export", "bios-minimal", "-o", str(out)])
    assert result.exit_code == 0
    assert out.exists()


# ---------------------------------------------------------------------------
# Dry-run is the default for everything that mutates
# ---------------------------------------------------------------------------

def _write_config(tmp_path):
    path = tmp_path / "vm.yaml"
    path.write_text(
        "name: from-file\n"
        "ostype: Ubuntu_64\n"
        "cpu:\n  count: 2\n"
        "memory:\n  mb: 2048\n"
        "disks:\n  - name: system\n    size_mb: 20480\n    bootable: true\n"
        "networks:\n  - network_type: nat\n"
    )
    return path


def test_import_is_dry_run_by_default(runner, vbox, tmp_path):
    result = runner.invoke(cli, ["import", str(_write_config(tmp_path))])
    assert result.exit_code == 0
    assert "Dry-run" in result.output
    assert "VBoxManage createvm" in result.output
    assert not any(c[1] == "createvm" for c in vbox), "dry-run must not execute"


def test_import_new_name_overrides_the_file(runner, vbox, tmp_path):
    result = runner.invoke(
        cli, ["import", str(_write_config(tmp_path)), "--new-name", "renamed"]
    )
    assert result.exit_code == 0
    assert "--name renamed" in result.output


def test_create_is_dry_run_by_default(runner, vbox):
    result = runner.invoke(cli, ["create", "bios-minimal", "--new-name", "clone"])
    assert result.exit_code == 0
    assert "Dry-run" in result.output
    assert not any(c[1] == "createvm" for c in vbox)


def test_batch_template_then_create_is_dry_run(runner, vbox, tmp_path):
    template = tmp_path / "batch.yaml"
    assert runner.invoke(cli, ["batch", "template", "-o", str(template)]).exit_code == 0
    assert template.exists()
    result = runner.invoke(cli, ["batch", "create", str(template)])
    assert result.exit_code == 0
    assert "Would create 3 VMs" in result.output
    assert not any(c[1] == "createvm" for c in vbox)


def test_validate_accepts_a_good_config(runner, vbox, tmp_path):
    result = runner.invoke(cli, ["validate", str(_write_config(tmp_path))])
    assert result.exit_code == 0
    assert "valid" in result.output


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------

def test_unknown_command_exits_nonzero(runner):
    assert runner.invoke(cli, ["nope"]).exit_code != 0


def test_missing_config_file_exits_nonzero(runner, tmp_path):
    result = runner.invoke(cli, ["import", str(tmp_path / "absent.yaml")])
    assert result.exit_code != 0


def test_unsupported_extension_is_rejected(runner, vbox, tmp_path):
    path = tmp_path / "vm.txt"
    path.write_text("name: x\n")
    result = runner.invoke(cli, ["import", str(path)])
    assert result.exit_code == 1
    assert "Error" in result.output


def test_delete_asks_before_destroying(runner, vbox):
    result = runner.invoke(cli, ["delete", "bios-minimal"], input="n\n")
    assert not any("unregistervm" in c for c in vbox)
    assert "cancelled" in result.output.lower()


# ---------------------------------------------------------------------------
# Known-broken behaviour, pinned
# ---------------------------------------------------------------------------

def test_delete_abort_currently_exits_zero(runner, vbox):
    """L-01 — an aborted delete is indistinguishable from success in scripts."""
    result = runner.invoke(cli, ["delete", "bios-minimal"], input="n\n")
    assert result.exit_code == 0, "documented current behaviour"


@pytest.mark.documents_bug
@pytest.mark.xfail(strict=True, reason="L-01: aborting a delete exits 0")
def test_delete_abort_should_exit_nonzero(runner, vbox):
    result = runner.invoke(cli, ["delete", "bios-minimal"], input="n\n")
    assert result.exit_code != 0


@pytest.mark.documents_bug
@pytest.mark.xfail(strict=True, reason="F-09: the emitted variable is __VMCTL_COMPLETE (double underscore)")
@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_completion_emits_the_variable_click_reads(runner, shell):
    result = runner.invoke(cli, ["completion", shell])
    assert result.exit_code == 0
    assert "__VMCTL_COMPLETE" not in result.output
    assert "_VMCTL_COMPLETE" in result.output


@pytest.mark.documents_bug
@pytest.mark.xfail(strict=True, reason="F-06: a malformed config raises TypeError out of the CLI")
def test_malformed_config_exits_cleanly(runner, vbox, tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("name: v\nunknown_field: 5\n")
    result = runner.invoke(cli, ["validate", str(path)])
    assert result.exit_code == 1
    assert not isinstance(result.exception, TypeError)


@pytest.mark.documents_bug
@pytest.mark.xfail(strict=True, reason="F-10: edit prints a config but never applies it")
def test_edit_applies_its_change(runner, vbox):
    runner.invoke(cli, ["edit", "bios-minimal", "--cpus", "8"])
    assert any("modifyvm" in c for c in vbox)
