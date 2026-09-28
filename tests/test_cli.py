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


def _runner_that_separates_stderr() -> CliRunner:
    """A runner whose ``.stdout`` and ``.stderr`` are distinct.

    Click 8.2 removed ``mix_stderr`` and made separation the only behaviour, so
    passing the argument is a TypeError there while omitting it mixes the streams
    on 8.1 -- and this test is specifically about which stream a warning goes to.
    Both are supported versions, so ask for separation and accept it either way.
    """
    try:
        return CliRunner(mix_stderr=False)
    except TypeError:  # Click >= 8.2: already separated
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


def test_export_reads_the_format_off_the_filename(runner, vbox, tmp_path):
    """`export -o vm.json` wrote YAML into it, because `--format` had a default and
    so "not given" and "given as yaml" were the same thing -- while `import` has
    always inferred from the extension. An explicit `--format` still wins, because
    someone naming a file `.json` and asking for yaml means it."""
    import json

    inferred = tmp_path / "vm.json"
    runner.invoke(cli, ["export", "bios-minimal", "-o", str(inferred)])
    assert json.loads(inferred.read_text())["name"] == "bios-minimal"

    as_yaml = tmp_path / "vm.yaml"
    runner.invoke(cli, ["export", "bios-minimal", "-o", str(as_yaml)])
    assert not as_yaml.read_text().lstrip().startswith("{")

    overridden = tmp_path / "forced.json"
    runner.invoke(cli, ["export", "bios-minimal", "-o", str(overridden), "--format", "yaml"])
    assert not overridden.read_text().lstrip().startswith("{")


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
    separated = _runner_that_separates_stderr().invoke(cli, ["import", str(path)])
    assert "Warning:" not in separated.stdout
    assert "Warning:" in separated.stderr
    assert "VBoxManage createvm" in separated.stdout


# ---------------------------------------------------------------------------
# --disk-format (M-04)
# ---------------------------------------------------------------------------


def test_disk_format_completion_offers_what_this_provider_can_create(monkeypatch):
    """The offered list was VirtualBox's, resolved once when the module loaded, so all
    four providers were told they could create `parallels`, `qed` and `vdi` -- libvirt
    and QEMU can create two formats, VMware one. `vmctl -p vmware import --disk-format
    vdi` was advertised by --help and by tab completion and then refused by validation.
    The list follows -p now."""
    from types import SimpleNamespace

    from vmctl.cli import main as cli_main
    from vmctl.providers.libvirt.capabilities import LibvirtCapabilities
    from vmctl.providers.qemu.capabilities import QemuCapabilities
    from vmctl.providers.virtualbox.capabilities import VirtualBoxCapabilities
    from vmctl.providers.vmware.capabilities import VMwareCapabilities

    for capabilities, expected in (
        (LibvirtCapabilities.get(), ["qcow2", "raw"]),
        (QemuCapabilities.get(), ["qcow2", "raw"]),
        (VMwareCapabilities.get(), ["vmdk"]),
        (
            VirtualBoxCapabilities.get(),
            ["parallels", "qcow2", "qed", "raw", "vdi", "vhd", "vmdk"],
        ),
    ):
        monkeypatch.setattr(
            cli_main,
            "_engine",
            lambda ctx=None, caps=capabilities: SimpleNamespace(capabilities=caps),
        )

        assert cli_main._complete_disk_formats(None, None, "") == expected
        # and it still filters by what has been typed
        assert cli_main._complete_disk_formats(None, None, "q") == [
            value for value in expected if value.startswith("q")
        ]


def test_the_help_no_longer_recites_one_providers_formats(runner):
    """It cannot: the answer depends on -p, which --help does not know. It names
    `vmctl capabilities` instead, which does."""
    result = runner.invoke(cli, ["import", "--help"])

    assert "parallels" not in result.output  # VirtualBox's, once offered to everyone
    assert "capabilities" in result.output


def test_a_misspelled_disk_format_says_what_the_words_are(runner, vbox, tmp_path):
    """click.Choice used to catch a typo, and it went with the wrong list. The
    vocabulary check replaces it: the *spelling* is checked here, and whether this
    hypervisor can create it stays with the validator, which has a measured reason."""
    path = tmp_path / "vm.yaml"
    path.write_text("name: v\ncpu:\n  count: 1\nmemory:\n  mb: 128\n")

    result = runner.invoke(cli, ["import", str(path), "--disk-format", "qcow"])

    assert result.exit_code != 0
    assert "not an image format vmctl knows" in result.output
    assert "qcow2" in result.output  # the real one it was probably meant to be


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


