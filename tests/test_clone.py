"""Bringing disk contents along (E-03).

``plan_clone`` is the one code path behind ``migrate --with-disks``,
``import --clone-disks`` and ``create --clone-disks``, so these tests check it
directly: the orchestration around it is covered in ``test_migrate.py`` and
``test_cli.py``, and duplicating that here would only mean three places to update
when the answer changes.

Real files in ``tmp_path``, because every interesting decision it makes -- is the
image there, how big is it -- is a question about the filesystem.
"""

import pytest

from vmctl.core.clone import CloneResult, Origin, plan_clone
from vmctl.core.storage import directory
from vmctl.core.vmconfig import (
    BootConfig,
    CPUConfig,
    DeviceKind,
    DiskFormat,
    FirmwareConfig,
    MemoryConfig,
    StorageDevice,
    VMConfig,
)
from vmctl.providers.libvirt.capabilities import LibvirtCapabilities
from vmctl.providers.virtualbox.capabilities import VirtualBoxCapabilities


@pytest.fixture
def caps():
    """libvirt's declaration: it creates qcow2 and cannot write VDI (F-31)."""
    return LibvirtCapabilities.get()


@pytest.fixture
def location(tmp_path):
    return directory(str(tmp_path / "images"), nest_per_vm=True)


def _image(tmp_path, name, megabytes=1):
    """Write a sparse image of a given apparent size.

    Sparse on purpose: an image's *apparent* size is what a copy has to read, which
    is what ``plan_clone`` reports -- and writing 1.5 GB of zeroes to check that a
    number is printed as "1.5 GB" would make this file the slowest in the suite.
    """
    path = tmp_path / name
    with open(path, "wb") as handle:
        handle.truncate(megabytes * 1024 * 1024)
    return str(path)


def _vm(*devices, name="target"):
    return VMConfig(
        name=name,
        cpu=CPUConfig(count=1),
        memory=MemoryConfig(mb=128),
        firmware=FirmwareConfig(),
        storage=list(devices),
        networks=[],
        boot=BootConfig(),
        storage_controllers=[],
    )


# ---------------------------------------------------------------------------
# What gets copied
# ---------------------------------------------------------------------------


def test_a_disk_that_is_here_is_copied_into_the_new_vms_place(tmp_path, caps, location):
    origin = _image(tmp_path, "root.qcow2")
    vm = _vm(StorageDevice(name="root", disk_path=origin, format=DiskFormat.QCOW2))

    result = plan_clone(vm, caps, location)

    assert len(result.requests) == 1
    request = result.requests[0]
    assert request.source == origin
    assert request.target == str(tmp_path / "images" / "target" / "target_root.qcow2")
    # And the device now points at the copy, so the emitter attaches it rather
    # than creating a blank disk -- which is the whole point.
    assert vm.storage[0].source == request.target


def test_source_is_preferred_over_disk_path(tmp_path, caps, location):
    """What a config *states* beats where a device happened to be read from.

    An exported configuration leaves ``disk_path`` out on purpose -- it describes a
    host, not the VM -- so ``source:`` is the only way a file can say where the data
    is, and it has to win.
    """
    stated = _image(tmp_path, "stated.qcow2")
    vm = _vm(StorageDevice(name="root", source=stated, disk_path=_image(tmp_path, "read.qcow2")))

    result = plan_clone(vm, caps, location)

    assert [r.source for r in result.requests] == [stated]


def test_an_image_named_by_source_is_copied_rather_than_shared(tmp_path, caps, location):
    """Attaching one file to two VMs is data corruption waiting for both to boot."""
    shared = _image(tmp_path, "golden.qcow2")
    vm = _vm(StorageDevice(name="root", source=shared))

    plan_clone(vm, caps, location)

    assert vm.storage[0].source != shared


def test_an_explicit_origin_wins_over_the_configuration(tmp_path, caps, location):
    """``create`` reads the origins before renaming the VM, and passes them in."""
    origin = _image(tmp_path, "live.qcow2")
    vm = _vm(StorageDevice(name="root", disk_path="/gone/stale.qcow2"))

    result = plan_clone(vm, caps, location, origins=[Origin(origin)])

    assert [r.source for r in result.requests] == [origin]


def test_the_origins_format_is_used_not_the_one_being_asked_for(tmp_path, caps, location):
    """F-38: `--disk-format raw` rewrites each device's format, and reading that back
    as the *source's* format made `qemu-img convert -f raw` copy a qcow2 container
    into a file called `.raw` -- then attach it as raw. The guest would find a qcow2
    header where its partition table should be."""
    origin = _image(tmp_path, "root.qcow2")
    # The device says raw because that is what was *requested*.
    vm = _vm(StorageDevice(name="root", disk_path=origin, format=DiskFormat.RAW))

    result = plan_clone(vm, caps, location, origins=[Origin(origin, DiskFormat.QCOW2)])

    request = result.requests[0]
    assert request.source_format is DiskFormat.QCOW2
    assert request.target_format is DiskFormat.RAW


