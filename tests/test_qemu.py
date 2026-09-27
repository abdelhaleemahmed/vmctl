"""Plain QEMU provider tests (P-05).

Hermetic: the emitter is a pure function to a command line and the parser a pure
function from one, so nothing here starts a VM. The real lifecycle is exercised
separately against QEMU 10.1.0.

This provider is the one that asks whether the abstraction is about *hypervisors*
or only about managers of them. QEMU keeps no state: a VM is a directory with a run
script in it, and reading a VM means parsing the command line that starts it.
"""

import copy

import pytest

from vmctl.core.devices import BusType, DeviceKind, DiskFormat
from vmctl.core.plan import StepKind
from vmctl.core.platform import Arch, NicModel
from vmctl.core.storage import directory
from vmctl.core.translate import Policy, Translator
from vmctl.core.vmconfig import (
    BootConfig,
    CPUConfig,
    FirmwareConfig,
    MemoryConfig,
    NetworkConfig,
    NetworkType,
    StorageDevice,
    VMConfig,
)
from vmctl.providers.qemu.capabilities import QemuCapabilities
from vmctl.providers.qemu.emitter import QemuEmitter
from vmctl.providers.qemu.parser import QemuParser


@pytest.fixture
def emitter():
    return QemuEmitter(
        "demo",
        location=directory("/vms", nest_per_vm=True),
        binary="/usr/libexec/qemu-kvm",
        accel="kvm",
    )


@pytest.fixture
def parser():
    return QemuParser()


@pytest.fixture
def vm():
    return VMConfig(
        name="demo",
        cpu=CPUConfig(count=4, sockets=2, cores=2, threads=1),
        memory=MemoryConfig(mb=512),
        firmware=FirmwareConfig(),
        storage=[
            StorageDevice(
                name="root",
                size_mb=64,
                format=DiskFormat.QCOW2,
                bus=BusType.VIRTIO_BLK,
                discard=True,
                bootable=True,
            ),
            StorageDevice(name="cd", kind=DeviceKind.CDROM, bus=BusType.SATA),
        ],
        networks=[NetworkConfig(network_type=NetworkType.NAT, model=NicModel.VIRTIO)],
        boot=BootConfig(order=["disk", "dvd", "none", "none"]),
        storage_controllers=[],
    )


def _argv(emitter, vm, translator=None):
    return emitter.build_argv(vm, translator)


# ---------------------------------------------------------------------------
# The command line is the native format
# ---------------------------------------------------------------------------


def test_the_plan_writes_a_script_and_makes_it_runnable(emitter, vm):
    """QEMU has nowhere to put a definition, so the script *is* the VM."""
    plan = emitter.emit_create_vm(vm)
    kinds = [s.kind for s in plan]
    assert StepKind.WRITE_FILE in kinds
    assert plan.steps[-1].argv[:2] == ["chmod", "+x"]
    assert len(plan.native_artifacts()) == 1


def test_images_are_created_before_the_script_is_written(emitter, vm):
    verbs = [s.argv[0] if s.argv else "write" for s in emitter.emit_create_vm(vm)]
    assert verbs.index("qemu-img") < verbs.index("write")


def test_only_real_disks_get_an_image(emitter, vm):
    creates = [s for s in emitter.emit_create_vm(vm) if s.argv and s.argv[0] == "qemu-img"]
    assert len(creates) == 1
    assert "/vms/demo/demo_root.qcow2" in creates[0].argv


def test_creating_an_image_can_be_undone(emitter, vm):
    create = [s for s in emitter.emit_create_vm(vm) if s.argv and s.argv[0] == "qemu-img"][0]
    assert create.undo is not None and create.undo.destructive


def test_the_script_is_a_runnable_shell_script(emitter, vm):
    script = emitter.render_script(_argv(emitter, vm))
    assert script.startswith("#!/bin/sh")
    assert "exec /usr/libexec/qemu-kvm" in script
    # One option per line, so a person can read it and a diff is legible.
    assert "\n  -name demo \\\n" in script


