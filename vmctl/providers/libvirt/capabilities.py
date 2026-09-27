"""
libvirt capabilities.

**Measured, not remembered** -- the recording is
``tests/fixtures/libvirt_attach_matrix.json``, produced by defining a domain for
every (device kind, bus) pair against libvirt 11.10.0 / QEMU 10.1.0 and keeping
what it said.

One finding changes the design. Unlike VirtualBox, **libvirt's matrix depends on
the QEMU build and the machine type**, so a static table can only ever be a
conservative default:

* ``IDE controllers are unsupported for this QEMU binary or machine type`` --
  q35 has no IDE controller at all. The same domain validates on ``pc``
  (i440fx), which this host also offers.
* ``This QEMU doesn't support the LSI 53C895A SCSI controller`` -- the bus is
  fine, the *controller model* is missing from this build. virtio-scsi works.
* ``NVMe disks are not supported with this QEMU binary``.
* ``disk type of 'vda' does not support ejectable media`` -- virtio-blk and
  ``sd`` cannot carry an optical drive, which is a property of the bus itself
  and will hold everywhere.

So the declaration below is what this build accepts on q35. Asking the host with
``virsh domcapabilities`` is the only way to be exact, which is ``E-05`` in
PLAN.md -- for libvirt that is a requirement rather than a refinement.
"""

from ...core.capabilities import BusSpec, Capabilities, FormatSpec, Support
from ...core.vmconfig import DiskFormat, DeviceKind, FirmwareType, BusType
from ...core.platform import Arch
from .tables import GUEST_OS_TO_OSINFO, NIC_MODEL_TO_LIBVIRT

_BUS = BusType

#: libvirt assigns addresses itself, so port counts are vmctl's own bookkeeping
#: rather than a limit the hypervisor imposes. They are generous on purpose.
BUSES = {
    _BUS.SATA: BusSpec("sata", "ahci", 1, 6, default_ports=6, controller_name="sata0"),
    _BUS.VIRTIO_SCSI: BusSpec(
        "virtio-scsi", "virtio-scsi", 1, 256, default_ports=256, controller_name="scsi0"
    ),
    _BUS.USB: BusSpec(
        "usb", "qemu-xhci", 1, 8, default_ports=8, bootable=False, controller_name="usb0"
    ),
    _BUS.FLOPPY: BusSpec("fdc", "fdc", 1, 2, default_ports=2, controller_name="fdc0"),
    _BUS.SCSI: BusSpec("scsi", "lsilogic", 1, 256, default_ports=256, controller_name="scsi-lsi0"),
    # virtio-blk: the fastest disk bus, and the reason to run a guest under KVM.
    # It needs no controller element of its own, and cannot carry removable
    # media. Declarable only since M-01 gave the model a name for it.
    _BUS.VIRTIO_BLK: BusSpec(
        "virtio", "virtio-blk", 1, 256, default_ports=256, controller_name="virtio-blk0"
    ),
}

#: Measured on this build with machine type q35.
ATTACH = {
    (DeviceKind.DISK, _BUS.SATA): True,
    (DeviceKind.CDROM, _BUS.SATA): True,
    (DeviceKind.FLOPPY, _BUS.SATA): False,
    (DeviceKind.DISK, _BUS.VIRTIO_SCSI): True,
    (DeviceKind.CDROM, _BUS.VIRTIO_SCSI): True,
    (DeviceKind.FLOPPY, _BUS.VIRTIO_SCSI): False,
    (DeviceKind.DISK, _BUS.USB): True,
    (DeviceKind.CDROM, _BUS.USB): True,
    (DeviceKind.FLOPPY, _BUS.USB): False,
    (DeviceKind.DISK, _BUS.FLOPPY): False,
    (DeviceKind.CDROM, _BUS.FLOPPY): False,
    (DeviceKind.FLOPPY, _BUS.FLOPPY): True,
    # The LSI controller is absent from this QEMU build, so plain SCSI is
    # declared unusable here even though libvirt understands the bus.
    (DeviceKind.DISK, _BUS.SCSI): False,
    (DeviceKind.CDROM, _BUS.SCSI): False,
    (DeviceKind.FLOPPY, _BUS.SCSI): False,
    # `disk|virtio: true` has been in the recording since it was first captured;
    # the declaration could not say so until the model had the word (M-01).
    # An optical drive on it is refused with "disk type of 'vda' does not support
    # ejectable media", which is a property of the bus and holds everywhere.
    (DeviceKind.DISK, _BUS.VIRTIO_BLK): True,
    (DeviceKind.CDROM, _BUS.VIRTIO_BLK): False,
    (DeviceKind.FLOPPY, _BUS.VIRTIO_BLK): False,
}

