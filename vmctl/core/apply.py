"""
Making a hypervisor match a file (E-02).

``import`` creates and ``edit`` changes; ``apply`` is the one that makes vmctl
declarative rather than one-shot. You run it against the same file as often as you
like: it creates the VM when it is missing, changes what drifted when it is not, and
does nothing at all when the two already agree.

It is built out of what is already here -- ``core/diff.py`` says what differs, the
provider's ``emit_modify_vm`` says how to change it -- and adds one thing of its own:
the rule for **what a file is allowed to decide**.

* **A default is not a request.** The same rule E-01 needed, and here it is not
  cosmetic but destructive: a `VMConfig` loaded from a file is full of defaults, so
  converging towards it wholesale would reset every setting the file never mentioned.
  A file that says only ``cpu: {count: 4}`` must change the CPU count and nothing
  else.
* **The fields a diff will not report are the fields an apply will not change.** The
  ignore lists in ``core/diff.py`` are exactly the things only the hypervisor can know
  -- where an image is on this host, the MAC it generated, a libvirt domain's UUID --
  so they are carried over from the live VM rather than taken from the file. That is
  not tidiness: libvirt and QEMU redefine a VM wholesale, so a disk whose path was
  dropped would come back pointing somewhere else, and an adapter whose MAC was
  dropped would give the guest a new network card.
* **Disks are never created or destroyed on an existing VM.** Converging a definition
  is safe; resizing or replacing an image is not what "apply" should quietly mean. A
  device the file adds is reported instead, with the reason.

What a provider cannot change in place it already reports (VirtualBox: storage and
network layout), and that reaches the user as a warning rather than a silent no-op.
"""

import copy
from dataclasses import dataclass, field, fields, is_dataclass
from enum import Enum
from typing import Any, List, Optional, Set

from .diff import (
    IGNORED_DEVICE_FIELDS,
    IGNORED_NETWORK_FIELDS,
    IGNORED_VM_FIELDS,
    Change,
    by_address,
    diff,
    mentions,
)
from .vmconfig import NetworkConfig, StorageDevice, VMConfig

#: The lists a device and an adapter carry over from the VM rather than take from the
#: file, and the same sets ``diff`` refuses to report on -- deliberately one
#: declaration, since the two questions are the same question.
CARRIED_DEVICE_FIELDS = IGNORED_DEVICE_FIELDS - {"name"}
CARRIED_NETWORK_FIELDS = IGNORED_NETWORK_FIELDS

#: Handled by name below rather than by the field walk, because a list is replaced
#: rather than merged: a file that states ``storage:`` is stating the whole list.
_LISTS = ("storage", "storage_controllers", "networks")


class Action(Enum):
    """What applying a file to a hypervisor turns out to mean."""

    CREATE = "create"
    CONVERGE = "converge"
    NOTHING = "nothing"


@dataclass
class Convergence:
    """What ``apply`` is going to do.

    Attributes:
        action: Create, converge, or nothing.
        vm: The configuration to create, or the live VM with the file's stated
            fields laid over it.
        changes: The drift, from :func:`vmctl.core.diff.diff`. Empty unless the
            action is ``CONVERGE``.
        warnings: Things the file asks for that converging cannot do.
    """

    action: Action
    vm: VMConfig
    changes: List[Change] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def describe(self) -> str:
        """Return one line saying what will happen, for printing before it does."""
        if self.action is Action.CREATE:
            return f"{self.vm.name} does not exist; it will be created"
        if self.action is Action.NOTHING:
            return f"{self.vm.name} already matches the file; nothing to do"
        return f"{self.vm.name} differs from the file in {len(self.changes)} place(s)"


def plan_convergence(
    live: Optional[VMConfig], desired: VMConfig, stated: Optional[Set[str]] = None
) -> Convergence:
    """Decide what applying *desired* means for a VM.

    Args:
        live: The VM as the hypervisor reports it, or None when there is no such VM.
        desired: The configuration the file describes.
        stated: The field paths the file actually contains, from
            :func:`vmctl.core.diff.stated_paths`. Without it every default in the
            model counts as a request, which is the destructive reading.

    Returns:
        Convergence: what to do, and what the VM should become.
    """
    if live is None:
        return Convergence(Action.CREATE, desired)

    changes = diff(live, desired, stated)
    if not changes:
        return Convergence(Action.NOTHING, live)

    target = overlay(live, desired, stated)
    return Convergence(Action.CONVERGE, target, changes, _unconvergeable(live, target))


def overlay(live: VMConfig, desired: VMConfig, stated: Optional[Set[str]] = None) -> VMConfig:
    """Return the live VM with the file's stated fields laid over it.

    This is the whole safety property of ``apply``: what the file does not talk about
    is left exactly as the hypervisor reported it, down to the identifiers only the
    hypervisor can know.

    Args:
        live: The VM as it is.
        desired: The VM as the file describes it -- full of defaults, most of which
            are not requests.
        stated: The paths the file states. None means "all of it", which is what
            ``import`` already does.

    Returns:
        VMConfig: a new configuration; neither argument is modified.
    """
    result = copy.deepcopy(live)
    _overlay_fields(result, desired, "", stated, skip=set(IGNORED_VM_FIELDS))

    if mentions(stated, "storage"):
        result.storage = _carried_devices(live.storage, desired.storage, stated)
    if mentions(stated, "storage_controllers"):
        result.storage_controllers = copy.deepcopy(desired.storage_controllers)
    if mentions(stated, "networks"):
        result.networks = _carried_networks(live.networks, desired.networks, stated)
    return result


