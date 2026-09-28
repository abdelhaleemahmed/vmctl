"""VMware Workstation provider tests (P-02).

Hermetic: the emitter is a pure function to a ``.vmx`` and the parser a pure function
from one. The real product was used to establish *what* to emit -- every table in
this provider was measured on VMware Workstation 17 -- and those measurements are
what these tests pin.

The three rules that came from the product rather than the model are each covered
below, because each one is a VM that silently does the wrong thing if it is dropped:
one key at most, the PCI bridges, and port limits vmctl has to enforce because
VMware will not.
"""

import os

import pytest

from vmctl.core.devices import Allocation, BusType, DeviceKind, DiskFormat
from vmctl.core.exceptions import ProviderError, ValidationError
from vmctl.core.plan import StepKind
from vmctl.core.platform import NicModel
from vmctl.core.storage import directory
from vmctl.core.translate import Policy, Translator
from vmctl.core.vmconfig import (
    BootConfig,
    CPUConfig,
    FirmwareConfig,
    FirmwareType,
    MemoryConfig,
    NetworkConfig,
    NetworkType,
    StorageDevice,
    VMConfig,
)
from vmctl.providers.vmware.capabilities import VMwareCapabilities
from vmctl.providers.vmware.emitter import VMwareEmitter
from vmctl.providers.vmware.parser import VMwareParser


@pytest.fixture
def emitter():
    return VMwareEmitter(
        "demo",
        location=directory("/vms", nest_per_vm=True),
        tools_dir="",
    )


@pytest.fixture
def parser():
    return VMwareParser()


@pytest.fixture
def vm():
    return VMConfig(
        name="demo",
        cpu=CPUConfig(count=2, cores=2),
        memory=MemoryConfig(mb=512),
        firmware=FirmwareConfig(),
        storage=[
            StorageDevice(
                name="root",
                size_mb=64,
                format=DiskFormat.VMDK,
                bus=BusType.SCSI,
                nonrotational=True,
                bootable=True,
            ),
            StorageDevice(name="cd", kind=DeviceKind.CDROM, bus=BusType.SATA, source="in.iso"),
        ],
        networks=[NetworkConfig(network_type=NetworkType.NAT, model=NicModel.E1000E)],
        boot=BootConfig(order=["disk", "dvd", "none", "none"]),
        storage_controllers=[],
        guest_os="debian12",
    )


def _keys(emitter, vm, translator=None):
    return emitter.build_vmx(vm, translator)


# ---------------------------------------------------------------------------
# The rules the product imposes
# ---------------------------------------------------------------------------


def test_no_key_is_written_twice(emitter, vm):
    """A duplicate makes VMware refuse the whole file -- "Cannot read the virtual
    machine configuration" -- so the emitter builds a mapping rather than appending
    lines, and the rule is enforced by the structure."""
    text = emitter.render(_keys(emitter, vm))
    written = [line.split("=")[0].strip() for line in text.splitlines() if "=" in line]
    assert len(written) == len(set(written)), "a .vmx may not repeat a key"


def test_the_pci_bridges_are_there(emitter, vm):
    """Without them a PCIe device is refused with "Device nvme0 requested without
    secondary PCI slots available", which reads exactly like NVMe being absent."""
    keys = _keys(emitter, vm)
    for bridge in ("pciBridge0", "pciBridge4", "pciBridge5", "pciBridge6", "pciBridge7"):
        assert keys[f"{bridge}.present"] == "TRUE"
    assert keys["pciBridge4.virtualDev"] == "pcieRootPort"


def test_an_nvme_disk_can_be_expressed(emitter, vm):
    """The bus that the missing bridges made look unsupported."""
    vm.storage[0].bus = BusType.NVME
    keys = _keys(emitter, vm)
    assert keys["nvme0.present"] == "TRUE"
    assert keys["nvme0:0.fileName"] == "demo_root.vmdk"


