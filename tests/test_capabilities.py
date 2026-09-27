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
from vmctl.core.vmconfig import DiskFormat, DiskType, StorageControllerType
from vmctl.providers.virtualbox.capabilities import VirtualBoxCapabilities

FIXTURES = Path(__file__).parent / "fixtures"

KIND_NAMES = {"disk": DiskType.HDD, "cdrom": DiskType.DVD, "floppy": DiskType.FLOPPY}


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
        bus = StorageControllerType(bus_name)
        declared = caps.can_attach(kind, bus)
        if declared != observed:
            mismatches.append(f"{key}: declared={declared} observed={observed}")
    assert not mismatches, "declaration disagrees with the probe:\n" + "\n".join(mismatches)


def test_every_probed_port_range_matches_the_declaration(caps, probed_attach):
    mismatches = []
    for bus_name, (low, high) in probed_attach["port_ranges"].items():
        spec = caps.bus(StorageControllerType(bus_name))
        assert spec is not None, bus_name
        if (spec.min_ports, spec.max_ports) != (low, high):
            mismatches.append(
                f"{bus_name}: declared=({spec.min_ports}, {spec.max_ports}) "
                f"observed=({low}, {high})"
            )
    assert not mismatches, "\n".join(mismatches)


def test_nvme_carries_disks_only(caps):
    """Measured: "The attachment is not supported by the storage controller"."""
    assert caps.can_attach(DiskType.HDD, StorageControllerType.NVME)
    assert not caps.can_attach(DiskType.DVD, StorageControllerType.NVME)
    assert not caps.can_attach(DiskType.FLOPPY, StorageControllerType.NVME)


def test_only_the_floppy_controller_carries_a_floppy(caps):
    for bus in caps.buses:
        expected = bus is StorageControllerType.FLOPPY
        assert caps.can_attach(DiskType.FLOPPY, bus) is expected, bus


def test_the_floppy_controller_carries_nothing_else(caps):
    assert not caps.can_attach(DiskType.HDD, StorageControllerType.FLOPPY)
    assert not caps.can_attach(DiskType.DVD, StorageControllerType.FLOPPY)


def test_an_ssd_is_a_disk_not_a_separate_kind(caps):
    """SSD is a hint on a disk, so it attaches wherever a disk does."""
    for bus in caps.buses:
        assert caps.can_attach(DiskType.SSD, bus) == caps.can_attach(DiskType.HDD, bus)


def test_an_unknown_combination_is_refused_rather_than_guessed(caps):
    caps.attach.pop((DiskType.DVD, StorageControllerType.SATA), None)
    assert caps.can_attach(DiskType.DVD, StorageControllerType.SATA) is False


# ---------------------------------------------------------------------------
# Port counts
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bus,ports",
    [
        (StorageControllerType.IDE, 2),
        (StorageControllerType.SCSI, 16),
        (StorageControllerType.USB, 8),
        (StorageControllerType.FLOPPY, 1),
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
    spec = caps.bus(StorageControllerType.SATA)
    assert spec.fixed_port_count is None
    assert spec.clamp_ports(4) == 4
    assert spec.clamp_ports(99) == 30
    assert spec.clamp_ports(0) == 1


def test_ide_carries_two_devices_per_port(caps):
    assert caps.bus(StorageControllerType.IDE).units_per_port == 2
    assert caps.bus(StorageControllerType.SATA).units_per_port == 1


def test_usb_is_not_bootable(caps):
    assert caps.bus(StorageControllerType.USB).bootable is False
    assert caps.bus(StorageControllerType.SATA).bootable is True


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
