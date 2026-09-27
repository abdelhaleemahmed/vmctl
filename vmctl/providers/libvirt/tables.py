"""
libvirt translation tables: data, not logic.

Interpreted in both directions by :mod:`vmctl.core.mapping`, exactly as the
VirtualBox tables are, so a setting is declared once per provider.

The interesting difference from VirtualBox is how much libvirt does *not* take
from us. It assigns PCI addresses, target device names and a CPU model itself, so
this provider deliberately under-specifies and lets libvirt fill the rest in --
the opposite of VirtualBox, where vmctl chooses the port and device numbers.
"""

from ...core.codecs import EnumCodec, Int, OnOff, Str
from ...core.mapping import Field
from ...core.vmconfig import (
    DiskFormat,
    DiskType,
    FirmwareType,
    NetworkType,
    StorageControllerType,
)

#: Model's bus -> libvirt ``<target bus='...'>`` value. virtio-scsi is expressed
#: as ``bus='scsi'`` plus a ``<controller model='virtio-scsi'>``, which is why it
#: shares a value with plain SCSI.
BUS_TO_LIBVIRT = {
    StorageControllerType.IDE: "ide",
    StorageControllerType.SATA: "sata",
    StorageControllerType.SCSI: "scsi",
    StorageControllerType.SAS: "scsi",
    StorageControllerType.VIRTIO_SCSI: "scsi",
    StorageControllerType.USB: "usb",
    StorageControllerType.NVME: "nvme",
    StorageControllerType.FLOPPY: "fdc",
}

#: ``<controller model='...'>`` to add for buses that need one.
BUS_CONTROLLER_MODEL = {
    StorageControllerType.VIRTIO_SCSI: "virtio-scsi",
    StorageControllerType.SCSI: "lsilogic",
    StorageControllerType.SAS: "lsisas1078",
}

#: Target device prefix per libvirt bus. libvirt derives the rest, but a name is
#: required, so vmctl allocates within each prefix.
TARGET_PREFIX = {
    "virtio": "vd",
    "sata": "sd",
    "scsi": "sd",
    "usb": "sd",
    "ide": "hd",
    "fdc": "fd",
    "nvme": "vd",
}

#: Device kind -> libvirt ``<disk device='...'>``.
KIND_TO_DEVICE = {
    DiskType.HDD: "disk",
    DiskType.SSD: "disk",
    DiskType.DVD: "cdrom",
    DiskType.FLOPPY: "floppy",
}

#: Model format -> libvirt ``<driver type='...'>``.
FORMAT_TO_DRIVER = {
    DiskFormat.QCOW2: "qcow2",
    DiskFormat.RAW: "raw",
    DiskFormat.VDI: "vdi",
    DiskFormat.VMDK: "vmdk",
    DiskFormat.VHD: "vpc",
    DiskFormat.VHDX: "vhdx",
    DiskFormat.QED: "qed",
    DiskFormat.PARALLELS: "parallels",
}
DRIVER_TO_FORMAT = {v: k for k, v in FORMAT_TO_DRIVER.items()}

#: Network mode -> libvirt ``<interface type='...'>``.
#: A VirtualBox NAT adapter is per-VM outbound-only networking with no host
#: interface, which is exactly QEMU's `type='user'` -- not libvirt's `default`
#: network. Mapping it to `default` made a domain fail to start on a session
#: connection, where that network does not exist.
NETWORK_TO_LIBVIRT = {
    NetworkType.NAT: "user",
    NetworkType.NATNETWORK: "network",
    NetworkType.BRIDGED: "bridge",
    NetworkType.HOSTONLY: "network",
    NetworkType.INTERNAL: "network",
}

#: NIC chipset names, in both directions. VirtualBox names appear here because a
#: config exported from VirtualBox carries them; A-10 replaces this with a
#: neutral NicModel so the mapping is not provider-to-provider.
NIC_MODEL_FROM_NATIVE = {
    "82540em": "e1000",
    "82543gc": "e1000",
    "82545em": "e1000",
    "am79c970a": "pcnet",
    "am79c973": "pcnet",
    "virtio": "virtio",
    "virtio-net": "virtio",
    "e1000": "e1000",
    "e1000e": "e1000e",
    "rtl8139": "rtl8139",
    "vmxnet3": "vmxnet3",
}

#: libvirt firmware value per model firmware type. BIOS needs no attribute.
FIRMWARE_TO_LIBVIRT = {
    FirmwareType.BIOS: "",
    FirmwareType.EFI: "efi",
    FirmwareType.EFI32: "efi",
    FirmwareType.EFI64: "efi",
}

#: Boot device -> libvirt ``<boot dev='...'>``.
BOOT_DEVICE = {"disk": "hd", "dvd": "cdrom", "floppy": "fd", "network": "network"}

#: Scalar settings, read from and written to the same declaration. These are read
#: out of the parsed XML by the libvirt parser, which flattens the document into
#: the same ``key -> value`` shape the VirtualBox parser produces.
FIELDS = (
    Field("memory.mb", "memory_mib", Int(minimum=4)),
    Field("cpu.count", "vcpu", Int(minimum=1)),
    Field("firmware.type", "firmware", EnumCodec(FirmwareType), None),
    Field("boot.acpi", "feature_acpi", OnOff()),
    Field("boot.ioapic", "feature_apic", OnOff()),
    Field("boot.hpet", "timer_hpet", OnOff()),
    Field("rtc_utc", "clock_utc", OnOff()),
    Field("description", "description", Str()),
    Field("ostype", "os_type", Str()),
)
