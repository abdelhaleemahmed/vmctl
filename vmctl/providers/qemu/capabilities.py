"""
QEMU capabilities.

**Measured, not remembered.** The recording is
``tests/fixtures/qemu_attach_matrix.json`` (plus ``_pc.json`` for the other machine
type), produced by ``scripts/probe-qemu-matrix.py``, which starts QEMU once per
(device kind, bus) pair with the guest halted and keeps what it said.

Three findings shape the declaration, and none of them would have survived being
assumed:

* **This build has no NVMe, no LSI SCSI and no MegaRAID SAS.** ``-device help``
  lists neither, so three buses vmctl can name are simply absent here. A QEMU build
  is a build-time selection of devices; RHEL's is a narrow one.
* **Only qcow2 and raw can be attached read-write.** ``-drive format=help`` reports
  vdi, vhdx, vmdk and vpc as read-only, and ``qed``/``parallels`` are not in the
  build at all. ``qemu-img`` will happily *create* a VMDK, which is what made this
  easy to get wrong (F-31).
* **The floppy controller depends on the machine type.** ``isa-fdc`` is refused on
  q35 ("not supported with machine type pc-q35-rhel9.8.0") and accepted on ``pc``.
  The two matrices differ in that one cell and nothing else.
"""

from typing import Any, Dict

from ...core.capabilities import BusSpec, Capabilities, FormatSpec, Support
from ...core.devices import BusType, DeviceKind, DiskFormat
from ...core.platform import Arch
from ...core.vmconfig import FirmwareType
from .tables import NIC_MODEL_TO_QEMU

_BUS = BusType

#: Port counts are vmctl's own bookkeeping: QEMU addresses AHCI ports explicitly
#: and lets every other bus assign addresses itself, so these are the limits of the
#: controller rather than of the command line.
BUSES = {
    _BUS.SATA: BusSpec("sata", "ich9-ahci", 1, 6, default_ports=6, controller_name="sata0"),
    _BUS.IDE: BusSpec(
        "ide",
        "builtin",
        1,
        2,
        units_per_port=2,
        default_ports=2,
        controller_name="ide0",
    ),
    _BUS.VIRTIO_BLK: BusSpec(
        "virtio-blk",
        "virtio-blk-pci",
        1,
        32,
        default_ports=32,
        controller_name="virtio-blk0",
    ),
    _BUS.VIRTIO_SCSI: BusSpec(
        "virtio-scsi",
        "virtio-scsi-pci",
        1,
        256,
        default_ports=256,
        controller_name="scsi0",
    ),
    _BUS.USB: BusSpec(
        "usb",
        "qemu-xhci",
        1,
        8,
        default_ports=8,
        bootable=False,
        hotplug=True,
        controller_name="usb0",
    ),
}

#: Measured on q35. Every True here started; every False carries the reason in the
#: recording's ``notes``.
ATTACH: Dict[Any, bool] = {}
for _bus in (_BUS.IDE, _BUS.SATA, _BUS.VIRTIO_BLK, _BUS.VIRTIO_SCSI, _BUS.USB):
    ATTACH[(DeviceKind.DISK, _bus)] = True
    # Even virtio-blk: QEMU builds the machine, though the guest cannot eject.
    ATTACH[(DeviceKind.CDROM, _bus)] = True
    ATTACH[(DeviceKind.FLOPPY, _bus)] = False

#: The floppy bus is absent altogether: ``isa-fdc`` is refused on q35, and a bus
#: with no rows is a bus vmctl will not offer. ``machine: pc`` has it, which is the
#: kind of per-machine difference E-05's host probing would have to notice -- the
#: recording keeps both matrices so the difference is written down rather than
#: implied.

#: What the block layer will attach, which is not what qemu-img can create (F-31).
FORMATS = {
    DiskFormat.QCOW2: FormatSpec(Support.NATIVE, ("qcow2",), "qcow2", ("thin", "thick")),
    DiskFormat.RAW: FormatSpec(Support.READ_WRITE, ("raw", "img"), "raw", ("thin", "thick")),
    DiskFormat.VMDK: FormatSpec(Support.READ_ONLY, ("vmdk",), "vmdk", ()),
    DiskFormat.VDI: FormatSpec(Support.READ_ONLY, ("vdi",), "vdi", ()),
    DiskFormat.VHD: FormatSpec(Support.READ_ONLY, ("vhd", "vpc"), "vpc", ()),
    DiskFormat.VHDX: FormatSpec(Support.READ_ONLY, ("vhdx",), "vhdx", ()),
    DiskFormat.QED: FormatSpec(Support.UNSUPPORTED, ("qed",), "qed", ()),
    DiskFormat.PARALLELS: FormatSpec(Support.UNSUPPORTED, ("hdd",), "parallels", ()),
}

