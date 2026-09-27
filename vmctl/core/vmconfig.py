"""
Core VM configuration models - the center of gravity
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

from .exceptions import ValidationError


class FirmwareType(Enum):
    """VM firmware type.

    Determines the boot firmware used by the virtual machine.
    EFI64 is required for Windows 11 and Secure Boot.
    """

    BIOS = "bios"
    EFI = "efi"
    EFI64 = "efi64"
    EFI32 = "efi32"


class DiskType(Enum):
    """Virtual disk media type.

    HDD and SSD have identical performance in VirtualBox; the distinction
    is metadata-only. DVD represents an optical drive backed by an ISO image,
    and FLOPPY a floppy drive. DVD and FLOPPY are *removable* devices: no medium
    is created for them, and ``size_mb`` is not meaningful.
    """

    HDD = "hdd"
    SSD = "ssd"
    DVD = "dvd"
    FLOPPY = "floppy"


class DiskVariant(Enum):
    """Disk allocation strategy.

    THIN (dynamic): the image file grows on demand up to ``size_mb``.
    THICK (fixed): the full ``size_mb`` is pre-allocated on creation.
    RAW format always uses THICK regardless of this setting.
    """

    THIN = "thin"  # Dynamic/Standard - grows as needed
    THICK = "thick"  # Fixed - preallocated


class DiskFormat(Enum):
    """Disk image file format.

    VDI is the native VirtualBox format and is recommended for most use cases.
    VMDK and VHD are useful when the image needs to be shared with VMware or
    Hyper-V respectively. RAW produces a flat binary image with no metadata.
    """

    VDI = "vdi"  # VirtualBox native format
    VMDK = "vmdk"  # VMware format (also supported by VirtualBox)
    VHD = "vhd"  # Microsoft Virtual Hard Disk
    RAW = "raw"  # Raw disk image


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


class StorageControllerType(Enum):
    """Storage bus / controller chipset type.

    IDE supports up to 2 ports (legacy; useful for optical drives).
    SATA (IntelAHCI) supports up to 30 ports and is the default.
    SCSI (LsiLogic) and SAS (LsiLogicSas) support up to 254/255 ports
    and are useful for high port-count configurations.
    NVME (PCIe) is available on VirtualBox 6.0 and later.
    FLOPPY (I82078) carries floppy drives only.
    USB requires exactly 8 ports on VirtualBox.
    VIRTIO_SCSI (VirtIO) is available on VirtualBox 7.x; note that its
    ``--add`` value is ``virtio-scsi``, which ``VBoxManage storagectl --help``
    does not list.
    """

    IDE = "ide"
    SATA = "sata"
    SCSI = "scsi"
    SAS = "sas"
    NVME = "nvme"
    FLOPPY = "floppy"
    USB = "usb"
    VIRTIO_SCSI = "virtio-scsi"


@dataclass
class CPUConfig:
    """CPU configuration.

    Attributes:
        count: Number of virtual CPUs (1–128).
        hotplug: Allow CPUs to be added/removed while the VM is running.
        execution_cap: Maximum percentage of host CPU time the VM may use (1–100).
        pae: Enable Physical Address Extension for 32-bit OSes.
        nested_virt: Enable nested virtualisation (required for running KVM inside VirtualBox).
    """

    count: int = 2
    hotplug: bool = False
    execution_cap: int = 100  # Percentage
    pae: bool = False  # Physical Address Extension
    nested_virt: bool = False

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


@dataclass
class DiskConfig:
    """Disk configuration.

    Attributes:
        name: Unique identifier for this disk within the VM (e.g. ``"system"``, ``"data"``).
        size_mb: Disk capacity in megabytes (10–1,048,576).
        type: Media type — HDD, SSD, or DVD.
        format: Image file format — VDI, VMDK, VHD, or RAW.
        variant: Allocation strategy — THIN (dynamic) or THICK (fixed).
        controller: Storage bus type the disk is attached to.
        controller_name: Exact controller name (e.g. ``"SATA Controller"``), or
            None to use whichever controller serves ``controller``.
        port: Controller port number (0-based).
        device: Device number on the port (0 or 1).
        bootable: Mark this disk as a boot device.
        disk_path: Original image path on the source system (not exported).
        source: Existing medium to attach for removable devices (e.g. an ISO
            path for a DVD drive). Ignored for non-removable disks.
    """

    name: str
    size_mb: int = 20480  # 20GB default
    type: DiskType = DiskType.HDD
    format: DiskFormat = DiskFormat.VDI  # Disk image format
    variant: DiskVariant = DiskVariant.THIN  # Thin provisioned by default
    controller: StorageControllerType = StorageControllerType.SATA
    # None means "whichever controller serves `controller`". It used to default
    # to the literal "SATA", which claimed a specific controller name even for a
    # disk on another bus, and forced consumers to special-case that string
    # (F-01). A config that names "SATA" still works: it is matched as a real
    # name first, and falls back to bus matching if nothing has that name.
    controller_name: Optional[str] = None
    port: int = 0
    device: int = 0
    bootable: bool = False
    disk_path: Optional[str] = None  # Original disk path (for reference)
    source: Optional[str] = None  # Existing medium to attach (ISO for DVD, etc.)

    @property
    def is_removable(self) -> bool:
        """True for devices whose medium is inserted, not created.

        A DVD or floppy drive is attached either empty or pointing at an
        existing image; vmctl must never create a medium for one.
        """
        return self.type in (DiskType.DVD, DiskType.FLOPPY)

    def to_dict(self) -> dict:
        """Return disk configuration as a plain dictionary.

        Enum values are serialised to their string representations.
        ``disk_path`` is excluded because it is host-specific.

        Returns:
            dict: Exportable fields with enum values as strings.
        """
        result = asdict(self)
        result["type"] = self.type.value
        result["format"] = self.format.value
        result["variant"] = self.variant.value
        result["controller"] = self.controller.value
        # Don't include disk_path in export (it's system-specific)
        result.pop("disk_path", None)
        # `source` is only meaningful for removable media; omit it otherwise so
        # exports of ordinary disks keep their 1.1.x shape.
        if not self.is_removable or self.source is None:
            result.pop("source", None)
        return result


@dataclass
class NetworkConfig:
    """Network adapter configuration.

    Attributes:
        adapter_type: NIC chipset emulation (e.g. ``"82540EM"`` for Intel PRO/1000 MT Desktop).
        network_type: Connection mode (NAT, BRIDGED, HOSTONLY, INTERNAL, NATNETWORK).
        adapter_name: Physical or virtual interface name used for BRIDGED / HOSTONLY modes.
        mac_address: Custom MAC address; ``None`` lets VirtualBox assign one automatically.
        promiscuous_mode: Allow the adapter to receive packets not addressed to it.
    """

    adapter_type: str = "82540EM"  # Default Intel PRO/1000 MT Desktop
    network_type: NetworkType = NetworkType.NAT
    adapter_name: Optional[str] = None  # For bridged/host-only
    mac_address: Optional[str] = None
    promiscuous_mode: bool = False

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

    order: List[str] = field(default_factory=lambda: ["disk", "dvd", "none"])
    boot1: str = "disk"
    boot2: str = "dvd"
    boot3: str = "none"
    boot4: str = "none"
    acpi: bool = True
    ioapic: bool = False
    hpet: bool = False

    def to_dict(self) -> dict:
        """Return boot configuration as a plain dictionary.

        Returns:
            dict: All fields with their current values.
        """
        return asdict(self)


@dataclass
class StorageControllerConfig:
    """Storage controller configuration.

    Attributes:
        name: Unique controller name as it appears in VirtualBox
            (e.g. ``"SATA Controller"``).
        controller_type: Bus/chipset type (IDE, SATA, SCSI, SAS).
        port_count: Number of available device ports.
        bootable: Mark this controller as capable of booting.
    """

    name: str
    controller_type: StorageControllerType
    port_count: int = 30
    bootable: bool = False

    def to_dict(self) -> dict:
        """Return storage controller configuration as a plain dictionary.

        Returns:
            dict: Fields with ``controller_type`` as a string value.
        """
        result = asdict(self)
        result["controller_type"] = self.controller_type.value
        return result


def resolve_controller(
    disk: "DiskConfig",
    by_name: Dict[str, "StorageControllerConfig"],
    by_bus: Dict[Any, "StorageControllerConfig"],
) -> Optional["StorageControllerConfig"]:
    """Find the controller a device attaches to.

    An explicit name wins if something actually has that name; otherwise the
    device goes to whichever controller serves its bus. Kept here, in the model,
    so the validator and the emitter cannot disagree about where a disk lands --
    they did, and the disagreement was how a system disk ended up on a floppy
    controller.

    Args:
        disk: The device to place.
        by_name: Controllers keyed by name.
        by_bus: One controller per bus type.

    Returns:
        The matching controller, or None if neither lookup succeeds.
    """
    if disk.controller_name and disk.controller_name in by_name:
        return by_name[disk.controller_name]
    return by_bus.get(disk.controller)


@dataclass
class VMConfig:
    """Canonical VM configuration - the center of gravity"""

    name: str
    cpu: CPUConfig
    memory: MemoryConfig
    firmware: FirmwareConfig
    disks: List[DiskConfig]
    networks: List[NetworkConfig]
    boot: BootConfig
    storage_controllers: List[StorageControllerConfig]
    ostype: str = "Ubuntu_64"
    description: Optional[str] = None
    audio_enabled: bool = False
    clipboard_mode: str = "disabled"
    draganddrop: str = "disabled"
    usb_enabled: bool = False
    rtc_utc: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        """Ensure the VM has at least one disk after construction.

        If ``disks`` is empty a default system disk named ``<vm_name>_system``
        is created automatically with all default ``DiskConfig`` values.
        """
        # Ensure at least one disk exists
        if not self.disks:
            self.disks = [DiskConfig(name=f"{self.name}_system")]

    def controller_for(self, disk: DiskConfig) -> Optional[StorageControllerConfig]:
        """Return the declared controller this device attaches to, if any.

        Args:
            disk: A device belonging to this VM.

        Returns:
            The controller, or None when the VM declares nothing suitable (the
            provider is then expected to supply one).
        """
        by_name = {sc.name: sc for sc in self.storage_controllers}
        by_bus: Dict[Any, StorageControllerConfig] = {}
        for sc in self.storage_controllers:
            by_bus.setdefault(sc.controller_type, sc)
        return resolve_controller(disk, by_name, by_bus)

    def to_dict(self) -> dict:
        """Convert VMConfig to dictionary for serialization"""
        result = {
            "name": self.name,
            "ostype": self.ostype,
            "description": self.description,
            "cpu": self.cpu.to_dict(),
            "memory": self.memory.to_dict(),
            "firmware": self.firmware.to_dict(),
            "disks": [disk.to_dict() for disk in self.disks],
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
        if data is None:
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
