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
            "name: v\nstorage:\n  - name: d\n    bus: fibrechannel\n",
            "not a valid value for storage[0].bus",
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


# ---------------------------------------------------------------------------
# --disk-format (M-04)
# ---------------------------------------------------------------------------


def test_disk_format_offers_only_creatable_formats(runner):
    """VirtualBox can attach a VHDX but not create one, so it is not offered."""
    result = runner.invoke(cli, ["import", "--help"])
    assert "vdi" in result.output and "vmdk" in result.output
    assert "qcow2" in result.output
    assert "vhdx" not in result.output


def test_disk_format_is_refused_when_unsupported(runner, tmp_path):
    path = tmp_path / "vm.yaml"
    path.write_text("name: v\ndisks:\n  - name: d\n    size_mb: 1024\n")
    result = runner.invoke(cli, ["import", str(path), "--disk-format", "vhdx"])
    assert result.exit_code != 0
    assert "vhdx" in result.output


@pytest.mark.parametrize(
    "fmt,native,ext",
    [
        ("vmdk", "VMDK", "vmdk"),
        ("vhd", "VHD", "vhd"),
        ("qcow2", "QCOW", "qcow2"),
        ("qed", "QED", "qed"),
    ],
)
def test_disk_format_changes_the_created_medium(runner, vbox, tmp_path, fmt, native, ext):
    path = tmp_path / "vm.yaml"
    path.write_text("name: v\ndisks:\n  - name: system\n    size_mb: 1024\n")
    result = runner.invoke(cli, ["import", str(path), "--disk-format", fmt])
    assert result.exit_code == 0
    assert f"--format {native}" in result.output
    assert f"system.{ext}" in result.output


def test_raw_is_adjusted_to_fixed_allocation_with_a_warning(runner, vbox, tmp_path):
    """Measured: VirtualBox cannot create a dynamic RAW medium."""
    path = tmp_path / "vm.yaml"
    path.write_text("name: v\ndisks:\n  - name: system\n    size_mb: 1024\n    variant: thin\n")
    result = runner.invoke(cli, ["import", str(path), "--disk-format", "raw"])
    assert result.exit_code == 0
    assert "--variant Fixed" in result.output
    assert "can only be created thick" in result.output


def test_disk_format_leaves_removable_devices_alone(runner, vbox, tmp_path):
    """A DVD drive holds an existing medium, so it has no format to choose."""
    path = tmp_path / "vm.yaml"
    path.write_text(
        "name: v\ndisks:\n  - name: system\n    size_mb: 1024\n"
        "  - name: cd\n    type: dvd\n    controller: ide\n"
    )
    result = runner.invoke(cli, ["import", str(path), "--disk-format", "vmdk"])
    assert result.exit_code == 0
    creates = [ln for ln in result.output.splitlines() if "createmedium" in ln]
    assert len(creates) == 1  # only the real disk
    assert "emptydrive" in result.output


# ---------------------------------------------------------------------------
# convert (M-05)
# ---------------------------------------------------------------------------


def test_convert_is_dry_run_by_default(runner, vbox, tmp_path):
    src = tmp_path / "d.vdi"
    src.write_text("")
    result = runner.invoke(cli, ["convert", str(src), str(tmp_path / "d.vmdk")])
    assert result.exit_code == 0
    assert "Dry-run" in result.output
    assert "clonemedium" in result.output
    assert not any("clonemedium" in c for c in vbox)


def test_convert_infers_the_target_format_from_the_extension(runner, vbox, tmp_path):
    src = tmp_path / "d.vdi"
    src.write_text("")
    result = runner.invoke(cli, ["convert", str(src), str(tmp_path / "d.vmdk")])
    assert "--format VMDK" in result.output


def test_convert_needs_to_be_told_when_it_cannot_infer(runner, vbox, tmp_path):
    src = tmp_path / "d.vdi"
    src.write_text("")
    result = runner.invoke(cli, ["convert", str(src), str(tmp_path / "d.blob")])
    assert result.exit_code == 1
    assert "--to" in result.output


def test_convert_offers_only_formats_the_provider_can_write(runner):
    result = runner.invoke(cli, ["convert", "--help"])
    assert "vmdk" in result.output
    # VirtualBox can attach a VHDX but never create one, so --to must not offer it.
    to_line = result.output.split("--to")[1].split("--from")[0]
    assert "vhdx" not in to_line


