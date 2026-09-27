"""
VMware translation tables: data, not logic.

Every value here was read off VMware Workstation 17 (vmrun 1.17.0.25388281,
``virtualHW.version = "22"``) on a real host: the attach matrix in
``tests/fixtures/vmware_attach_matrix.json``, the port limits by attaching a disk at
each address and asking the log whether it opened, and the guest OS ids from
``vmcli VM Create -g`` plus a power-on test each.
"""

from typing import Dict, NamedTuple, Optional

from ...core.codecs import EnumCodec, Int, Str
from ...core.devices import Allocation, BusType, DeviceKind, DiskFormat
from ...core.mapping import Field
from ...core.platform import NicModel
from ...core.vmconfig import FirmwareType, NetworkType


class BusKeys(NamedTuple):
    """How one bus is spelled in a ``.vmx``.

    Attributes:
        prefix: The key prefix (``sata``, ``scsi``, ``ide``, ``nvme``).
        virtual_dev: The ``<prefix>0.virtualDev`` value, when the bus needs one.
            Only SCSI does -- and which one is the difference between LSI Logic,
            LSI SAS and VMware's own paravirtual adapter.
        channelled: True when the *port* selects a controller channel and the unit
            selects the device on it (``ide0:1``), rather than the port being the
            device number (``sata0:5``). Only IDE works that way.
    """

    prefix: str
    virtual_dev: Optional[str] = None
    channelled: bool = False


#: Per bus, how to name its controller and devices. Plain SCSI, SAS and the
#: paravirtual adapter are the same ``scsi`` bus with different ``virtualDev``
#: values, which is why one neutral bus cannot cover all three.
BUS_KEYS: Dict[BusType, BusKeys] = {
    BusType.IDE: BusKeys("ide", channelled=True),
    BusType.SATA: BusKeys("sata"),
    BusType.SCSI: BusKeys("scsi", virtual_dev="lsilogic"),
    BusType.SAS: BusKeys("scsi", virtual_dev="lsisas1068"),
    BusType.NVME: BusKeys("nvme"),
    BusType.FLOPPY: BusKeys("floppy"),
}

#: ``virtualDev`` -> bus, for reading a ``.vmx`` back. ``pvscsi`` is VMware's
#: paravirtual adapter; vmctl has no neutral name for it, so it reads as plain SCSI
#: and is preserved through ``provider_options``.
VIRTUAL_DEV_TO_BUS = {
    "lsilogic": BusType.SCSI,
    "lsilogicsas": BusType.SAS,
    "lsisas1068": BusType.SAS,
    "pvscsi": BusType.SCSI,
    "buslogic": BusType.SCSI,
}

#: Device kind -> ``deviceType``. An empty optical drive is ``atapi-cdrom``, which
#: is a drive with nothing in it rather than a drive with no medium *file*.
KIND_TO_DEVICE_TYPE = {
    DeviceKind.DISK: "disk",
    DeviceKind.CDROM: "cdrom-image",
    DeviceKind.FLOPPY: "file",
}
EMPTY_CDROM_DEVICE_TYPE = "atapi-cdrom"

#: ``deviceType`` -> device kind, for reading.
DEVICE_TYPE_TO_KIND = {
    "disk": DeviceKind.DISK,
    "rawdisk": DeviceKind.DISK,
    "cdrom-image": DeviceKind.CDROM,
    "cdrom-raw": DeviceKind.CDROM,
    "atapi-cdrom": DeviceKind.CDROM,
    "file": DeviceKind.FLOPPY,
}

#: ``vmware-vdiskmanager -t`` type per allocation. Type 0 grows, type 2 is
#: preallocated; the split variants (1 and 3) are for filesystems with a file size
#: limit and are not something vmctl chooses on anyone's behalf.
ALLOCATION_TO_DISK_TYPE = {Allocation.THIN: "0", Allocation.THICK: "2"}

#: The only image format a ``.vmx`` can attach. Not a simplification: VMware has no
#: way to use a qcow2 or a VDI, and its own converter reads nothing else either --
#: which makes migrating *onto* VMware from libvirt a job for ``qemu-img`` first.
NATIVE_FORMAT = DiskFormat.VMDK

