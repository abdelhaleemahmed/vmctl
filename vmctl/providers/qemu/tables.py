"""
QEMU translation tables: data, not logic.

Everything here was read off the binary rather than remembered --
``-device help``, ``-machine help``, ``-drive format=help`` and the attach matrix
in ``tests/fixtures/qemu_attach_matrix.json`` -- because a QEMU build is a
build-time selection of devices, not a product with a fixed feature list. This one
(RHEL 9's ``qemu-kvm-10.1.0``) has no NVMe, no LSI SCSI and no MegaRAID at all.
"""

from typing import Dict, NamedTuple, Optional

from ...core.devices import BusType, DeviceKind, DiskFormat
from ...core.platform import NicModel
from ...core.vmconfig import NetworkType


class BusDevices(NamedTuple):
    """How one bus is expressed on a QEMU command line.

    Attributes:
        controller: ``-device`` for the controller, or None when the machine
            provides the bus itself. ``{bus}`` is substituted with its id.
        disk: ``-device`` for a hard disk on it.
        cdrom: ``-device`` for an optical drive, or None when it carries none.
        floppy: ``-device`` for a floppy drive, or None.
        addresses: Whether devices on this bus take a ``bus=`` address. A bus the
            machine provides is addressed implicitly.
    """

    controller: Optional[str]
    disk: Optional[str]
    cdrom: Optional[str] = None
    floppy: Optional[str] = None
    addresses: bool = True


#: Per bus, the devices to emit. Measured against QEMU 10.1.0 on machine q35.
#:
#: ``ide`` is the interesting one: QEMU's ``ide-hd`` binds to whatever IDE-ish bus
#: the machine provides, which on q35 is its built-in AHCI. libvirt, asked for the
#: same thing, reports IDE as unsupported on q35 -- because libvirt models IDE as a
#: ``piix3-ide`` controller, which q35 has not got. Same QEMU, different answers:
#: an attach matrix belongs to a provider, not to a hypervisor family.
BUS_DEVICES: Dict[BusType, BusDevices] = {
    BusType.IDE: BusDevices(
        controller=None,
        disk="ide-hd,drive={drive}",
        cdrom="ide-cd,drive={drive}",
        addresses=False,
    ),
    BusType.SATA: BusDevices(
        controller="ich9-ahci,id={bus}",
        disk="ide-hd,bus={bus}.{port},drive={drive}",
        cdrom="ide-cd,bus={bus}.{port},drive={drive}",
    ),
    BusType.VIRTIO_BLK: BusDevices(
        controller=None,
        # No SCSI layer, so no controller and no bus address: the drive is the
        # device. It also cannot eject, which is why an optical drive here is a
        # block device that merely claims to be one -- QEMU allows it, libvirt
        # refuses it, and vmctl declares what each one does.
        disk="virtio-blk-pci,drive={drive}",
        cdrom="virtio-blk-pci,drive={drive}",
        addresses=False,
    ),
    BusType.VIRTIO_SCSI: BusDevices(
        controller="virtio-scsi-pci,id={bus}",
        disk="scsi-hd,bus={bus}.0,drive={drive}",
        cdrom="scsi-cd,bus={bus}.0,drive={drive}",
    ),
    BusType.USB: BusDevices(
        controller="qemu-xhci,id={bus}",
        disk="usb-storage,bus={bus}.0,drive={drive}",
        cdrom="usb-storage,bus={bus}.0,drive={drive}",
    ),
    BusType.FLOPPY: BusDevices(
        controller="isa-fdc,id={bus}",
        disk=None,
        floppy="floppy,unit={port},drive={drive}",
    ),
}

#: The device name each bus's drives carry, for reading a command line back.
DEVICE_TO_BUS = {
    "ide-hd": BusType.IDE,
    "ide-cd": BusType.IDE,
    "virtio-blk-pci": BusType.VIRTIO_BLK,
    "virtio-blk": BusType.VIRTIO_BLK,
    "scsi-hd": BusType.VIRTIO_SCSI,
    "scsi-cd": BusType.VIRTIO_SCSI,
    "usb-storage": BusType.USB,
    "floppy": BusType.FLOPPY,
}

#: Which controller a bus id was created by, for the same purpose. ``scsi-hd`` on
#: its own does not say whether its controller is virtio-scsi or LSI.
CONTROLLER_TO_BUS = {
    "ich9-ahci": BusType.SATA,
    "ahci": BusType.SATA,
    "virtio-scsi-pci": BusType.VIRTIO_SCSI,
    "virtio-scsi": BusType.VIRTIO_SCSI,
    "qemu-xhci": BusType.USB,
    "isa-fdc": BusType.FLOPPY,
}

#: QEMU device name -> what the guest sees. ``usb-storage`` is both, decided by
#: the drive's ``media=``.
DEVICE_TO_KIND = {
    "ide-hd": DeviceKind.DISK,
    "ide-cd": DeviceKind.CDROM,
    "scsi-hd": DeviceKind.DISK,
    "scsi-cd": DeviceKind.CDROM,
    "virtio-blk-pci": DeviceKind.DISK,
    "virtio-blk": DeviceKind.DISK,
    "floppy": DeviceKind.FLOPPY,
}

#: Model format -> ``-drive format=`` value, which is also ``qemu-img``'s.
FORMAT_TO_DRIVER = {
    DiskFormat.QCOW2: "qcow2",
    DiskFormat.RAW: "raw",
    DiskFormat.VMDK: "vmdk",
    DiskFormat.VDI: "vdi",
    DiskFormat.VHD: "vpc",
    DiskFormat.VHDX: "vhdx",
}
DRIVER_TO_FORMAT = {v: k for k, v in FORMAT_TO_DRIVER.items()}

#: Neutral NIC model -> QEMU device name, from ``-device help``. This build has
#: four of the seven models vmctl can name: ``pcnet``, ``ne2k_pci`` and ``vmxnet3``
#: are not in it, and asking for one answers "'vmxnet3' is not a valid device model
#: name" (F-37).
#:
#: This table was first written by reading libvirt's rather than the binary's, which
#: is how the wrong three got in: libvirt *defines* a domain with vmxnet3 happily and
#: then fails to start it. One provider's table is not evidence for another's, even
#: when one of them is a manager of the other.
NIC_MODEL_TO_QEMU = {
    NicModel.VIRTIO: "virtio-net-pci",
    NicModel.E1000: "e1000",
    NicModel.E1000E: "e1000e",
    NicModel.RTL8139: "rtl8139",
}
NIC_MODEL_FROM_QEMU = {v: k for k, v in NIC_MODEL_TO_QEMU.items()}
NIC_MODEL_FROM_QEMU["virtio-net"] = NicModel.VIRTIO

#: Network mode -> ``-netdev`` backend. QEMU has no host-only or internal network
#: of its own: those are objects a *manager* creates, which is precisely the
#: difference between QEMU and libvirt.
NETWORK_TO_NETDEV = {
    NetworkType.NAT: "user",
    NetworkType.BRIDGED: "bridge",
}

#: Boot device -> the letter ``-boot order=`` takes.
BOOT_LETTER = {"floppy": "a", "disk": "c", "dvd": "d", "network": "n"}
BOOT_FROM_LETTER = {v: k for k, v in BOOT_LETTER.items()}
