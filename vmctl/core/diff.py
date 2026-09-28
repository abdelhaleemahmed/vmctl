"""
What is different between a VM and a file.

The command a config-as-code tool is not finished without: a VM drifts, and the only
honest way to know is to ask both sides and compare. It is read-only, it reuses the
parser and the model rather than adding a second description of a VM, and it is the
foundation ``apply`` needs -- reconciling means knowing what differs first (E-01,
then E-02 in PLAN.md).

Three decisions do the work, and each is about *what not to report*:

* **Devices are matched by where they are, not by what they are called.** A file says
  ``name: root``; VirtualBox has nowhere to store that, so the parser calls the same
  device ``disk_SATA Controller_0_0``. Comparing names would report drift on every
  device of every VM. The address -- bus, slot, unit -- is what both sides genuinely
  agree on.
* **A field only one side can state is not drift.** ``disk_path`` describes a host,
  ``metadata`` carries a provider's native hints (a libvirt UUID), and a generated MAC
  is the hypervisor's to choose. A diff that lists those is a diff nobody reads.
* **Only what the file actually says is compared.** This is the one that decides
  whether the command is usable at all. A ``VMConfig`` loaded from a file is full of
  defaults, and a default is not a request: a file that never mentions ``bootable``
  would otherwise "disagree" with every VM whose first disk is bootable, and one that
  never mentions ``machine`` would disagree with every VM that resolved to ``q35``.
  So the raw file is consulted for which fields it states, and a *setting* it does not
  state is skipped -- while a *device* or an *adapter* it does not have is still
  reported, because a disk nobody asked for is exactly the drift this command is for.
"""

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

from .vmconfig import (
    LEGACY_CONTROLLER_FIELDS,
    LEGACY_DEVICE_FIELDS,
    LEGACY_NETWORK_FIELDS,
    LEGACY_VM_FIELDS,
    StorageDevice,
    VMConfig,
)

#: Keys that could be talking about more than one of today's fields, taken to be
#: talking about all of them: being generous costs one compared field, while guessing
#: wrong drops a field from the comparison entirely.
#:
#: ``controller`` in 1.1.x meant the bus and today means the controller, and only the
#: *value* tells them apart. ``name`` is the sharper case: 1.1.x called a controller's
#: native name ``name``, so translating it everywhere quietly renamed a *VM's* ``name``
#: to ``native_name`` and left the VM's own name out of every comparison.
_AMBIGUOUS_NAMES = {
    "controller": ("controller", "bus"),
    "name": ("name", "native_name"),
}

#: Every 1.1.x name, so an older file's paths line up with today's fields. Flattened
#: across levels because the rest are unambiguous on their own.
_LEGACY_NAMES = {
    **LEGACY_VM_FIELDS,
    **LEGACY_DEVICE_FIELDS,
    **LEGACY_NETWORK_FIELDS,
    **LEGACY_CONTROLLER_FIELDS,
}


class ChangeKind(Enum):
    """What kind of difference this is."""

    CHANGED = "changed"
    ADDED = "added"  # in the file, not on the VM
    REMOVED = "removed"  # on the VM, not in the file


@dataclass(frozen=True)
class Change:
    """One difference between a VM and a file.

    Attributes:
        path: Where it is, e.g. ``cpu.count`` or ``storage[sata0:1].size_mb``.
        kind: Changed, added or removed.
        live: What the VM has, or None when it has no such thing.
        desired: What the file says, or None when it says nothing.
    """

    path: str
    kind: ChangeKind
    live: Any = None
    desired: Any = None

    def as_dict(self) -> Dict[str, Any]:
        """Return this difference as JSON-safe data.

        The same rendering as :meth:`render` uses for its values, so the text and
        the JSON cannot disagree about what a VM has -- a second opinion about how
        to print an enum is how the two drift apart.
        """
        return {
            "path": self.path,
            "kind": self.kind.value,
            "vm": None if self.live is None else _show(self.live),
            "file": None if self.desired is None else _show(self.desired),
        }

    def render(self) -> str:
        """Return one line describing this difference."""
        if self.kind is ChangeKind.ADDED:
            return f"+ {self.path}  only in the file: {_show(self.desired)}"
        if self.kind is ChangeKind.REMOVED:
            return f"- {self.path}  only on the VM: {_show(self.live)}"
        return f"~ {self.path}  vm {_show(self.live)}  file {_show(self.desired)}"