def test_a_path_with_a_space_survives_the_script(emitter, vm):
    """The script is shell, so quoting is not optional -- and a state directory
    under a user's home is exactly where a space turns up."""
    spaced = QemuEmitter("demo", location=directory("/my vms", nest_per_vm=True))
    script = spaced.render_script(spaced.build_argv(vm))
    # The whole -drive value is one argv element, so the quoting goes round all of
    # it -- which is also what QEMU expects to receive.
    assert "'file=/my vms/demo/demo_root.qcow2" in script
    assert "-pidfile '/my vms/demo/qemu.pid'" in script


# ---------------------------------------------------------------------------
# What the command line says
# ---------------------------------------------------------------------------


def test_the_accelerator_is_stated_rather_than_left_to_a_fallback(emitter, vm):
    """Asking for kvm without /dev/kvm makes QEMU print two errors and fall back to
    tcg, so the backend decides and the command line says which."""
    argv = _argv(emitter, vm)
    assert argv[argv.index("-accel") + 1] == "kvm"


def test_the_cpu_topology_is_part_of_smp(emitter, vm):
    argv = _argv(emitter, vm)
    assert argv[argv.index("-smp") + 1] == "4,sockets=2,cores=2,threads=1"


def test_nested_virtualisation_asks_for_the_host_cpu(emitter, vm):
    vm.cpu.nested_virt = True
    argv = _argv(emitter, vm)
    assert argv[argv.index("-cpu") + 1] == "host"


def test_a_drive_is_separate_from_the_device_the_guest_sees(emitter, vm):
    """`if=none` throughout: the drive is the backing store and the -device is the
    guest's view, which is the only form that lets vmctl choose the bus rather than
    letting QEMU infer one from the file name."""
    argv = _argv(emitter, vm)
    drive = argv[argv.index("-drive") + 1]
    assert "if=none" in drive and "id=drive-root" in drive
    assert "virtio-blk-pci,drive=drive-root" in argv


def test_trim_passthrough_is_a_drive_option(emitter, vm):
    assert "discard=unmap" in _argv(emitter, vm)[1:][_argv(emitter, vm)[1:].index("-drive") + 1]


def test_a_controller_is_added_once_for_the_bus_that_needs_one(emitter, vm):
    argv = _argv(emitter, vm)
    assert "ich9-ahci,id=sata0" in argv  # SATA needs one
    assert not any(a.startswith("virtio-blk-pci,id=") for a in argv)  # virtio-blk does not


def test_an_empty_optical_drive_is_a_device_with_no_medium(emitter, vm):
    """The drive has to exist even with nothing in it, which is the whole of F-22."""
    argv = _argv(emitter, vm)
    drives = [argv[i + 1] for i, a in enumerate(argv) if a == "-drive"]
    empty = [d for d in drives if "media=cdrom" in d][0]
    assert empty.startswith("file=,")
    assert "ide-cd,bus=sata0.0,drive=drive-cd" in argv


def test_the_boot_order_becomes_letters(emitter, vm):
    argv = _argv(emitter, vm)
    assert argv[argv.index("-boot") + 1] == "order=cd"


def test_a_vm_vmctl_starts_has_no_window_and_a_pidfile(emitter, vm):
    """Something has to know whether the VM is running, and QEMU will not."""
    argv = _argv(emitter, vm)
    assert argv[argv.index("-display") + 1] == "none"
    assert argv[argv.index("-pidfile") + 1] == "/vms/demo/qemu.pid"
    assert "-daemonize" in argv


def test_a_virtualbox_mac_is_reformatted(emitter, vm):
    """A config from VirtualBox carries 080027AA0001, which QEMU rejects."""
    vm.networks[0].mac_address = "080027AA0001"
    device = [a for a in _argv(emitter, vm) if a.startswith("virtio-net-pci")][0]
    assert "mac=08:00:27:aa:00:01" in device


# ---------------------------------------------------------------------------
# What QEMU cannot do
# ---------------------------------------------------------------------------