def test_a_device_beyond_the_port_count_is_refused_here():
    """VMware *silently drops* a device it cannot place: sata0:30 powers on happily
    without the disk. Nothing but vmctl's own check stands between that config and a
    VM that boots with no disk (A-06)."""
    from vmctl.core.slots import place

    vm = VMConfig(
        name="toomany",
        cpu=CPUConfig(),
        memory=MemoryConfig(),
        firmware=FirmwareConfig(),
        storage=[StorageDevice(name="d", size_mb=64, bus=BusType.SATA, slot=30)],
        networks=[],
        boot=BootConfig(),
        storage_controllers=[],
    )
    with pytest.raises(ValidationError, match="slot 30"):
        place(vm, VMwareCapabilities.get())


# ---------------------------------------------------------------------------
# What the file says
# ---------------------------------------------------------------------------


def test_the_plan_writes_one_file_and_a_disk_per_device(emitter, vm):
    plan = emitter.emit_create_vm(vm)
    kinds = [s.kind for s in plan]
    assert kinds.count(StepKind.WRITE_FILE) == 1
    disks = [s for s in plan if s.argv and "vdiskmanager" in s.argv[0]]
    assert len(disks) == 1  # the CD-ROM holds an existing medium
    assert disks[0].argv[-1].endswith("demo_root.vmdk")


def test_a_thick_disk_is_a_different_vdiskmanager_type(emitter, vm):
    """Type 0 grows, type 2 is preallocated. The allocation is not a .vmx key at all
    -- it is decided when the disk is made."""
    vm.storage[0].allocation = Allocation.THICK
    create = [s for s in emitter.emit_create_vm(vm) if s.argv and "vdiskmanager" in s.argv[0]][0]
    assert create.argv[create.argv.index("-t") + 1] == "2"


def test_disks_are_named_by_file_not_by_path(emitter, vm):
    """A .vmx that names its disks by bare file name can be moved or copied, which is
    how VMware writes one itself."""
    assert _keys(emitter, vm)["scsi0:0.fileName"] == "demo_root.vmdk"


def test_solid_state_is_a_key_on_the_device(emitter, vm):
    assert _keys(emitter, vm)["scsi0:0.virtualSSD"] == "TRUE"


def test_scsi_says_which_adapter_it_is(emitter, vm):
    """Plain SCSI, SAS and the paravirtual adapter are all the `scsi` bus with
    different virtualDev values, which is why one neutral bus cannot cover them."""
    assert _keys(emitter, vm)["scsi0.virtualDev"] == "lsilogic"
    vm.storage[0].bus = BusType.SAS
    assert _keys(emitter, vm)["scsi0.virtualDev"] == "lsisas1068"


def test_ide_is_addressed_by_channel_and_unit(emitter, vm):
    """Measured: ide0:2 is ignored, so IDE has two devices per channel and the key's
    numbers mean something different from every other bus."""
    vm.storage[0].bus = BusType.IDE
    vm.storage[0].slot, vm.storage[0].unit = 1, 1
    assert "ide1:1.present" in _keys(emitter, vm)


def test_an_empty_optical_drive_is_still_a_drive(emitter, vm):
    """F-22, in VMware's spelling: a drive with no medium is `atapi-cdrom`."""
    vm.storage[1].source = None
    keys = _keys(emitter, vm)
    assert keys["sata0:0.present"] == "TRUE"
    assert keys["sata0:0.deviceType"] == "atapi-cdrom"


def test_the_boot_order_uses_vmwares_words(emitter, vm):
    assert _keys(emitter, vm)["bios.bootOrder"] == "hdd,cdrom"


def test_a_virtualbox_mac_is_reformatted(emitter, vm):
    vm.networks[0].mac_address = "080027AA0001"
    keys = _keys(emitter, vm)
    assert keys["ethernet0.address"] == "08:00:27:aa:00:01"
    assert keys["ethernet0.addressType"] == "static"


def test_secure_boot_and_a_tpm_are_keys(emitter, vm):
    vm.firmware.type = FirmwareType.EFI64
    vm.firmware.secure_boot = True
    vm.firmware.tpm = True
    keys = _keys(emitter, vm)
    assert keys["firmware"] == "efi"
    assert keys["uefi.secureBoot.enabled"] == "TRUE"
    assert keys["vtpm.present"] == "TRUE"