#: Fields that only one side can know, so a difference in them is not drift.
#:
#: ``name`` is on the list for devices only: the VM's name itself is compared, but a
#: *device's* name cannot be stored by VirtualBox or VMware at all.
IGNORED_DEVICE_FIELDS = frozenset({"name", "disk_path", "provider_options"})
IGNORED_VM_FIELDS = frozenset({"metadata", "storage", "networks", "storage_controllers"})
IGNORED_NETWORK_FIELDS = frozenset({"mac_address", "provider_options"})

_ABSENT = object()


def diff(live: VMConfig, desired: VMConfig, stated: Optional[Set[str]] = None) -> List[Change]:
    """Return every difference between a VM and a configuration.

    Args:
        live: The VM as the hypervisor reports it.
        desired: The VM as a file describes it.
        stated: The field paths the file actually contains, from
            :func:`stated_paths`. Without it every default in the model counts as a
            request, and the answer is unreadable.

    Returns:
        list[Change]: in a stable order -- settings, then devices, then adapters --
        so that two runs of the same comparison read identically.
    """
    changes: List[Change] = []
    changes += _settings(live, desired, stated)
    changes += _devices(live, desired, stated)
    changes += _networks(live, desired, stated)
    return changes


def stated_paths(data: Any, prefix: str = "") -> Set[str]:
    """Return the field paths a loaded config file actually contains.

    List indices are dropped -- ``storage.size_mb`` rather than
    ``storage[0].size_mb`` -- because what matters is whether the file talks about a
    field at all, and a file that states a size for one disk is talking about sizes.

    1.1.x names are translated to current ones, so an older file's ``disks:`` and
    ``type:`` line up with today's ``storage`` and ``kind``.

    Args:
        data: The mapping as loaded from YAML or JSON.
        prefix: Path prefix, used when recursing.

    Returns:
        set[str]: dotted paths.
    """
    found: Set[str] = set()
    if isinstance(data, dict):
        for key, value in data.items():
            readings = _AMBIGUOUS_NAMES.get(str(key)) or (_LEGACY_NAMES.get(str(key), str(key)),)
            for reading in readings:
                path = f"{prefix}.{reading}" if prefix else reading
                found.add(path)
                found |= stated_paths(value, path)
    elif isinstance(data, list):
        for item in data:
            found |= stated_paths(item, prefix)
    return found


def mentions(stated: Optional[Set[str]], path: str) -> bool:
    """Whether the file said anything about this path.

    Public because ``apply`` asks the same question for the opposite reason: what a
    file does not mention is not drift, and it is also not something to change
    (E-02). One rule, one implementation.
    """
    if stated is None:
        return True
    return _without_indices(path) in stated


def _without_indices(path: str) -> str:
    """Return a path with its list indices removed."""
    return re.sub(r"\[[^\]]*\]", "", path)


def _settings(live: VMConfig, desired: VMConfig, stated: Optional[Set[str]] = None) -> List[Change]:
    """Compare everything that is not a list of devices or adapters."""
    changes: List[Change] = []
    _walk("", live.to_dict(), desired.to_dict(), IGNORED_VM_FIELDS, changes, stated)
    return changes


def _walk(
    prefix: str,
    left: Dict[str, Any],
    right: Dict[str, Any],
    ignored: "frozenset[str]",
    changes: List[Change],
    stated: Optional[Set[str]] = None,
) -> None:
    """Compare two mappings, descending into nested ones.

    Without the descent a difference in one boot slot printed both whole ``boot``
    dictionaries and left the reader to spot it.
    """
    for key in sorted(set(left) | set(right)):
        if key in ignored:
            continue
        path = f"{prefix}.{key}" if prefix else key
        mine, theirs = left.get(key, _ABSENT), right.get(key, _ABSENT)
        if isinstance(mine, dict) and isinstance(theirs, dict):
            _walk(path, mine, theirs, ignored, changes, stated)
            continue
        if not mentions(stated, path):
            continue
        changes += _compare(path, mine, theirs)


