"""Emitter tests — canonical VMConfig in, VBoxManage commands out.

The golden files in ``tests/golden/`` are the regression baseline for the
working version: any change to emitted commands shows up as a golden diff and
has to be acknowledged deliberately (see ``tests/regenerate_golden.py``).
"""

import pytest

from vmctl.core.vmconfig import (
    BootConfig,
    CPUConfig,
    StorageDevice,
    DiskFormat,
    DeviceKind,
    Allocation,
    FirmwareConfig,
    FirmwareType,
    MemoryConfig,
    NetworkConfig,
    NetworkType,
    StorageController,
    BusType,
    VMConfig,
)
from vmctl.providers.virtualbox.emitter import VirtualBoxEmitter

from conftest import assert_golden, emit, parse_label


# Builders live here (not only in conftest) so regenerate_golden.py can reuse
# them without importing pytest fixtures.


def build_minimal() -> VMConfig:
    """The smallest config a user could hand-write — as printed in the README."""
    return VMConfig(
        name="minimal-vm",
        cpu=CPUConfig(count=2),
        memory=MemoryConfig(mb=2048),
        firmware=FirmwareConfig(),
        storage=[StorageDevice(name="system", size_mb=20480, bootable=True)],
        networks=[NetworkConfig()],
        boot=BootConfig(),
        storage_controllers=[],
    )


def build_full() -> VMConfig:
    return VMConfig(
        name="full-vm",
        cpu=CPUConfig(count=8, hotplug=True, execution_cap=90, pae=True, nested_virt=True),
        memory=MemoryConfig(mb=16384, vram_mb=128, page_fusion=True),
        firmware=FirmwareConfig(type=FirmwareType.EFI64, secure_boot=True, tpm=True),
        storage=[
            StorageDevice(
                name="system",
                size_mb=51200,
                bootable=True,
                bus=BusType.SATA,
                controller="SATA Controller",
            ),
            StorageDevice(
                name="data",
                size_mb=102400,
                kind=DeviceKind.DISK,
                nonrotational=True,
                format=DiskFormat.VMDK,
                allocation=Allocation.THICK,
                bus=BusType.SAS,
                controller="SAS Controller",
                slot=1,
            ),
            StorageDevice(
                name="cd",
                kind=DeviceKind.CDROM,
                size_mb=700,
                bus=BusType.IDE,
                controller="IDE Controller",
                slot=1,
            ),
        ],
        networks=[
            NetworkConfig(network_type=NetworkType.NAT),
            NetworkConfig(
                network_type=NetworkType.BRIDGED,
                adapter_name="eth0",
                mac_address="080027AA0001",
                promiscuous_mode=True,
            ),
            NetworkConfig(network_type=NetworkType.INTERNAL, adapter_name="lab-net"),
        ],
        boot=BootConfig(order=["disk", "dvd", "network", "none"], ioapic=True, hpet=True),
        storage_controllers=[
            StorageController(
                name="SATA Controller",
                bus=BusType.SATA,
                port_count=2,
                bootable=True,
            ),
            StorageController(name="SAS Controller", bus=BusType.SAS, port_count=16),
            StorageController(name="IDE Controller", bus=BusType.IDE, port_count=2),
        ],
        description="every field set",
        audio_enabled=True,
        usb_enabled=True,
    )


# ---------------------------------------------------------------------------
# Golden regression baseline
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "label",
    [
        "bios_minimal",
        "efi_secureboot",
        "iso_attached",
        "multidisk",
        "multinic",
        "floppy_first",
    ],
)
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
    cmds = VirtualBoxEmitter(vm_full.name).emit_create_vm(vm_full).as_argv_lists()
    assert cmds[0][:2] == ["VBoxManage", "createvm"]
    assert "--register" in cmds[0]


def test_every_command_targets_this_vm(vm_full):
    """No command may reference a VM other than the one being created."""
    cmds = VirtualBoxEmitter(vm_full.name).emit_create_vm(vm_full).as_argv_lists()
    for cmd in cmds:
        if cmd[1] in ("modifyvm", "storagectl", "storageattach", "createvm"):
            assert vm_full.name in cmd, cmd


def test_a_guest_os_is_emitted_as_the_id_createvm_accepts():
    """VirtualBox reports "Red Hat (64-bit)" and accepts only "RedHat_64".

    Since A-05 the parser turns the description into vmctl's neutral id, and the
    emitter turns that back into the id the product takes -- so a config is
    portable and the command is still valid.
    """
    vm = parse_label("multidisk")
    assert vm.guest_os == "rhel"
    cmds = VirtualBoxEmitter(vm.name).emit_create_vm(vm).as_argv_lists()
    assert cmds[0][cmds[0].index("--ostype") + 1] == "RedHat_64"