# ---------------------------------------------------------------------------
# Guest OS ids, which VMware validates
# ---------------------------------------------------------------------------


def test_a_guest_os_is_translated_to_vmwares_id(emitter, vm):
    assert _keys(emitter, vm)["guestOS"] == "debian12-64"


def test_windows_10_is_still_called_windows_9(emitter, vm):
    """VMware never renamed the id after Windows 9 became Windows 10."""
    vm.guest_os = "win10"
    assert _keys(emitter, vm)["guestOS"] == "windows9-64"


def test_a_guest_os_vmware_does_not_have_is_substituted(emitter, vm):
    """Measured: an unknown id is refused at power-on with "[msg.guestos.badname]
    Guest operating system 'x' is not supported", so passing one through would fail
    at the last possible moment."""
    vm.guest_os = "haiku"
    translator = Translator(VMwareCapabilities.get(), Policy.NEAREST)
    assert emitter.build_vmx(vm, translator)["guestOS"] == "other-64"
    assert any(s.field == "guest_os" for s in translator.report.substitutions)


def test_arch_and_alpine_fall_back_to_generic_linux(emitter, vm):
    """Measured: `arch-64` and `alpine-64` are not ids VMware has."""
    for guest in ("archlinux", "alpine"):
        vm.guest_os = guest
        assert _keys(emitter, vm)["guestOS"] == "other6xlinux-64", guest


# ---------------------------------------------------------------------------
# Reading a .vmx back
# ---------------------------------------------------------------------------


def test_a_vmx_round_trips(emitter, parser, vm):
    text = emitter.render(_keys(emitter, vm))
    once = parser.parse_text("demo", text)
    assert once.name == "demo"
    assert once.guest_os == "debian12"
    assert (once.cpu.count, once.cpu.cores, once.cpu.sockets) == (2, 2, 1)
    assert once.memory.mb == 512
    by_bus = {d.bus: d for d in once.storage}
    assert by_bus[BusType.SCSI].nonrotational is True
    assert by_bus[BusType.SCSI].bootable is True
    assert by_bus[BusType.SATA].kind is DeviceKind.CDROM
    assert by_bus[BusType.SATA].source == "in.iso"
    assert once.networks[0].model is NicModel.E1000E


def test_emitting_twice_is_stable(emitter, parser, vm):
    """A-07's property. VMware appends 28 keys of its own on first power-on -- PCI
    slot numbers, UUIDs, a generated MAC -- so "emit == input" was never the test;
    the second generation onwards being fixed is."""
    first = emitter.render(_keys(emitter, vm))
    second = emitter.render(emitter.build_vmx(parser.parse_text("demo", first)))
    third = emitter.render(emitter.build_vmx(parser.parse_text("demo", second)))
    assert second == third


def test_the_keys_vmware_adds_are_ignored(parser, emitter, vm):
    """A real .vmx after a power-on carries slot numbers, UUIDs and a scoreboard file
    name. None of it is vmctl's, and none of it may confuse the read."""
    text = emitter.render(_keys(emitter, vm)) + "\n".join(
        [
            'uuid.bios = "56 4d 90 9b 67 ac c4 b1-af e6 2b 4b 2a df 16 c4"',
            'scsi0.pciSlotNumber = "16"',
            'ethernet0.generatedAddress = "00:0c:29:df:16:c4"',
            'svga.vramSize = "268435456"',
            'cleanShutdown = "FALSE"',
        ]
    )
    once = parser.parse_text("demo", text)
    assert len(once.storage) == 2
    assert len(once.networks) == 1


def test_a_repeated_key_is_refused_rather_than_guessed(parser):
    """VMware will not read such a file, so vmctl does not pretend to know which one
    was meant."""
    with pytest.raises(ProviderError, match="repeated key"):
        parser.parse_text("x", 'memsize = "512"\nmemsize = "1024"\n')