def _overlay_fields(
    target: Any, source: Any, prefix: str, stated: Optional[Set[str]], skip: Set[str]
) -> None:
    """Copy every stated field of *source* onto *target*, descending into dataclasses.

    The descent is what lets a file say ``memory: {mb: 4096}`` without also resetting
    ``vram_mb`` and ``ballooning`` to their defaults.
    """
    for spec in fields(target):
        if spec.name in skip:
            continue
        path = f"{prefix}.{spec.name}" if prefix else spec.name
        mine, theirs = getattr(target, spec.name), getattr(source, spec.name, None)
        if is_dataclass(mine) and is_dataclass(theirs):
            _overlay_fields(mine, theirs, path, stated, skip=set())
            continue
        if mentions(stated, path):
            setattr(target, spec.name, copy.deepcopy(theirs))


def _carried_devices(
    live: List[StorageDevice],
    desired: List[StorageDevice],
    stated: Optional[Set[str]] = None,
) -> List[StorageDevice]:
    """Return the file's devices, each keeping what only the VM can know.

    Matched by address, the same way ``diff`` matches them, so the device at
    ``sata/0`` keeps the image it has rather than being pointed at a path computed
    from its name -- which is how a redefinition would silently detach a disk.

    For a device that **already exists**, only the fields the file states are taken
    from it: the rest stay as the VM has them. Replacing the whole device instead was
    the device-level version of the mistake ``apply`` exists to avoid -- a file saying
    ``storage: [{name: system, size_mb: 512}]`` also reset ``bootable``, ``discard``,
    ``allocation`` and every other unstated field to the model's default, and the
    first symptom was VirtualBox warning about a storage layout that had not changed
    (F-46).
    """
    on_vm = by_address(live)
    in_file = by_address(desired)
    out: List[StorageDevice] = []
    for address, device in in_file.items():
        existing = on_vm.get(address)
        if existing is None:
            out.append(copy.deepcopy(device))  # a new device is the file's, entirely
            continue
        merged = copy.deepcopy(existing)
        _overlay_fields(merged, device, "storage", stated, skip=set(CARRIED_DEVICE_FIELDS))
        # The name is the file's: it is what the user calls the device, and the
        # providers that cannot store one generate something unreadable.
        merged.name = device.name or merged.name
        out.append(merged)
    return out


def _carried_networks(
    live: List[NetworkConfig],
    desired: List[NetworkConfig],
    stated: Optional[Set[str]] = None,
) -> List[NetworkConfig]:
    """Return the file's adapters, each keeping the MAC the hypervisor generated.

    By position, which is what every provider numbers adapters by, and field by field
    for an adapter that already exists -- the same rule as for devices (F-46). A file
    that *does* state a MAC keeps its own: this carries a value over, it does not
    override one.
    """
    out: List[NetworkConfig] = []
    for index, adapter in enumerate(desired):
        if index >= len(live):
            out.append(copy.deepcopy(adapter))
            continue
        merged = copy.deepcopy(live[index])
        _overlay_fields(merged, adapter, "networks", stated, skip=set(CARRIED_NETWORK_FIELDS))
        for name in CARRIED_NETWORK_FIELDS:
            if getattr(adapter, name, None) not in (None, {}):
                setattr(merged, name, copy.deepcopy(getattr(adapter, name)))
        out.append(merged)
    return out


def _unconvergeable(live: VMConfig, target: VMConfig) -> List[str]:
    """Return what the file asks for that converging a definition cannot do.

    Both entries are about disks, because a definition is cheap to rewrite and an
    image is not. Saying so is the point: an ``apply`` that silently fails to
    converge is worse than one that refuses, and the same file applied twice would
    otherwise keep reporting the same drift with no explanation.
    """
    on_vm = by_address(live.storage)
    messages = []
    for address, device in by_address(target.storage).items():
        existing = on_vm.get(address)
        if device.is_removable:
            continue
        if existing is None:
            if not (device.source or device.disk_path):
                messages.append(
                    f"the file adds a disk at {address} with no image behind it; apply "
                    f"does not create disks on an existing VM, so attach one with "
                    f"'vmctl convert' or recreate the VM from the file"
                )
            continue
        if device.size_mb and existing.size_mb and device.size_mb != existing.size_mb:
            messages.append(
                f"the disk at {address} is {existing.size_mb} MB and the file asks for "
                f"{device.size_mb} MB; apply does not resize images, so it was left "
                f"alone -- grow it with the hypervisor's own tool"
            )
    for address, device in on_vm.items():
        if address not in by_address(target.storage) and not device.is_removable:
            messages.append(
                f"the file does not mention the disk at {address}, so it is detached "
                f"from the VM; the image file itself is left on disk"
            )
    return messages
