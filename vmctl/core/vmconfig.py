"""
Core VM configuration models - the center of gravity.

What a VM *is*, in terms no hypervisor owns. The storage vocabulary a device is
described with -- kinds, buses, formats, allocations -- lives in
:mod:`vmctl.core.devices`, because a capability table and a provider's tables
need to name a bus without importing a VM.

Those names are re-exported here, including the pre-M-01 ones (``DiskType``,
``DiskVariant``, ``StorageControllerType``), so that every import a 1.1.x caller
wrote keeps working. They are documented where they are defined rather than
twice.
"""

import copy
import difflib
from dataclasses import dataclass, field, asdict, fields, is_dataclass, MISSING
from enum import Enum
from functools import lru_cache
from typing import (
    Any,
    Dict,
    List,
    Optional,
    Union,
    get_args,
    get_origin,
    get_type_hints,
)

from .devices import Allocation, BusType, DeviceKind, DiskFormat
from .oscatalog import DEFAULT_ID as DEFAULT_GUEST_OS
from .platform import Arch, NicModel

# Re-exported and unused here: `from vmctl.core.vmconfig import DiskType` is the
# import a 1.1.x caller wrote, and it keeps working after the M-01 split.
from .devices import DiskType, DiskVariant, StorageControllerType  # noqa: F401
from .exceptions import ValidationError

#: What this module defines. The storage vocabulary it re-exports is deliberately
#: absent: those names belong to :mod:`vmctl.core.devices`, and listing them here
#: would have the API docs describe each one twice under two homes.
__all__ = [
    "BootConfig",
    "CPUConfig",
    "DEFAULT_DISK_MB",
    "Arch",
    "DEFAULT_GUEST_OS",
    "DiskConfig",
    "FirmwareConfig",
    "FirmwareType",
    "MemoryConfig",
    "NetworkConfig",
    "NetworkType",
    "NicModel",
    "PortForward",
    "StorageController",
    "StorageControllerConfig",
    "StorageDevice",
    "VMConfig",
    "default_controller_id",
    "resolve_controller",
]


class FirmwareType(Enum):
    """VM firmware type.

    Determines the boot firmware used by the virtual machine.
    EFI64 is required for Windows 11 and Secure Boot.
    """

    BIOS = "bios"
    EFI = "efi"
    EFI64 = "efi64"
    EFI32 = "efi32"


class NetworkType(Enum):
    """Network adapter connection mode.

    NAT: outbound internet through the host; guests are not directly reachable.
    BRIDGED: guest appears as a first-class device on the physical network.
    HOSTONLY: isolated network shared only between the host and VMs.
    INTERNAL: VM-to-VM only; the host has no access.
    NATNETWORK: like NAT but multiple VMs share one DHCP domain.
    """

    NAT = "nat"
    BRIDGED = "bridged"
    HOSTONLY = "hostonly"
    INTERNAL = "internal"
    NATNETWORK = "natnetwork"


@dataclass
class CPUConfig:
    """CPU configuration.

    Attributes:
        count: Number of virtual CPUs (1–128).
        hotplug: Allow CPUs to be added/removed while the VM is running.
        execution_cap: Maximum percentage of host CPU time the VM may use (1–100).
        pae: Enable Physical Address Extension for 32-bit OSes.
        nested_virt: Enable nested virtualisation (required for running KVM inside VirtualBox).
        sockets: Sockets to present, or None to let the provider decide.
        cores: Cores per socket, or None.
        threads: Threads per core, or None.
        model: The CPU model the guest sees: ``host`` to pass the host's CPU
            through, ``host-model`` for the closest migratable description, or a
            named model such as ``Skylake-Client``. None leaves it to the
            provider. VirtualBox has no such setting and reports it (A-10).

    A topology is not free: libvirt refuses a domain whose sockets x cores x
    threads does not equal its vCPU count, so :mod:`vmctl.validators` checks the
    two agree rather than letting the hypervisor discover it.
    """

    count: int = 2
    hotplug: bool = False
    execution_cap: int = 100  # Percentage
    pae: bool = False  # Physical Address Extension
    nested_virt: bool = False
    sockets: Optional[int] = None
    cores: Optional[int] = None
    threads: Optional[int] = None
    model: Optional[str] = None

    @property
    def has_topology(self) -> bool:
        """Whether the configuration states a topology at all."""
        return any(v is not None for v in (self.sockets, self.cores, self.threads))

    def to_dict(self) -> dict:
        """Return CPU configuration as a plain dictionary.

        Returns:
            dict: All fields with their current values.
        """
        return asdict(self)


@dataclass
class MemoryConfig:
    """Memory configuration.

    Attributes:
        mb: RAM in megabytes (4–1,048,576).
        vram_mb: Video RAM in megabytes (1–256).
        page_fusion: Enable kernel same-page merging to reduce physical RAM usage.
        ballooning: Enable dynamic memory ballooning (guest must support it).
    """

    mb: int = 2048  # Memory in MB
    vram_mb: int = 16  # Video RAM in MB
    page_fusion: bool = False
    ballooning: bool = False

    def to_dict(self) -> dict:
        """Return memory configuration as a plain dictionary.

        Returns:
            dict: All fields with their current values.
        """
        return asdict(self)


