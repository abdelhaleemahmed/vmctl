"""Snapshots across four hypervisors (E-09).

Every parser here is fed a **capture from the real product** -- the fixtures were
taken from VirtualBox 7.1.18, libvirt 11.10.0, qemu-img 10.1.0 and vmrun 1.17.0 while
the feature was being written -- so what these tests pin is what those tools actually
print, not what this code hopes they print.
"""

import pytest

from vmctl.core.capabilities import Capabilities, FormatSpec, Support
from vmctl.core.devices import DeviceKind, DiskFormat
from vmctl.core.snapshots import Snapshot, unsupported_disks
from vmctl.core.vmconfig import (
    BootConfig,
    CPUConfig,
    FirmwareConfig,
    MemoryConfig,
    StorageDevice,
    VMConfig,
)

from conftest import read_fixture

# ---------------------------------------------------------------------------
# The vocabulary
# ---------------------------------------------------------------------------


def test_a_snapshot_renders_what_it_has():
    line = Snapshot("before-test", "why", "2026-09-28 00:17:19", True, state="shutoff").render()
    assert line.startswith("* before-test")
    assert "2026-09-28" in line and "shutoff" in line and "-- why" in line


def test_a_snapshot_renders_a_name_alone():
    """vmrun reports names and nothing else, so a listing has to read well with one."""
    assert Snapshot("only-a-name").render().strip() == "only-a-name"


def test_a_snapshot_is_json_safe():
    data = Snapshot("x", current=True).as_dict()
    assert data["name"] == "x" and data["current"] is True
    assert set(data) == {"name", "description", "created", "current", "parent", "state"}


# ---------------------------------------------------------------------------
# Whether the disks can hold one at all
# ---------------------------------------------------------------------------


def _caps(formats):
    return Capabilities(
        provider="fake",
        formats={
            DiskFormat.QCOW2: FormatSpec(Support.NATIVE, ("qcow2",), "qcow2"),
            DiskFormat.RAW: FormatSpec(Support.READ_WRITE, ("raw",), "raw"),
        },
        native_format=DiskFormat.QCOW2,
        snapshots=Support.NATIVE,
        snapshot_formats=formats,
        evidence="fake",
    )


def _vm(*devices):
    return VMConfig(
        name="vm",
        cpu=CPUConfig(count=1),
        memory=MemoryConfig(mb=128),
        firmware=FirmwareConfig(),
        storage=list(devices),
        networks=[],
        boot=BootConfig(),
        storage_controllers=[],
    )


def test_no_format_restriction_means_every_disk_is_fine():
    """VirtualBox snapshots by writing a differencing image, so the base format is not
    the mechanism -- measured by snapshotting a VM with a RAW disk attached."""
    vm = _vm(StorageDevice(name="root", format=DiskFormat.RAW))
    assert unsupported_disks(vm, _caps(())) == ()


def test_a_format_that_cannot_hold_a_snapshot_is_named():
    """libvirt: "internal snapshot for disk sda unsupported for storage type raw".
    qemu-img: "Operation not supported". Asked before the command runs, because both
    of them answer it half way through."""
    vm = _vm(StorageDevice(name="root", format=DiskFormat.RAW))

    problems = unsupported_disks(vm, _caps((DiskFormat.QCOW2,)))

    assert len(problems) == 1
    assert "root is raw" in problems[0] and "qcow2" in problems[0]


def test_a_disk_with_no_stated_format_is_judged_by_what_would_be_created():
    vm = _vm(StorageDevice(name="root"))
    assert unsupported_disks(vm, _caps((DiskFormat.QCOW2,))) == ()


def test_a_removable_medium_is_not_snapshotted():
    """An ISO is inserted, not snapshotted, so its format is not a reason to refuse."""
    vm = _vm(
        StorageDevice(name="root", format=DiskFormat.QCOW2),
        StorageDevice(name="cd", kind=DeviceKind.CDROM, source="/i.iso", slot=1),
    )
    assert unsupported_disks(vm, _caps((DiskFormat.QCOW2,))) == ()


