"""The capability declaration must match what the hypervisor actually did.

``tests/fixtures/attach_matrix.json`` and ``format_matrix.json`` are recordings
of VirtualBox 7.1.18 being asked to create every controller, attach every device
kind to it, and create a medium in every format. These tests hold the declaration
in ``providers/virtualbox/capabilities.py`` against those recordings, so the
table cannot drift into being a remembered one (M-03, A-02).
"""

import json
from pathlib import Path

import pytest

from vmctl.core.capabilities import Support
from vmctl.core.vmconfig import DiskFormat, DeviceKind, BusType
from vmctl.providers.virtualbox.capabilities import VirtualBoxCapabilities

FIXTURES = Path(__file__).parent / "fixtures"

KIND_NAMES = {"disk": DeviceKind.DISK, "cdrom": DeviceKind.CDROM, "floppy": DeviceKind.FLOPPY}


@pytest.fixture(scope="module")
def caps():
    return VirtualBoxCapabilities.get()


@pytest.fixture(scope="module")
def probed_attach():
    return json.loads((FIXTURES / "attach_matrix.json").read_text())


@pytest.fixture(scope="module")
def probed_formats():
    return json.loads((FIXTURES / "format_matrix.json").read_text())


def test_the_declaration_records_where_it_came_from(caps):
    assert "7.1.18" in caps.evidence
    assert "attach_matrix.json" in caps.evidence


# ---------------------------------------------------------------------------
# The attach matrix
# ---------------------------------------------------------------------------


def test_every_probed_attachment_matches_the_declaration(caps, probed_attach):
    mismatches = []
    for key, observed in probed_attach["attach"].items():
        kind_name, bus_name = key.split("|")
        if observed is None:
            continue  # the controller itself could not be created in that probe
        kind = KIND_NAMES[kind_name]
        bus = BusType(bus_name)
        declared = caps.can_attach(kind, bus)
        if declared != observed:
            mismatches.append(f"{key}: declared={declared} observed={observed}")
    assert not mismatches, "declaration disagrees with the probe:\n" + "\n".join(mismatches)


def test_every_probed_port_range_matches_the_declaration(caps, probed_attach):
    mismatches = []
    for bus_name, (low, high) in probed_attach["port_ranges"].items():
        spec = caps.bus(BusType(bus_name))
        assert spec is not None, bus_name
        if (spec.min_ports, spec.max_ports) != (low, high):
            mismatches.append(
                f"{bus_name}: declared=({spec.min_ports}, {spec.max_ports}) "
                f"observed=({low}, {high})"
            )
    assert not mismatches, "\n".join(mismatches)


def test_nvme_carries_disks_only(caps):
    """Measured: "The attachment is not supported by the storage controller"."""
    assert caps.can_attach(DeviceKind.DISK, BusType.NVME)
    assert not caps.can_attach(DeviceKind.CDROM, BusType.NVME)
    assert not caps.can_attach(DeviceKind.FLOPPY, BusType.NVME)


def test_only_the_floppy_controller_carries_a_floppy(caps):
    for bus in caps.buses:
        expected = bus is BusType.FLOPPY
        assert caps.can_attach(DeviceKind.FLOPPY, bus) is expected, bus


def test_the_floppy_controller_carries_nothing_else(caps):
    assert not caps.can_attach(DeviceKind.DISK, BusType.FLOPPY)
    assert not caps.can_attach(DeviceKind.CDROM, BusType.FLOPPY)


def test_there_is_no_separate_kind_for_solid_state(caps):
    """Solid state is a flag on a disk, not a kind of device (M-01).

    The old model had `DiskType.SSD`, and every lookup here had to fold it onto
    HDD first -- no hypervisor's attach matrix has a row for it. A kind that has
    to be translated away before it can be used is not a kind.
    """
    assert [k.value for k in DeviceKind] == ["disk", "cdrom", "floppy"]
    assert not any(k.value == "ssd" for k in DeviceKind)


def test_an_unknown_combination_is_refused_rather_than_guessed(caps):
    caps.attach.pop((DeviceKind.CDROM, BusType.SATA), None)
    assert caps.can_attach(DeviceKind.CDROM, BusType.SATA) is False


# ---------------------------------------------------------------------------
# Port counts
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bus,ports",
    [
        (BusType.IDE, 2),
        (BusType.SCSI, 16),
        (BusType.USB, 8),
        (BusType.FLOPPY, 1),
    ],
)
def test_buses_that_accept_exactly_one_port_count(caps, bus, ports):
    """Measured: "Invalid port count: N (must be in range [16, 16])"."""
    spec = caps.bus(bus)
    assert spec.fixed_port_count == ports
    # Whatever the config asks for, the only valid answer comes back.
    for requested in (None, 1, 4, 999):
        assert spec.clamp_ports(requested) == ports


