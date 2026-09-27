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
    DeviceKind,
    FirmwareType,
    NetworkType,
    BusType,
)

#: Model's bus -> libvirt ``<target bus='...'>`` value. virtio-scsi is expressed
#: as ``bus='scsi'`` plus a ``<controller model='virtio-scsi'>``, which is why it
#: shares a value with plain SCSI.
BUS_TO_LIBVIRT = {
    BusType.IDE: "ide",
    # virtio-blk is a plain block device with no SCSI layer -- a different bus
    # from virtio-scsi, not a spelling of it. The old model had no name for it,
    # so a domain using it was read back as virtio-scsi and re-emitted on
    # bus='scsi': the guest's /dev/vda silently became /dev/sda (F-25, M-01).
    BusType.VIRTIO_BLK: "virtio",
    BusType.SATA: "sata",
    BusType.SCSI: "scsi",
    BusType.SAS: "scsi",
    BusType.VIRTIO_SCSI: "scsi",
    BusType.USB: "usb",
    BusType.NVME: "nvme",
    BusType.FLOPPY: "fdc",
}

#: ``<controller model='...'>`` to add for buses that need one.
BUS_CONTROLLER_MODEL = {
    BusType.VIRTIO_SCSI: "virtio-scsi",
    BusType.SCSI: "lsilogic",
    BusType.SAS: "lsisas1078",
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

#: Buses that accept ``<target rotation_rate='...'>``, which is how libvirt says
#: solid-state. Measured, not assumed: on libvirt 11.10.0 any other bus is
#: refused with "rotation rate is only valid for SCSI/IDE/SATA bus" -- notably
#: virtio-blk, so a nonrotational disk there is reported as dropped rather than
#: silently presented as spinning (M-01).
ROTATION_RATE_BUSES = ("scsi", "ide", "sata")

#: What libvirt calls a solid-state disk. QEMU takes a rate in RPM; 1 is the
#: convention for "not rotating at all".
SSD_ROTATION_RATE = "1"

#: libvirt's ``<driver discard='...'>`` value for passing TRIM/UNMAP through.
#: Accepted on sata, virtio and scsi on libvirt 11.10.0 and echoed back by
#: ``dumpxml``, so unlike the rotation rate it needs no per-bus table.
DISCARD_ON = "unmap"

#: libvirt names a guest OS nowhere in the domain itself: the convention, shared
#: with virt-install and GNOME Boxes, is a `libosinfo`_ id in ``<metadata>``.
#: Verified on libvirt 11.10.0 -- it accepts the element and echoes it back
#: unchanged, so a guest OS survives a round trip here (A-05).
#:
#: .. _libosinfo: https://libosinfo.org/
OSINFO_NS = "http://libosinfo.org/xmlns/libvirt/domain/1.0"

#: Neutral guest id -> libosinfo os id. Only the ids vmctl names neutrally; a
#: provider string passes through, and cannot be expressed here.
GUEST_OS_TO_OSINFO = {
    "linux": "http://libosinfo.org/linux/2019",
    "ubuntu20.04": "http://ubuntu.com/ubuntu/20.04",
    "ubuntu22.04": "http://ubuntu.com/ubuntu/22.04",
    "ubuntu24.04": "http://ubuntu.com/ubuntu/24.04",
    "debian11": "http://debian.org/debian/11",
    "debian12": "http://debian.org/debian/12",
    "rhel8": "http://redhat.com/rhel/8.0",
    "rhel9": "http://redhat.com/rhel/9.0",
    "centos7": "http://centos.org/centos/7.0",
    "fedora": "http://fedoraproject.org/fedora/40",
    "opensuse": "http://opensuse.org/opensuse/15.5",
    "oracle9": "http://oracle.com/ol/9.0",
    "archlinux": "http://archlinux.org/archlinux/rolling",
    "alpine": "http://alpinelinux.org/alpine/3.19",
    "win10": "http://microsoft.com/win/10",
    "win11": "http://microsoft.com/win/11",
    "win2019": "http://microsoft.com/win/2k19",
    "win2022": "http://microsoft.com/win/2k22",
    "freebsd": "http://freebsd.org/freebsd/14.0",
    "openbsd": "http://openbsd.org/openbsd/7.4",
    "macos": "http://apple.com/macos/10.15",
    "solaris11": "http://oracle.com/solaris/11.4",
}

#: The same, reversed.
GUEST_OS_FROM_OSINFO = {v: k for k, v in GUEST_OS_TO_OSINFO.items()}

#: Device kind -> libvirt ``<disk device='...'>``.
KIND_TO_DEVICE = {
    DeviceKind.DISK: "disk",
    DeviceKind.CDROM: "cdrom",
    DeviceKind.FLOPPY: "floppy",
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
    # `ostype` used to be mapped to the *machine type* here, which put
    # "pc-q35-rhel9.8.0" in a field meaning "which OS the guest runs" -- a
    # category error. The guest OS is read from libosinfo metadata instead
    # (A-05), and the machine type belongs to A-10.
)