#: What this QEMU build's *block layer* will actually attach, which is not the same
#: as what ``qemu-img`` can create (F-31). ``-drive format=help`` reports qcow2 and
#: raw read-write, and vdi, vhdx, vmdk and vpc read-only; qed and parallels are
#: absent from the build entirely.
#:
#: This was declared from memory of QEMU in general before it was measured, and the
#: error it hid is the worst kind: libvirt *defines* a domain with a VMDK disk
#: happily and then fails to start it with "Driver 'vmdk' can only be used for
#: read-only devices" -- so vmctl reported success and left a VM that could not run.
FORMATS = {
    DiskFormat.QCOW2: FormatSpec(Support.NATIVE, ("qcow2",), "qcow2", ("thin", "thick")),
    DiskFormat.RAW: FormatSpec(Support.READ_WRITE, ("raw", "img"), "raw", ("thin", "thick")),
    # Attachable, but only read-only, so vmctl cannot give a VM one as its disk.
    DiskFormat.VMDK: FormatSpec(Support.READ_ONLY, ("vmdk",), "vmdk", ()),
    DiskFormat.VDI: FormatSpec(Support.READ_ONLY, ("vdi",), "vdi", ()),
    DiskFormat.VHD: FormatSpec(Support.READ_ONLY, ("vhd", "vpc"), "vpc", ()),
    DiskFormat.VHDX: FormatSpec(Support.READ_ONLY, ("vhdx",), "vhdx", ()),
    # Not in this build at all: "Unknown driver 'qed'".
    DiskFormat.QED: FormatSpec(Support.UNSUPPORTED, ("qed",), "qed", ()),
    DiskFormat.PARALLELS: FormatSpec(Support.UNSUPPORTED, ("hdd",), "parallels", ()),
}

REMOVABLE_EXTENSIONS = ("iso", "img", "ima", "dsk", "flp", "vfd", "cdr")


class LibvirtCapabilities:
    """libvirt-specific capabilities and limits."""

    @staticmethod
    def get() -> Capabilities:
        """Return the typed capability declaration for libvirt."""
        return Capabilities(
            provider="libvirt",
            min_version="8.0",
            max_cpus=240,
            max_memory_mb=4_194_304,
            # libvirt has no VRAM setting in the VirtualBox sense; video memory
            # is a device property. The limit is nominal.
            max_vram_mb=512,
            max_network_adapters=8,
            max_disks=256,
            buses=dict(BUSES),
            attach=dict(ATTACH),
            formats=dict(FORMATS),
            native_format=DiskFormat.QCOW2,
            native_buses={
                # virtio-scsi rather than the faster virtio-blk: one controller
                # carries both disks and optical drives, so a VM arriving from
                # another hypervisor with an installer DVD keeps it. virtio-blk
                # is available and is the better choice for a disk-only guest.
                DeviceKind.DISK: _BUS.VIRTIO_SCSI,
                DeviceKind.CDROM: _BUS.SATA,
                DeviceKind.FLOPPY: _BUS.FLOPPY,
            },
            removable_extensions=REMOVABLE_EXTENSIONS,
            firmware={
                FirmwareType.BIOS: Support.NATIVE,
                FirmwareType.EFI: Support.READ_WRITE,
                FirmwareType.EFI64: Support.READ_WRITE,
                # libvirt's firmware='efi' has no 32-bit form; it resolves a
                # loader for the guest's architecture.
                FirmwareType.EFI32: Support.UNSUPPORTED,
            },
            # libvirt is permissive about domain names, but vmctl writes a
            # definition file named after one, so separators are still out.
            name_pattern=r"^(?!\.\.?$)[^/\\\x00-\x1f]+$",
            name_max_length=253,
            supports_tpm=True,
            secure_boot_readable=True,
            # x86_64 only on this build: `machine='virt'` is ARM and is refused
            # here with "machine type not supported". The machine types are the
            # aliases this QEMU offers, and libvirt expands them to the versioned
            # ones it resolved (A-10).
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
            nic_models=dict(NIC_MODEL_TO_LIBVIRT),
            cpu_topology=True,
            cpu_model_choice=True,
            # libvirt records a guest OS as a libosinfo id, so what it can
            # express is exactly what vmctl has an id for.
            supported_os_types=tuple(GUEST_OS_TO_OSINFO),
            supported_network_types=("nat", "bridged", "hostonly", "internal"),
            evidence=(
                "probed on libvirt 11.10.0 / QEMU 10.1.0, machine q35; see "
                "tests/fixtures/libvirt_attach_matrix.json. The formats were "
                "re-measured against `-drive format=help` and by starting a domain "
                "per format: this build attaches only qcow2 and raw read-write "
                "(F-31). libvirt's matrix depends on the QEMU build and machine "
                "type -- E-05 probing is required for exactness."
            ),
        )