# ---------------------------------------------------------------------------
# What each product actually prints
# ---------------------------------------------------------------------------


def test_virtualbox_reads_the_tree_out_of_the_keys():
    """VirtualBox puts the tree in the key suffix -- `SnapshotName-1-1` is a
    grandchild -- and offers no parent column and no timestamps at all."""
    from vmctl.providers.virtualbox.backend import parse_snapshots

    found = parse_snapshots(read_fixture("snapshot_list_vbox.txt"))

    assert [s.name for s in found] == [
        "before-test",
        "with-raw-disk",
        "with-vdi",
        "with-raw",
        "with-raw-attached",
    ]
    assert found[0].description == "a description with spaces"
    assert found[1].parent == "before-test"
    assert found[2].parent == "with-raw-disk"
    assert [s.name for s in found if s.current] == ["with-raw-attached"]
    assert all(s.created is None for s in found)


def test_virtualbox_reports_no_snapshots_as_an_answer():
    """With none, VirtualBox exits non-zero and prints a sentence. That is an answer."""
    from vmctl.providers.virtualbox.backend import parse_snapshots

    assert parse_snapshots("This machine does not have any snapshots\n") == []


def test_libvirt_reads_a_table_whose_dates_contain_spaces():
    """The creation time is "2026-09-28 00:17:19 +0000" -- splitting on whitespace
    turns one snapshot into three fields."""
    from vmctl.providers.libvirt.backend import parse_snapshots

    found = parse_snapshots(read_fixture("snapshot_list_libvirt.txt"))

    assert [s.name for s in found] == ["before-test", "second"]
    assert found[0].created == "2026-09-28 00:17:19 +0000"
    assert found[0].state == "shutoff"
    assert found[0].parent is None
    assert found[1].parent == "before-test"


def test_libvirt_finds_a_description_in_the_snapshot_xml():
    """The only place libvirt keeps it: not in the listing, so it costs a call each."""
    from vmctl.providers.libvirt.backend import _description_in

    assert (
        _description_in("<domainsnapshot><description>why</description></domainsnapshot>") == "why"
    )
    assert _description_in("<domainsnapshot><name>x</name></domainsnapshot>") is None
    assert _description_in("not xml at all") is None


def test_qemu_reads_a_listing_whose_columns_run_together():
    """`VM_SIZE` is "0 B" and the date follows after a single space, so a column split
    glues them into one field."""
    from vmctl.providers.qemu.backend import parse_snapshots

    found = parse_snapshots(read_fixture("snapshot_list_qemu.txt"))

    assert [s.name for s in found] == ["before-test", "second", "manual-test"]
    assert found[0].created == "2026-09-28 00:17:19"
    assert all(s.description is None for s in found)


def test_vmware_reads_names_and_skips_its_own_header():
    from vmctl.providers.vmware.backend import parse_snapshots

    found = parse_snapshots(read_fixture("snapshot_list_vmware.txt"))

    assert [s.name for s in found] == ["before-test", "third"]


def test_vmware_reports_an_error_as_no_snapshots():
    from vmctl.providers.vmware.backend import parse_snapshots

    assert parse_snapshots("Error: Cannot open VM: x.vmx, not found") == []


# ---------------------------------------------------------------------------
# What each provider declares
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "provider,formats,descriptions",
    [
        ("virtualbox", (), True),
        ("libvirt", (DiskFormat.QCOW2,), True),
        ("qemu", (DiskFormat.QCOW2,), False),
        ("vmware", (), False),
    ],
)
def test_the_declarations_match_what_was_measured(provider, formats, descriptions):
    """Each of these was established against the running product, not read anywhere."""
    import vmctl.providers  # noqa: F401
    from vmctl.core import registry

    caps = registry.create(provider).capabilities

    assert caps.snapshots is Support.NATIVE
    assert caps.snapshot_formats == formats
    assert caps.snapshot_descriptions is descriptions