def test_a_guest_vmctl_cannot_name_neutrally_still_round_trips():
    """F-29 -- the hand-written map covered about forty descriptions, so any other
    guest exported to a config `createvm` rejected with "Unknown or invalid guest
    OS type given". The generated table covers all 227 the product knows."""
    from vmctl.providers.virtualbox.tables import GuestOSCodec

    assert GuestOSCodec().load("Debian 12 Bookworm (64-bit)") == "debian12"
    assert GuestOSCodec().dump("debian12") == "Debian12_64"
    # And one with no neutral id at all keeps VirtualBox's own, which is valid.
    assert GuestOSCodec().load("Windows 3.1") == "Windows31"
    assert GuestOSCodec().dump("Windows31") == "Windows31"


def test_a_native_os_id_in_a_config_passes_through(vm_minimal):
    """A 1.1.x config holds VirtualBox's own id, and it stays legal -- that is
    what "raw provider strings accepted as passthrough" means (A-05)."""
    vm_minimal.guest_os = "Windows2022_64"
    cmds = VirtualBoxEmitter(vm_minimal.name).emit_create_vm(vm_minimal).as_argv_lists()
    assert cmds[0][cmds[0].index("--ostype") + 1] == "Windows2022_64"


def test_the_neutral_default_is_the_1_1_x_default(vm_minimal):
    """The default was the VirtualBox id `Ubuntu_64`; it must still create that."""
    cmds = VirtualBoxEmitter(vm_minimal.name).emit_create_vm(vm_minimal).as_argv_lists()
    assert cmds[0][cmds[0].index("--ostype") + 1] == "Ubuntu_64"


def test_raw_format_is_forced_to_fixed_allocation():
    vm = build_minimal()
    vm.storage[0].format = DiskFormat.RAW
    vm.storage[0].allocation = Allocation.THIN
    create = [
        c
        for c in VirtualBoxEmitter(vm.name).emit_create_vm(vm).as_argv_lists()
        if c[1] == "createmedium"
    ][0]
    assert create[create.index("--variant") + 1] == "Fixed"
    assert create[create.index("--filename") + 1].endswith(".img")


def test_promiscuous_and_mac_are_emitted():
    vm = build_full()
    nic = [
        c for c in VirtualBoxEmitter(vm.name).emit_create_vm(vm).as_argv_lists() if "--nic2" in c
    ][0]
    assert "--promiscuous2" in nic
    assert nic[nic.index("--macaddress2") + 1] == "080027AA0001"


def test_every_boot_slot_is_emitted_including_the_empty_ones(vm_minimal):
    """F-48. This test used to assert the opposite -- that a ``none`` slot is skipped --
    which was written from the same assumption as the code and disproved by the host:
    a new VirtualBox VM defaults to floppy, dvd, **disk**, none, so leaving slot 3 unset
    left `disk` in it. A config asking for "disk, dvd, nothing, nothing" produced a VM
    that disagreed with it for ever. Found by `vmctl selftest` (E-19), which reads a
    created VM back and compares it.
    """
    import re as _re

    cmds = VirtualBoxEmitter(vm_minimal.name).emit_create_vm(vm_minimal).as_argv_lists()

    boot_flags = [tok for c in cmds for tok in c if _re.fullmatch(r"--boot\d", tok)]
    assert boot_flags == ["--boot1", "--boot2", "--boot3", "--boot4"]
    emitted = [c for c in cmds if "--boot3" in c][0]
    assert emitted[emitted.index("--boot3") + 1] == "none"


# ---------------------------------------------------------------------------
# Known-broken behaviour, pinned
# ---------------------------------------------------------------------------


def test_minimal_config_creates_its_controller_before_attaching():
    """F-01 — the README's own example config cannot be imported."""
    cmds = VirtualBoxEmitter("minimal-vm").emit_create_vm(build_minimal()).as_argv_lists()
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
    cmds = VirtualBoxEmitter(vm.name).emit_create_vm(vm).as_argv_lists()

    created = [c[c.index("--filename") + 1] for c in cmds if c[1] == "createmedium"]
    assert len(created) == 1, f"a medium was created for the optical drive: {created}"

    dvd_attach = [c for c in cmds if c[1] == "storageattach" and "dvddrive" in c][0]
    medium = dvd_attach[dvd_attach.index("--medium") + 1]
    assert medium == "emptydrive" or medium.lower().endswith(".iso"), medium
    assert medium not in created


