"""
The storage vocabulary: what a device is, where it hangs, and how it is stored.

Four independent questions were previously answered by two enums that conflated
them (M-01 in PLAN.md):

* ``DiskType{HDD, SSD, DVD}`` mixed **what the guest sees** -- a disk, an optical
  drive, a floppy -- with **a performance hint**. ``SSD`` is not a kind of
  device: every hypervisor models it as a flag on a disk, which is what
  :attr:`~vmctl.core.vmconfig.StorageDevice.nonrotational` now is. The evidence
  was already in the code before the split: ``Capabilities.can_attach`` had to
  fold ``SSD`` onto ``HDD`` before looking anything up, because no attach matrix
  has a row for it.
* ``StorageControllerType`` was named after controllers but its docstring
  described VirtualBox chipsets (``IntelAHCI``, ``LsiLogic``) while its *values*
  were buses (``sata``, ``scsi``). A bus is neutral and a chipset is not: libvirt
  puts ``bus='scsi'`` on the disk and ``model='virtio-scsi'`` on the controller,
  so the two cannot be one field. :class:`BusType` keeps the neutral half; the
  chipset lives in each provider's tables.

Splitting them is not a tidy-up. ``BusType.VIRTIO_BLK`` is the clearest case:
libvirt's probed matrix has recorded ``disk|virtio: true`` since the matrix was
first captured, and the capability declaration still could not say so, because
the old enum had no name for the fastest disk bus KVM offers. A vocabulary that
cannot express a measurement is the thing being fixed.

The enums are here rather than in :mod:`vmctl.core.vmconfig` so that
:mod:`vmctl.core.capabilities`, the codecs and every provider's tables can name a
bus without importing the configuration model -- they describe a hypervisor, not
a VM.

``DiskVariant``, ``StorageControllerType`` and ``DiskType`` remain importable and
keep working -- the deprecated aliases at the end of this module say how.
"""

from enum import Enum

__all__ = [
    "DeviceKind",
    "BusType",
    "DiskFormat",
    "Allocation",
    # Deprecated aliases, kept so existing code and configs keep working.
    "DiskVariant",
    "StorageControllerType",
    "DiskType",
]


class DeviceKind(Enum):
    """What the guest sees on the bus.

    This is the *device*, not its contents: a ``CDROM`` with no ISO in it is
    still a drive the guest can see and boot from, which is why an empty
    removable drive has to survive a round trip (F-22).

    ``DISK`` covers rotational and solid-state alike --
    :attr:`~vmctl.core.vmconfig.StorageDevice.nonrotational` is the difference.
    ``CDROM`` and ``FLOPPY`` are *removable*: no medium is created for them and
    ``size_mb`` is not meaningful.
    """

    DISK = "disk"
    CDROM = "cdrom"
    FLOPPY = "floppy"

    @property
    def is_removable(self) -> bool:
        """Whether the medium is inserted rather than created."""
        return self is not DeviceKind.DISK


class BusType(Enum):
    """The interface a device hangs off.

    Neutral by design: the value names the bus, and what chipset implements it is
    the provider's business. ``VIRTIO_BLK`` and ``VIRTIO_SCSI`` are genuinely
    different buses rather than two names for one -- virtio-blk is a block device
    with no SCSI layer, which is why it is faster and why it cannot carry an
    optical drive (libvirt: ``disk type of 'vda' does not support ejectable
    media``).

    Which of these a provider has, how many ports each takes, and which device
    kinds it will carry are all declared per provider in
    :mod:`vmctl.core.capabilities` -- none of it is implied by membership here.
    """

    IDE = "ide"
    SATA = "sata"
    SCSI = "scsi"
    SAS = "sas"
    NVME = "nvme"
    VIRTIO_BLK = "virtio-blk"
    VIRTIO_SCSI = "virtio-scsi"
    USB = "usb"
    FLOPPY = "floppy"


class Allocation(Enum):
    """How a medium's space is claimed on the host.

    ``THIN`` (dynamic/sparse): the image grows on demand up to its nominal size.
    ``THICK`` (fixed/preallocated): the whole size is claimed at creation.

    A format may be creatable in only one of the two -- VirtualBox can make a
    dynamic QCOW2 but not a fixed one, and a fixed RAW but not a dynamic one
    (F-20) -- so this is a request, resolved against
    :attr:`~vmctl.core.capabilities.FormatSpec.allocations`.
    """

    THIN = "thin"
    THICK = "thick"


class DiskFormat(Enum):
    """The backing file format of a medium.

    Which of these a provider can *attach* and which it can *create* are
    different sets, declared per provider: VirtualBox 7.1.18 can attach a VHDX
    but not create one, and can create a dynamic QCOW2 but not a fixed one.
    """

    VDI = "vdi"  # VirtualBox native format
    VMDK = "vmdk"  # VMware format (also supported by VirtualBox)
    VHD = "vhd"  # Microsoft Virtual Hard Disk
    VHDX = "vhdx"  # Hyper-V; VirtualBox can read one but not create one
    QCOW2 = "qcow2"  # QEMU/KVM native
    QED = "qed"  # QEMU enhanced disk
    PARALLELS = "parallels"  # Parallels Desktop
    RAW = "raw"  # Raw disk image


# ---------------------------------------------------------------------------
# Deprecated aliases
#
# Ground rule 2 of PLAN.md is that the working version keeps working, and that
# covers code as well as configuration files. ``Allocation`` and ``BusType`` are
# pure renames -- same members, same values -- so the old names are bound to the
# same objects and ``DiskVariant.THIN is Allocation.THIN``.
#
# ``DiskType`` cannot be an alias, because it is the enum that was split. The
# shim below maps its members onto the kinds they became, so comparisons such as
# ``device.kind is DiskType.DVD`` still hold. What it cannot carry is the flag:
# ``DiskType.SSD`` is ``DeviceKind.DISK``, and code that means "solid state" has
# to set ``nonrotational`` -- which is the point of the split, and the one thing
# a caller has to change by hand.
# ---------------------------------------------------------------------------

#: Deprecated alias for :class:`Allocation`.
DiskVariant = Allocation

#: Deprecated alias for :class:`BusType`.
StorageControllerType = BusType


class _LegacyDiskType:
    """Deprecated alias for :class:`DeviceKind`, mapping the split-up members.

    Not an ``Enum``: its members *are* ``DeviceKind`` members, so that old and
    new code comparing the same device agree.
    """

    DISK = DeviceKind.DISK
    HDD = DeviceKind.DISK
    SSD = DeviceKind.DISK  # the flag is lost; set ``nonrotational`` instead
    DVD = DeviceKind.CDROM
    CDROM = DeviceKind.CDROM
    FLOPPY = DeviceKind.FLOPPY

    _BY_VALUE = {
        "hdd": DeviceKind.DISK,
        "ssd": DeviceKind.DISK,
        "dvd": DeviceKind.CDROM,
        "disk": DeviceKind.DISK,
        "cdrom": DeviceKind.CDROM,
        "floppy": DeviceKind.FLOPPY,
    }

    def __call__(self, value: object) -> DeviceKind:
        """Look a kind up by its old or new value, as ``DiskType("ssd")`` did."""
        if isinstance(value, DeviceKind):
            return value
        try:
            return self._BY_VALUE[str(value).strip().lower()]
        except KeyError:
            raise ValueError(f"{value!r} is not a valid device kind") from None

    def __iter__(self):
        """Iterate the kinds, so ``list(DiskType)`` still works."""
        return iter(DeviceKind)


#: Deprecated alias for :class:`DeviceKind`. ``ssd`` resolves to ``DISK``.
DiskType = _LegacyDiskType()