def test_a_guest_os_is_reported_as_dropped(emitter, vm):
    """VirtualBox stores an OS type and libvirt records a libosinfo id; QEMU has
    neither, so it says so rather than forgetting quietly."""
    vm.guest_os = "win11"
    translator = Translator(QemuCapabilities.get(), Policy.NEAREST)
    emitter.build_argv(vm, translator)
    assert any(d.field == "guest_os" for d in translator.report.drops)


def test_a_setting_at_its_default_is_not_reported(emitter, vm):
    """A line in every report is how people learn to skip reports: `execution_cap:
    100` means "no cap", so there is nothing for QEMU to fail to do."""
    translator = Translator(QemuCapabilities.get(), Policy.NEAREST)
    emitter.build_argv(vm, translator)
    fields = {d.field for d in translator.report.drops}
    assert "cpu.execution_cap" not in fields
    assert "memory.vram_mb" not in fields


def test_a_host_only_network_has_no_qemu_equivalent(emitter, vm):
    """QEMU has user networking and a bridge helper; a host-only network is an
    object a *manager* creates -- which is the difference from libvirt."""
    vm.networks[0].network_type = NetworkType.HOSTONLY
    translator = Translator(QemuCapabilities.get(), Policy.NEAREST)
    argv = emitter.build_argv(vm, translator)
    assert "user,id=net0" in argv
    assert any("network_type" in d.field for d in translator.report.drops)


def test_a_nic_model_is_the_qemu_device_name(emitter, vm):
    vm.networks[0].model = NicModel.RTL8139
    assert any(a.startswith("rtl8139,netdev=") for a in _argv(emitter, vm))


def test_a_nic_model_this_build_lacks_is_substituted(emitter, vm):
    """F-37 -- this table was first written by reading libvirt's instead of
    `-device help`, so it claimed vmxnet3, pcnet and ne2k_pci. QEMU answers
    "'vmxnet3' is not a valid device model name" and refuses to start."""
    vm.networks[0].model = NicModel.VMXNET3
    translator = Translator(QemuCapabilities.get(), Policy.NEAREST)
    argv = emitter.build_argv(vm, translator)
    assert any(a.startswith("e1000,netdev=") for a in argv)
    assert any(s.field.endswith("model") for s in translator.report.substitutions)


# ---------------------------------------------------------------------------
# Reading a command line back
# ---------------------------------------------------------------------------


def test_a_script_round_trips(emitter, parser, vm):
    """The script is parsed with shlex rather than by matching what the emitter
    wrote, so a hand-edited one still loads -- which is the normal way to use plain
    QEMU."""
    script = emitter.render_script(_argv(emitter, vm))
    once = parser.parse_script("demo", script)
    assert once.name == "demo"
    assert once.memory.mb == 512
    assert (once.cpu.count, once.cpu.sockets, once.cpu.cores) == (4, 2, 2)
    assert once.machine == "q35"
    assert once.arch is Arch.X86_64
    assert [d.kind.value for d in once.storage] == ["disk", "cdrom"]
    assert [d.bus for d in once.storage] == [BusType.VIRTIO_BLK, BusType.SATA]
    assert once.storage[0].discard is True
    assert once.networks[0].model is NicModel.VIRTIO
    assert once.networks[0].network_type is NetworkType.NAT


def test_emitting_twice_is_stable(emitter, parser, vm):
    """A-07's property: the second generation onwards must be a fixed point."""
    first = emitter.render_script(_argv(emitter, vm))
    second = emitter.render_script(emitter.build_argv(parser.parse_script("demo", first)))
    third = emitter.render_script(emitter.build_argv(parser.parse_script("demo", second)))
    assert second == third


def test_a_hand_written_command_line_loads(parser):
    """Nobody writing plain QEMU by hand writes it the way vmctl does."""
    vm = parser.parse_argv(
        "byhand",
        [
            "qemu-system-x86_64",
            "-m",
            "1024",
            "-smp",
            "2",
            "-drive",
            "file=/srv/disk.raw,if=none,id=d0,format=raw",
            "-device",
            "virtio-blk-pci,drive=d0",
            "-netdev",
            "user,id=n0",
            "-device",
            "e1000,netdev=n0",
        ],
    )
    assert vm.name == "byhand"
    assert vm.memory.mb == 1024
    assert vm.cpu.count == 2
    assert vm.storage[0].format is DiskFormat.RAW
    assert vm.storage[0].bus is BusType.VIRTIO_BLK
    assert vm.networks[0].model is NicModel.E1000