@pytest.mark.parametrize(
    "flag",
    [
        "--pae",
        "--nested-hw-virt",
        "--cpuhotplug",
        "--cpuexecutioncap",
        "--pagefusion",
        "--hpet",
        "--clipboard-mode",
        "--draganddrop",
    ],
)
def test_every_configured_field_reaches_a_command(flag):
    vm = build_full()
    vm.clipboard_mode = "bidirectional"
    vm.draganddrop = "bidirectional"
    flat = {
        token
        for cmd in VirtualBoxEmitter(vm.name).emit_create_vm(vm).as_argv_lists()
        for token in cmd
    }
    assert flag in flat


def test_description_is_not_double_quoted():
    vm = build_minimal()
    vm.description = "a lab vm"
    desc = [
        c
        for c in VirtualBoxEmitter(vm.name).emit_create_vm(vm).as_argv_lists()
        if "--description" in c
    ][0]
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
    attach = [
        c
        for c in VirtualBoxEmitter(vm.name).emit_create_vm(vm).as_argv_lists()
        if c[1] == "storageattach"
    ][0]
    assert attach[attach.index("--storagectl") + 1] == "SATA"


def test_floppy_controller_is_not_created_as_sata():
    vm = parse_label("floppy_first")
    ctl = [
        c
        for c in VirtualBoxEmitter(vm.name).emit_create_vm(vm).as_argv_lists()
        if c[1] == "storagectl" and "Floppy" in c
    ][0]
    assert ctl[ctl.index("--add") + 1] != "sata"


def test_solid_state_is_an_attachment_flag_not_a_medium_format():
    """`--nonrotational on` rides on the attach, not on `createmedium` (M-01).

    Which is the reason `ssd` was never a device kind: the medium is identical,
    and what changes is how the attachment presents it to the guest.
    """
    vm = VMConfig(
        name="ssd-vm",
        cpu=CPUConfig(),
        memory=MemoryConfig(),
        firmware=FirmwareConfig(),
        storage=[
            StorageDevice(name="fast", size_mb=1024, nonrotational=True),
            StorageDevice(name="slow", size_mb=1024, slot=1),
            StorageDevice(name="cd", kind=DeviceKind.CDROM, nonrotational=True, slot=2),
        ],
        networks=[],
        boot=BootConfig(),
        storage_controllers=[],
    )
    attaches = [
        c
        for c in VirtualBoxEmitter(vm.name).emit_create_vm(vm).as_argv_lists()
        if c[1] == "storageattach"
    ]
    flagged = ["--nonrotational" in c for c in attaches]
    assert flagged == [True, False, False], "only the disk that asked for it, and no optical drive"
    creates = [
        c
        for c in VirtualBoxEmitter(vm.name).emit_create_vm(vm).as_argv_lists()
        if c[1] == "createmedium"
    ]
    assert not any("--nonrotational" in c for c in creates)


def test_trim_passthrough_and_hotplug_ride_on_the_attachment():
    """`discard` and `hotpluggable` are attachment properties, like nonrotational.

    Measured on VirtualBox 7.1.18: every disk bus accepts `--discard`, and only
    SATA and USB accept `--hotpluggable` -- the rest answer "Controller 'x' does
    not support changing the hot-pluggable device flag" (M-02).
    """
    from vmctl.core.translate import Policy

    vm = VMConfig(
        name="flags-vm",
        cpu=CPUConfig(),
        memory=MemoryConfig(),
        firmware=FirmwareConfig(),
        storage=[
            StorageDevice(name="sata", size_mb=1024, discard=True, hotpluggable=True),
            StorageDevice(name="sas", size_mb=1024, bus=BusType.SAS, hotpluggable=True),
        ],
        networks=[],
        boot=BootConfig(),
        storage_controllers=[],
    )
    emitter = VirtualBoxEmitter(vm.name, policy=Policy.NEAREST)
    attaches = [c for c in emitter.emit_create_vm(vm).as_argv_lists() if c[1] == "storageattach"]
    assert "--discard" in attaches[0] and "--hotpluggable" in attaches[0]
    # The SAS bus cannot carry the flag, so it is reported rather than emitted.
    assert "--hotpluggable" not in attaches[1]
    assert any("hot-pluggable" in d.reason for d in emitter.report.drops)