def test_a_file_with_no_settings_is_refused(parser):
    with pytest.raises(ProviderError, match="no .vmx settings"):
        parser.parse_text("x", "# just a comment\n")


def test_the_scalar_settings_come_from_the_shared_field_table():
    """A-11's cheapest payoff: a .vmx is key/value, so reading cpu, memory, firmware
    and the guest OS is one declaration rather than one function per field."""
    from vmctl.providers.vmware.tables import FIELDS

    paths = {field.path for field in FIELDS}
    assert {"memory.mb", "cpu.count", "firmware.type", "guest_os"} <= paths


def test_a_paravirtual_scsi_adapter_reads_as_scsi(parser):
    """vmctl has no neutral name for pvscsi, so it reads as the SCSI bus it is."""
    text = (
        'displayName = "x"\nscsi0.present = "TRUE"\nscsi0.virtualDev = "pvscsi"\n'
        'scsi0:0.present = "TRUE"\nscsi0:0.fileName = "d.vmdk"\nscsi0:0.deviceType = "disk"\n'
    )
    assert parser.parse_text("x", text).storage[0].bus is BusType.SCSI


# ---------------------------------------------------------------------------
# The capability declaration against its probe
# ---------------------------------------------------------------------------


def test_the_declaration_records_that_it_was_measured():
    caps = VMwareCapabilities.get()
    assert "vmware_attach_matrix.json" in caps.evidence
    assert "17" in caps.evidence


def test_nvme_carries_disks_but_no_optical_drive():
    """The one refusal in the matrix, and the same one VirtualBox makes."""
    caps = VMwareCapabilities.get()
    assert caps.can_attach(DeviceKind.DISK, BusType.NVME)
    assert not caps.can_attach(DeviceKind.CDROM, BusType.NVME)


def test_the_measured_port_limits_are_declared():
    """sata0:30, scsi0:16, nvme0:64 and ide0:2 were all ignored by VMware."""
    caps = VMwareCapabilities.get()
    assert caps.bus(BusType.SATA).max_ports == 30
    assert caps.bus(BusType.SCSI).max_ports == 16
    assert caps.bus(BusType.NVME).max_ports == 64
    assert caps.bus(BusType.IDE).max_ports == 2
    assert caps.bus(BusType.IDE).units_per_port == 2


def test_vmdk_is_the_only_format():
    """Not a simplification: a .vmx cannot attach anything else, and VMware's own
    converter reads nothing else either."""
    caps = VMwareCapabilities.get()
    creatable = {f.value for f, spec in caps.formats.items() if spec.support.creatable}
    assert creatable == {"vmdk"}
    assert not caps.format_spec(DiskFormat.QCOW2).support.usable


def test_the_converter_will_not_pretend_to_read_qcow2():
    """Which makes migrating onto VMware from libvirt a job for qemu-img first, and
    vmctl says so instead of producing a VM that cannot boot."""
    from vmctl.providers.vmware.convert import VDiskManagerConverter

    converter = VDiskManagerConverter()
    assert converter.can_convert(DiskFormat.VMDK, DiskFormat.VMDK)
    assert not converter.can_convert(DiskFormat.QCOW2, DiskFormat.VMDK)
    assert not converter.can_convert(DiskFormat.VMDK, DiskFormat.QCOW2)


def test_vmware_has_no_virtio_nic():
    """Measured: `ethernet0.virtualDev = "virtio"` is refused."""
    caps = VMwareCapabilities.get()
    assert caps.native_nic_model(NicModel.VIRTIO) is None
    assert caps.native_nic_model(NicModel.E1000E) == "e1000e"
    assert caps.native_nic_model(NicModel.VMXNET3) == "vmxnet3"


def test_there_is_no_machine_type_to_choose():
    """The chipset follows virtualHW.version, which is not a per-VM choice."""
    assert VMwareCapabilities.get().machine_types == ()


# ---------------------------------------------------------------------------
# A VM is a directory (the backend)
# ---------------------------------------------------------------------------


