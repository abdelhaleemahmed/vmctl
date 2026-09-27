"""
Bringing a VM's disk *contents* along.

Every piece of vmctl's documentation used to warn that configuration moves and data
does not. This is the other half (E-03 in PLAN.md), and it is deliberately not new
machinery: copying a disk into a new VM's place is the same operation as converting
one, so it goes through :mod:`vmctl.core.convert` -- a copy is a conversion whose
source and target formats happen to match.

That reuse is the reason ``migrate --with-disks`` and ``import --clone-disks`` behave
identically, down to the refusal: if the target's own tool cannot read the source
format, both say so rather than producing a VM with an empty disk.

Two facts about the world shape the rest:

* **The images may not be here.** A configuration exported from another machine names
  paths on that machine, so an unreachable image is reported and the disk is created
  blank -- which is what the VM would have got anyway.
* **A removable medium is not data to copy.** An ISO path belongs to the host it was
  read from; carrying it across would point a new VM at a file it cannot see.
* **An image named by ``source:`` is copied, not shared.** Without cloning, ``source``
  means "attach this existing image" -- and attaching one file to a second VM is data
  corruption waiting for both of them to boot. Asking for the contents therefore gives
  the new VM its *own* copy.
* **Where the data is and what it is now are one fact** (:class:`Origin`), separate from
  what the new VM should have. Conflating the two produced F-38: ``--disk-format raw``
  rewrote each device's format, ``plan_clone`` read that back as the *source's* format,
  and ``qemu-img convert -f raw`` copied a qcow2 container into a file called ``.raw``
  which was then attached as raw -- a VM whose disk holds a qcow2 header where its
  partition table should be. So an origin is passed in, or read before anything
  overwrites it, and never inferred from what was asked for.
"""

import os
from dataclasses import dataclass, field
from typing import List, Optional

from .convert import ConversionRequest
from .devices import DiskFormat
from .storage import StorageLocation
from .vmconfig import VMConfig


@dataclass
class Origin:
    """Where a device's data is now, and what format it is in now.

    Attributes:
        path: The existing image, or None when there is nothing to copy.
        format: Its format, as read from the source -- not the format the new VM
            is being asked for. None lets the conversion tool detect it, which is
            better than asserting it wrongly (F-38).
    """

    path: Optional[str]
    format: Optional[DiskFormat] = None


@dataclass
class CloneResult:
    """What cloning a VM's disks will involve.

    Attributes:
        requests: The copies (or conversions) to run, in device order.
        unreachable: Source images that cannot be read from this machine, whose
            devices will be created blank instead.
        total_mb: How much data the copies amount to, for a warning worth reading.
    """

    requests: List[ConversionRequest] = field(default_factory=list)
    unreachable: List[str] = field(default_factory=list)
    total_mb: int = 0

    def __bool__(self) -> bool:
        """True when there is anything to copy."""
        return bool(self.requests)

    def describe(self) -> str:
        """Return a one-line summary, for telling a user what they asked for.

        The size is the point: copying disks is the slow, space-hungry part of
        creating a VM, and "up front" is the only useful time to say so.
        """
        if not self.requests:
            return "no disk contents to copy"
        gigabytes = self.total_mb / 1024
        size = f"{gigabytes:.1f} GB" if gigabytes >= 1 else f"{self.total_mb} MB"
        return (
            f"copying {len(self.requests)} disk image(s), about {size}; "
            f"this takes time and space on the target"
        )


def plan_clone(
    vm: VMConfig,
    capabilities,
    location: StorageLocation,
    origins: Optional[List[Optional[Origin]]] = None,
) -> CloneResult:
    """Work out how to bring *vm*'s disk contents to a new place.

    The configuration is **modified in place**: each device that will get a copy is
    pointed at it, so the emitter attaches the copy instead of creating a blank disk.
    That is the same handover ``migrate`` uses.

    Args:
        vm: The configuration to be created. Modified.
        capabilities: The target provider's declaration, for which formats it can
            create.
        location: Where the target keeps images.
        origins: Where each device's data is now and what format it is in, one per
            device in ``vm.storage`` order. None reads that from the devices
            themselves, which is right whenever nothing has overwritten it -- a
            caller that rewrites formats first must capture the origins before it
            does (F-38).

    Returns:
        CloneResult: the copies to run, what could not be read, and how much data.
    """
    result = CloneResult()
    for index, device in enumerate(vm.storage):
        if device.is_removable:
            # A medium is inserted, not copied: the path belongs to the other host.
            device.source = None
            continue
        # `source` first: it is what a configuration *states*, while `disk_path` is
        # only where a device was read from -- and exports leave that out on purpose,
        # because it describes a host rather than the VM.
        given = origins[index] if origins is not None and index < len(origins) else None
        origin = given or Origin(device.source or device.disk_path, device.format)
        if not origin.path:
            continue
        if not os.path.exists(origin.path):
            result.unreachable.append(origin.path)
            continue

        # A device with no format stated wants "whatever this provider creates", which
        # is also the right answer for a format it cannot create (M-04).
        target_format: DiskFormat = capabilities.native_format
        if device.format is not None:
            spec = capabilities.format_spec(device.format)
            if spec.support.creatable:
                target_format = device.format
        extension = capabilities.format_spec(target_format).extension
        copy_to = location.image_path(vm.name, f"{vm.name}_{device.name}.{extension}")

        result.requests.append(
            ConversionRequest(
                source=origin.path,
                target=copy_to,
                target_format=target_format,
                # The origin's own format, never the requested one (F-38).
                source_format=origin.format,
                label=device.name,
            )
        )
        result.total_mb += _size_of(origin.path, device.size_mb)
        # The emitter attaches the copy rather than creating a blank.
        device.source = copy_to
        device.format = target_format
    return result


def _size_of(path: str, declared: Optional[int]) -> int:
    """Return how much data a copy will involve, in MB.

    The file's own size, because that is what will be read; a sparse image's declared
    capacity would overstate the work by an order of magnitude.
    """
    try:
        return max(0, os.path.getsize(path) // (1024 * 1024))
    except OSError:
        return declared or 0