def test_a_flexible_bus_clamps_into_its_range(caps):
    spec = caps.bus(BusType.SATA)
    assert spec.fixed_port_count is None
    assert spec.clamp_ports(4) == 4
    assert spec.clamp_ports(99) == 30
    assert spec.clamp_ports(0) == 1


def test_ide_carries_two_devices_per_port(caps):
    assert caps.bus(BusType.IDE).units_per_port == 2
    assert caps.bus(BusType.SATA).units_per_port == 1


def test_usb_is_not_bootable(caps):
    assert caps.bus(BusType.USB).bootable is False
    assert caps.bus(BusType.SATA).bootable is True


# ---------------------------------------------------------------------------
# Formats
# ---------------------------------------------------------------------------


def test_every_probed_format_matches_the_declaration(caps, probed_formats):
    mismatches = []
    for key, result in probed_formats.items():
        native, variant = key.split("|")
        alloc = "thin" if variant == "Standard" else "thick"
        spec = next((s for s in caps.formats.values() if s.native_name == native), None)
        if spec is None:
            continue
        declared = alloc in spec.allocations
        if declared != result["ok"]:
            mismatches.append(f"{native} {alloc}: declared={declared} observed={result['ok']}")
    assert not mismatches, "\n".join(mismatches)


def test_vhdx_can_be_attached_but_never_created(caps):
    spec = caps.format_spec(DiskFormat.VHDX)
    assert spec.support is Support.READ_ONLY
    assert spec.support.usable and not spec.support.creatable
    assert DiskFormat.VHDX not in caps.creatable_formats()
    assert DiskFormat.VHDX in caps.usable_formats()


def test_qcow2_is_creatable_but_only_dynamically(caps):
    """A surprise worth recording: VirtualBox can make a qcow2."""
    spec = caps.format_spec(DiskFormat.QCOW2)
    assert spec.support.creatable
    assert spec.allocations == ("thin",)


def test_raw_is_the_opposite_and_must_be_preallocated(caps):
    assert caps.format_spec(DiskFormat.RAW).allocations == ("thick",)


def test_vdi_is_native_and_the_default(caps):
    assert caps.native_format is DiskFormat.VDI
    assert caps.format_spec(DiskFormat.VDI).support is Support.NATIVE


def test_creatable_formats_are_what_disk_format_should_offer(caps):
    creatable = set(caps.creatable_formats())
    assert {
        DiskFormat.VDI,
        DiskFormat.VMDK,
        DiskFormat.VHD,
        DiskFormat.QCOW2,
        DiskFormat.QED,
        DiskFormat.RAW,
    } <= creatable
    assert DiskFormat.VHDX not in creatable


# ---------------------------------------------------------------------------
# The platform fields A-10 added
# ---------------------------------------------------------------------------


def test_each_provider_declares_what_it_can_do_about_a_cpu_topology():
    """VirtualBox has `--cpus N` and nothing else; libvirt models sockets, cores
    and threads. Declaring it is what lets the emitters report instead of guess."""
    from vmctl.providers.libvirt.capabilities import LibvirtCapabilities
    from vmctl.providers.virtualbox.capabilities import VirtualBoxCapabilities

    assert VirtualBoxCapabilities.get().cpu_topology is False
    assert LibvirtCapabilities.get().cpu_topology is True


def test_a_provider_with_no_machine_types_says_so_rather_than_naming_one():
    """VirtualBox emulates one machine model and has no name for it, which is not
    the same as "q35 by default"."""
    from vmctl.providers.virtualbox.capabilities import VirtualBoxCapabilities

    caps = VirtualBoxCapabilities.get()
    assert caps.machine_types == ()
    assert caps.default_machine is None


def test_nic_models_are_declared_per_provider():
    from vmctl.core.platform import NicModel
    from vmctl.providers.libvirt.capabilities import LibvirtCapabilities
    from vmctl.providers.virtualbox.capabilities import VirtualBoxCapabilities

    vbox = VirtualBoxCapabilities.get()
    libvirt = LibvirtCapabilities.get()
    # Measured: VirtualBox refuses these four outright.
    for absent in (NicModel.E1000E, NicModel.RTL8139, NicModel.NE2K, NicModel.VMXNET3):
        assert vbox.native_nic_model(absent) is None
        assert libvirt.native_nic_model(absent) is not None
    assert vbox.native_nic_model(NicModel.E1000) == "82540EM"
    assert libvirt.native_nic_model(NicModel.E1000) == "e1000"


def test_the_nic_fallback_is_a_card_a_driverless_guest_can_see():
    """Substituting virtio would leave a Windows installer with no network at all."""
    from vmctl.core.platform import NicModel
    from vmctl.providers.libvirt.capabilities import LibvirtCapabilities
    from vmctl.providers.virtualbox.capabilities import VirtualBoxCapabilities

    for caps in (VirtualBoxCapabilities.get(), LibvirtCapabilities.get()):
        assert caps.nic_model_fallback() is NicModel.E1000