def test_a_quoted_path_with_a_space_is_read_as_one_word(parser, emitter, vm):
    spaced = QemuEmitter("demo", location=directory("/my vms", nest_per_vm=True))
    script = spaced.render_script(spaced.build_argv(vm))
    once = parser.parse_script("demo", script)
    assert once.storage[0].disk_path == "/my vms/demo/demo_root.qcow2"


def test_a_script_with_no_command_is_refused(parser):
    from vmctl.core.exceptions import ProviderError

    with pytest.raises(ProviderError, match="no QEMU command"):
        parser.parse_script("x", "#!/bin/sh\n# nothing here\n")


def test_the_boot_disk_survives_a_round_trip(emitter, parser, vm):
    """`-boot order=` is VM-level, so which disk boots is not stated per device.
    Read it the way the other two providers do (L-03)."""
    once = parser.parse_script("demo", emitter.render_script(_argv(emitter, vm)))
    assert once.storage[0].bootable is True
    assert once.storage[1].bootable is False


# ---------------------------------------------------------------------------
# The capability declaration against its probe
# ---------------------------------------------------------------------------


def test_the_declaration_records_that_it_was_measured():
    caps = QemuCapabilities.get()
    assert "10.1.0" in caps.evidence
    assert "qemu_attach_matrix.json" in caps.evidence


def test_buses_this_build_has_not_got_are_absent():
    """No NVMe, no LSI SCSI, no MegaRAID: `-device help` lists none of them. A QEMU
    build is a build-time selection of devices, not a fixed product."""
    caps = QemuCapabilities.get()
    for absent in (BusType.NVME, BusType.SCSI, BusType.SAS):
        assert absent not in caps.buses
        assert not caps.can_attach(DeviceKind.DISK, absent)


def test_the_floppy_bus_is_absent_because_q35_has_no_controller():
    """Measured: "Device isa-fdc is not supported with machine type pc-q35". The
    recording keeps the `pc` matrix too, where it is the one cell that differs."""
    caps = QemuCapabilities.get()
    assert BusType.FLOPPY not in caps.buses
    assert not caps.can_attach(DeviceKind.FLOPPY, BusType.FLOPPY)


def test_only_qcow2_and_raw_can_be_created():
    """F-31 -- qemu-img will make a VMDK, but QEMU's block layer attaches one
    read-only, so a VM cannot be given it as a disk."""
    caps = QemuCapabilities.get()
    creatable = {f.value for f, spec in caps.formats.items() if spec.support.creatable}
    assert creatable == {"qcow2", "raw"}
    assert caps.format_spec(DiskFormat.VMDK).support.usable
    assert not caps.format_spec(DiskFormat.QED).support.usable


def test_an_optical_drive_on_virtio_blk_is_allowed_here_and_not_on_libvirt():
    """The same QEMU, two providers, two answers -- which is why an attach matrix
    belongs to a provider. QEMU builds the machine; libvirt refuses the request
    with "disk type of 'vda' does not support ejectable media"."""
    from vmctl.providers.libvirt.capabilities import LibvirtCapabilities

    assert QemuCapabilities.get().can_attach(DeviceKind.CDROM, BusType.VIRTIO_BLK)
    assert not LibvirtCapabilities.get().can_attach(DeviceKind.CDROM, BusType.VIRTIO_BLK)


def test_the_provider_has_no_ioapic_setting_to_warn_about():
    """q35 and pc always have an I/O APIC, so warning that it is off would describe
    a knob that does not exist."""
    assert QemuCapabilities.get().ioapic_optional is False


# ---------------------------------------------------------------------------
# A VM is a directory (the backend)
# ---------------------------------------------------------------------------