@pytest.fixture
def backend(tmp_path):
    from vmctl.providers.vmware.backend import VMwareBackend

    return VMwareBackend(state_dir=str(tmp_path / "vms"), tools_dir="")


def _apply(plan, base):
    """Carry out a plan's file steps in-process, so no shell is needed."""
    import os

    for step in plan:
        if step.kind is StepKind.EXEC and step.argv and step.argv[0] == "mkdir":
            os.makedirs(step.argv[-1], exist_ok=True)
        elif step.kind is StepKind.WRITE_FILE and step.path is not None:
            step.path.parent.mkdir(parents=True, exist_ok=True)
            step.path.write_text(step.content or "")


def test_a_directory_with_a_vmx_in_it_is_a_vm(backend, vm):
    """VMware's own inventory is whatever its GUI has opened, which is not a fact
    about the host -- so listing VMs is listing directories, as with QEMU."""
    assert backend.list_vms() == []
    _apply(backend.create_vm(vm, execute=False), backend.state_dir)
    assert backend.list_vms() == ["demo"]
    assert backend.vm_exists("demo")


def test_a_created_vm_reads_back(backend, vm):
    _apply(backend.create_vm(vm, execute=False), backend.state_dir)
    again = backend.read_vm("demo")
    assert again.memory.mb == vm.memory.mb
    assert again.guest_os == "debian12"


def test_reading_a_vm_that_is_not_there_says_so(backend):
    from vmctl.core.exceptions import VMNotFoundError

    with pytest.raises(VMNotFoundError):
        backend.read_vm("absent")


def test_the_tools_directory_distinguishes_on_path_from_missing(tmp_path):
    """Three answers, not two. Collapsing "" (on PATH) with None (absent) -- an empty
    string is falsy -- made a provider that had found its tools report itself
    missing."""
    from vmctl.providers.vmware.backend import VMwareBackend

    assert VMwareBackend(tools_dir="").tools_dir == ""
    assert VMwareBackend(tools_dir="/opt/vmware").tools_dir == "/opt/vmware"


def test_the_state_directory_is_per_vm(backend):
    """VMware's own convention: the .vmx, the disks, the NVRAM and the logs sit
    together, which is also what makes delete complete."""
    location = backend.storage_location()
    assert location.nest_per_vm
    assert location.image_path("demo", "demo.vmx").endswith("demo/demo.vmx")


def test_a_plan_built_here_can_name_windows_paths():
    """A-09's separator, doing the job it was added for: vmctl runs on Linux and
    VMware is on a Windows host, so the plan's paths are the *target's*."""
    windows = directory(r"C:\VMs", separator="\\", nest_per_vm=True)
    emitter = VMwareEmitter(
        "demo",
        location=windows,
        tools_dir=r"C:\Program Files\VMware\VMware Workstation",
    )
    assert emitter.vmx_path("demo") == r"C:\VMs\demo\demo.vmx"
    assert emitter.tool("vmrun") == r"C:\Program Files\VMware\VMware Workstation\vmrun"


def test_an_empty_drive_is_not_the_hosts_drive(emitter, vm):
    """F-33 -- `autodetect = "TRUE"` makes VMware reach for the host's optical
    device. A real power-on said "Unable to process CD-ROM device 'Z:'", and on a
    host that had a disc in it the guest would have seen it."""
    vm.storage[1].source = None
    keys = _keys(emitter, vm)
    assert keys["sata0:0.autodetect"] == "FALSE"
    assert keys["sata0:0.startConnected"] == "FALSE"


def test_a_vm_with_no_floppy_says_so(emitter, vm):
    """VMware's defaults add one pointed at the host's A: and then complain about it
    on every power-on. A VM vmctl creates has the devices the config asked for."""
    assert _keys(emitter, vm)["floppy0.present"] == "FALSE"


def test_a_vm_with_a_floppy_keeps_it(emitter, vm):
    vm.storage.append(
        StorageDevice(name="fd", kind=DeviceKind.FLOPPY, bus=BusType.FLOPPY, source="boot.flp")
    )
    keys = _keys(emitter, vm)
    assert keys.get("floppy0.present") != "FALSE"
    assert keys["floppy0.fileName"] == "boot.flp"


