"""Slot allocation tests (A-06).

Two properties matter: a configuration that does not state positions gets sensible
ones, and the answer is the same every time. `E-01 diff` and `E-02 apply` rest on
the second -- a diff that reported re-allocation as a change would be useless.
"""

import copy

import pytest

from vmctl.core.exceptions import ValidationError
from vmctl.core.slots import Placement, place
from vmctl.core.vmconfig import (
    DiskConfig,
    DeviceKind,
    StorageControllerConfig,
    BusType,
)
from vmctl.providers.libvirt.capabilities import LibvirtCapabilities
from vmctl.providers.virtualbox.capabilities import VirtualBoxCapabilities


@pytest.fixture
def caps():
    return VirtualBoxCapabilities.get()


def _disk(name, bus=BusType.SATA, **kwargs):
    return DiskConfig(name=name, size_mb=1024, controller=bus, **kwargs)


# ---------------------------------------------------------------------------
# Filling in what the configuration does not say
# ---------------------------------------------------------------------------


def test_unplaced_devices_get_sequential_ports(caps, vm_minimal):
    vm_minimal.disks = [_disk("a"), _disk("b"), _disk("c")]
    placed = place(vm_minimal, caps)
    assert [(p.port, p.unit) for p in placed] == [(0, 0), (1, 0), (2, 0)]
    assert all(not p.explicit for p in placed)


def test_ide_uses_both_devices_on_a_port_before_the_next(caps, vm_minimal):
    """IDE is master/slave: two devices per port, unlike every other bus."""
    vm_minimal.disks = [_disk(n, BusType.IDE) for n in "abcd"]
    placed = place(vm_minimal, caps)
    assert [(p.port, p.unit) for p in placed] == [(0, 0), (0, 1), (1, 0), (1, 1)]


def test_sata_uses_one_device_per_port(caps, vm_minimal):
    vm_minimal.disks = [_disk("a"), _disk("b")]
    assert [p.unit for p in place(vm_minimal, caps)] == [0, 0]


def test_devices_on_different_buses_are_placed_independently(caps, vm_minimal):
    vm_minimal.disks = [
        _disk("sata-a"),
        _disk("ide-a", BusType.IDE),
        _disk("sata-b"),
    ]
    placed = place(vm_minimal, caps)
    assert placed[0].controller != placed[1].controller
    assert (placed[0].port, placed[0].unit) == (0, 0)
    assert (placed[1].port, placed[1].unit) == (0, 0)  # a different controller
    assert (placed[2].port, placed[2].unit) == (1, 0)


def test_a_stated_position_is_kept_exactly(caps, vm_minimal):
    vm_minimal.disks = [_disk("pinned", port=5), _disk("free")]
    placed = place(vm_minimal, caps)
    assert (placed[0].port, placed[0].explicit) == (5, True)
    assert (placed[1].port, placed[1].explicit) == (0, False)


def test_allocation_goes_around_a_stated_position(caps, vm_minimal):
    """A pinned device is reserved first, so an allocated one never lands on it."""
    vm_minimal.disks = [_disk("free-a"), _disk("pinned", port=0), _disk("free-b")]
    placed = place(vm_minimal, caps)
    assert (placed[1].port, placed[1].unit) == (0, 0)
    assert {(placed[0].port, placed[0].unit), (placed[2].port, placed[2].unit)} == {
        (1, 0),
        (2, 0),
    }


def test_placement_does_not_modify_the_configuration(caps, vm_minimal):
    vm_minimal.disks = [_disk("a"), _disk("b")]
    before = copy.deepcopy(vm_minimal)
    place(vm_minimal, caps)
    assert vm_minimal == before


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_the_same_configuration_always_places_the_same_way(caps, vm_minimal):
    vm_minimal.disks = [
        _disk("a"),
        _disk("b", BusType.IDE),
        _disk("c"),
        _disk("d", BusType.IDE),
    ]
    first = place(vm_minimal, caps)
    for _ in range(20):
        assert place(copy.deepcopy(vm_minimal), caps) == first


def test_placement_follows_configuration_order_not_object_identity(caps, vm_minimal):
    """Order comes from the config, so it cannot depend on hashing or timing."""
    vm_minimal.disks = [_disk("z"), _disk("a"), _disk("m")]
    placed = place(vm_minimal, caps)
    assert [p.port for p in placed] == [0, 1, 2]


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


def test_two_stated_positions_that_clash_are_refused(caps, vm_minimal):
    vm_minimal.disks = [_disk("a", port=1), _disk("b", port=1)]
    with pytest.raises(ValidationError, match="both attached to"):
        place(vm_minimal, caps)


def test_a_port_the_bus_does_not_have_is_refused(caps, vm_minimal):
    vm_minimal.disks = [_disk("a", BusType.IDE, port=9)]
    with pytest.raises(ValidationError, match="port 9"):
        place(vm_minimal, caps)


def test_a_second_device_on_a_non_ide_port_is_refused(caps, vm_minimal):
    vm_minimal.disks = [_disk("a", device=1)]
    with pytest.raises(ValidationError, match="device 1"):
        place(vm_minimal, caps)


def test_a_declared_controllers_port_count_is_the_tighter_limit(caps, vm_minimal):
    vm_minimal.storage_controllers = [
        StorageControllerConfig(name="Small", controller_type=BusType.SATA, port_count=2)
    ]
    vm_minimal.disks = [_disk(n, controller_name="Small") for n in "abc"]
    with pytest.raises(ValidationError, match="all 2 position"):
        place(vm_minimal, caps)


def test_running_out_of_room_says_what_to_do(caps, vm_minimal):
    vm_minimal.disks = [
        _disk("a", BusType.FLOPPY, type=DeviceKind.FLOPPY),
        _disk("b", BusType.FLOPPY, type=DeviceKind.FLOPPY),
    ]
    with pytest.raises(ValidationError) as excinfo:
        place(vm_minimal, caps)
    assert "does not fit" in str(excinfo.value)
    assert "port count" in (excinfo.value.recovery_hint or "")


def test_an_unsupported_bus_is_refused(caps, vm_minimal):
    caps.buses.pop(BusType.NVME)
    vm_minimal.disks = [_disk("a", BusType.NVME)]
    with pytest.raises(ValidationError, match="does not support"):
        place(vm_minimal, caps)


# ---------------------------------------------------------------------------
# The same allocator serves a provider with different limits
# ---------------------------------------------------------------------------


def test_the_allocator_follows_each_providers_limits(vm_minimal):
    """One implementation, two providers: libvirt's SATA holds 6, VirtualBox's 30."""
    vm_minimal.disks = [_disk(f"d{i}") for i in range(7)]
    place(vm_minimal, VirtualBoxCapabilities.get())  # 30 ports: fine
    with pytest.raises(ValidationError, match="does not fit"):
        place(vm_minimal, LibvirtCapabilities.get())  # 6 ports


def test_placement_is_reported_as_explicit_or_assigned(caps, vm_minimal):
    """A provider can tell a user's choice from vmctl's."""
    vm_minimal.disks = [_disk("pinned", port=3), _disk("auto")]
    placed = place(vm_minimal, caps)
    assert placed[0] == Placement(placed[0].controller, 3, 0, explicit=True)
    assert placed[1].explicit is False