REMOVABLE_EXTENSIONS = ("iso", "img", "ima", "dsk", "flp", "vfd", "cdr")


class QemuCapabilities:
    """What this QEMU build can do."""

    @staticmethod
    def get() -> Capabilities:
        """Return the typed capability declaration for plain QEMU."""
        return Capabilities(
            provider="qemu",
            min_version="6.0",
            max_cpus=240,
            max_memory_mb=4_194_304,
            # QEMU's display devices have video memory; the VM has no such setting.
            max_vram_mb=512,
            max_network_adapters=8,
            max_disks=256,
            buses=dict(BUSES),
            attach=dict(ATTACH),
            formats=dict(FORMATS),
            native_format=DiskFormat.QCOW2,
            native_buses={
                # virtio-blk is the fastest and needs no controller; an optical
                # drive goes on SATA, where it can actually eject.
                DeviceKind.DISK: _BUS.VIRTIO_BLK,
                DeviceKind.CDROM: _BUS.SATA,
            },
            removable_extensions=REMOVABLE_EXTENSIONS,
            firmware={
                FirmwareType.BIOS: Support.NATIVE,
                # OVMF is a firmware *file* passed with -bios/-pflash; vmctl does
                # not choose one for you yet, so EFI is declared as convertible
                # rather than native.
                FirmwareType.EFI: Support.READ_WRITE,
                FirmwareType.EFI64: Support.READ_WRITE,
                FirmwareType.EFI32: Support.UNSUPPORTED,
            },
            arches=(Arch.X86_64, Arch.I686),
            # Measured with `-machine help` (QEMU) and `virsh capabilities` (libvirt),
            # which list the same set: the two aliases plus every versioned type this
            # build carries. Both spellings matter -- libvirt *expands* an alias when it
            # echoes a domain back, so a config read from libvirt carries
            # "pc-q35-rhel9.8.0", and declaring only the alias made a round trip
            # substitute it for one that floats with the next upgrade.
            machine_types=(
                "q35",
                "pc",
                "pc-i440fx-rhel7.6.0",
                "pc-q35-rhel7.6.0",
                "pc-q35-rhel8.0.0",
                "pc-q35-rhel8.1.0",
                "pc-q35-rhel8.2.0",
                "pc-q35-rhel8.3.0",
                "pc-q35-rhel8.4.0",
                "pc-q35-rhel8.5.0",
                "pc-q35-rhel8.6.0",
                "pc-q35-rhel9.0.0",
                "pc-q35-rhel9.2.0",
                "pc-q35-rhel9.4.0",
                "pc-q35-rhel9.6.0",
                "pc-q35-rhel9.8.0",
            ),
            default_machine="q35",
            nic_models=dict(NIC_MODEL_TO_QEMU),
            cpu_topology=True,
            cpu_model_choice=True,
            # q35 and pc both always have an I/O APIC and no way to remove it.
            ioapic_optional=False,
            # A name becomes a directory under the state directory and goes into a
            # generated script, so separators and control characters are out.
            name_pattern=r"^(?!\.\.?$)[^/\\\x00-\x1f]+$",
            name_max_length=128,
            supports_tpm=False,
            secure_boot_readable=False,
            # QEMU has user networking and a bridge helper. A host-only or internal
            # network is an object something else has to create -- which is the
            # difference between a hypervisor and a manager of one.
            supported_network_types=("nat", "bridged"),
            supported_os_types=(),
            evidence=(
                "probed on QEMU 10.1.0 (qemu-kvm-10.1.0-17.el9_8.5), machine q35; "
                "see tests/fixtures/qemu_attach_matrix.json and _pc.json. No NVMe, "
                "LSI SCSI or MegaRAID in this build; only qcow2 and raw attach "
                "read-write; isa-fdc needs machine 'pc'."
            ),
        )