@pytest.fixture
def backend(tmp_path):
    from vmctl.providers.qemu.backend import QemuBackend

    return QemuBackend(state_dir=str(tmp_path / "state"), binary="/bin/true")


@pytest.fixture
def no_probe(monkeypatch):
    """Answer image questions without running qemu-img.

    The same injection the other providers get through a MediumProbe (T-04): a
    command line does not state a disk's size, so reading one means asking the
    image -- and a test should not need an image to ask.
    """
    from vmctl.core.devices import Allocation
    from vmctl.providers.qemu.backend import QemuBackend

    monkeypatch.setattr(
        QemuBackend,
        "probe_medium",
        lambda self, path: {
            "size_mb": 64,
            "format": DiskFormat.QCOW2,
            "variant": Allocation.THIN,
        },
    )


def _apply(plan):
    """Carry out a plan's file steps in-process.

    The suite must not need a shell any more than it needs a hypervisor, and the
    directory semantics below are about files rather than about running anything --
    so the plan is applied here instead of through the backend's ``run_plan``.
    """
    for step in plan:
        if step.kind is StepKind.WRITE_FILE and step.path is not None:
            step.path.parent.mkdir(parents=True, exist_ok=True)
            step.path.write_text(step.content or "")


def test_a_directory_with_a_script_in_it_is_a_vm(backend, vm):
    """No registry: listing VMs is listing directories, because an index would be a
    second source of truth that could disagree with what actually starts the VM."""
    assert backend.list_vms() == []
    _apply(backend.create_vm(vm, execute=False))
    assert backend.list_vms() == ["demo"]
    assert backend.vm_exists("demo")


def test_a_stray_directory_is_not_a_vm(backend, tmp_path):
    (tmp_path / "state" / "not-a-vm").mkdir(parents=True)
    assert backend.list_vms() == []


def test_a_created_vm_reads_back(backend, vm, no_probe):
    _apply(backend.create_vm(vm, execute=False))
    again = backend.read_vm("demo")
    assert again.memory.mb == vm.memory.mb
    assert [d.kind for d in again.storage] == [d.kind for d in vm.storage]


def test_a_vm_with_no_process_is_stopped(backend, vm):
    _apply(backend.create_vm(vm, execute=False))
    assert backend.get_vm_status("demo") == "stopped"


def test_a_stale_pidfile_does_not_mean_running(backend, vm):
    """The common case after a host reboot: the file is there and the process is not."""
    _apply(backend.create_vm(vm, execute=False))
    with open(backend.pidfile_path("demo"), "w") as handle:
        handle.write("999999\n")
    assert backend.get_vm_status("demo") == "stopped"


def test_deleting_removes_everything_the_vm_owns(backend, vm):
    """All of it is in one directory, so this is complete by construction -- the
    problem F-28 had to solve for libvirt does not arise here."""
    import os

    _apply(backend.create_vm(vm, execute=False))
    directory_ = backend.vm_dir("demo")
    assert os.path.isdir(directory_)
    assert backend.delete_vm("demo") is True
    assert not os.path.exists(directory_)


def test_reading_a_vm_that_is_not_there_says_so(backend):
    from vmctl.core.exceptions import VMNotFoundError

    with pytest.raises(VMNotFoundError):
        backend.read_vm("absent")


def test_editing_rewrites_the_command_line(backend, vm, no_probe):
    _apply(backend.create_vm(vm, execute=False))
    vm.memory.mb = 1024
    _apply(backend.edit_vm("demo", vm, execute=False))
    assert backend.read_vm("demo").memory.mb == 1024


def test_the_state_directory_is_per_vm(backend):
    """A VM *is* its directory, so the nesting is not a convention -- it is what
    makes listing and deleting possible at all (A-09)."""
    location = backend.storage_location()
    assert location.nest_per_vm
    assert location.image_path("demo", "run.sh").endswith("/demo/run.sh")


