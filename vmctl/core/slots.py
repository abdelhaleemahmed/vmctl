"""
Where each storage device sits, decided once.

Hypervisors address devices differently -- VirtualBox by controller name plus
port and device number, libvirt by a target name like ``vda`` on a bus, Hyper-V by
controller type, number and location -- but *choosing* a free position is the
same problem for all of them. Doing it here means:

* a configuration does not have to state positions it does not care about, which
  was real friction: for libvirt they are vmctl's bookkeeping rather than
  something the hypervisor needs, yet omitting them made two devices collide in
  slot 0;
* the answer is **deterministic**, so the same configuration produces the same
  layout on every run. ``E-01 diff`` and ``E-02 apply`` are meaningless without
  that -- a diff would report changes that are only re-allocation;
* the emitter and the validator agree about placement, because they call the
  same function rather than each working it out (A-06 in PLAN.md).
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .capabilities import Capabilities
from .exceptions import ValidationError
from .vmconfig import DiskConfig, VMConfig


@dataclass(frozen=True)
class Placement:
    """Where one device ends up.

    Attributes:
        controller: Name of the controller it attaches to.
        port: Port number on that controller.
        unit: Device number on that port. Only IDE uses anything but 0.
        explicit: True when the configuration stated this position, so a
            provider can tell a user's choice from vmctl's.
    """

    controller: str
    port: int
    unit: int
    explicit: bool = False


def _controller_name(vm: VMConfig, disk: DiskConfig, caps: Capabilities) -> str:
    """Return the name of the controller a device attaches to.

    Falls back to the canonical name for the device's bus, which is the name the
    provider will synthesise a controller under.
    """
    declared = vm.controller_for(disk)
    if declared is not None:
        return declared.name
    spec = caps.bus(disk.controller)
    if spec is None:
        raise ValidationError(
            f"disk {disk.name!r} uses the {disk.controller.value!r} bus, which "
            f"this provider does not support",
            field="controller",
            value=disk.controller.value,
            expected=" | ".join(sorted(b.value for b in caps.buses)),
        )
    return spec.controller_name


def place(vm: VMConfig, caps: Capabilities) -> List[Placement]:
    """Resolve every device's position.

    Positions the configuration states are kept exactly. The rest are filled with
    the lowest free position on their controller, in configuration order, so the
    result depends only on the configuration and not on dictionary ordering or
    the time of day.

    Args:
        vm: The configuration to place. **Not modified** -- the result is
            returned rather than written back, so a caller can compare a
            configuration against a VM without changing either.
        caps: The provider's capability declaration, for each bus's port limit
            and devices-per-port.

    Returns:
        list[Placement]: one entry per device, in ``vm.disks`` order.

    Raises:
        ValidationError: If two devices are placed in the same position, a stated
            position is outside its bus's range, or a controller has no room
            left.
    """
    placements: List[Optional[Placement]] = [None] * len(vm.disks)
    taken: Dict[str, Dict[Tuple[int, int], int]] = {}
    names: List[str] = []

    for index, disk in enumerate(vm.disks):
        names.append(_controller_name(vm, disk, caps))

    # Stated positions first, so they cannot be taken by an allocated one.
    for index, disk in enumerate(vm.disks):
        if disk.port is None and disk.device is None:
            continue
        name = names[index]
        port = disk.port or 0
        unit = disk.device or 0
        _check_bounds(vm, disk, index, name, port, unit, caps)
        slot = (port, unit)
        occupied = taken.setdefault(name, {})
        if slot in occupied:
            other = vm.disks[occupied[slot]]
            raise ValidationError(
                f"disks[{index}] ({disk.name}) and disks[{occupied[slot]}] "
                f"({other.name}) are both attached to controller {name!r} "
                f"port {port} device {unit}",
                field=f"disks[{index}]",
                recovery_hint="Give each device its own port or device number, "
                "or leave both unset and vmctl will place them.",
            )
        occupied[slot] = index
        placements[index] = Placement(name, port, unit, explicit=True)

    # Then fill the rest, lowest free position first.
    for index, disk in enumerate(vm.disks):
        if placements[index] is not None:
            continue
        name = names[index]
        occupied = taken.setdefault(name, {})
        slot = _next_free(vm, disk, index, name, occupied, caps)
        occupied[slot] = index
        placements[index] = Placement(name, slot[0], slot[1], explicit=False)

    return [p for p in placements if p is not None]


def _limits(name: str, disk: DiskConfig, caps: Capabilities) -> Tuple[int, int]:
    """Return ``(max_ports, units_per_port)`` for a device's bus."""
    spec = caps.bus(disk.controller)
    if spec is None:
        return (1, 1)
    return (spec.max_ports, spec.units_per_port)


def _check_bounds(
    vm: VMConfig,
    disk: DiskConfig,
    index: int,
    name: str,
    port: int,
    unit: int,
    caps: Capabilities,
) -> None:
    """Reject a stated position the bus cannot provide."""
    max_ports, units = _limits(name, disk, caps)
    declared = vm.controller_for(disk)
    # A declared controller's own port count is the tighter limit.
    if declared is not None and declared.port_count:
        max_ports = min(max_ports, declared.port_count)

    if port < 0 or port >= max_ports:
        raise ValidationError(
            f"disks[{index}] ({disk.name}) uses port {port} on controller "
            f"{name!r}, which has {max_ports} port(s) (0-{max_ports - 1})",
            field=f"disks[{index}].port",
            value=port,
            expected=f"0-{max_ports - 1}",
        )
    if unit < 0 or unit >= units:
        raise ValidationError(
            f"disks[{index}] ({disk.name}) uses device {unit} on a "
            f"{disk.controller.value} controller, which allows {units} "
            f"device(s) per port",
            field=f"disks[{index}].device",
            value=unit,
            expected=f"0-{units - 1}",
        )


def _next_free(
    vm: VMConfig,
    disk: DiskConfig,
    index: int,
    name: str,
    occupied: Dict[Tuple[int, int], int],
    caps: Capabilities,
) -> Tuple[int, int]:
    """Return the lowest position on *name* that nothing else uses."""
    max_ports, units = _limits(name, disk, caps)
    declared = vm.controller_for(disk)
    if declared is not None and declared.port_count:
        max_ports = min(max_ports, declared.port_count)

    for port in range(max_ports):
        for unit in range(units):
            if (port, unit) not in occupied:
                return (port, unit)

    raise ValidationError(
        f"disks[{index}] ({disk.name}) does not fit: controller {name!r} has "
        f"{max_ports} port(s) with {units} device(s) each, and all "
        f"{max_ports * units} position(s) are taken",
        field=f"disks[{index}]",
        recovery_hint="Add another controller, or raise this one's port count.",
    )