def test_a_device_can_name_its_controller_by_logical_id():
    """The id is what makes a config portable: `sata0` means the same everywhere,
    while "SATA Controller" is a VirtualBox string (M-02)."""
    vm = VMConfig(
        name="id-vm",
        cpu=CPUConfig(),
        memory=MemoryConfig(),
        firmware=FirmwareConfig(),
        storage=[StorageDevice(name="root", size_mb=1024, controller="sata0")],
        networks=[],
        boot=BootConfig(),
        storage_controllers=[
            StorageController(id="sata0", bus=BusType.SATA, native_name="Fast Controller")
        ],
    )
    commands = VirtualBoxEmitter(vm.name).emit_create_vm(vm).as_argv_lists()
    attach = [c for c in commands if c[1] == "storageattach"][0]
    # The plan uses the provider's own name, because that is what VBoxManage takes.
    assert attach[attach.index("--storagectl") + 1] == "Fast Controller"


def test_a_nic_model_virtualbox_lacks_is_substituted_and_reported():
    """Measured: VirtualBox answers "Invalid NIC type 'e1000e' specified for NIC 1",
    so four of the model's chipsets are genuinely absent here (A-10)."""
    from vmctl.core.platform import NicModel
    from vmctl.core.translate import Policy

    vm = build_minimal()
    vm.networks[0].model = NicModel.VMXNET3
    emitter = VirtualBoxEmitter(vm.name, policy=Policy.NEAREST)
    nic = [c for c in emitter.emit_create_vm(vm).as_argv_lists() if "--nic1" in c][0]
    assert nic[nic.index("--nictype1") + 1] == "82540EM"
    assert any(s.field == "networks[0].model" for s in emitter.report.substitutions)


def test_a_nic_model_virtualbox_lacks_is_refused_under_strict():
    from vmctl.core.exceptions import ValidationError
    from vmctl.core.platform import NicModel

    vm = build_minimal()
    vm.networks[0].model = NicModel.RTL8139
    with pytest.raises(ValidationError, match="network chipset"):
        VirtualBoxEmitter(vm.name).emit_create_vm(vm)


def test_the_exact_intel_variant_survives_a_round_trip():
    """A-10 -- 82540EM, 82543GC and 82545EM are all `e1000` to the model. The one
    the VM had is kept as a native hint, so re-creating it gives the guest back the
    same card rather than the family's default."""
    vm = parse_label("multinic")
    kept = [n for n in vm.networks if n.provider_options][0]
    assert kept.provider_options["virtualbox"]["nictype"] == "82545EM"
    cmds = VirtualBoxEmitter(vm.name).emit_create_vm(vm).as_argv_lists()
    nictypes = [c[c.index("--nictype4") + 1] for c in cmds if "--nictype4" in c]
    assert nictypes == ["82545EM"]


def test_editing_the_model_overrides_a_kept_native_chipset():
    """The hint is fidelity, not a lock: changing the model has to win."""
    from vmctl.core.platform import NicModel

    vm = parse_label("multinic")
    net = [n for n in vm.networks if n.provider_options][0]
    net.model = NicModel.VIRTIO
    cmds = VirtualBoxEmitter(vm.name).emit_create_vm(vm).as_argv_lists()
    nictypes = [c[c.index("--nictype4") + 1] for c in cmds if "--nictype4" in c]
    assert nictypes == ["virtio"]


def test_settings_virtualbox_cannot_express_are_reported():
    """A CPU topology, a CPU model, a machine type and an architecture all have no
    VirtualBox equivalent. The VM is still worth creating -- but a setting that
    vanishes without a word is how a config comes to describe a machine that does
    not exist (A-04/A-10)."""
    from vmctl.core.platform import Arch
    from vmctl.core.translate import Policy

    vm = build_minimal()
    vm.cpu.count, vm.cpu.sockets, vm.cpu.cores = 4, 2, 2
    vm.cpu.model = "host"
    vm.machine = "q35"
    vm.arch = Arch.AARCH64
    emitter = VirtualBoxEmitter(vm.name, policy=Policy.NEAREST)
    emitter.emit_create_vm(vm)
    dropped = {d.field for d in emitter.report.drops}
    assert {"cpu", "cpu.model", "machine", "arch"} <= dropped


def test_audio_turns_the_streams_on_not_just_the_device(vm_full):
    """F-53: measured on 7.1.18, a VM given only `--audio-enabled on` reports
    `audio_out="off"` and `audio_in="off"` -- a sound device with both streams shut. The
    parser reads those two keys (correctly: `audio=` names the driver, not the state,
    which was F-21), so the VM disagreed with the file that made it, and the guest had
    no audio either."""
    vm_full.audio_enabled = True

    cmds = VirtualBoxEmitter(vm_full.name).emit_create_vm(vm_full).as_argv_lists()
    audio = [c for c in cmds if "--audio-driver" in c][0]

    assert audio[audio.index("--audio-out") + 1] == "on"
    assert audio[audio.index("--audio-in") + 1] == "on"
