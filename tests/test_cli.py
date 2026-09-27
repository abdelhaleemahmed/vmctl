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
            label = {"bios-minimal": "bios_minimal", "multi-disk": "multidisk"}.get(
                cmd[2], "bios_minimal"
            )
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
    for command in (
        "list",
        "status",
        "start",
        "stop",
        "read",
        "export",
        "import",
        "create",
        "edit",
        "delete",
        "validate",
        "batch",
        "completion",
    ):
        assert command in result.output


def test_list_simple(runner, vbox):
    result = runner.invoke(cli, ["list", "--format", "simple"])
    assert result.exit_code == 0
    # Names come from the captured `VBoxManage list vms` fixture.
    assert "vmctl-t-bios" in result.output
    assert "vmctl-t-multi" in result.output


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

    assert json.loads(result.output)["cpu"]["count"] == 1


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
    result = runner.invoke(cli, ["import", str(_write_config(tmp_path)), "--new-name", "renamed"])
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
    assert "unsupported file format" in result.output.lower()


def test_delete_asks_before_destroying(runner, vbox):
    result = runner.invoke(cli, ["delete", "vmctl-t-bios"], input="n\n")
    assert not any("unregistervm" in c for c in vbox)
    assert "cancelled" in result.output.lower()


# ---------------------------------------------------------------------------
# Known-broken behaviour, pinned
# ---------------------------------------------------------------------------


def test_delete_abort_exits_nonzero(runner, vbox):
    """L-01 - a declined delete used to exit 0, indistinguishable from success."""
    result = runner.invoke(cli, ["delete", "vmctl-t-bios"], input="n\n")
    assert result.exit_code != 0
    assert not any("unregistervm" in c for c in vbox)


@pytest.mark.parametrize("shell", ["bash", "zsh", "fish"])
def test_completion_emits_a_real_script(runner, shell):
    """F-09 - it used to print an instruction with a mistyped variable name.

    The variable was `__VMCTL_COMPLETE` (one underscore too many), and the
    output was a command rather than a script, so the documented
    `eval "$(vmctl completion bash)"` evaluated vmctl's help text.
    """
    result = runner.invoke(cli, ["completion", shell])
    assert result.exit_code == 0
    assert "__VMCTL_COMPLETE" not in result.output
    assert "_VMCTL_COMPLETE" in result.output
    # A script, not a one-line instruction.
    assert len(result.output.splitlines()) > 3
    assert "vmctl" in result.output


def test_malformed_config_exits_cleanly(runner, vbox, tmp_path):
    """F-06 - a typo used to produce a raw TypeError traceback."""
    path = tmp_path / "bad.yaml"
    path.write_text("name: v\nunknown_field: 5\n")
    result = runner.invoke(cli, ["validate", str(path)])
    assert result.exit_code == 1
    assert not isinstance(result.exception, TypeError)
    assert "Unknown field 'unknown_field'" in result.output


def test_edit_is_dry_run_by_default(runner, vbox):
    """F-10 - edit used to print a config and never apply anything."""
    result = runner.invoke(cli, ["edit", "vmctl-t-bios", "--cpus", "8"])
    assert result.exit_code == 0
    assert "Dry-run" in result.output
    assert "--cpus 8" in result.output
    assert not any("modifyvm" in c for c in vbox)


def test_edit_applies_its_change(runner, vbox):
    result = runner.invoke(cli, ["edit", "vmctl-t-bios", "--cpus", "8", "--execute"])
    assert result.exit_code == 0
    modify = [c for c in vbox if len(c) > 1 and c[1] == "modifyvm"]
    assert modify, "no modifyvm command was run"
    assert "--cpus" in modify[0]


def test_edit_emits_only_what_changed(runner, vbox):
    """Editing one setting must not re-apply the whole configuration."""
    result = runner.invoke(cli, ["edit", "vmctl-t-bios", "--memory", "512"])
    assert result.exit_code == 0
    assert "--memory 512" in result.output
    assert "--vram" not in result.output
    assert "--cpus" not in result.output


def test_edit_with_no_changes_says_so(runner, vbox):
    result = runner.invoke(cli, ["edit", "vmctl-t-bios"])
    assert result.exit_code == 0
    assert "No changes" in result.output