def test_a_version_vmware_cannot_record_is_reported(emitter, vm):
    """VMware has one id for every Ubuntu, so `ubuntu22.04` is stored as `ubuntu-64`
    and reads back as `ubuntu`. Losing the version is unavoidable; losing it in
    silence is not -- it showed up as the one difference in a real round trip."""
    vm.guest_os = "ubuntu22.04"
    translator = Translator(VMwareCapabilities.get(), Policy.NEAREST)
    assert emitter.build_vmx(vm, translator)["guestOS"] == "ubuntu-64"
    reported = [s for s in translator.report.substitutions if s.field == "guest_os"]
    assert reported and reported[0].used == "ubuntu"


def test_a_version_vmware_does_record_is_not_reported(emitter, vm):
    vm.guest_os = "debian12"
    translator = Translator(VMwareCapabilities.get(), Policy.NEAREST)
    emitter.build_vmx(vm, translator)
    assert not [s for s in translator.report.substitutions if s.field == "guest_os"]


# ---------------------------------------------------------------------------
# F-39: editing must not make the disks again
# ---------------------------------------------------------------------------


def test_editing_does_not_run_vdiskmanager_over_an_existing_disk(emitter, vm):
    """The `.vmx` *is* the configuration, so changing a VM means writing it again --
    and the plan that writes it also created the disks (F-39)."""
    import copy

    current = copy.deepcopy(vm)
    current.storage[0].disk_path = "/vms/demo/demo_system.vmdk"
    desired = copy.deepcopy(current)
    desired.memory.mb = 1024

    plan = emitter.emit_modify_vm(current, desired)

    assert not [step for step in plan if "vmware-vdiskmanager" in " ".join(step.argv or [])]
    # ...and the file is still written, because that is the actual change.
    assert [step for step in plan if step.kind is StepKind.WRITE_FILE]


# ---------------------------------------------------------------------------
# F-43: availability has to be asked the way the provider asks it
# ---------------------------------------------------------------------------


def test_vmware_is_available_when_installed_but_not_on_path(monkeypatch):
    """Measured on the real host: Workstation's installer does not put its tools on
    PATH, so checking PATH reported "not installed" on a machine where vmctl had just
    created and deleted a VM. `vmctl providers` said unavailable, auto-detection never
    chose VMware, and `vmctl doctor` agreed with both."""
    import shutil as _shutil

    from vmctl.providers.vmware import backend as vmware_backend

    installed = vmware_backend.TOOL_DIRS[0]
    monkeypatch.setattr(_shutil, "which", lambda name: None)
    monkeypatch.setattr(
        vmware_backend.os.path,
        "isfile",
        lambda path: path
        in (os.path.join(installed, "vmrun"), os.path.join(installed, "vmrun.exe")),
    )

    assert vmware_backend.VMwareBackend.is_available() is True
    assert vmware_backend.VMwareBackend().tools_dir == installed


def test_vmware_is_unavailable_when_nothing_is_there(monkeypatch):
    import shutil as _shutil

    from vmctl.providers.vmware import backend as vmware_backend

    monkeypatch.setattr(_shutil, "which", lambda name: None)
    monkeypatch.setattr(vmware_backend.os.path, "isfile", lambda path: False)

    assert vmware_backend.VMwareBackend.is_available() is False


def test_tools_on_path_still_count_as_installed(monkeypatch):
    """The empty string means "on PATH", and it is falsy -- which is how a provider
    that had found its tools once reported itself missing."""
    import shutil as _shutil

    from vmctl.providers.vmware import backend as vmware_backend

    monkeypatch.setattr(vmware_backend.os.path, "isfile", lambda path: False)
    monkeypatch.setattr(_shutil, "which", lambda name: "/usr/bin/vmrun")

    assert vmware_backend.VMwareBackend.find_tools() == ""
    assert vmware_backend.VMwareBackend.is_available() is True