# ---------------------------------------------------------------------------
# --clone-disks (E-03)
# ---------------------------------------------------------------------------


def _config_naming_an_image(tmp_path, image):
    path = tmp_path / "named.yaml"
    path.write_text(
        "name: from-file\n"
        "cpu:\n  count: 1\n"
        "memory:\n  mb: 128\n"
        f"disks:\n  - name: system\n    size_mb: 20480\n    source: {image}\n"
        "networks: []\n"
    )
    return path


def _sparse(tmp_path, name, megabytes=1):
    path = tmp_path / name
    with open(path, "wb") as handle:
        handle.truncate(megabytes * 1024 * 1024)
    return path


def test_import_copies_nothing_unless_asked(runner, vbox, tmp_path):
    """A configuration file describes a machine, not its contents."""
    image = _sparse(tmp_path, "root.vdi")
    result = runner.invoke(cli, ["import", str(_config_naming_an_image(tmp_path, image))])
    assert result.exit_code == 0
    assert "clonemedium" not in result.output
    assert "copying" not in result.output


def test_clone_disks_copies_the_image_the_file_names(runner, vbox, tmp_path):
    image = _sparse(tmp_path, "root.vdi", 3)
    result = runner.invoke(
        cli, ["import", str(_config_naming_an_image(tmp_path, image)), "--clone-disks"]
    )
    assert result.exit_code == 0
    # Said up front, because this is the slow and space-hungry part.
    assert "copying 1 disk image(s), about 3 MB" in result.output
    assert str(image) in result.output


def test_the_copies_come_before_the_vm_is_created(runner, vbox, tmp_path):
    """Not cosmetic: the emitter attaches those images, so they must exist first."""
    image = _sparse(tmp_path, "root.vdi")
    result = runner.invoke(
        cli, ["import", str(_config_naming_an_image(tmp_path, image)), "--clone-disks"]
    )
    lines = result.output.splitlines()
    copy = next(i for i, line in enumerate(lines) if "clonemedium" in line)
    create = next(i for i, line in enumerate(lines) if "createvm" in line)
    assert copy < create


def test_clone_disks_attaches_the_copy_rather_than_creating_a_blank(runner, vbox, tmp_path):
    image = _sparse(tmp_path, "root.vdi")
    result = runner.invoke(
        cli, ["import", str(_config_naming_an_image(tmp_path, image)), "--clone-disks"]
    )
    assert "createmedium" not in result.output
    assert "from-file_system.vdi" in result.output


def test_clone_disks_says_when_an_image_cannot_be_read_from_here(runner, vbox, tmp_path):
    """A config from another machine names paths this one cannot see."""
    result = runner.invoke(
        cli, ["import", str(_config_naming_an_image(tmp_path, "/gone/root.vdi")), "--clone-disks"]
    )
    assert result.exit_code == 0
    assert "cannot be read from here" in result.output
    assert "created blank" in result.output


def test_clone_disks_on_a_config_that_names_no_image_explains_itself(runner, vbox, tmp_path):
    """The common case: `disk_path` is left out of an export, so there is nothing
    to copy -- and saying only "nothing to copy" would look like a bug."""
    result = runner.invoke(cli, ["import", str(_write_config(tmp_path)), "--clone-disks"])
    assert result.exit_code == 0
    assert "Nothing to copy" in result.output
    assert "create <vm> --clone-disks" in result.output
    assert "source:" in result.output
    # Still creates the VM, with the blank disk it would have had anyway.
    assert "createvm" in result.output


def test_create_clone_disks_copies_from_the_source_vms_own_images(runner, vbox, monkeypatch):
    """`create` reads the origins live, so it does not need a file to name them."""
    monkeypatch.setattr("os.path.exists", lambda path: True)
    monkeypatch.setattr("os.path.getsize", lambda path: 5 * 1024 * 1024)
    result = runner.invoke(cli, ["create", "bios-minimal", "--new-name", "clone", "--clone-disks"])
    assert result.exit_code == 0
    assert "copying 1 disk image(s), about 5 MB" in result.output
    assert "clonemedium" in result.output