def test_a_migrated_disk_is_created_here_not_pointed_at_the_other_host(emitter, vm):
    """`disk_path` says where a device was read *from*; only `source` means "attach
    this image". Treating disk_path as "already there" pointed a QEMU command line
    at C:\\vms\\sys.vdi and created nothing, so the VM booted from a 20 GB default
    that was never there either."""
    vm.storage[0].disk_path = r"C:\vms\demo\sys.vdi"
    plan = emitter.emit_create_vm(vm)
    creates = [s for s in plan if s.argv and s.argv[0] == "qemu-img"]
    assert len(creates) == 1, "the disk still has to be created on this host"
    assert "/vms/demo/demo_root.qcow2" in creates[0].argv
    written = [s for s in plan if s.content][0]
    assert "C:\\vms" not in written.content


def test_an_attached_existing_image_is_not_re_created(emitter, vm):
    """Which is what `migrate --with-disks` relies on: the converted copy exists."""
    vm.storage[0].source = "/converted/root.qcow2"
    plan = emitter.emit_create_vm(vm)
    assert not [s for s in plan if s.argv and s.argv[0] == "qemu-img"]
    assert "/converted/root.qcow2" in [s for s in plan if s.content][0].content


def test_a_versioned_machine_type_is_accepted_as_it_comes(emitter, vm):
    """libvirt expands an alias when it echoes a domain back, so a config read from
    libvirt carries `pc-q35-rhel9.8.0`. Declaring only the alias made a
    libvirt-to-QEMU migration substitute it for one that floats with the next
    upgrade -- a change nobody asked for."""
    vm.machine = "pc-q35-rhel9.8.0"
    translator = Translator(QemuCapabilities.get(), Policy.NEAREST)
    argv = emitter.build_argv(vm, translator)
    assert argv[argv.index("-machine") + 1] == "pc-q35-rhel9.8.0"
    assert not translator.report.substitutions


def test_a_machine_type_this_build_lacks_is_substituted_and_reported(emitter, vm):
    """Measured: `-machine virt` is ARM's and this binary refuses it."""
    vm.machine = "virt"
    translator = Translator(QemuCapabilities.get(), Policy.NEAREST)
    argv = emitter.build_argv(vm, translator)
    assert argv[argv.index("-machine") + 1] == "q35"
    assert any(s.field == "machine" for s in translator.report.substitutions)


# ---------------------------------------------------------------------------
# F-39: editing must not make the disks again
# ---------------------------------------------------------------------------


def test_editing_does_not_create_the_image_again(emitter, vm):
    """Measured on a real VM before it was fixed: a pattern written into the image did
    not survive `vmctl edit --memory`, because the modify plan was the create plan and
    `qemu-img create` truncates whatever is already there."""
    current = copy.deepcopy(vm)
    current.storage[0].disk_path = "/vms/demo/demo_root.qcow2"
    desired = copy.deepcopy(current)
    desired.memory.mb = 1024

    plan = emitter.emit_modify_vm(current, desired)

    assert not [step for step in plan if (step.argv or [None])[0] == "qemu-img"]


def test_editing_still_attaches_the_image_the_vm_has(emitter, vm):
    """A hand-edited script is the normal way to use plain QEMU, so the image may not
    be where vmctl would have put it. Recomputing the path would detach the data."""
    current = copy.deepcopy(vm)
    current.storage[0].disk_path = "/srv/elsewhere/original.qcow2"
    desired = copy.deepcopy(current)
    desired.memory.mb = 1024

    plan = emitter.emit_modify_vm(current, desired)
    script = [step for step in plan if step.kind is StepKind.WRITE_FILE][0]

    assert "file=/srv/elsewhere/original.qcow2" in script.content


def test_renaming_does_not_claim_the_old_vms_images(emitter, vm):
    """A new name is a new VM here -- its own directory -- so it gets its own disks."""
    current = copy.deepcopy(vm)
    current.storage[0].disk_path = "/vms/demo/demo_root.qcow2"
    desired = copy.deepcopy(current)
    desired.name = "renamed"

    plan = emitter.emit_modify_vm(current, desired)

    assert [step for step in plan if (step.argv or [None])[0] == "qemu-img"]