#: Neutral NIC model -> ``ethernetN.virtualDev``. Measured: e1000, e1000e, vmxnet3,
#: vlance and vmxnet are accepted and ``virtio`` is refused -- VMware has no virtio.
NIC_MODEL_TO_VMWARE = {
    NicModel.E1000: "e1000",
    NicModel.E1000E: "e1000e",
    NicModel.VMXNET3: "vmxnet3",
    NicModel.PCNET: "vlance",
}
NIC_MODEL_FROM_VMWARE = {v: k for k, v in NIC_MODEL_TO_VMWARE.items()}
#: VMware's first-generation paravirtual NIC. Read as vmxnet3, the one vmctl names.
NIC_MODEL_FROM_VMWARE["vmxnet"] = NicModel.VMXNET3

#: Network mode -> ``ethernetN.connectionType``. All four were accepted; ``custom``
#: additionally needs ``ethernetN.vnet``, which is how a named VMnet is reached.
NETWORK_TO_CONNECTION = {
    NetworkType.NAT: "nat",
    NetworkType.BRIDGED: "bridged",
    NetworkType.HOSTONLY: "hostonly",
    NetworkType.INTERNAL: "custom",
    NetworkType.NATNETWORK: "nat",
}
CONNECTION_TO_NETWORK = {
    "nat": NetworkType.NAT,
    "bridged": NetworkType.BRIDGED,
    "hostonly": NetworkType.HOSTONLY,
    "custom": NetworkType.INTERNAL,
}

#: Firmware -> ``firmware``. VMware has one EFI, so both of vmctl's EFI spellings
#: land on it; there is no 32-bit variant.
FIRMWARE_TO_VMWARE = {
    FirmwareType.BIOS: "bios",
    FirmwareType.EFI: "efi",
    FirmwareType.EFI64: "efi",
}
FIRMWARE_FROM_VMWARE = {"bios": FirmwareType.BIOS, "efi": FirmwareType.EFI64}

#: Boot device -> the word ``bios.bootOrder`` takes.
BOOT_DEVICE = {"disk": "hdd", "dvd": "cdrom", "floppy": "floppy", "network": "ethernet0"}
BOOT_FROM_DEVICE = {v: k for k, v in BOOT_DEVICE.items()}

#: Neutral guest id -> ``guestOS``. Every value was accepted by a real power-on;
#: VMware validates this key and refuses an unknown one outright with
#: "[msg.guestos.badname] Guest operating system 'x' is not supported", so a table
#: from memory would fail at the last possible moment.
#:
#: Note ``win10`` -> ``windows9-64``: VMware never renamed the id after Windows 9
#: became Windows 10. Arch, Alpine and OpenBSD have no id at all and fall back to
#: the generic Linux and Other ones.
GUEST_OS_TO_VMWARE = {
    "linux": "other6xlinux-64",
    "ubuntu": "ubuntu-64",
    "ubuntu20.04": "ubuntu-64",
    "ubuntu22.04": "ubuntu-64",
    "ubuntu24.04": "ubuntu-64",
    "debian": "debian12-64",
    "debian11": "debian11-64",
    "debian12": "debian12-64",
    "rhel": "rhel9-64",
    "rhel8": "rhel8-64",
    "rhel9": "rhel9-64",
    "centos7": "centos7-64",
    "fedora": "fedora-64",
    "opensuse": "opensuse-64",
    "oracle9": "oraclelinux9-64",
    "archlinux": "other6xlinux-64",
    "alpine": "other6xlinux-64",
    "win10": "windows9-64",
    "win11": "windows11-64",
    "win2019": "windows2019srv-64",
    "win2022": "windows2019srvNext-64",
    "freebsd": "freebsd13-64",
    "openbsd": "other-64",
    "macos": "darwin20-64",
    "solaris11": "solaris11-64",
    "other": "other-64",
}
#: VMware id -> neutral id, written out rather than derived. Several neutral ids
#: share one VMware id -- every Ubuntu version maps to ``ubuntu-64``, because VMware
#: has only one -- so reversing the table automatically either loses the version
#: (``debian12-64`` reading back as ``debian``) or invents one (``ubuntu-64`` reading
#: back as ``ubuntu22.04``). Which way each id should read is a judgement, so it is
#: stated: keep a version VMware actually distinguishes, and drop one it does not.
GUEST_OS_FROM_VMWARE = {
    "other6xlinux-64": "linux",
    "ubuntu-64": "ubuntu",
    "debian11-64": "debian11",
    "debian12-64": "debian12",
    "debian13-64": "debian12",
    "rhel8-64": "rhel8",
    "rhel9-64": "rhel9",
    "rhel10-64": "rhel9",
    "centos7-64": "centos7",
    "centos8-64": "rhel8",
    "centos9-64": "rhel9",
    "fedora-64": "fedora",
    "opensuse-64": "opensuse",
    "oraclelinux9-64": "oracle9",
    "vmware-photon-64": "linux",
    "windows9-64": "win10",
    "windows11-64": "win11",
    "windows2019srv-64": "win2019",
    "windows2019srvNext-64": "win2022",
    "freebsd13-64": "freebsd",
    "darwin20-64": "macos",
    "solaris11-64": "solaris11",
    "other-64": "other",
}

