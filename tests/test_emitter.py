"""Emitter tests — canonical VMConfig in, VBoxManage commands out.

The golden files in ``tests/golden/`` are the regression baseline for the
working version: any change to emitted commands shows up as a golden diff and
has to be acknowledged deliberately (see ``tests/regenerate_golden.py``).
"""
import pytest

from vmctl.core.vmconfig import (
    BootConfig, CPUConfig, DiskConfig, DiskFormat, DiskType, DiskVariant,
    FirmwareConfig, FirmwareType, MemoryConfig, NetworkConfig, NetworkType,
    StorageControllerConfig, StorageControllerType, VMConfig,
)
from vmctl.providers.virtualbox.emitter import VirtualBoxEmitter

from conftest import assert_golden, parse_label, render_commands


# Builders live here (not only in conftest) so regenerate_golden.py can reuse
# them without importing pytest fixtures.

def build_minimal() -> VMConfig:
    """The smallest config a user could hand-write — as printed in the README."""
    return VMConfig(
        name="minimal-vm",
        cpu=CPUConfig(count=2),
        memory=MemoryConfig(mb=2048),
        firmware=FirmwareConfig(),
        disks=[DiskConfig(name="system", size_mb=20480, bootable=True)],
        networks=[NetworkConfig()],
        boot=BootConfig(),
        storage_controllers=[],
    )


def build_full() -> VMConfig:
    return VMConfig(
        name="full-vm",
        cpu=CPUConfig(count=8, hotplug=True, execution_cap=90, pae=True,
                      nested_virt=True),
        memory=MemoryConfig(mb=16384, vram_mb=128, page_fusion=True),
        firmware=FirmwareConfig(type=FirmwareType.EFI64, secure_boot=True,
                                tpm=True),
        disks=[
            DiskConfig(name="system", size_mb=51200, bootable=True,
                       controller=StorageControllerType.SATA,
                       controller_name="SATA Controller"),
            DiskConfig(name="data", size_mb=102400, type=DiskType.SSD,
                       format=DiskFormat.VMDK, variant=DiskVariant.THICK,
                       controller=StorageControllerType.SAS,
                       controller_name="SAS Controller", port=1),
            DiskConfig(name="cd", type=DiskType.DVD, size_mb=700,
                       controller=StorageControllerType.IDE,
                       controller_name="IDE Controller", port=1),
        ],
        networks=[
            NetworkConfig(network_type=NetworkType.NAT),
            NetworkConfig(network_type=NetworkType.BRIDGED, adapter_name="eth0",
                          mac_address="080027AA0001", promiscuous_mode=True),
            NetworkConfig(network_type=NetworkType.INTERNAL,
                          adapter_name="lab-net"),
        ],
        boot=BootConfig(order=["disk", "dvd", "network", "none"], ioapic=True,
                        hpet=True),
        storage_controllers=[
            StorageControllerConfig(name="SATA Controller",
                                    controller_type=StorageControllerType.SATA,
                                    port_count=2, bootable=True),
            StorageControllerConfig(name="SAS Controller",
                                    controller_type=StorageControllerType.SAS,
                                    port_count=16),
            StorageControllerConfig(name="IDE Controller",
                                    controller_type=StorageControllerType.IDE,
                                    port_count=2),
        ],
        description="every field set",
        audio_enabled=True,
        usb_enabled=True,
    )


def emit(vm) -> str:
    return render_commands(VirtualBoxEmitter(vm.name).emit_create_vm(vm))


# ---------------------------------------------------------------------------
# Golden regression baseline
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("label", [
    "bios_minimal", "efi_secureboot", "iso_attached", "multidisk", "multinic",
    "floppy_first",
])
def test_emitted_commands_match_golden(label):
    assert_golden(f"emit_{label}.txt", emit(parse_label(label)))


def test_minimal_matches_golden():
    assert_golden("emit_minimal.txt", emit(build_minimal()))


def test_full_matches_golden():
    assert_golden("emit_full.txt", emit(build_full()))


# ---------------------------------------------------------------------------
# Structural invariants that must hold for any config
# ---------------------------------------------------------------------------

def test_first_command_registers_the_vm(vm_full):
    cmds = VirtualBoxEmitter(vm_full.name).emit_create_vm(vm_full)
    assert cmds[0][:2] == ["VBoxManage", "createvm"]
    assert "--register" in cmds[0]


def test_every_command_targets_this_vm(vm_full):
    """No command may reference a VM other than the one being created."""
    cmds = VirtualBoxEmitter(vm_full.name).emit_create_vm(vm_full)
    for cmd in cmds:
        if cmd[1] in ("modifyvm", "storagectl", "storageattach", "createvm"):
            assert vm_full.name in cmd, cmd


def test_display_ostype_names_are_mapped_to_internal_ids():
    """The parser reads 'Red Hat (64-bit)'; createvm needs 'RedHat_64'."""
    vm = parse_label("multidisk")
    assert vm.ostype == "Red Hat (64-bit)"
    cmds = VirtualBoxEmitter(vm.name).emit_create_vm(vm)
    assert cmds[0][cmds[0].index("--ostype") + 1] == "RedHat_64"