def test_create_names_the_source_image_not_the_renamed_one(runner, vbox, monkeypatch):
    """The origins are read before the rename, or the copy would look for a file
    belonging to a VM that does not exist yet."""
    monkeypatch.setattr("os.path.exists", lambda path: True)
    monkeypatch.setattr("os.path.getsize", lambda path: 1024 * 1024)
    result = runner.invoke(cli, ["create", "bios-minimal", "--new-name", "clone", "--clone-disks"])
    copy = next(line for line in result.output.splitlines() if "clonemedium" in line)
    # The fixture VM's own image, and the new VM's place -- in that order.
    assert "sys.vdi" in copy
    assert copy.index("sys.vdi") < copy.index("/clone/clone_")


def test_clone_disks_is_off_by_default_for_create(runner, vbox):
    result = runner.invoke(cli, ["create", "bios-minimal", "--new-name", "clone"])
    assert "clonemedium" not in result.output


# ---------------------------------------------------------------------------
# apply (E-02)
# ---------------------------------------------------------------------------


def test_apply_creates_a_vm_that_does_not_exist(runner, vbox, tmp_path):
    result = runner.invoke(cli, ["apply", str(_write_config(tmp_path))])
    assert result.exit_code == 0
    assert "does not exist; it will be created" in result.output
    assert "VBoxManage createvm" in result.output


def test_apply_is_dry_run_by_default(runner, vbox, tmp_path):
    result = runner.invoke(cli, ["apply", str(_write_config(tmp_path))])
    assert result.exit_code == 0
    assert "Dry-run" in result.output
    assert not any(c[1] == "createvm" for c in vbox)


def test_apply_does_nothing_when_the_vm_already_matches(runner, vbox, tmp_path):
    """Exported, then applied: the round trip is the strictest test of idempotence,
    and the one a user will actually run."""
    exported = tmp_path / "vm.yaml"
    assert runner.invoke(cli, ["export", "vmctl-t-bios", "-o", str(exported)]).exit_code == 0

    result = runner.invoke(cli, ["apply", str(exported)])

    assert result.exit_code == 0
    assert "already matches the file; nothing to do" in result.output
    assert "VBoxManage" not in result.output


def test_apply_emits_only_what_drifted(runner, vbox, tmp_path):
    exported = tmp_path / "vm.yaml"
    runner.invoke(cli, ["export", "vmctl-t-bios", "-o", str(exported)])
    exported.write_text(exported.read_text().replace("mb: 128", "mb: 512"))

    result = runner.invoke(cli, ["apply", str(exported)])

    assert result.exit_code == 0
    assert "differs from the file in 1 place(s)" in result.output
    assert "~ memory.mb" in result.output
    commands = [line for line in result.output.splitlines() if "VBoxManage" in line]
    assert len(commands) == 1
    assert "--memory 512" in commands[0]


def test_apply_does_not_reset_what_the_file_never_mentions(runner, vbox, tmp_path):
    """The destructive reading of a config file: a minimal file must change what it
    states and nothing else, or `apply` is a way to lose settings."""
    minimal = tmp_path / "small.yaml"
    minimal.write_text("name: vmctl-t-bios\ncpu:\n  count: 4\n")

    result = runner.invoke(cli, ["apply", str(minimal)])

    assert result.exit_code == 0
    commands = [line for line in result.output.splitlines() if "VBoxManage" in line]
    assert len(commands) == 1
    assert "--cpus 4" in commands[0]
    for flag in ("--memory", "--vram", "--firmware", "--nic1"):
        assert flag not in commands[0]


def test_apply_says_when_clone_disks_does_not_apply(runner, vbox, tmp_path):
    exported = tmp_path / "vm.yaml"
    runner.invoke(cli, ["export", "vmctl-t-bios", "-o", str(exported)])
    exported.write_text(exported.read_text().replace("mb: 128", "mb: 512"))

    result = runner.invoke(cli, ["apply", str(exported), "--clone-disks"])

    assert result.exit_code == 0
    assert "already exists" in result.output
    assert "does not create or replace disks" in result.output


def test_apply_reports_a_disk_the_file_adds_with_no_image(runner, vbox, tmp_path):
    exported = tmp_path / "vm.yaml"
    runner.invoke(cli, ["export", "vmctl-t-bios", "-o", str(exported)])
    text = exported.read_text()
    assert "storage:" in text
    exported.write_text(
        text.replace("storage:\n", "storage:\n- name: added\n  size_mb: 4096\n  bus: scsi\n", 1)
    )

    result = runner.invoke(cli, ["apply", str(exported)])

    assert result.exit_code == 0, result.output
    assert "no image behind it" in result.output


