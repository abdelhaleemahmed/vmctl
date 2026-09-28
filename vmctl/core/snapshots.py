"""
Snapshots: the same four operations on four different mechanisms (E-09).

The documented reason to want them is "before a destructive test", which is also why
this belongs in vmctl rather than in a note telling people to use each hypervisor's
own command: a lab that is described by files should be restorable by the same tool
that created it.

All four providers can do it, and they do it in genuinely different ways -- which is
why only the *vocabulary* is here and the commands are each provider's own:

* **VirtualBox** keeps a tree of differencing images. Measured: snapshotting a VM
  whose disk is a VDI writes a new ``{uuid}.vdi`` under ``Snapshots/``, and a VM with
  a RAW disk attached snapshots just as happily -- so the disk format is not a
  constraint there.
* **libvirt** and **plain QEMU** use *internal* qcow2 snapshots, so the format is the
  whole mechanism: both refuse anything else. libvirt says "internal snapshot for
  disk sda unsupported for storage type raw"; ``qemu-img`` says "Operation not
  supported". That is a capability, declared and checked before a command runs.
* **VMware** has ``vmrun snapshot`` and no description or timestamp to report.

So a listing is best-effort by construction: two providers have descriptions, two have
timestamps, and a field a hypervisor does not keep is None rather than invented.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .capabilities import Capabilities
from .devices import DiskFormat
from .vmconfig import VMConfig


@dataclass
class Snapshot:
    """One snapshot, as neutrally as four hypervisors allow.

    Attributes:
        name: What it is called. The only field every provider has.
        description: Why it was taken, where the hypervisor keeps one.
        created: When, as the tool reports it. Text rather than a datetime: these
            come from four different formats and vmctl is not going to guess a
            timezone it was not told.
        current: Whether the VM is at this snapshot now.
        parent: The snapshot this one was taken from, where that is reported.
        state: Whether the VM was running when it was taken, where reported.
    """

    name: str
    description: Optional[str] = None
    created: Optional[str] = None
    current: bool = False
    parent: Optional[str] = None
    state: Optional[str] = None

    def render(self) -> str:
        """Return one line for a listing."""
        marker = "*" if self.current else " "
        parts = [f"{marker} {self.name}"]
        if self.created:
            parts.append(self.created)
        if self.state:
            parts.append(self.state)
        if self.description:
            parts.append(f"-- {self.description}")
        return "  ".join(parts)

    def as_dict(self) -> Dict[str, Any]:
        """Return this snapshot as JSON-safe data (E-07)."""
        return {
            "name": self.name,
            "description": self.description,
            "created": self.created,
            "current": self.current,
            "parent": self.parent,
            "state": self.state,
        }


def unsupported_disks(vm: VMConfig, capabilities: Capabilities) -> Tuple[str, ...]:
    """Return the disks whose format cannot hold a snapshot on this provider.

    Asked *before* the command runs, because the alternative is what the hypervisors
    do: libvirt creates nothing and fails with a sentence about storage types, and
    ``qemu-img`` says "Operation not supported" per image -- after vmctl has already
    reported taking a snapshot of the first one.

    Args:
        vm: The VM as it is now.
        capabilities: The provider's declaration.

    Returns:
        tuple[str, ...]: one message per disk that cannot, empty when all can.
    """
    allowed = capabilities.snapshot_formats
    if not allowed:
        return ()
    problems: List[str] = []
    for device in vm.storage:
        if device.is_removable:
            continue  # a medium is not snapshotted; it is inserted
        fmt: Optional[DiskFormat] = device.format or capabilities.native_format
        if fmt not in allowed:
            names = ", ".join(sorted(f.value for f in allowed))
            problems.append(
                f"{device.name} is {fmt.value if fmt else 'of no stated format'}, and "
                f"{capabilities.provider} can only snapshot {names}"
            )
    return tuple(problems)