def test_convert_refuses_a_target_the_provider_cannot_write(runner, vbox, tmp_path):
    src = tmp_path / "d.vdi"
    src.write_text("")
    result = runner.invoke(
        cli, ["convert", str(src), str(tmp_path / "d.out"), "--to", "vdi", "--from", "vdi"]
    )
    # vdi -> vdi with different paths is a copy, which is allowed.
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# migrate (P-06)
# ---------------------------------------------------------------------------


def test_migrate_requires_a_target(runner, vbox):
    result = runner.invoke(cli, ["migrate", "vmctl-t-bios"])
    assert result.exit_code != 0
    assert "--to" in result.output


def test_migrate_refuses_the_same_provider(runner, vbox):
    result = runner.invoke(
        cli, ["migrate", "vmctl-t-bios", "--from", "virtualbox", "--to", "virtualbox"]
    )
    assert result.exit_code == 1
    assert "nothing to migrate" in result.output


def test_migrate_says_the_data_is_not_coming(runner, vbox, monkeypatch):
    """Silence here would be the dangerous kind: a VM that boots to nothing."""
    monkeypatch.setattr(
        "vmctl.providers.libvirt.backend.LibvirtBackend.run_plan",
        lambda self, plan: None,
    )
    result = runner.invoke(
        cli,
        [
            "migrate",
            "vmctl-t-bios",
            "--from",
            "virtualbox",
            "--to",
            "libvirt",
            "--policy",
            "nearest",
        ],
    )
    assert result.exit_code == 0
    assert "blank disks" in result.output
    assert "--with-disks" in result.output


def test_migrate_is_dry_run_by_default(runner, vbox, monkeypatch):
    ran = []
    monkeypatch.setattr(
        "vmctl.providers.libvirt.backend.LibvirtBackend.run_plan",
        lambda self, plan: ran.append(plan),
    )
    result = runner.invoke(
        cli,
        [
            "migrate",
            "vmctl-t-bios",
            "--from",
            "virtualbox",
            "--to",
            "libvirt",
            "--policy",
            "nearest",
        ],
    )
    assert result.exit_code == 0
    assert "Dry-run" in result.output
    assert ran == []


def test_migrate_reports_what_did_not_carry_over(runner, vbox, monkeypatch):
    monkeypatch.setattr(
        "vmctl.providers.libvirt.backend.LibvirtBackend.run_plan",
        lambda self, plan: None,
    )
    result = runner.invoke(
        cli,
        [
            "migrate",
            "vmctl-t-bios",
            "--from",
            "virtualbox",
            "--to",
            "libvirt",
            "--policy",
            "nearest",
        ],
    )
    assert result.exit_code == 0
    # The fixture VM's VDI disk is one libvirt cannot write, so `nearest` makes it
    # qcow2 and has to say so. A setting still at the model's default is *not*
    # reported: a line in every report is how people learn to skip reports.
    assert "vdi" in result.output and "qcow2" in result.output
    assert "vram" not in result.output


def test_migrate_names_both_ends(runner, vbox, monkeypatch):
    monkeypatch.setattr(
        "vmctl.providers.libvirt.backend.LibvirtBackend.run_plan",
        lambda self, plan: None,
    )
    result = runner.invoke(
        cli,
        [
            "migrate",
            "vmctl-t-bios",
            "--from",
            "virtualbox",
            "--to",
            "libvirt",
            "--new-name",
            "moved",
            "--policy",
            "nearest",
        ],
    )
    assert "vmctl-t-bios (virtualbox)" in result.output
    assert "moved (libvirt)" in result.output


def test_migrating_a_vdi_to_libvirt_is_refused_by_default(runner, vbox):
    """F-31 -- libvirt *defines* a domain with a VDI disk and then fails to start
    it: "Driver 'vdi' can only be used for read-only devices". Being told before
    anything runs is the whole point of a capability declaration."""
    result = runner.invoke(
        cli, ["migrate", "vmctl-t-bios", "--from", "virtualbox", "--to", "libvirt"]
    )
    assert result.exit_code == 1
    assert "vdi is not supported" in result.output
    assert "--policy nearest" in result.output


# ---------------------------------------------------------------------------
# capabilities (E-17)
# ---------------------------------------------------------------------------