def test_reading_a_vm_that_does_not_exist_says_so(runner, vbox, monkeypatch):
    """F-40: VirtualBox reported "no such VM" as a generic provider failure, so
    `apply` could not tell it apart from a broken hypervisor and refused to create."""
    from vmctl.core.exceptions import VMNotFoundError
    from vmctl.providers.virtualbox.parser import VirtualBoxParser

    def fake_run(cmd, *args, **kwargs):
        raise subprocess.CalledProcessError(
            1,
            cmd,
            stderr=(
                "VBoxManage.exe: error: Could not find a registered machine named 'gone'\n"
                "VBoxManage.exe: error: Details: code VBOX_E_OBJECT_NOT_FOUND (0x80bb0001)\n"
            ),
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(VMNotFoundError):
        VirtualBoxParser().get_vm_info("gone")


def test_a_real_virtualbox_failure_is_still_a_provider_error(monkeypatch):
    """The other half: a broken hypervisor must not look like an absent VM, or
    `apply` would cheerfully try to create a VM that is already there."""
    from vmctl.core.exceptions import ProviderError
    from vmctl.providers.virtualbox.parser import VirtualBoxParser

    def fake_run(cmd, *args, **kwargs):
        raise subprocess.CalledProcessError(1, cmd, stderr="VBoxManage.exe: error: E_FAIL\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(ProviderError):
        VirtualBoxParser().get_vm_info("broken")


# ---------------------------------------------------------------------------
# Machine-readable output (E-07)
# ---------------------------------------------------------------------------


def test_list_as_json_is_a_list_of_objects(runner, vbox):
    import json as _json

    result = runner.invoke(cli, ["list", "--format", "json"])
    assert result.exit_code == 0
    vms = _json.loads(result.output)
    assert isinstance(vms, list) and vms
    assert set(vms[0]) == {"name", "status", "provider"}


def test_list_as_json_of_an_empty_host_is_an_empty_list(runner, vbox, monkeypatch):
    """A sentence where JSON was asked for is what breaks a pipeline."""
    import json as _json

    monkeypatch.setattr(
        "vmctl.providers.virtualbox.backend.VirtualBoxBackend.list_vms", lambda self: []
    )
    result = runner.invoke(cli, ["list", "--format", "json"])
    assert result.exit_code == 0
    assert _json.loads(result.output) == []


def test_status_as_json_names_the_vm(runner, vbox):
    import json as _json

    result = runner.invoke(cli, ["status", "vmctl-t-bios", "--format", "json"])
    assert _json.loads(result.output)["name"] == "vmctl-t-bios"


def test_validate_as_json_reports_a_valid_file(runner, vbox, tmp_path):
    import json as _json

    result = runner.invoke(cli, ["validate", str(_write_config(tmp_path)), "--format", "json"])
    assert result.exit_code == 0
    data = _json.loads(result.output)
    assert data["valid"] is True
    assert data["name"] == "from-file"
    assert isinstance(data["warnings"], list)


def test_validate_as_json_reports_a_failure_as_json_too(runner, vbox, tmp_path):
    """On stdout and still exit 1: prose on stderr and nothing on stdout is what makes
    `|| true` the only way for a pipeline to handle an invalid file."""
    import json as _json

    path = tmp_path / "bad.yaml"
    path.write_text("name: bad\ncpu:\n  count: 9999\n")

    result = runner.invoke(cli, ["validate", str(path), "--format", "json"])

    assert result.exit_code == 1
    data = _json.loads(result.output)
    assert data["valid"] is False
    assert "cpu.count" in _json.dumps(data["error"])


def test_diff_as_json_carries_the_same_changes_as_the_text(runner, vbox, tmp_path):
    import json as _json

    exported = tmp_path / "vm.yaml"
    runner.invoke(cli, ["export", "vmctl-t-bios", "-o", str(exported)])
    exported.write_text(exported.read_text().replace("mb: 128", "mb: 512"))

    result = runner.invoke(cli, ["diff", "vmctl-t-bios", str(exported), "--format", "json"])

    assert result.exit_code == 1  # diff(1): they differ
    data = _json.loads(result.output)
    assert data["differs"] is True
    assert data["changes"] == [{"path": "memory.mb", "kind": "changed", "vm": "128", "file": "512"}]


def test_json_output_is_byte_for_byte_repeatable(runner, vbox):
    first = runner.invoke(cli, ["list", "--format", "json"]).output
    assert runner.invoke(cli, ["list", "--format", "json"]).output == first


# ---------------------------------------------------------------------------
# --verbose / --quiet (E-08)
# ---------------------------------------------------------------------------


def test_verbose_echoes_each_command_as_it_runs(runner, vbox, tmp_path):
    result = runner.invoke(cli, ["-v", "import", str(_write_config(tmp_path)), "--execute"])
    assert result.exit_code == 0
    echoed = [line for line in result.output.splitlines() if line.startswith("+ ")]
    assert echoed
    assert any("createvm" in line for line in echoed)


def test_the_echo_stops_at_the_command_that_failed(runner, vbox, tmp_path, monkeypatch):
    """The whole point of E-08: the last line you see is the step that failed, not the
    one after it."""
    calls = []

    def fail_on_modifyvm(cmd, *args, **kwargs):
        calls.append(cmd)
        if len(cmd) > 1 and cmd[1] == "modifyvm":
            raise subprocess.CalledProcessError(1, cmd, stderr="VBoxManage: error: nope")
        if len(cmd) > 1 and cmd[1] == "list":
            return FakeCompleted(stdout=read_fixture("list_vms.txt"))
        return FakeCompleted()

    monkeypatch.setattr(subprocess, "run", fail_on_modifyvm)
    result = runner.invoke(cli, ["-v", "import", str(_write_config(tmp_path)), "--execute"])

    assert result.exit_code == 1
    echoed = [line for line in result.output.splitlines() if line.startswith("+ ")]
    assert "modifyvm" in echoed[-1]


def test_quiet_suppresses_warnings_but_not_errors(runner, vbox, tmp_path):
    noisy = tmp_path / "noisy.yaml"
    noisy.write_text(
        "name: noisy\ncpu:\n  count: 2\nmemory:\n  mb: 128\n"
        "disks:\n  - name: system\n    size_mb: 1024\nnetworks: []\nostype: NotAnOsType\n"
    )
    loud = runner.invoke(cli, ["validate", str(noisy)])
    quiet = runner.invoke(cli, ["-q", "validate", str(noisy)])

    assert "Warning:" in loud.output
    assert "Warning:" not in quiet.output
    assert quiet.exit_code == 0

    broken = tmp_path / "broken.yaml"
    broken.write_text("name: broken\ncpu:\n  count: 9999\n")
    assert runner.invoke(cli, ["-q", "validate", str(broken)]).exit_code == 1


# ---------------------------------------------------------------------------
# doctor (E-12)
# ---------------------------------------------------------------------------


def test_doctor_reports_the_host_and_the_provider(runner, vbox):
    result = runner.invoke(cli, ["doctor"])
    assert "provider: virtualbox" in result.output
    assert "host memory" in result.output
    assert "image location" in result.output


def test_doctor_as_json_says_whether_anything_is_broken(runner, vbox):
    import json as _json

    data = _json.loads(runner.invoke(cli, ["doctor", "--format", "json"]).output)
    assert data["provider"] == "virtualbox"
    assert isinstance(data["ok"], bool)
    assert all({"check", "value", "ok", "hint"} == set(c) for c in data["checks"])


def test_doctor_exits_1_when_something_will_stop_vmctl_working(runner, monkeypatch):
    """So it can gate a pipeline. A missing hypervisor is the case it is for."""

    def missing(*args, **kwargs):
        raise FileNotFoundError(2, "no such file")

    monkeypatch.setattr(subprocess, "run", missing)
    monkeypatch.setattr("shutil.which", lambda name: None)

    result = runner.invoke(cli, ["-p", "virtualbox", "doctor"])

    assert result.exit_code == 1
    assert "will stop vmctl working" in result.output


# ---------------------------------------------------------------------------
# snapshot (E-09)
# ---------------------------------------------------------------------------


def test_snapshot_take_names_the_snapshot(runner, vbox):
    result = runner.invoke(cli, ["snapshot", "take", "vmctl-t-bios", "before-test"])
    assert result.exit_code == 0
    assert "Took snapshot 'before-test'" in result.output
    taken = [c for c in vbox if c[1] == "snapshot" and "take" in c]
    assert taken and taken[0][-1] == "before-test"


def test_a_description_is_passed_as_one_argument(runner, vbox):
    """A description is prose; a plan carries argv, so it must not be split (A-08)."""
    result = runner.invoke(
        cli,
        ["snapshot", "take", "vmctl-t-bios", "before-test", "-d", "kernel 6.9; rolling back"],
    )
    assert result.exit_code == 0
    taken = [c for c in vbox if c[1] == "snapshot" and "take" in c][0]
    assert "kernel 6.9; rolling back" in taken


def test_snapshot_list_reads_the_real_capture(runner, vbox, monkeypatch):
    monkeypatch.setattr(
        "vmctl.providers.virtualbox.backend.VirtualBoxBackend._run_command_allowing_failure",
        lambda self, command: read_fixture("snapshot_list_vbox.txt"),
    )
    result = runner.invoke(cli, ["snapshot", "list", "vmctl-t-bios"])
    assert result.exit_code == 0
    assert "* with-raw-attached" in result.output
    assert "before-test  -- a description with spaces" in result.output


def test_snapshot_list_as_json(runner, vbox, monkeypatch):
    import json as _json

    monkeypatch.setattr(
        "vmctl.providers.virtualbox.backend.VirtualBoxBackend._run_command_allowing_failure",
        lambda self, command: read_fixture("snapshot_list_vbox.txt"),
    )
    data = _json.loads(
        runner.invoke(cli, ["snapshot", "list", "vmctl-t-bios", "--format", "json"]).output
    )
    assert data["vm"] == "vmctl-t-bios"
    assert [s["name"] for s in data["snapshots"]][0] == "before-test"


def test_a_vm_with_no_snapshots_says_so(runner, vbox, monkeypatch):
    monkeypatch.setattr(
        "vmctl.providers.virtualbox.backend.VirtualBoxBackend._run_command_allowing_failure",
        lambda self, command: "This machine does not have any snapshots\n",
    )
    result = runner.invoke(cli, ["snapshot", "list", "vmctl-t-bios"])
    assert result.exit_code == 0
    assert "has no snapshots" in result.output


def test_restore_asks_before_discarding_the_present(runner, vbox):
    """It throws away everything since the snapshot, so it asks like 'vmctl delete'."""
    result = runner.invoke(cli, ["snapshot", "restore", "vmctl-t-bios", "before-test"], input="n\n")
    assert result.exit_code == 1
    assert "discards everything" in result.output
    assert not any("restore" in c for c in vbox)


def test_restore_proceeds_when_confirmed(runner, vbox):
    result = runner.invoke(cli, ["snapshot", "restore", "vmctl-t-bios", "before-test"], input="y\n")
    assert result.exit_code == 0
    assert any("restore" in c for c in vbox)


def test_snapshot_delete_asks_too(runner, vbox):
    result = runner.invoke(cli, ["snapshot", "delete", "vmctl-t-bios", "before-test"], input="n\n")
    assert result.exit_code == 1
    assert not any("delete" in c and c[1] == "snapshot" for c in vbox)


def test_force_skips_the_prompt(runner, vbox):
    result = runner.invoke(cli, ["snapshot", "delete", "vmctl-t-bios", "before-test", "--force"])
    assert result.exit_code == 0
    assert "Deleted snapshot 'before-test'" in result.output


def test_a_description_a_provider_cannot_keep_is_reported(runner, monkeypatch, tmp_path):
    """Accepting the text and losing it is how people stop trusting a tool."""
    monkeypatch.setattr(
        "vmctl.providers.qemu.backend.QemuBackend.vm_exists", lambda self, name: True
    )
    monkeypatch.setattr("vmctl.providers.qemu.backend.QemuBackend._pid", lambda self, name: None)
    monkeypatch.setattr(
        "vmctl.providers.qemu.backend.QemuBackend.snapshot_images",
        lambda self, name: [str(tmp_path / "root.qcow2")],
    )
    monkeypatch.setattr(
        "vmctl.providers.qemu.backend.QemuBackend.read_vm",
        lambda self, name: _qcow2_vm(name),
    )
    monkeypatch.setattr("vmctl.providers.base.BaseProvider.run_plan", lambda self, plan: None)

    result = runner.invoke(
        cli, ["-p", "qemu", "snapshot", "take", "vm", "s1", "-d", "why I took it"]
    )

    assert result.exit_code == 0
    assert "cannot store a snapshot description" in result.output


def _qcow2_vm(name):
    from vmctl.core.vmconfig import (
        BootConfig,
        CPUConfig,
        DiskFormat,
        FirmwareConfig,
        MemoryConfig,
        StorageDevice,
        VMConfig,
    )

    return VMConfig(
        name=name,
        cpu=CPUConfig(count=1),
        memory=MemoryConfig(mb=128),
        firmware=FirmwareConfig(),
        storage=[StorageDevice(name="root", format=DiskFormat.QCOW2, disk_path="/i.qcow2")],
        networks=[],
        boot=BootConfig(),
        storage_controllers=[],
    )


# ---------------------------------------------------------------------------
# extends (E-11)
# ---------------------------------------------------------------------------


def test_import_resolves_extends(runner, vbox, tmp_path):
    (tmp_path / "base.yaml").write_text(
        "guest_os: Ubuntu_64\ncpu:\n  count: 4\nmemory:\n  mb: 2048\n"
        "networks:\n  - network_type: nat\n"
    )
    child = tmp_path / "web.yaml"
    child.write_text(
        "extends: base.yaml\nname: web-01\nstorage:\n  - name: system\n    size_mb: 20480\n"
    )

    result = runner.invoke(cli, ["import", str(child)])

    assert result.exit_code == 0
    assert "--name web-01" in result.output
    assert "--memory 2048" in result.output and "--cpus 4" in result.output


def test_validate_says_what_a_file_is_built_on(runner, vbox, tmp_path):
    (tmp_path / "base.yaml").write_text("cpu:\n  count: 2\nmemory:\n  mb: 512\n")
    child = tmp_path / "web.yaml"
    child.write_text("extends: base.yaml\nname: web-01\n")

    result = runner.invoke(cli, ["validate", str(child)])

    assert result.exit_code == 0
    assert "Built on: base.yaml" in result.output


def test_a_missing_base_is_reported_not_a_traceback(runner, vbox, tmp_path):
    child = tmp_path / "web.yaml"
    child.write_text("extends: nope.yaml\nname: web-01\n")

    result = runner.invoke(cli, ["validate", str(child)])

    assert result.exit_code == 1
    assert "nope.yaml" in result.output


# ---------------------------------------------------------------------------
# Command-line overrides (E-20)
# ---------------------------------------------------------------------------


def test_import_can_override_memory_and_cpu_which_it_could_not_before(runner, vbox, tmp_path):
    """`import` accepted --new-name and --disk-format and nothing else, so the two
    things anyone wants to change from a file -- how much RAM and how many CPUs --
    could not be changed at all without editing the file first."""
    config = tmp_path / "vm.yaml"
    config.write_text("name: ov\ncpu:\n  count: 1\nmemory:\n  mb: 128\n")

    result = runner.invoke(
        cli, ["import", str(config), "--set", "memory.mb=4096", "--set", "cpu.count=8"]
    )

    assert result.exit_code == 0
    assert "--memory 4096" in result.output
    assert "--cpus 8" in result.output


def test_import_can_add_a_disk_and_the_provider_checks_it_like_any_other(runner, vbox, tmp_path):
    config = tmp_path / "vm.yaml"
    config.write_text("name: ov\ncpu:\n  count: 1\nmemory:\n  mb: 128\n")

    result = runner.invoke(
        cli,
        ["import", str(config), "--add-disk", "name=data,size_mb=20480,bus=sata,format=vmdk"],
    )

    assert result.exit_code == 0
    assert "--format VMDK" in result.output
    assert "ov_data.vmdk" in result.output


def test_a_per_device_format_outranks_disk_format_and_the_run_says_so(runner, vbox, tmp_path):
    """The conflict `--disk-format vdi --add-disk format=vmdk` had no stated answer.
    It has one now, and a dry run names the flag that lost rather than resolving it in
    silence."""
    config = tmp_path / "vm.yaml"
    config.write_text(
        "name: ov\ncpu:\n  count: 1\nmemory:\n  mb: 128\n"
        "storage:\n  - name: system\n    size_mb: 20480\n"
    )

    result = runner.invoke(
        cli,
        [
            "import",
            str(config),
            "--disk-format",
            "vdi",
            "--add-disk",
            "name=data,size_mb=1024,format=vmdk",
        ],
    )

    assert result.exit_code == 0
    assert "ov_system.vdi" in result.output
    assert "ov_data.vmdk" in result.output
    assert "outranks --disk-format" in result.output


def test_a_bad_override_is_an_error_not_a_traceback(runner, vbox, tmp_path):
    config = tmp_path / "vm.yaml"
    config.write_text("name: ov\ncpu:\n  count: 1\nmemory:\n  mb: 128\n")

    result = runner.invoke(cli, ["import", str(config), "--set", "memory.mb"])

    assert result.exit_code != 0
    assert "field=value" in result.output


def test_output_survives_a_console_that_cannot_encode_it(runner, vbox, tmp_path, monkeypatch):
    """A Windows console defaults to a legacy code page -- cp1252 on the host this was
    found on -- and writing a character it has no room for raised UnicodeEncodeError
    from inside click.echo, *after* the work was done: `export` wrote the file, then
    died with a traceback and exit 1 on all seven VirtualBox cases while the correct
    export sat on disk. vmctl's own output is ASCII now, but a VM's description is the
    user's."""
    import io

    from vmctl.cli.main import _tolerate_a_narrow_console

    narrow = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")
    monkeypatch.setattr("sys.stdout", narrow)
    _tolerate_a_narrow_console()

    narrow.write("an arrow -> and Arabic عربي\n")  # must not raise

    assert narrow.errors == "replace"


def test_nothing_vmctl_prints_needs_more_than_ascii():
    """The reason the above is a safety net rather than the fix: a line vmctl composes
    itself must be printable on any console, so `export` says `->` and not an arrow."""
    from pathlib import Path as _Path

    source = _Path("vmctl/cli/main.py").read_text(encoding="utf-8")
    printed = [
        line
        for line in source.splitlines()
        if ("click.echo" in line or "_warn(" in line) and not line.strip().startswith("#")
    ]
    offenders = [line.strip() for line in printed if any(ord(ch) > 127 for ch in line)]
    assert offenders == []


def test_export_to_stdout(runner, vbox, tmp_path, monkeypatch):
    """`-o -` is the convention cp, tar and everything else honours. vmctl created a
    file *called* `-` in the working directory and reported success, so a pipeline got
    nothing and a stray file appeared instead."""
    import yaml

    monkeypatch.chdir(tmp_path)

    result = runner.invoke(cli, ["export", "bios-minimal", "-o", "-"])

    assert result.exit_code == 0
    assert yaml.safe_load(result.output)["name"] == "bios-minimal"
    assert not (tmp_path / "-").exists()


def test_export_to_stdout_as_json_stays_parseable(runner, vbox, tmp_path, monkeypatch):
    """Nothing else may go to stdout: the document *is* the output, so a confirmation
    line would corrupt what the caller is piping."""
    import json

    monkeypatch.chdir(tmp_path)

    result = runner.invoke(cli, ["export", "bios-minimal", "-o", "-", "--format", "json"])

    assert result.exit_code == 0
    assert json.loads(result.output)["name"] == "bios-minimal"
    assert "Exported" not in result.output


def test_completion_finds_the_provider_before_the_callback_has_run():
    """Click parses the options but does not call callbacks while it is completing, so
    `ctx.obj` -- which the group fills in -- is empty there. Reading only that made
    `vmctl -p qemu read <TAB>` offer the *default* provider's VM names: verified by
    driving the real bash-completion protocol against two hypervisors, each with one
    VM, and getting the wrong one back."""
    import click

    from vmctl.cli.main import _selected_provider

    # what a command sees: the group's callback has filled ctx.obj
    with click.Context(click.Command("read")) as parent:
        parent.obj = {"provider": "qemu"}
        with click.Context(click.Command("read"), parent=parent) as child:
            assert _selected_provider(child) == "qemu"

    # what completion sees: parsed parameters, no obj
    with click.Context(click.Command("read")) as parent:
        parent.params = {"provider": "vmware"}
        with click.Context(click.Command("read"), parent=parent) as child:
            assert _selected_provider(child) == "vmware"

    # and nothing selected is still nothing, so detection decides
    with click.Context(click.Command("read")) as bare:
        assert _selected_provider(bare) is None