def _devices(live: VMConfig, desired: VMConfig, stated: Optional[Set[str]] = None) -> List[Change]:
    """Compare storage devices, matched by address.

    The address is what both sides agree on. Where a device has none -- a file that
    leaves the position to vmctl -- position in the list is the fallback, which is
    also the order the allocator would place them in.
    """
    if not mentions(stated, "storage"):
        # A file with no storage section says nothing about devices, which is not the
        # same as asking for none. Reporting every disk as "only on the VM" made a
        # file that sets the memory disagree with the VM about its hardware, and
        # `apply` then reported drift it would never converge (E-02 found this).
        return []
    on_vm = by_address(live.storage)
    in_file = by_address(desired.storage)
    changes: List[Change] = []
    for address in sorted(set(on_vm) | set(in_file), key=str):
        where = f"storage[{address}]"
        vm_device = on_vm.get(address)
        file_device = in_file.get(address)
        if vm_device is None:
            changes.append(Change(where, ChangeKind.ADDED, desired=_summary(file_device)))
            continue
        if file_device is None:
            changes.append(Change(where, ChangeKind.REMOVED, live=_summary(vm_device)))
            continue
        left = vm_device.to_dict()
        right = file_device.to_dict()
        for key in sorted(set(left) | set(right)):
            if key in IGNORED_DEVICE_FIELDS or not mentions(stated, f"storage.{key}"):
                continue
            changes += _compare(f"{where}.{key}", left.get(key, _ABSENT), right.get(key, _ABSENT))
    return changes


def _networks(live: VMConfig, desired: VMConfig, stated: Optional[Set[str]] = None) -> List[Change]:
    """Compare adapters by position, which is what every provider numbers them by."""
    if not mentions(stated, "networks"):
        return []  # as with devices: an absent section is not a request for none
    changes: List[Change] = []
    for index in range(max(len(live.networks), len(desired.networks))):
        where = f"networks[{index}]"
        vm_nic = live.networks[index] if index < len(live.networks) else None
        file_nic = desired.networks[index] if index < len(desired.networks) else None
        if vm_nic is None:
            changes.append(Change(where, ChangeKind.ADDED, desired=_nic_summary(file_nic)))
            continue
        if file_nic is None:
            changes.append(Change(where, ChangeKind.REMOVED, live=_nic_summary(vm_nic)))
            continue
        left, right = vm_nic.to_dict(), file_nic.to_dict()
        for key in sorted(set(left) | set(right)):
            if key in IGNORED_NETWORK_FIELDS or not mentions(stated, f"networks.{key}"):
                continue
            changes += _compare(f"{where}.{key}", left.get(key, _ABSENT), right.get(key, _ABSENT))
    return changes


def _compare(path: str, left: Any, right: Any) -> List[Change]:
    """Return a change for one field, or nothing when the two agree.

    A field is compared only when *both* sides state a value. A file that says nothing
    has no opinion; a VM that reports nothing cannot be contradicted -- QEMU records no
    device position, so a file asking for ``slot: 0`` is not in disagreement with a VM
    that has no answer. Presence and absence are reported for whole devices and
    adapters, where they mean something, and not for fields, where they do not.
    """
    if left is _ABSENT or right is _ABSENT or right is None or left == right:
        return []
    return [Change(path, ChangeKind.CHANGED, live=left, desired=right)]


def by_address(devices: List[StorageDevice]) -> Dict[str, StorageDevice]:
    """Return devices keyed by bus and their order on it.

    Not by the raw address: a provider that assigns addresses itself reports none, so
    a file saying ``slot: 0`` and a QEMU VM reporting nothing would look like two
    different devices -- one added and one removed, where nothing had changed. Bus
    plus position on that bus is what both sides can always agree on, and for a
    provider that *does* record addresses the order follows them anyway.
    """
    out: Dict[str, StorageDevice] = {}
    counters: Dict[str, int] = {}
    for device in sorted(devices, key=lambda d: (d.bus.value, d.slot or 0, d.unit or 0)):
        bus = device.bus.value
        index = counters.get(bus, 0)
        counters[bus] = index + 1
        out[f"{bus}/{index}"] = device
    return out


def _summary(device: Optional[StorageDevice]) -> str:
    """Return a short description of a device, for an added/removed line."""
    if device is None:
        return "nothing"
    parts = [device.kind.value, device.bus.value]
    if device.size_mb:
        parts.append(f"{device.size_mb} MB")
    if device.source:
        parts.append(device.source)
    return ", ".join(parts)


def _nic_summary(nic) -> str:
    """Return a short description of an adapter."""
    if nic is None:
        return "nothing"
    parts = [nic.network_type.value, nic.model.value]
    if nic.adapter_name:
        parts.append(nic.adapter_name)
    return ", ".join(parts)


def _show(value: Any) -> str:
    """Render a value for a diff line."""
    if value is _ABSENT or value is None:
        return "(unset)"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def summarise(changes: List[Change]) -> str:
    """Return a one-line summary of a comparison."""
    if not changes:
        return "no differences"
    counts: List[Tuple[str, int]] = []
    for kind in ChangeKind:
        found = sum(1 for change in changes if change.kind is kind)
        if found:
            counts.append((kind.value, found))
    return ", ".join(f"{count} {name}" for name, count in counts)