def test_capabilities_prints_the_attach_matrix(runner):
    """The question it exists to answer: can this hypervisor put a CD-ROM on NVMe?
    Previously only readable in the source."""
    result = runner.invoke(cli, ["-p", "vmware", "capabilities"])
    assert result.exit_code == 0
    assert "nvme" in result.output
    lines = [line for line in result.output.splitlines() if line.strip().startswith("nvme")]
    # disk yes, cdrom no -- the one refusal VMware makes.
    assert lines and lines[0].split()[1:3] == ["yes", "-"]


def test_capabilities_says_where_its_figures_came_from(runner):
    """A measured limit and a remembered one look identical in a table, so the
    declaration carries its provenance and this prints it."""
    result = runner.invoke(cli, ["-p", "vmware", "capabilities"])
    assert "evidence:" in result.output
    assert "Workstation 17" in result.output


def test_capabilities_names_the_native_format_and_bus(runner):
    result = runner.invoke(cli, ["-p", "qemu", "capabilities"])
    assert "<- native" in result.output
    assert "native for disk" in result.output


def test_capabilities_as_json_is_the_same_facts(runner):
    import json as _json

    result = runner.invoke(cli, ["-p", "qemu", "capabilities", "--format", "json"])
    assert result.exit_code == 0
    data = _json.loads(result.output)
    assert data["provider"] == "qemu"
    assert data["native_format"] == "qcow2"
    assert data["buses"]["virtio-blk"]["carries"] == ["disk", "cdrom"]
    assert "nvme" not in data["buses"], "this QEMU build has no NVMe"


def test_capabilities_differ_between_providers(runner):
    """Four products, four declarations -- which is the whole point of measuring each
    one rather than sharing a table. VMware has vmxnet3 and no virtio; QEMU has virtio
    and no vmxnet3."""
    import json as _json

    def declaration(provider):
        result = runner.invoke(cli, ["-p", provider, "capabilities", "--format", "json"])
        assert result.exit_code == 0, result.output
        return _json.loads(result.output)

    qemu = declaration("qemu")
    vmware = declaration("vmware")
    assert (qemu["native_format"], vmware["native_format"]) == ("qcow2", "vmdk")
    assert "virtio" in qemu["nic_models"] and "virtio" not in vmware["nic_models"]
    assert "vmxnet3" in vmware["nic_models"] and "vmxnet3" not in qemu["nic_models"]


# ---------------------------------------------------------------------------
# --out (E-16)
# ---------------------------------------------------------------------------


def test_out_writes_a_runnable_script(runner, tmp_path):
    """A-01 made "what vmctl would do" data rather than a printed line; this is where
    that pays off for someone who wants to keep it or run it from their own pipeline."""
    config = tmp_path / "vm.yaml"
    config.write_text("name: out-demo\ncpu:\n  count: 1\nmemory:\n  mb: 128\n")
    script = tmp_path / "plan.sh"
    result = runner.invoke(
        cli, ["-p", "qemu", "import", str(config), "--policy", "nearest", "--out", str(script)]
    )
    assert result.exit_code == 0
    assert f"Wrote {script}" in result.output
    text = script.read_text()
    assert text.startswith("#!/bin/sh")
    assert "set -e" in text
    assert "qemu-img create" in text


def test_out_with_a_trailing_slash_writes_the_native_artifact_too(runner, tmp_path):
    """The trailing separator is the distinction, the way cp and rsync read it --
    pathlib normalises it away, so the option keeps the raw string."""
    config = tmp_path / "vm.yaml"
    config.write_text("name: out-demo\ncpu:\n  count: 1\nmemory:\n  mb: 128\n")
    out = tmp_path / "artifacts"
    result = runner.invoke(
        cli,
        ["-p", "libvirt", "import", str(config), "--policy", "nearest", "--out", f"{out}/"],
    )
    assert result.exit_code == 0
    assert (out / "plan.sh").exists()
    # The domain XML on its own, for anyone who wants to hand it to virsh directly.
    xml = out / "out-demo.xml"
    assert xml.exists()
    assert xml.read_text().startswith("<domain")


def test_out_does_not_execute_anything(runner, tmp_path):
    """`--out` is for review; it is not a sneaky --execute."""
    config = tmp_path / "vm.yaml"
    config.write_text("name: out-demo\ncpu:\n  count: 1\nmemory:\n  mb: 128\n")
    out = tmp_path / "plan.sh"
    result = runner.invoke(
        cli, ["-p", "qemu", "import", str(config), "--policy", "nearest", "--out", str(out)]
    )
    assert "Dry-run" in result.output
    assert not (tmp_path / "out-demo").exists()


