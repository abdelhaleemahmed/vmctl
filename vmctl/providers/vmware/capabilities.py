"""
VMware Workstation capabilities.

**Measured, not remembered.** The attach matrix is
``tests/fixtures/vmware_attach_matrix.json``, produced by writing a one-device
``.vmx`` per (device kind, bus) pair, powering the VM on with ``vmrun start nogui``
and reading the verdict out of ``vmware.log``. Port limits were measured the same
way, by attaching a disk at each address and asking the log whether it opened.

The finding that matters most is not in the table. **VMware silently drops a device
it cannot place**: ``sata0:30``, ``scsi0:16``, ``nvme0:64`` and ``ide0:2`` all power
on happily *without the disk* and say nothing. So these port counts are not
decoration -- they are the only thing standing between a config and a VM that boots
with no disk and no error.
"""

from typing import Any, Dict

from ...core.capabilities import BusSpec, Capabilities, FormatSpec, Support
from ...core.devices import BusType, DeviceKind, DiskFormat
from ...core.platform import Arch
from ...core.vmconfig import FirmwareType
from .tables import NIC_MODEL_TO_VMWARE, VERIFIED_GUEST_OS

_BUS = BusType

#: Measured port limits. ``max_ports`` is the first address VMware ignored, minus
#: one; ``units_per_port`` is 2 only for IDE, which addresses master and slave.
BUSES = {
    _BUS.IDE: BusSpec(
        "ide",
        "ATAPI",
        1,
        2,
        units_per_port=2,
        default_ports=2,
        controller_name="ide0",
    ),
    _BUS.SATA: BusSpec("sata", "AHCI", 1, 30, default_ports=30, controller_name="sata0"),
    _BUS.SCSI: BusSpec(
        "scsi",
        "lsilogic",
        1,
        16,
        default_ports=16,
        controller_name="scsi0",
    ),
    _BUS.SAS: BusSpec(
        "scsi",
        "lsisas1068",
        1,
        16,
        default_ports=16,
        controller_name="scsi0",
    ),
    # 64 devices: :63 attached and :64 was ignored.
    _BUS.NVME: BusSpec("nvme", "NVMe", 1, 64, default_ports=64, controller_name="nvme0"),
    _BUS.FLOPPY: BusSpec("floppy", "floppy", 1, 2, default_ports=2, controller_name="floppy0"),
}

#: From the recording. Everything carries disks; everything but NVMe carries optical
#: drives -- the one refusal, and the same one VirtualBox makes.
ATTACH: Dict[Any, bool] = {}
for _bus in (_BUS.IDE, _BUS.SATA, _BUS.SCSI, _BUS.SAS, _BUS.NVME):
    ATTACH[(DeviceKind.DISK, _bus)] = True
    ATTACH[(DeviceKind.CDROM, _bus)] = _bus is not _BUS.NVME
    ATTACH[(DeviceKind.FLOPPY, _bus)] = False
ATTACH[(DeviceKind.FLOPPY, _BUS.FLOPPY)] = True
ATTACH[(DeviceKind.DISK, _BUS.FLOPPY)] = False
ATTACH[(DeviceKind.CDROM, _BUS.FLOPPY)] = False

#: VMDK and nothing else. A ``.vmx`` has no way to attach a qcow2 or a VDI, and
#: ``vmware-vdiskmanager`` reads nothing but VMDK either -- so unlike the other
#: providers there is no format here that vmctl can convert *from* using the
#: product's own tools. That is what makes VMware the honest test of the matrix:
#: a config that names any other format has to be refused or converted elsewhere.
FORMATS = {
    DiskFormat.VMDK: FormatSpec(Support.NATIVE, ("vmdk",), "vmdk", ("thin", "thick")),
    DiskFormat.QCOW2: FormatSpec(Support.UNSUPPORTED, ("qcow2",), "qcow2", ()),
    DiskFormat.VDI: FormatSpec(Support.UNSUPPORTED, ("vdi",), "vdi", ()),
    DiskFormat.VHD: FormatSpec(Support.UNSUPPORTED, ("vhd",), "vhd", ()),
    DiskFormat.VHDX: FormatSpec(Support.UNSUPPORTED, ("vhdx",), "vhdx", ()),
    DiskFormat.QED: FormatSpec(Support.UNSUPPORTED, ("qed",), "qed", ()),
    DiskFormat.PARALLELS: FormatSpec(Support.UNSUPPORTED, ("hdd",), "hdd", ()),
    DiskFormat.RAW: FormatSpec(Support.UNSUPPORTED, ("raw", "img"), "raw", ()),
}

REMOVABLE_EXTENSIONS = ("iso", "flp", "img", "ima", "dsk", "vfd", "cdr")


class VMwareCapabilities:
    """What VMware Workstation 17 can do, as measured on a real host."""

    @staticmethod
    def get() -> Capabilities:
        """Return the typed capability declaration for VMware."""
        return Capabilities(
            provider="vmware",
            min_version="17.0",
            max_cpus=240,
            max_memory_mb=4_194_304,
            # Video memory is svga.vramSize, a device property rather than a VM one.
            max_vram_mb=512,
            max_network_adapters=10,
            max_disks=120,
            buses=dict(BUSES),
            attach=dict(ATTACH),
            formats=dict(FORMATS),
            native_format=DiskFormat.VMDK,
            native_buses={
                # What the product itself picks for a modern guest.
                DeviceKind.DISK: _BUS.SCSI,
                DeviceKind.CDROM: _BUS.SATA,
                DeviceKind.FLOPPY: _BUS.FLOPPY,
            },
            removable_extensions=REMOVABLE_EXTENSIONS,
            firmware={
                FirmwareType.BIOS: Support.NATIVE,
                FirmwareType.EFI: Support.NATIVE,
                FirmwareType.EFI64: Support.NATIVE,
                # There is one `firmware = "efi"`, with no 32-bit form.
                FirmwareType.EFI32: Support.UNSUPPORTED,
            },
            arches=(Arch.X86_64,),
            # A .vmx names no machine type: the hardware version decides the
            # chipset, and that is `virtualHW.version`, not a choice per VM.
            machine_types=(),
            nic_models=dict(NIC_MODEL_TO_VMWARE),
            cpu_topology=True,
            # `cpuid.coresPerSocket` divides the vCPUs; there is no CPU *model*.
            cpu_model_choice=False,
            ioapic_optional=False,
            # A name becomes a directory and a .vmx file name.
            name_pattern=r"^(?!\.\.?$)[^/\\\x00-\x1f]+$",
            name_max_length=80,
            supports_tpm=True,
            # `uefi.secureBoot.enabled` is written and read back like any other key.
            secure_boot_readable=True,
            supported_network_types=("nat", "bridged", "hostonly", "internal"),
            supported_os_types=VERIFIED_GUEST_OS,
            evidence=(
                "probed on VMware Workstation 17 (vmrun 1.17.0.25388281, "
                "virtualHW.version 22) on a real host; see "
                "tests/fixtures/vmware_attach_matrix.json. Port limits measured by "
                "attaching a disk at each address -- VMware silently ignores one it "
                "cannot place, so the limits are the only thing that catches it. "
                "Every guestOS id was accepted by an actual power-on."
            ),
        )