def test_internal_ostype_ids_pass_through(vm_minimal):
    cmds = VirtualBoxEmitter(vm_minimal.name).emit_create_vm(vm_minimal)
    assert cmds[0][cmds[0].index("--ostype") + 1] == "Ubuntu_64"


def test_raw_format_is_forced_to_fixed_allocation():
    vm = build_minimal()
    vm.disks[0].format = DiskFormat.RAW
    vm.disks[0].variant = DiskVariant.THIN
    create = [c for c in VirtualBoxEmitter(vm.name).emit_create_vm(vm)
              if c[1] == "createmedium"][0]
    assert create[create.index("--variant") + 1] == "Fixed"
    assert create[create.index("--filename") + 1].endswith(".img")


def test_promiscuous_and_mac_are_emitted():
    vm = build_full()
    nic = [c for c in VirtualBoxEmitter(vm.name).emit_create_vm(vm)
           if "--nic2" in c][0]
    assert "--promiscuous2" in nic
    assert nic[nic.index("--macaddress2") + 1] == "080027AA0001"


def test_none_boot_slots_are_not_emitted(vm_minimal):
    cmds = VirtualBoxEmitter(vm_minimal.name).emit_create_vm(vm_minimal)
    import re as _re
    boot_flags = [tok for c in cmds for tok in c
                  if _re.fullmatch(r"--boot\d", tok)]
    assert boot_flags == ["--boot1", "--boot2"]  # 'none' in slot 3 is skipped


# ---------------------------------------------------------------------------
# Known-broken behaviour, pinned
# ---------------------------------------------------------------------------

def test_minimal_config_creates_its_controller_before_attaching():
    """F-01 — the README's own example config cannot be imported."""
    cmds = VirtualBoxEmitter("minimal-vm").emit_create_vm(build_minimal())
    verbs = [c[1] for c in cmds]
    assert "storagectl" in verbs, "storageattach has no controller to attach to"
    assert verbs.index("storagectl") < verbs.index("storageattach")




def test_optical_drive_does_not_create_a_hard_disk():
    """F-04 — any VM with an ISO attached cannot be recreated.

    The VM has one disk and one optical drive, so exactly one medium should be
    created. The optical drive should be attached as ``emptydrive`` (portable) or
    as the ISO itself — never as a freshly created blank image, which
    VBoxManage rejects for ``--type dvddrive``.
    """
    vm = parse_label("iso_attached")
    cmds = VirtualBoxEmitter(vm.name).emit_create_vm(vm)

    created = [c[c.index("--filename") + 1] for c in cmds if c[1] == "createmedium"]
    assert len(created) == 1, f"a medium was created for the optical drive: {created}"

    dvd_attach = [c for c in cmds
                  if c[1] == "storageattach" and "dvddrive" in c][0]
    medium = dvd_attach[dvd_attach.index("--medium") + 1]
    assert medium == "emptydrive" or medium.lower().endswith(".iso"), medium
    assert medium not in created




@pytest.mark.parametrize("flag", [
    "--pae", "--nested-hw-virt", "--cpuhotplug", "--cpuexecutioncap",
    "--pagefusion", "--hpet", "--clipboard-mode", "--draganddrop",
])
def test_every_configured_field_reaches_a_command(flag):
    vm = build_full()
    vm.clipboard_mode = "bidirectional"
    vm.draganddrop = "bidirectional"
    flat = {token for cmd in VirtualBoxEmitter(vm.name).emit_create_vm(vm)
            for token in cmd}
    assert flag in flat


def test_description_is_not_double_quoted():
    vm = build_minimal()
    vm.description = "a lab vm"
    desc = [c for c in VirtualBoxEmitter(vm.name).emit_create_vm(vm)
            if "--description" in c][0]
    assert desc[desc.index("--description") + 1] == "a lab vm"


# ---------------------------------------------------------------------------
# Controller resolution (found by the golden tests, see PLAN.md)
# ---------------------------------------------------------------------------

def test_emitted_command_order_is_deterministic():
    """F-14 regression guard: emitting twice must produce identical output."""
    vm = parse_label("iso_attached")
    assert emit(vm) == emit(parse_label("iso_attached"))


def test_disk_is_attached_to_the_controller_it_was_parsed_from():
    """F-01 + F-15 — the disk's real controller name is thrown away.

    Because VirtualBox names its default SATA controller literally ``SATA``, the
    emitter treats the parsed name as "unset" and re-resolves by type, picking
    the first type-matching controller. When a floppy controller is mis-typed as
    SATA (F-15) and sorts first, the system disk is attached to the *floppy*
    controller.
    """
    vm = parse_label("floppy_first")
    attach = [c for c in VirtualBoxEmitter(vm.name).emit_create_vm(vm)
              if c[1] == "storageattach"][0]
    assert attach[attach.index("--storagectl") + 1] == "SATA"




def test_floppy_controller_is_not_created_as_sata():
    vm = parse_label("floppy_first")
    ctl = [c for c in VirtualBoxEmitter(vm.name).emit_create_vm(vm)
           if c[1] == "storagectl" and "Floppy" in c][0]
    assert ctl[ctl.index("--add") + 1] != "sata"
