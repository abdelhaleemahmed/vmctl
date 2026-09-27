"""
VirtualBox capabilities and constraints.

**Every figure in this file was measured, not remembered.** The probes live in
``tests/fixtures/attach_matrix.json`` and ``tests/fixtures/format_matrix.json``,
captured from VirtualBox 7.1.18 by creating each controller and medium on a real
host and recording what it said. A capability table written from memory is worse
than none, because validation then rejects configurations that would have worked.

Several of these are not documented anywhere obvious:

* IDE, SCSI, USB and floppy controllers accept **exactly one** port count
  (2, 16, 8 and 1). Asking for anything else fails with
  ``Invalid port count: N (must be in range [16, 16])``.
* ``virtio-scsi`` is a valid ``--add`` value although
  ``VBoxManage storagectl --help`` does not list it.
* NVMe carries **disks only** -- a CD-ROM is refused with "The attachment is not
  supported by the storage controller".
* No bus except the floppy controller carries a floppy drive, and the floppy
  controller carries nothing else.
* VirtualBox **can** create a dynamic QCOW2, but not a fixed one; it can attach
  a VHDX but cannot create one at all.
"""

from typing import Any, Dict

from ...core.capabilities import BusSpec, Capabilities, FormatSpec, Support
from ...core.vmconfig import DiskFormat, DiskType, FirmwareType, StorageControllerType

_BUS = StorageControllerType
_ALL_ALLOC = ("thin", "thick")

#: Bus rules. ``min_ports``/``max_ports`` are the ranges VirtualBox reported when
#: asked for an impossible port count.
BUSES = {
    _BUS.IDE: BusSpec(
        "ide",
        "PIIX4",
        2,
        2,
        units_per_port=2,
        default_ports=2,
        controller_name="IDE Controller",
    ),
    _BUS.SATA: BusSpec(
        "sata",
        "IntelAhci",
        1,
        30,
        default_ports=30,
        controller_name="SATA Controller",
    ),
    _BUS.SCSI: BusSpec(
        "scsi",
        "LSILogic",
        16,
        16,
        default_ports=16,
        controller_name="SCSI Controller",
    ),
    _BUS.SAS: BusSpec(
        "sas",
        "LSILogicSAS",
        1,
        255,
        default_ports=16,
        controller_name="SAS Controller",
    ),
    _BUS.NVME: BusSpec(
        "pcie",
        "NVMe",
        1,
        255,
        default_ports=8,
        controller_name="NVMe Controller",
    ),
    _BUS.VIRTIO_SCSI: BusSpec(
        "virtio-scsi",
        "VirtIO",
        1,
        256,
        default_ports=16,
        controller_name="VirtIO SCSI Controller",
    ),
    _BUS.USB: BusSpec(
        "usb",
        "USB",
        8,
        8,
        default_ports=8,
        bootable=False,
        controller_name="USB Controller",
    ),
    _BUS.FLOPPY: BusSpec(
        "floppy",
        "I82078",
        1,
        1,
        default_ports=1,
        controller_name="Floppy",
    ),
}

#: Which device kinds each bus carries. Measured: every bus except the floppy
#: controller takes disks, all but NVMe also take optical drives, and only the
#: floppy controller takes a floppy.
_DISK_BUSES = (_BUS.IDE, _BUS.SATA, _BUS.SCSI, _BUS.SAS, _BUS.NVME, _BUS.VIRTIO_SCSI, _BUS.USB)
_CDROM_BUSES = (_BUS.IDE, _BUS.SATA, _BUS.SCSI, _BUS.SAS, _BUS.VIRTIO_SCSI, _BUS.USB)

ATTACH: Dict[Any, bool] = {}
for _bus in BUSES:
    ATTACH[(DiskType.HDD, _bus)] = _bus in _DISK_BUSES
    ATTACH[(DiskType.DVD, _bus)] = _bus in _CDROM_BUSES
    ATTACH[(DiskType.FLOPPY, _bus)] = _bus is _BUS.FLOPPY

#: Format support. ``allocations`` records which allocations VirtualBox can
#: actually create, which is not the same for every format.
#: Extensions come from `VBoxManage list hddbackends` on 7.1.18.
FORMATS = {
    DiskFormat.VDI: FormatSpec(Support.NATIVE, ("vdi",), "VDI", _ALL_ALLOC),
    DiskFormat.VMDK: FormatSpec(Support.READ_WRITE, ("vmdk",), "VMDK", _ALL_ALLOC),
    DiskFormat.VHD: FormatSpec(Support.READ_WRITE, ("vhd",), "VHD", _ALL_ALLOC),
    # Creating a VHDX is refused for both allocations, so it is read-only.
    DiskFormat.VHDX: FormatSpec(Support.READ_ONLY, ("vhdx",), "VHDX", ()),
    DiskFormat.QCOW2: FormatSpec(Support.READ_WRITE, ("qcow2", "qcow"), "QCOW", ("thin",)),
    DiskFormat.QED: FormatSpec(Support.READ_WRITE, ("qed",), "QED", ("thin",)),
    DiskFormat.PARALLELS: FormatSpec(Support.READ_WRITE, ("hdd",), "Parallels", ("thin",)),
    # RAW is the mirror image of QCOW2: fixed only. Its backend owns both `img`
    # and `raw` for hard disks.
    DiskFormat.RAW: FormatSpec(Support.READ_WRITE, ("img", "raw"), "RAW", ("thick",)),
}

#: Optical and floppy image extensions, from the RAW/DMG/CUE backends. An
#: attachment with one of these is a removable medium, not a disk.
REMOVABLE_EXTENSIONS = ("iso", "cdr", "dmg", "cue", "viso", "ima", "dsk", "flp", "vfd")


class VirtualBoxCapabilities:
    """VirtualBox-specific capabilities and limits."""

    @staticmethod
    def get() -> Capabilities:
        """Return the typed capability declaration.

        Returns:
            Capabilities: limits, bus rules, the attach matrix and format
            support for VirtualBox.
        """
        return Capabilities(
            provider="virtualbox",
            # The emitter needs `modifynvram enrollmssignatures`, `--tpm-type`
            # and the virtio-scsi bus, none of which exist before 7.0.
            min_version="7.0",
            max_cpus=128,
            max_memory_mb=1_048_576,
            max_vram_mb=256,
            max_network_adapters=8,
            max_disks=255,
            buses=dict(BUSES),
            attach=dict(ATTACH),
            formats=dict(FORMATS),
            native_format=DiskFormat.VDI,
            native_buses={
                DiskType.HDD: _BUS.SATA,
                DiskType.DVD: _BUS.IDE,
                DiskType.FLOPPY: _BUS.FLOPPY,
            },
            removable_extensions=REMOVABLE_EXTENSIONS,
            firmware={
                FirmwareType.BIOS: Support.NATIVE,
                FirmwareType.EFI: Support.READ_WRITE,
                FirmwareType.EFI32: Support.READ_WRITE,
                FirmwareType.EFI64: Support.READ_WRITE,
            },
            supports_tpm=True,
            secure_boot_readable=False,
            supported_network_types=(
                "nat",
                "bridged",
                "hostonly",
                "internal",
                "natnetwork",
            ),
            evidence=(
                "probed on VirtualBox 7.1.18; see tests/fixtures/attach_matrix.json "
                "and tests/fixtures/format_matrix.json"
            ),
        )