def test_an_origin_of_unknown_format_lets_the_tool_detect_it(tmp_path, caps, location):
    """Better than asserting a format wrongly, which is how F-38 did damage."""
    origin = _image(tmp_path, "root.img")
    vm = _vm(StorageDevice(name="root", disk_path=origin, format=DiskFormat.QCOW2))

    result = plan_clone(vm, caps, location, origins=[Origin(origin)])

    assert result.requests[0].source_format is None


def test_several_disks_are_copied_in_device_order(tmp_path, caps, location):
    vm = _vm(
        StorageDevice(name="root", disk_path=_image(tmp_path, "a.qcow2")),
        StorageDevice(name="data", disk_path=_image(tmp_path, "b.qcow2"), slot=1),
    )

    result = plan_clone(vm, caps, location)

    assert [r.label for r in result.requests] == ["root", "data"]


# ---------------------------------------------------------------------------
# What does not get copied
# ---------------------------------------------------------------------------


def test_a_removable_medium_is_not_data_to_copy(tmp_path, caps, location):
    """An ISO belongs to the host it was read from, so the path is dropped."""
    iso = _image(tmp_path, "install.iso")
    vm = _vm(StorageDevice(name="cd", kind=DeviceKind.CDROM, source=iso))

    result = plan_clone(vm, caps, location)

    assert result.requests == []
    assert vm.storage[0].source is None


def test_an_unreachable_image_is_reported_and_the_disk_left_blank(caps, location):
    """A configuration from another machine names paths this one cannot see."""
    vm = _vm(StorageDevice(name="root", disk_path=r"C:\vms\src\root.vdi"))

    result = plan_clone(vm, caps, location)

    assert result.unreachable == [r"C:\vms\src\root.vdi"]
    assert result.requests == []
    # Not pointed at something that does not exist: blank is what it would have got.
    assert vm.storage[0].source is None


def test_a_device_with_no_image_at_all_is_simply_skipped(caps, location):
    vm = _vm(StorageDevice(name="root", size_mb=8192))

    result = plan_clone(vm, caps, location)

    assert not result
    assert result.unreachable == []


# ---------------------------------------------------------------------------
# Formats: a copy is a conversion whose formats happen to match
# ---------------------------------------------------------------------------


def test_a_format_the_target_can_create_is_kept(tmp_path, caps, location):
    vm = _vm(
        StorageDevice(name="root", disk_path=_image(tmp_path, "root.raw"), format=DiskFormat.RAW)
    )

    result = plan_clone(vm, caps, location)

    assert result.requests[0].target_format is DiskFormat.RAW
    assert result.requests[0].target.endswith(".raw")


def test_a_format_the_target_cannot_write_becomes_its_native_one(tmp_path, caps, location):
    """libvirt attaches VDI read-only (F-31), so a copy has to be converted."""
    vm = _vm(
        StorageDevice(name="root", disk_path=_image(tmp_path, "root.vdi"), format=DiskFormat.VDI)
    )

    result = plan_clone(vm, caps, location)

    assert result.requests[0].target_format is DiskFormat.QCOW2
    assert result.requests[0].source_format is DiskFormat.VDI
    assert vm.storage[0].format is DiskFormat.QCOW2


def test_a_device_that_states_no_format_gets_the_targets_own(tmp_path, location):
    vm = _vm(StorageDevice(name="root", disk_path=_image(tmp_path, "root.img")))

    result = plan_clone(vm, VirtualBoxCapabilities.get(), location)

    assert result.requests[0].target_format is DiskFormat.VDI
    assert result.requests[0].target.endswith(".vdi")


# ---------------------------------------------------------------------------
# Saying how much work this is, before it starts
# ---------------------------------------------------------------------------


def test_the_size_is_the_files_own_not_the_declared_capacity(tmp_path, caps, location):
    """A sparse 20 GB image holding 2 MB is 2 MB of copying, not 20 GB of it."""
    vm = _vm(StorageDevice(name="root", disk_path=_image(tmp_path, "root.qcow2", 2), size_mb=20480))

    result = plan_clone(vm, caps, location)

    assert result.total_mb == 2


def test_the_summary_says_how_many_and_how_much(tmp_path, caps, location):
    vm = _vm(
        StorageDevice(name="root", disk_path=_image(tmp_path, "a.qcow2")),
        StorageDevice(name="data", disk_path=_image(tmp_path, "b.qcow2"), slot=1),
    )

    described = plan_clone(vm, caps, location).describe()

    assert "2 disk image(s)" in described
    assert "2 MB" in described
    assert "takes time and space" in described


def test_gigabytes_are_spelled_as_gigabytes(tmp_path, caps, location):
    """ "about 1536 MB" is a number a person has to convert themselves."""
    vm = _vm(StorageDevice(name="root", disk_path=_image(tmp_path, "root.qcow2", 1536)))

    assert "1.5 GB" in plan_clone(vm, caps, location).describe()


def test_nothing_to_copy_says_so_rather_than_claiming_a_copy():
    assert CloneResult().describe() == "no disk contents to copy"
    assert not CloneResult()