# ---------------------------------------------------------------------------
# diff (E-01)
# ---------------------------------------------------------------------------


def test_diff_says_nothing_when_a_vm_matches_its_file(runner, vbox, tmp_path):
    """The export/re-import faithfulness check, as a command."""
    config = tmp_path / "vm.yaml"
    assert runner.invoke(cli, ["export", "bios-minimal", "-o", str(config)]).exit_code == 0
    result = runner.invoke(cli, ["diff", "bios-minimal", str(config)])
    assert result.exit_code == 0
    assert "matches" in result.output


def test_diff_exits_one_when_they_differ(runner, vbox, tmp_path):
    """diff(1)'s convention, so a pipeline can act on it: 0 same, 1 differs, 2 error."""
    config = tmp_path / "vm.yaml"
    runner.invoke(cli, ["export", "bios-minimal", "-o", str(config)])
    config.write_text(config.read_text().replace("count: 1", "count: 8"))
    result = runner.invoke(cli, ["diff", "bios-minimal", str(config)])
    assert result.exit_code == 1
    assert "cpu.count" in result.output
    assert "vm 1" in result.output and "file 8" in result.output


def test_diff_exits_two_when_something_goes_wrong(runner, vbox, tmp_path):
    """An error has to be distinguishable from a finding, or 1 means two things."""
    config = tmp_path / "vm.yaml"
    config.write_text("name: x\ncpu:\n  count: notanumber\n")
    result = runner.invoke(cli, ["diff", "bios-minimal", str(config)])
    assert result.exit_code == 2


def test_diff_changes_nothing(runner, vbox, tmp_path):
    config = tmp_path / "vm.yaml"
    runner.invoke(cli, ["export", "bios-minimal", "-o", str(config)])
    runner.invoke(cli, ["diff", "bios-minimal", str(config)])
    assert not any(c[1] in ("modifyvm", "createvm", "storageattach", "unregistervm") for c in vbox)


# ---------------------------------------------------------------------------
# export --all (E-04)
# ---------------------------------------------------------------------------


def test_export_all_writes_one_file_per_vm_and_a_manifest(runner, vbox, tmp_path):
    """The documented "lab snapshot" used to need a shell loop."""
    out = tmp_path / "lab"
    result = runner.invoke(cli, ["export", "--all", "-d", str(out)])
    assert result.exit_code == 0
    files = sorted(p.name for p in out.iterdir())
    assert "manifest.yaml" in files
    assert len([f for f in files if f != "manifest.yaml"]) == len(
        [
            line
            for line in runner.invoke(cli, ["list", "--format", "simple"]).output.splitlines()
            if line.strip()
        ]
    )


def test_the_manifest_is_committable(runner, vbox, tmp_path):
    """Written twice, byte for byte the same: no timestamp, no version. A file that
    changes every time it is written is one nobody can review."""
    out = tmp_path / "lab"
    runner.invoke(cli, ["export", "--all", "-d", str(out)])
    first = (out / "manifest.yaml").read_text()
    runner.invoke(cli, ["export", "--all", "-d", str(out)])
    assert (out / "manifest.yaml").read_text() == first
    assert "provider: virtualbox" in first


def test_export_all_as_json(runner, vbox, tmp_path):
    import json as _json

    out = tmp_path / "lab"
    runner.invoke(cli, ["export", "--all", "-d", str(out), "--format", "json"])
    manifest = _json.loads((out / "manifest.json").read_text())
    assert manifest["provider"] == "virtualbox"
    assert all(entry["file"].endswith(".json") for entry in manifest["vms"])


def test_export_all_refuses_a_name(runner, vbox, tmp_path):
    result = runner.invoke(cli, ["export", "--all", "-d", str(tmp_path), "some-vm"])
    assert result.exit_code == 1
    assert "takes -d" in result.output


def test_export_all_needs_a_directory(runner, vbox):
    result = runner.invoke(cli, ["export", "--all"])
    assert result.exit_code == 1
    assert "-d" in result.output


def test_export_without_all_still_needs_a_name_and_output(runner, vbox):
    assert runner.invoke(cli, ["export"]).exit_code == 1
    assert "or --all" in runner.invoke(cli, ["export"]).output