@dataclass
class FirmwareConfig:
    """Firmware configuration.

    Attributes:
        type: Firmware type (BIOS, EFI, EFI64, EFI32).
        secure_boot: Enable Secure Boot (requires EFI firmware).
        tpm: Enable TPM chip emulation.
    """

    type: FirmwareType = FirmwareType.BIOS
    secure_boot: bool = False
    tpm: bool = False

    def to_dict(self) -> dict:
        """Return firmware configuration as a plain dictionary.

        Enum values are serialised to their string representations.

        Returns:
            dict: Fields with ``type`` as a string value.
        """
        result = asdict(self)
        result["type"] = self.type.value
        return result


#: Capacity given to a disk that does not state one. 1.1.x kept this in the field
#: default, where it also reached optical and floppy drives -- which have no
#: capacity at all, so the number was noise the validator then had to warn about.
DEFAULT_DISK_MB = 20480


@dataclass
class StorageDevice:
    """A device on a storage bus: a disk, an optical drive or a floppy drive.

    Was ``DiskConfig``, which could only really describe a disk. Five of its
    fields have clearer names here, and all five keep working -- see
    :data:`LEGACY_DEVICE_FIELDS`.

    Attributes:
        name: Identifier for this device within the VM (e.g. ``"system"``).
        kind: What the guest sees: a disk, a CD-ROM or a floppy drive. Was
            ``type``.
        bus: The interface it hangs off. Was ``controller`` -- which named a
            *bus* while ``controller_name`` named a *controller*, one word doing
            two jobs (M-02).
        controller: Which controller to attach to: a
            :attr:`StorageController.id`, or a provider's own name for one, or
            None to take whichever controller serves ``bus``. Was
            ``controller_name``.
        slot: Port on the controller (0-based), or None to be assigned. Was
            ``port``.
        unit: Device on that slot (0 or 1 on IDE), or None to be assigned. Was
            ``device``.
        size_mb: Capacity in megabytes. None on a removable drive, which has
            none; a disk that does not say gets :data:`DEFAULT_DISK_MB`.
        format: Image format, or None for whichever format the provider creates
            natively. None is what keeps a config portable: it is the difference
            between "a disk" and "a VirtualBox disk" (M-04).
        allocation: THIN (dynamic) or THICK (preallocated). Was ``variant``.
        source: An existing medium to attach instead of creating one -- an ISO
            for an optical drive, or an image that already exists, which is how a
            migration attaches the converted copy of a disk.
        readonly: Attach without write access.
        nonrotational: Present the disk to the guest as solid-state. Was
            ``type: ssd`` (M-01).
        discard: Pass the guest's TRIM/UNMAP through to the host, so freeing
            space in the guest frees it on the host.
        hotpluggable: Let the guest detach the device while it is running.
        bootable: Mark this device as a boot device.
        disk_path: Where the image is on the source host. Never exported: it
            describes a host, not the VM.
        provider_options: Settings only one hypervisor has, carried verbatim.
            vmctl does not interpret them; they exist so that a native detail can
            survive a round trip on its own provider instead of being dropped,
            without every such detail having to become a neutral field first.
    """

    name: str
    kind: DeviceKind = DeviceKind.DISK
    bus: BusType = BusType.SATA
    controller: Optional[str] = None
    slot: Optional[int] = None
    unit: Optional[int] = None
    size_mb: Optional[int] = None
    format: Optional[DiskFormat] = None
    allocation: Allocation = Allocation.THIN
    source: Optional[str] = None
    readonly: bool = False
    nonrotational: bool = False
    discard: bool = False
    hotpluggable: bool = False
    bootable: bool = False
    disk_path: Optional[str] = None
    provider_options: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Give a disk that states no capacity the default one.

        A removable drive is left exactly as it was given, including a capacity
        it cannot have: quietly dropping what someone wrote is how a config comes
        back different from the file, so the validator says so instead.
        """
        if self.size_mb is None and not self.kind.is_removable:
            self.size_mb = DEFAULT_DISK_MB

    #: 1.1.x spellings of ``type:``, and what they mean now. ``ssd`` was never a
    #: kind of device -- it also sets ``nonrotational`` (M-01/M-06).
    _LEGACY_KINDS = {"hdd": "disk", "ssd": "disk", "dvd": "cdrom"}

    @classmethod
    def _from_legacy(cls, data: dict, path: str) -> dict:
        """Translate a 1.1.x device mapping into current field names and values.

        Called by the loader before anything is validated, so an export from an
        older vmctl keeps loading unchanged (ground rule 2, M-06).

        Args:
            data: The mapping as it appeared in the file. Modified in place.
            path: Dotted path, for error messages.

        Returns:
            The same mapping, with legacy spellings replaced.

        Raises:
            ValidationError: If a field is given under both its old and its new
                name, since only one of the two can be honoured.
        """
        raw = data.get("type")
        if isinstance(raw, str):
            legacy = cls._LEGACY_KINDS.get(raw.strip().lower())
            if legacy is not None:
                if raw.strip().lower() == "ssd" and "nonrotational" not in data:
                    data["nonrotational"] = True
                data["type"] = legacy

        # `controller` is the one key whose *meaning* changed: in 1.1.x it named
        # a bus, and now it names a controller. The two are told apart by the
        # value, because a bus is spelled exactly like the enum ("sata") while an
        # id carries an index ("sata0"). A file that says `bus:` is current by
        # definition, so nothing is guessed there.
        wanted = data.get("controller")
        if "bus" not in data and isinstance(wanted, str):
            try:
                BusType(wanted.strip().lower())
            except ValueError:
                pass
            else:
                data["bus"] = data.pop("controller")

        return _rename_keys(data, LEGACY_DEVICE_FIELDS, path)

    @property
    def is_removable(self) -> bool:
        """True for devices whose medium is inserted, not created.

        A DVD or floppy drive is attached either empty or pointing at an
        existing image; vmctl must never create a medium for one.
        """
        return self.kind.is_removable

    def to_dict(self) -> dict:
        """Return the device as a plain dictionary.

        Enum values become their string form. ``disk_path`` is left out because
        it describes the host the device was read from, and anything unset is
        left out rather than written as ``null``: an absent ``format`` means
        "whatever this provider creates natively", which is a portable statement,
        and ``format: null`` only looks like a mistake.

        Returns:
            dict: Exportable fields, with enum values as strings.
        """
        result = asdict(self)
        for key in ("kind", "format", "allocation", "bus"):
            value = getattr(self, key)
            if value is not None:
                result[key] = value.value
        result.pop("disk_path", None)
        for key, value in list(result.items()):
            if value is None or (key == "provider_options" and not value):
                del result[key]
        return result


@dataclass
class StorageController:
    """A storage controller: one bus, some number of ports, a name on each side.

    Was ``StorageControllerConfig``, whose ``name`` was simultaneously the key a
    device referenced *and* the literal string VirtualBox uses. Those are two
    different things, and conflating them is what made ``"SATA"`` a magic value
    (F-01): a config saying ``controller_name: "SATA"`` could not be told apart
    from one that meant "the SATA bus, I don't care which controller".

    Attributes:
        id: Stable logical key, e.g. ``"sata0"``. What devices reference, and
            what stays the same when the same VM is expressed on another
            hypervisor.
        bus: The bus this controller implements. Was ``controller_type``, which
            was documented as a chipset and valued as a bus.
        model: Neutral model name, when the bus offers a choice. The chipset a
            provider uses to implement it is that provider's business, resolved
            through its tables.
        port_count: How many ports to give it, or None for the provider's own
            default -- which is the only safe answer, since a bus may accept
            exactly one number (IDE takes 2, USB takes 8) and it differs per
            provider.
        bootable: Whether the firmware may boot from this controller.
        native_name: What the provider calls it (``"SATA Controller"``). Kept so
            a same-provider round trip is faithful; meaningless anywhere else,
            which is why it is separate from ``id``.
    """

    id: str = ""
    bus: BusType = BusType.SATA
    model: Optional[str] = None
    port_count: Optional[int] = None
    bootable: bool = False
    native_name: Optional[str] = None

    def __post_init__(self) -> None:
        """Give an unnamed controller the conventional id for its bus."""
        if not self.id:
            self.id = default_controller_id(self.bus)

    @classmethod
    def _from_legacy(cls, data: dict, path: str) -> dict:
        """Translate a 1.1.x controller mapping: ``name`` was a native name."""
        return _rename_keys(data, LEGACY_CONTROLLER_FIELDS, path)

    def to_dict(self) -> dict:
        """Return the controller as a plain dictionary, without unset fields."""
        result = asdict(self)
        result["bus"] = self.bus.value
        return {k: v for k, v in result.items() if v is not None}


def default_controller_id(bus: BusType, index: int = 0) -> str:
    """Return the conventional logical id for a controller on *bus*.

    One rule, used by every provider's parser, so that the same VM read through
    two hypervisors names its controllers the same way.

    Args:
        bus: The bus the controller implements.
        index: Which controller on that bus, 0-based.

    Returns:
        An id such as ``"sata0"`` or ``"virtio-scsi1"``.
    """
    return f"{bus.value}{index}"


# ---------------------------------------------------------------------------
# 1.1.x names
#
# Both halves of compatibility, in one table each: the loader renames these keys
# in a config file, and the classes accept them as attributes and as constructor
# keywords. Nothing else in vmctl reads the old names, so the aliases are a
# boundary rather than a second vocabulary running in parallel.
# ---------------------------------------------------------------------------

#: Old name -> new name, for a device.
LEGACY_DEVICE_FIELDS = {
    "type": "kind",
    "variant": "allocation",
    "controller_name": "controller",
    "port": "slot",
    "device": "unit",
}

#: Old name -> new name, for a controller. ``name`` was the provider's own string
#: for it, which is now ``native_name``; the logical ``id`` is derived.
LEGACY_CONTROLLER_FIELDS = {"name": "native_name", "controller_type": "bus"}

#: Old name -> new name, on a network adapter.
LEGACY_NETWORK_FIELDS = {"adapter_type": "model"}

#: 1.1.x chipset spellings -> the neutral model, keyed the way a file writes them.
#: Both providers' native names are here because a config exported from either one has
#: to keep loading: VirtualBox reports ``82540EM`` and QEMU ``e1000``, and they are the
#: same card (A-10). The real spelling is the key so that the JSON Schema can offer it
#: (E-06); lookups go through the lower-cased view below, since the loader has always
#: been case-insensitive.
LEGACY_NIC_MODELS = {
    "82540EM": "e1000",
    "82543GC": "e1000",
    "82545EM": "e1000",
    "Am79C970A": "pcnet",
    "Am79C973": "pcnet",
    "Am79C960": "pcnet",
    "virtio-net": "virtio",
    "ne2k_pci": "ne2k",
}

#: The same table, for a lookup that does not care about case.
_LEGACY_NIC_LOOKUP = {name.lower(): model for name, model in LEGACY_NIC_MODELS.items()}

#: Old name -> new name, on the VM itself.
LEGACY_VM_FIELDS = {"disks": "storage", "ostype": "guest_os"}


def _rename_keys(data: dict, mapping: Dict[str, str], path: str) -> dict:
    """Rename old keys in a loaded mapping, refusing to guess between two.

    Args:
        data: Mapping straight from the config file. Modified in place.
        mapping: Old name -> new name.
        path: Dotted path, for error messages.

    Returns:
        The same mapping.

    Raises:
        ValidationError: If both names are present, since only one can be used.
    """
    for old, new in mapping.items():
        if old not in data:
            continue
        if new in data:
            raise ValidationError(
                f"{_label(path)} sets both {old!r} and {new!r}",
                field=_join(path, old),
                recovery_hint=f"{old!r} is the older name for {new!r}; keep one.",
            )
        data[new] = data.pop(old)
    return data


def _alias(old: str, new: str) -> property:
    """Return a read/write property forwarding *old* to *new*."""

    def read(self):
        return getattr(self, new)

    def write(self, value):
        setattr(self, new, value)

    return property(read, write, doc=f"Deprecated alias for :attr:`{new}`.")


def _bus_given_as_controller(kwargs: Dict[str, Any]) -> None:
    """Read ``controller=<a bus>`` as the bus it must have meant.

    ``controller`` is the one keyword whose *meaning* changed rather than its
    name: in 1.1.x it named a bus, and now it names a controller. The value tells
    the two apart with certainty, since a :class:`BusType` was never a controller
    id. (An attribute *assignment* cannot be intercepted this way, so the
    validator reports that case rather than acting on a guess.)
    """
    if isinstance(kwargs.get("controller"), BusType):
        kwargs.setdefault("bus", kwargs.pop("controller"))


def _nic_model_given_as_text(kwargs: Dict[str, Any]) -> None:
    """Read a chipset *name* as the model it is.

    1.1.x code wrote ``adapter_type="82540EM"`` -- a VirtualBox chipset id -- and
    a caller may equally write the QEMU spelling. Both name the same card, so both
    resolve to the same :class:`~vmctl.core.platform.NicModel` (A-10).
    """
    for key in ("adapter_type", "model"):
        value = kwargs.get(key)
        if not isinstance(value, str):
            continue
        text = value.strip().lower()
        kwargs[key] = NicModel(_LEGACY_NIC_LOOKUP.get(text, text))


def _accept_legacy_keywords(
    cls: type,
    mapping: Dict[str, str],
    fixup: Optional[Any] = None,
) -> None:
    """Let a class's constructor take the old keyword names too.

    Applied after the dataclass is built, because the old names must not become
    fields: they would then be compared, copied and serialised alongside the ones
    they alias.

    Args:
        cls: The dataclass to patch.
        mapping: Old keyword -> new keyword.
        fixup: Called with the keyword dict first, for a keyword whose meaning
            changed rather than its name.
    """
    original = cls.__init__  # type: ignore[misc]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        if fixup is not None:
            fixup(kwargs)
        for old, new in mapping.items():
            if old in kwargs:
                value = kwargs.pop(old)
                # An explicit new name wins, so mixing the two is not silently
                # order-dependent.
                kwargs.setdefault(new, value)
        original(self, *args, **kwargs)

    setattr(cls, "__init__", __init__)


for _old, _new in LEGACY_DEVICE_FIELDS.items():
    setattr(StorageDevice, _old, _alias(_old, _new))
_accept_legacy_keywords(StorageDevice, LEGACY_DEVICE_FIELDS, _bus_given_as_controller)

# `name` reads back as whatever the provider calls it, falling back to the
# logical id -- which is what 1.1.x code expects to find there.
setattr(
    StorageController,
    "name",
    property(
        lambda self: self.native_name or self.id,
        lambda self, value: setattr(self, "native_name", value),
        doc="Deprecated alias for :attr:`native_name`, falling back to :attr:`id`.",
    ),
)
setattr(StorageController, "controller_type", _alias("controller_type", "bus"))
_accept_legacy_keywords(StorageController, LEGACY_CONTROLLER_FIELDS)

#: Deprecated alias for :class:`StorageDevice`.
DiskConfig = StorageDevice

#: Deprecated alias for :class:`StorageController`.
StorageControllerConfig = StorageController


@dataclass
class PortForward:
    """One host port forwarded to a guest port through a NAT adapter (E-10).

    The setting a lab actually uses -- "ssh to localhost:2222 reaches the guest" --
    and one the model had no word for, so it was lost on every round trip.

    The field order follows VirtualBox's own spelling,
    ``name,protocol,hostip,hostport,guestip,guestport``, because that is the most
    detailed of the four and the others are subsets of it: QEMU's
    ``hostfwd=tcp:127.0.0.1:2222-:22`` is the same thing without a name, and
    libvirt's ``<portForward>`` the same again in XML.

    Attributes:
        host_port: The port on this machine. Required; there is no default worth
            guessing.
        guest_port: The port inside the guest. Required.
        protocol: ``tcp`` or ``udp``.
        name: What the rule is called. VirtualBox needs one and requires it to be
            unique; the others have no such concept, so vmctl fills one in rather
            than making every config carry a field only one provider reads.
        host_ip: Which host address to listen on. Empty means all of them, which is
            what every provider means by leaving it out -- and a reason to set it:
            ``127.0.0.1`` keeps a lab's forwarded ports off the network.
        guest_ip: Which guest address to send to. Empty means the address the
            guest's DHCP gave it, which is what a NAT adapter normally wants.
    """

    host_port: int = 0
    guest_port: int = 0
    protocol: str = "tcp"
    name: str = ""
    host_ip: str = ""
    guest_ip: str = ""

    def __post_init__(self) -> None:
        """Normalise the protocol and give the rule a name if it has none.

        A name is generated rather than demanded: only VirtualBox has the concept,
        and ``ssh-2222`` says more in a listing than ``rule1`` does.
        """
        self.protocol = str(self.protocol).strip().lower() or "tcp"
        if not self.name:
            self.name = f"{self.protocol}-{self.host_port}"

    @property
    def label(self) -> str:
        """Return the rule in one line, the way a person would say it."""
        where = self.host_ip or "*"
        target = self.guest_ip or "guest"
        return f"{self.protocol} {where}:{self.host_port} -> {target}:{self.guest_port}"

    def to_dict(self) -> Dict[str, Any]:
        """Return the rule as plain data, leaving out what was not set.

        An empty ``host_ip`` is "all addresses" rather than a value, so writing it
        out would turn a default into a statement -- which ``diff`` and ``apply``
        would then treat as a request (E-01, E-02).
        """
        data: Dict[str, Any] = {
            "name": self.name,
            "protocol": self.protocol,
            "host_port": self.host_port,
            "guest_port": self.guest_port,
        }
        if self.host_ip:
            data["host_ip"] = self.host_ip
        if self.guest_ip:
            data["guest_ip"] = self.guest_ip
        return data


@dataclass
class NetworkConfig:
    """Network adapter configuration.

    Attributes:
        model: The network chipset the guest sees. Was ``adapter_type``, which
            held VirtualBox's own id (``"82540EM"``) in the neutral model (A-10).
        network_type: Connection mode (NAT, BRIDGED, HOSTONLY, INTERNAL, NATNETWORK).
        adapter_name: Physical or virtual interface name used for BRIDGED / HOSTONLY modes.
        mac_address: Custom MAC address; ``None`` lets VirtualBox assign one automatically.
        promiscuous_mode: Allow the adapter to receive packets not addressed to it.
        port_forwards: Host ports forwarded into the guest. Only meaningful on a
            NAT adapter -- every other mode reaches the guest directly -- and not
            every provider can express them (E-10).
        provider_options: Native details with no neutral equivalent, by provider
            name. This is what keeps a same-provider round trip exact where the
            neutral vocabulary is deliberately coarser: VirtualBox has three Intel
            PRO/1000 variants that are all ``e1000`` to the model, and re-creating
            the VM should give the guest back the same card, not the family's
            default (A-10).
    """

    # e1000 rather than virtio: an emulated Intel card is what a guest with no
    # drivers can see, which is the right default for a VM that may be about to
    # install an OS. It is also what 1.1.x defaulted to, spelled neutrally.
    model: NicModel = NicModel.E1000
    network_type: NetworkType = NetworkType.NAT
    adapter_name: Optional[str] = None  # For bridged/host-only
    mac_address: Optional[str] = None
    promiscuous_mode: bool = False
    port_forwards: List[PortForward] = field(default_factory=list)
    provider_options: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def _from_legacy(cls, data: dict, path: str) -> dict:
        """Accept ``adapter_type:`` and the chipset names 1.1.x wrote there.

        A raw chipset name is translated rather than passed through, unlike a
        guest OS label: an unknown NIC model cannot be attached to anything, so
        carrying it forward would only postpone the error to the hypervisor.
        """
        raw = data.get("adapter_type")
        if isinstance(raw, str):
            data["adapter_type"] = _LEGACY_NIC_LOOKUP.get(raw.strip().lower(), raw)
        return _rename_keys(data, LEGACY_NETWORK_FIELDS, path)

    @property
    def needs_adapter_name(self) -> bool:
        """True when this mode is useless without a named network or interface.

        BRIDGED needs a host NIC, HOSTONLY a host-only interface, and NATNETWORK
        an existing NAT network. INTERNAL is excluded: VirtualBox defaults an
        unnamed internal network to ``intnet``, which works.
        """
        return self.network_type in (
            NetworkType.BRIDGED,
            NetworkType.HOSTONLY,
            NetworkType.NATNETWORK,
        )

    def to_dict(self) -> dict:
        """Return network configuration as a plain dictionary.

        Returns:
            dict: Fields with ``network_type`` as a string value.
        """
        result = asdict(self)
        result["network_type"] = self.network_type.value
        result["model"] = self.model.value
        if not self.provider_options:
            del result["provider_options"]
        if self.port_forwards:
            # Each rule renders itself, so an unset host_ip stays unset: `asdict`
            # would write it as "" and a default written down is a default that
            # `diff` and `apply` then treat as a request (E-01, E-02).
            result["port_forwards"] = [rule.to_dict() for rule in self.port_forwards]
        else:
            del result["port_forwards"]
        return result


@dataclass
class BootConfig:
    """Boot configuration.

    Attributes:
        order: Ordered list of boot devices; valid values are
            ``"disk"``, ``"dvd"``, ``"floppy"``, ``"network"``, ``"none"``.
        boot1–boot4: Individual boot slots derived from ``order``.
        acpi: Enable ACPI support (required by most modern OSes).
        ioapic: Enable I/O APIC (required for more than one CPU or for Windows).
        hpet: Enable High Precision Event Timer.
    """

    order: List[str] = field(default_factory=lambda: ["disk", "dvd", "none", "none"])
    boot1: str = "disk"
    boot2: str = "dvd"
    boot3: str = "none"
    boot4: str = "none"
    acpi: bool = True
    ioapic: bool = False
    hpet: bool = False

    #: How many slots the order has. Every provider addresses boot devices by slot
    #: -- VirtualBox literally as boot1..boot4 -- so the list is padded to that
    #: length. It used to default to three while every parser produced four, which
    #: made a VM differ from the file it was created from in a field neither had
    #: mentioned.
    SLOTS = 4

    def __post_init__(self) -> None:
        """Pad or trim the order to the four slots providers address."""
        order = list(self.order)[: self.SLOTS]
        order += ["none"] * (self.SLOTS - len(order))
        self.order = order

    def to_dict(self) -> dict:
        """Return boot configuration as a plain dictionary.

        Returns:
            dict: All fields with their current values.
        """
        return asdict(self)


def keeping_existing_images(current: "VMConfig", desired: "VMConfig") -> "VMConfig":
    """Return *desired* with every disk it already has attached rather than created.

    For the three providers whose configuration *is* a file -- libvirt's domain XML,
    the QEMU command line, the ``.vmx`` -- changing a VM means writing that file
    again, and the shortest way to write it is the same code that creates one. That
    shortcut destroyed data (F-39): the creation plan also *makes the disks*, so
    ``vmctl edit --memory`` on a QEMU VM ran ``qemu-img create`` over the VM's own
    image and the guest's filesystem was gone.

    Setting ``source`` is all it takes, because every emitter already knows that a
    device with a source is attached rather than created -- the path a migration
    needs. So this is the one rule rather than three careful loops.

    A rename is deliberately left alone: on those providers a new name is a new VM,
    and it must not claim the old one's images.

    Args:
        current: The VM as it is now, from the provider's own parser.
        desired: The VM as it should be.

    Returns:
        VMConfig: a copy safe to emit a definition from; neither argument is changed.
    """
    if desired.name != current.name:
        return desired
    result = copy.deepcopy(desired)
    for device in result.storage:
        if device.is_removable or device.source or not device.disk_path:
            continue
        device.source = device.disk_path
    return result


def resolve_controller(
    device: "StorageDevice",
    by_id: Dict[str, "StorageController"],
    by_bus: Dict[Any, "StorageController"],
    by_native: Optional[Dict[str, "StorageController"]] = None,
) -> Optional["StorageController"]:
    """Find the controller a device attaches to.

    Three lookups, in the order of how specific they are: the logical id a device
    names, then the provider's own name for a controller -- because a 1.1.x
    config referenced that string and must keep working -- then whichever
    controller serves the device's bus.

    Kept here, in the model, so the validator and the emitter cannot disagree
    about where a device lands. They did, and the disagreement is how a system
    disk ended up on a floppy controller (F-15).

    Args:
        device: The device to place.
        by_id: Controllers keyed by logical id.
        by_bus: One controller per bus.
        by_native: Controllers keyed by the provider's own name.

    Returns:
        The matching controller, or None if no lookup succeeds.
    """
    wanted = device.controller
    if wanted:
        if wanted in by_id:
            return by_id[wanted]
        if by_native and wanted in by_native:
            return by_native[wanted]
    return by_bus.get(device.bus)


@dataclass
class VMConfig:
    """Canonical VM configuration - the center of gravity"""

    name: str
    cpu: CPUConfig
    memory: MemoryConfig
    firmware: FirmwareConfig
    storage: List[StorageDevice]
    networks: List[NetworkConfig]
    boot: BootConfig
    storage_controllers: List[StorageControllerConfig]
    #: The architecture the guest's virtual CPU presents. VirtualBox has no such
    #: setting -- a VM runs the host's -- while libvirt requires one in every
    #: domain (A-10).
    arch: Arch = Arch.X86_64
    #: The machine type (chipset) to emulate, such as ``q35`` or ``pc``. None
    #: means the provider's own default, which is the portable answer: the set on
    #: offer depends on the QEMU build, and VirtualBox has no choice at all.
    machine: Optional[str] = None
    #: Which OS the guest runs, as a neutral id from
    #: :mod:`vmctl.core.oscatalog` (``ubuntu22.04``, ``win11``) -- or a
    #: provider's own string, which passes through untranslated. Was ``ostype``,
    #: whose default was the VirtualBox id ``Ubuntu_64``: the last vendor
    #: spelling left in the canonical model (A-05).
    guest_os: str = DEFAULT_GUEST_OS
    description: Optional[str] = None
    audio_enabled: bool = False
    clipboard_mode: str = "disabled"
    draganddrop: str = "disabled"
    usb_enabled: bool = False
    rtc_utc: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        """Ensure the VM has at least one disk after construction.

        If ``storage`` is empty a default system disk named ``<vm_name>_system``
        is created with all default :class:`StorageDevice` values.
        """
        if not self.storage:
            self.storage = [StorageDevice(name=f"{self.name}_system")]

    @classmethod
    def _from_legacy(cls, data: dict, path: str) -> dict:
        """Accept ``disks:`` for ``storage:``.

        The list holds optical and floppy drives too, so it was never a list of
        disks (M-02). Old files keep using the old key indefinitely.
        """
        return _rename_keys(data, LEGACY_VM_FIELDS, path)

    def controller_for(self, disk: StorageDevice) -> Optional[StorageController]:
        """Return the declared controller this device attaches to, if any.

        Args:
            disk: A device belonging to this VM.

        Returns:
            The controller, or None when the VM declares nothing suitable (the
            provider is then expected to supply one).
        """
        by_id = {sc.id: sc for sc in self.storage_controllers}
        by_native = {sc.native_name: sc for sc in self.storage_controllers if sc.native_name}
        by_bus: Dict[Any, StorageController] = {}
        for sc in self.storage_controllers:
            by_bus.setdefault(sc.bus, sc)
        return resolve_controller(disk, by_id, by_bus, by_native)

    def to_dict(self) -> dict:
        """Convert VMConfig to dictionary for serialization"""
        result = {
            "name": self.name,
            "arch": self.arch.value,
            "machine": self.machine,
            "guest_os": self.guest_os,
            "description": self.description,
            "cpu": self.cpu.to_dict(),
            "memory": self.memory.to_dict(),
            "firmware": self.firmware.to_dict(),
            "storage": [device.to_dict() for device in self.storage],
            "networks": [net.to_dict() for net in self.networks],
            "boot": self.boot.to_dict(),
            "storage_controllers": [sc.to_dict() for sc in self.storage_controllers],
            "audio_enabled": self.audio_enabled,
            "clipboard_mode": self.clipboard_mode,
            "draganddrop": self.draganddrop,
            "usb_enabled": self.usb_enabled,
            "rtc_utc": self.rtc_utc,
            "metadata": self.metadata,
        }
        return result

    @classmethod
    def from_dict(cls, data: dict) -> "VMConfig":
        """Create a VMConfig from a loaded YAML/JSON mapping.

        Validates as it goes: an unknown key, a value of the wrong type, a bad
        enum value or a missing required field each raise a
        :class:`~vmctl.core.exceptions.ValidationError` naming the field, rather
        than surfacing a ``TypeError`` from the constructor or a bare enum
        ``ValueError`` (F-06).

        The input mapping is not modified (F-07).

        Args:
            data: Mapping loaded from a config file.

        Returns:
            VMConfig: The parsed configuration.

        Raises:
            ValidationError: If the mapping cannot describe a VM.
        """
        if not data:
            # None *or* an empty mapping: a file that resolves to nothing is the same
            # mistake either way, and only the first spelling used to be caught (F-06).
            raise ValidationError(
                "The configuration is empty",
                recovery_hint="A config file needs at least a 'name' field.",
            )
        if not isinstance(data, dict):
            raise ValidationError(
                f"A configuration must be a mapping, got {type(data).__name__}",
                expected="a mapping of field names to values",
            )
        vm: "VMConfig" = _build(cls, copy.deepcopy(data))
        return vm


setattr(VMConfig, "disks", _alias("disks", "storage"))
setattr(VMConfig, "ostype", _alias("ostype", "guest_os"))
setattr(NetworkConfig, "adapter_type", _alias("adapter_type", "model"))
_accept_legacy_keywords(NetworkConfig, LEGACY_NETWORK_FIELDS, _nic_model_given_as_text)
_accept_legacy_keywords(VMConfig, LEGACY_VM_FIELDS)


# ---------------------------------------------------------------------------
# Loading configuration dictionaries
#
# Every check below is derived from the dataclasses above via introspection --
# there is no hand-maintained list of valid keys or types to drift out of sync.
# The goal is that a malformed config file produces one clear sentence naming
# the field, not a TypeError or a bare enum ValueError (F-06).
# ---------------------------------------------------------------------------


def _label(path: str) -> str:
    """Human name for a position in the config tree."""
    return path or "the configuration"


@lru_cache(maxsize=None)
def _hints(dc: type) -> Dict[str, Any]:
    """Resolved type hints for a dataclass, cached."""
    return get_type_hints(dc)


def _join(path: str, name: str) -> str:
    return f"{path}.{name}" if path else name


def _unknown_field(key: str, valid: List[str], path: str) -> ValidationError:
    """Build the error for a key the model does not define."""
    close = difflib.get_close_matches(key, valid, n=1)
    hint = f"Did you mean {close[0]!r}?" if close else None
    return ValidationError(
        f"Unknown field {key!r} in {_label(path)}",
        field=_join(path, key),
        constraints=[f"valid fields: {', '.join(sorted(valid))}"],
        recovery_hint=hint,
    )


def _coerce(value: Any, tp: Any, path: str) -> Any:
    """Convert a loaded value to the type the model declares.

    Args:
        value: Value straight from YAML/JSON.
        tp: Declared type (may be ``Optional[...]``, ``List[...]``, an Enum, a
            nested dataclass, or a plain scalar).
        path: Dotted path used in error messages.

    Returns:
        The converted value.

    Raises:
        ValidationError: If the value cannot be represented as ``tp``.
    """
    origin = get_origin(tp)

    # Optional[X] / Union[X, None]
    if origin is Union:
        args = [a for a in get_args(tp) if a is not type(None)]
        if value is None:
            return None
        return _coerce(value, args[0], path)

    if origin is list:
        if not isinstance(value, list):
            raise ValidationError(
                f"{_label(path)} must be a list, got {type(value).__name__}",
                field=path,
                expected="a list",
            )
        inner = (get_args(tp) or (Any,))[0]
        return [_coerce(v, inner, f"{path}[{i}]") for i, v in enumerate(value)]

    if origin is dict:
        if not isinstance(value, dict):
            raise ValidationError(
                f"{_label(path)} must be a mapping, got {type(value).__name__}",
                field=path,
                expected="a mapping",
            )
        return dict(value)

    if is_dataclass(tp):
        return _build(tp, value, path)

    if isinstance(tp, type) and issubclass(tp, Enum):
        if isinstance(value, tp):
            return value
        try:
            return tp(value)
        except ValueError:
            raise ValidationError(
                f"{value!r} is not a valid value for {_label(path)}",
                field=path,
                value=value,
                expected=" | ".join(e.value for e in tp),
            ) from None

    if tp is bool:
        if isinstance(value, bool):
            return value
        raise ValidationError(
            f"{_label(path)} must be true or false, got {value!r}",
            field=path,
            value=value,
            expected="true | false",
        )

    if tp is int:
        if isinstance(value, bool):
            raise ValidationError(
                f"{_label(path)} must be a number, got {value!r}",
                field=path,
                value=value,
                expected="a whole number",
            )
        if isinstance(value, int):
            return value
        # A quoted number in YAML is a common slip; accept it rather than
        # failing on something whose intent is unambiguous.
        if isinstance(value, str):
            try:
                return int(value.strip())
            except ValueError:
                pass
        raise ValidationError(
            f"{_label(path)} must be a number, got {value!r}",
            field=path,
            value=value,
            expected="a whole number",
        )

    if tp is str:
        if isinstance(value, str):
            return value
        raise ValidationError(
            f"{_label(path)} must be text, got {type(value).__name__}",
            field=path,
            value=value,
            expected="text",
        )

    return value


def _build(dc: Any, data: Any, path: str = "") -> Any:
    """Construct a dataclass from a loaded mapping.

    Unknown keys are rejected, missing optional sections are filled with their
    defaults, and missing required fields are named.

    Args:
        dc: Dataclass to construct.
        data: Mapping loaded from the config file.
        path: Dotted path used in error messages.

    Returns:
        An instance of ``dc``.

    Raises:
        ValidationError: On an unknown key, a bad value, or a missing
            required field.
    """
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ValidationError(
            f"{_label(path)} must be a mapping, got {type(data).__name__}",
            field=path,
            expected="a mapping",
        )

    # Older field names and values are translated before anything is checked,
    # so an export from a previous version loads without a warning (M-06).
    legacy = getattr(dc, "_from_legacy", None)
    if legacy is not None:
        data = legacy(data, path)

    spec = {f.name: f for f in fields(dc)}
    hints = _hints(dc)

    for key in data:
        if key not in spec:
            raise _unknown_field(key, list(spec), path)

    kwargs = {}
    for name, f in spec.items():
        tp = hints.get(name, Any)
        if name in data:
            kwargs[name] = _coerce(data[name], tp, _join(path, name))
            continue
        # Absent: fall back to the dataclass default where there is one.
        if f.default is not MISSING or f.default_factory is not MISSING:
            continue
        # No default. Nested sections and lists are optional in practice -- a
        # hand-written config routinely omits `firmware:` or `boot:`.
        if is_dataclass(tp):
            nested: Any = tp
            kwargs[name] = nested()
        elif get_origin(tp) is list:
            kwargs[name] = []
        else:
            required = [
                n
                for n, ff in spec.items()
                if ff.default is MISSING and ff.default_factory is MISSING
            ]
            raise ValidationError(
                f"{_label(path)} is missing required field {name!r}",
                field=_join(path, name),
                constraints=["required fields: " + ", ".join(required)],
            )
    return dc(**kwargs)