#: Every guestOS id verified to power on here, for the capability declaration.
VERIFIED_GUEST_OS = tuple(sorted(set(GUEST_OS_TO_VMWARE.values()))) + (
    "centos8-64",
    "centos9-64",
    "debian13-64",
    "rhel10-64",
    "vmware-photon-64",
)

#: The PCI bridges VMware writes itself, and without which a PCIe device is refused
#: with "Device nvme0 requested without secondary PCI slots available". Boilerplate
#: that belongs to the format rather than to the VM, so it is stated once here.
PCI_BOILERPLATE = (
    ("pciBridge0.present", "TRUE"),
    ("pciBridge4.present", "TRUE"),
    ("pciBridge4.virtualDev", "pcieRootPort"),
    ("pciBridge4.functions", "8"),
    ("pciBridge5.present", "TRUE"),
    ("pciBridge5.virtualDev", "pcieRootPort"),
    ("pciBridge5.functions", "8"),
    ("pciBridge6.present", "TRUE"),
    ("pciBridge6.virtualDev", "pcieRootPort"),
    ("pciBridge6.functions", "8"),
    ("pciBridge7.present", "TRUE"),
    ("pciBridge7.virtualDev", "pcieRootPort"),
    ("pciBridge7.functions", "8"),
    ("vmci0.present", "TRUE"),
)

#: The hardware version vmctl writes. 22 is Workstation 17's, and the version
#: decides which devices exist at all -- so it is stated rather than inherited.
VIRTUAL_HW_VERSION = "22"
CONFIG_VERSION = "8"


def address(bus: BusType, index: int, port: int, unit: int) -> str:
    """Return the ``.vmx`` key prefix for one device's position.

    IDE is addressed by channel and master/slave (``ide0:1``); every other bus
    numbers its devices directly (``sata0:5``). Measured: ``ide0:2`` is silently
    ignored, as is ``sata0:30``, ``scsi0:16`` and ``nvme0:64``.

    Args:
        bus: The bus the device is on.
        index: Which controller on that bus.
        port: Port (or device number).
        unit: Unit on the port; only IDE uses anything but 0.

    Returns:
        A key prefix such as ``"sata0:3"``.
    """
    keys = BUS_KEYS[bus]
    if bus is BusType.FLOPPY:
        return f"{keys.prefix}{port}"
    if keys.channelled:
        return f"{keys.prefix}{port}:{unit}"
    return f"{keys.prefix}{index}:{port}"


#: Fields read straight out of the ``.vmx``, in both directions where it makes
#: sense. The file is ``key = "value"``, so the shared mapping engine reads it with
#: no VMware-specific code at all -- the same declaration style the other three
#: providers use (A-11).
FIELDS = (
    Field("name", "displayName", Str()),
    Field("memory.mb", "memsize", Int(4, 4_194_304)),
    Field("cpu.count", "numvcpus", Int(1, 240)),
    Field("cpu.cores", "cpuid.coresPerSocket", Int(1, 240)),
    Field("guest_os", "guestOS", Str()),
    Field("description", "annotation", Str()),
    Field(
        "firmware.type",
        "firmware",
        EnumCodec(FirmwareType, FIRMWARE_FROM_VMWARE, default=FirmwareType.BIOS),
    ),
)
