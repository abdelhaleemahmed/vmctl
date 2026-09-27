"""
What a provider can actually do, declared as data.

Validation, defaulting and error messages all read this and nothing else, so a
limit lives in one place instead of being restated as a literal wherever it is
checked. Provider capabilities used to be a loose ``Dict[str, Any]`` whose keys
several checks ignored in favour of hardcoded numbers (A-02 in PLAN.md).

The interesting part is :attr:`Capabilities.attach`: which *device kinds* each
*bus* will carry. That is what makes "can this hypervisor put a CD-ROM on NVMe?"
a question the code can answer rather than a guess.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

from .vmconfig import DiskFormat, DiskType, FirmwareType, StorageControllerType


class Support(Enum):
    """How well a provider supports a value."""

    NATIVE = "native"  # the provider's own format; always creatable
    READ_WRITE = "read-write"  # fully usable, not native
    READ_ONLY = "read-only"  # existing media can be attached, none created
    CONVERT_ONLY = "convert-only"  # must be converted before use
    UNSUPPORTED = "unsupported"

    @property
    def usable(self) -> bool:
        """True when a medium in this format can be attached at all."""
        return self is not Support.UNSUPPORTED

    @property
    def creatable(self) -> bool:
        """True when the provider can create a new medium in this format."""
        return self in (Support.NATIVE, Support.READ_WRITE)

    def describe(self) -> str:
        """Return a phrase that reads naturally inside an error message."""
        return {
            Support.NATIVE: "is this provider's own format",
            Support.READ_WRITE: "is fully supported",
            Support.READ_ONLY: "can be attached but not created",
            Support.CONVERT_ONLY: "must be converted before it can be used",
            Support.UNSUPPORTED: "is not supported at all",
        }[self]


@dataclass(frozen=True)
class BusSpec:
    """The rules for one storage bus.

    Attributes:
        add: The provider's name for this bus when creating a controller.
        model: The controller chipset to use.
        min_ports: Fewest ports a controller may have.
        max_ports: Most ports a controller may have. Equal to ``min_ports``
            when the provider allows exactly one value -- VirtualBox does this
            for IDE (2), SCSI (16), USB (8) and floppy (1), which is not
            something the documentation says anywhere.
        units_per_port: Devices per port; 2 for IDE (master/slave), 1 elsewhere.
        default_ports: What vmctl uses when the config does not say.
        bootable: Whether a VM can boot from this bus.
        controller_name: The name to give a controller on this bus when the
            configuration does not declare one.
    """

    add: str
    model: str
    min_ports: int
    max_ports: int
    units_per_port: int = 1
    default_ports: int = 1
    bootable: bool = True
    controller_name: str = "Controller"

    @property
    def fixed_port_count(self) -> Optional[int]:
        """The only allowed port count, when the provider allows just one."""
        return self.min_ports if self.min_ports == self.max_ports else None

    def clamp_ports(self, requested: Optional[int]) -> int:
        """Return a port count this bus will accept.

        Args:
            requested: What the configuration asked for, or None.

        Returns:
            The requested value when it is allowed, otherwise the nearest
            allowed value.
        """
        fixed = self.fixed_port_count
        if fixed is not None:
            return fixed
        if requested is None:
            return self.default_ports
        return max(self.min_ports, min(self.max_ports, requested))


@dataclass(frozen=True)
class FormatSpec:
    """How well one disk image format is supported.

    Attributes:
        support: Whether media in this format can be attached and created.
        extensions: File extensions this format owns, most canonical first.
        native_name: The provider's own name for the format.
        allocations: Allocation strategies the provider can create. A format
            may be creatable in only one of them -- VirtualBox can make a
            dynamic qcow2 but not a fixed one, and a fixed RAW but not a
            dynamic one.
    """

    support: Support
    #: Extensions this format owns. The first is used for new media; the rest are
    #: recognised when reading. VirtualBox's RAW backend, for instance, owns both
    #: ``img`` and ``raw``.
    extensions: Tuple[str, ...]
    native_name: str
    allocations: Tuple[str, ...] = ("thin", "thick")

    @property
    def extension(self) -> str:
        """The extension to give a newly created medium."""
        return self.extensions[0]


@dataclass
class Capabilities:
    """Everything vmctl needs to know about one provider's limits."""

    provider: str
    min_version: str = ""

    max_cpus: int = 128
    max_memory_mb: int = 1_048_576
    max_vram_mb: int = 256
    max_network_adapters: int = 8
    max_disks: int = 255

    #: Bus -> its rules.
    buses: Dict[StorageControllerType, BusSpec] = field(default_factory=dict)
    #: (device kind, bus) -> whether the provider will attach it.
    attach: Dict[Tuple[DiskType, StorageControllerType], bool] = field(default_factory=dict)
    #: Image format -> how well it is supported.
    formats: Dict[DiskFormat, FormatSpec] = field(default_factory=dict)
    #: Format used when a configuration does not name one.
    native_format: DiskFormat = DiskFormat.VDI
    #: The bus a provider would idiomatically put each device kind on. Used when
    #: a bus has to be substituted, so the result lands somewhere a user of that
    #: hypervisor would expect rather than merely somewhere valid.
    native_buses: Dict[DiskType, StorageControllerType] = field(default_factory=dict)

    firmware: Dict[FirmwareType, Support] = field(default_factory=dict)
    #: What a VM name may contain. A name is interpolated into file paths and,
    #: for some providers, into a document, so a path separator in one redirects
    #: where files are written -- found by the conformance suite (A-07/A-08). The
    #: default forbids separators, control characters and a bare traversal.
    name_pattern: str = r"^(?!\.\.?$)[^/\\\x00-\x1f]+$"
    name_max_length: int = 128

    supports_tpm: bool = True
    #: Secure boot can be switched on, but cannot be read back -- see F-17.
    secure_boot_readable: bool = False

    #: Extensions that denote a removable medium (an optical or floppy image).
    #: Needed to recognise an attachment as a device at all.
    removable_extensions: Tuple[str, ...] = ()

    supported_network_types: Tuple[str, ...] = ()
    supported_os_types: Tuple[str, ...] = ()

    #: Where each figure above came from, so a reader can tell a measured limit
    #: from a remembered one.
    evidence: str = ""

    # -- queries -------------------------------------------------------------

    def bus(self, bus: StorageControllerType) -> Optional[BusSpec]:
        """Return the rules for a bus, or None when it is unsupported."""
        return self.buses.get(bus)

    def can_attach(self, kind: DiskType, bus: StorageControllerType) -> bool:
        """Whether this provider will put a device of *kind* on *bus*.

        Args:
            kind: The device kind.
            bus: The bus to attach it to.

        Returns:
            True when the combination is supported. An unknown combination is
            treated as unsupported, so a table that has not been probed refuses
            rather than guessing.
        """
        # SSD is a hint on a disk, not a different kind of device.
        if kind is DiskType.SSD:
            kind = DiskType.HDD
        return self.attach.get((kind, bus), False)

    def buses_for(self, kind: DiskType) -> List[StorageControllerType]:
        """Return every bus that will carry a device of *kind*."""
        return [b for b in self.buses if self.can_attach(kind, b)]

    def native_bus(self, kind: DiskType) -> Optional[StorageControllerType]:
        """Return the idiomatic bus for a device kind, if one is declared.

        Args:
            kind: The device kind.

        Returns:
            The preferred bus, or None when the provider does not say.
        """
        if kind is DiskType.SSD:
            kind = DiskType.HDD
        preferred = self.native_buses.get(kind)
        if preferred is not None and self.can_attach(kind, preferred):
            return preferred
        return None

    def format_spec(self, fmt: DiskFormat) -> FormatSpec:
        """Return the support entry for a format, defaulting to unsupported."""
        return self.formats.get(
            fmt, FormatSpec(Support.UNSUPPORTED, (fmt.value,), fmt.value.upper())
        )

    def medium_extensions(self) -> Tuple[str, ...]:
        """Every extension that identifies an attachment as a real medium.

        The parser uses this to tell a device from the metadata entries
        VirtualBox interleaves with them. Keeping a private list there meant a
        disk in a format vmctl could create -- qcow2 -- was not recognised as a
        disk at all, so the VM looked diskless and the model invented a default
        20 GB VDI in its place (F-24).
        """
        seen: List[str] = []
        for spec in self.formats.values():
            if not spec.support.usable:
                continue
            seen.extend(spec.extensions)
        seen.extend(self.removable_extensions)
        return tuple(dict.fromkeys(e.lower() for e in seen))

    def creatable_formats(self) -> List[DiskFormat]:
        """Return the formats this provider can create new media in."""
        return [f for f, spec in self.formats.items() if spec.support.creatable]

    def usable_formats(self) -> List[DiskFormat]:
        """Return the formats this provider can attach at all."""
        return [f for f, spec in self.formats.items() if spec.support.usable]