def test_edit_renames_before_other_changes(runner, vbox):
    """Later commands address the VM by its new name, so the rename is first."""
    result = runner.invoke(
        cli, ["edit", "vmctl-t-bios", "--new-name", "newname", "--memory", "512"]
    )
    lines = [ln for ln in result.output.splitlines() if "modifyvm" in ln]
    assert "--name newname" in lines[0]
    assert "newname --memory 512" in lines[1]


def test_edit_refuses_a_running_vm(runner, monkeypatch, vbox):
    """VirtualBox defers or rejects modifyvm on a running VM."""
    from vmctl.providers.virtualbox.backend import VirtualBoxBackend

    monkeypatch.setattr(VirtualBoxBackend, "get_vm_status", lambda self, n: "running")
    result = runner.invoke(cli, ["edit", "vmctl-t-bios", "--cpus", "2", "--execute"])
    assert result.exit_code == 1
    assert "running" in result.output


# ---------------------------------------------------------------------------
# Error and warning reporting (Phase 2)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body,needle",
    [
        ("", "empty"),
        ("name: v\nunknown_field: 5\n", "Unknown field 'unknown_field'"),
        ("name: v\ncpu:\n  cont: 2\n", "Did you mean 'count'?"),
        ("name: v\ncpu:\n  count: four\n", "must be a number"),
        ("disks: []\n", "missing required field 'name'"),
        (
            "name: v\ndisks:\n  - name: d\n    controller: fibrechannel\n",
            "not a valid value for disks[0].controller",
        ),
    ],
)
def test_malformed_configs_report_the_field(runner, vbox, tmp_path, body, needle):
    """F-06 - each of these used to be a raw TypeError or enum ValueError."""
    path = tmp_path / "bad.yaml"
    path.write_text(body)
    result = runner.invoke(cli, ["validate", str(path)])
    assert result.exit_code == 1
    assert needle in result.output
    assert "Traceback" not in result.output


def test_bad_enum_lists_the_accepted_values(runner, vbox, tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("name: v\nfirmware:\n  type: uefi\n")
    result = runner.invoke(cli, ["validate", str(path)])
    assert result.exit_code == 1
    for value in ("bios", "efi", "efi32", "efi64"):
        assert value in result.output


def test_validation_warnings_do_not_fail_the_command(runner, vbox, tmp_path):
    """A warning means 'probably not what you meant', not 'cannot be created'."""
    path = tmp_path / "warn.yaml"
    path.write_text(
        "name: warny\ncpu:\n  count: 1\nmemory:\n  mb: 2050\n"
        "disks:\n  - name: system\n    size_mb: 1024\n"
    )
    result = runner.invoke(cli, ["validate", str(path)])
    assert result.exit_code == 0
    assert "multiple of 4" in result.output
    assert "Configuration is valid!" in result.output


def test_a_slot_clash_is_caught_before_anything_runs(runner, vbox, tmp_path):
    """F-08 - this used to fail inside VBoxManage partway through a create."""
    path = tmp_path / "clash.yaml"
    path.write_text(
        "name: clash\n"
        "storage_controllers:\n  - name: SATA Controller\n"
        "    controller_type: sata\n    port_count: 4\n"
        "disks:\n"
        "  - name: a\n    size_mb: 1024\n    controller_name: SATA Controller\n    port: 0\n"
        "  - name: b\n    size_mb: 1024\n    controller_name: SATA Controller\n    port: 0\n"
    )
    result = runner.invoke(cli, ["validate", str(path)])
    assert result.exit_code == 1
    assert "both attached to" in result.output
    assert not any(c[1] == "createvm" for c in vbox)


def test_dry_run_stdout_carries_only_commands(runner, vbox, tmp_path):
    """Warnings go to stderr so `vmctl import ... | sh` stays usable."""
    path = tmp_path / "warn.yaml"
    path.write_text(
        "name: warny\ncpu:\n  count: 4\nmemory:\n  mb: 2050\n"
        "disks:\n  - name: system\n    size_mb: 1024\n"
        "networks:\n  - network_type: bridged\n"
    )
    result = runner.invoke(cli, ["import", str(path)], catch_exceptions=False)
    assert result.exit_code == 0
    runner_mixed = CliRunner(mix_stderr=False).invoke(cli, ["import", str(path)])
    assert "Warning:" not in runner_mixed.stdout
    assert "Warning:" in runner_mixed.stderr
    assert "VBoxManage createvm" in runner_mixed.stdout
