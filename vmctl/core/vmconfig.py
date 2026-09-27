"""
Core VM configuration models - the center of gravity
"""
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict, Any
from enum import Enum


class FirmwareType(Enum):
    """VM firmware type.

    Determines the boot firmware used by the virtual machine.
    EFI64 is required for Windows 11 and Secure Boot.
    """

    BIOS = "bios"
    EFI = "efi"
    EFI64 = "efi64"
    EFI32 = "efi32"


class DiskType(Enum):
    """Virtual disk media type.

    HDD and SSD have identical performance in VirtualBox; the distinction
    is metadata-only. DVD represents an optical drive backed by an ISO image,
    and FLOPPY a floppy drive. DVD and FLOPPY are *removable* devices: no medium
    is created for them, and ``size_mb`` is not meaningful.
    """

    HDD = "hdd"
    SSD = "ssd"
    DVD = "dvd"
    FLOPPY = "floppy"


class DiskVariant(Enum):
    """Disk allocation strategy.

    THIN (dynamic): the image file grows on demand up to ``size_mb``.
    THICK (fixed): the full ``size_mb`` is pre-allocated on creation.
    RAW format always uses THICK regardless of this setting.
    """

    THIN = "thin"      # Dynamic/Standard - grows as needed
    THICK = "thick"    # Fixed - preallocated


class DiskFormat(Enum):
    """Disk image file format.

    VDI is the native VirtualBox format and is recommended for most use cases.
    VMDK and VHD are useful when the image needs to be shared with VMware or
    Hyper-V respectively. RAW produces a flat binary image with no metadata.
    """

    VDI = "vdi"        # VirtualBox native format
    VMDK = "vmdk"      # VMware format (also supported by VirtualBox)
    VHD = "vhd"        # Microsoft Virtual Hard Disk
    RAW = "raw"        # Raw disk image


class NetworkType(Enum):
    """Network adapter connection mode.

    NAT: outbound internet through the host; guests are not directly reachable.
    BRIDGED: guest appears as a first-class device on the physical network.
    HOSTONLY: isolated network shared only between the host and VMs.
    INTERNAL: VM-to-VM only; the host has no access.
    NATNETWORK: like NAT but multiple VMs share one DHCP domain.
    """

    NAT = "nat"
    BRIDGED = "bridged"
    HOSTONLY = "hostonly"
    INTERNAL = "internal"
    NATNETWORK = "natnetwork"


class StorageControllerType(Enum):
    """Storage bus / controller chipset type.

    IDE supports up to 2 ports (legacy; useful for optical drives).
    SATA (IntelAHCI) supports up to 30 ports and is the default.
    SCSI (LsiLogic) and SAS (LsiLogicSas) support up to 254/255 ports
    and are useful for high port-count configurations.
    NVME (PCIe) is available on VirtualBox 6.0 and later.
    FLOPPY (I82078) carries floppy drives only.
    USB requires exactly 8 ports on VirtualBox.
    VIRTIO_SCSI (VirtIO) is available on VirtualBox 7.x; note that its
    ``--add`` value is ``virtio-scsi``, which ``VBoxManage storagectl --help``
    does not list.
    """

    IDE = "ide"
    SATA = "sata"
    SCSI = "scsi"
    SAS = "sas"
    NVME = "nvme"
    FLOPPY = "floppy"
    USB = "usb"
    VIRTIO_SCSI = "virtio-scsi"


@dataclass
class CPUConfig:
    """CPU configuration.

    Attributes:
        count: Number of virtual CPUs (1–128).
        hotplug: Allow CPUs to be added/removed while the VM is running.
        execution_cap: Maximum percentage of host CPU time the VM may use (1–100).
        pae: Enable Physical Address Extension for 32-bit OSes.
        nested_virt: Enable nested virtualisation (required for running KVM inside VirtualBox).
    """

    count: int = 2
    hotplug: bool = False
    execution_cap: int = 100  # Percentage
    pae: bool = False  # Physical Address Extension
    nested_virt: bool = False

    def to_dict(self) -> dict:
        """Return CPU configuration as a plain dictionary.

        Returns:
            dict: All fields with their current values.
        """
        return asdict(self)


@dataclass
class MemoryConfig:
    """Memory configuration.

    Attributes:
        mb: RAM in megabytes (4–1,048,576).
        vram_mb: Video RAM in megabytes (1–256).
        page_fusion: Enable kernel same-page merging to reduce physical RAM usage.
        ballooning: Enable dynamic memory ballooning (guest must support it).
    """

    mb: int = 2048  # Memory in MB
    vram_mb: int = 16  # Video RAM in MB
    page_fusion: bool = False
    ballooning: bool = False

    def to_dict(self) -> dict:
        """Return memory configuration as a plain dictionary.

        Returns:
            dict: All fields with their current values.
        """
        return asdict(self)


@dataclass
class FirmwareConfig:
    """Firmware configuration.

    Attributes:
        type: Firmware type (BIOS, EFI, EFI64, EFI32).
        secure_boot: Enable Secure Boot (requires EFI firmware).
        tpm: Enable TPM chip emulation.
    """

    type: FirmwareType = FirmwareType.BIOS
    secure_boot: bool = False
    tpm: bool = False

    def to_dict(self) -> dict:
        """Return firmware configuration as a plain dictionary.

        Enum values are serialised to their string representations.

        Returns:
            dict: Fields with ``type`` as a string value.
        """
        result = asdict(self)
        result['type'] = self.type.value
        return result


@dataclass
class DiskConfig:
    """Disk configuration.

    Attributes:
        name: Unique identifier for this disk within the VM (e.g. ``"system"``, ``"data"``).
        size_mb: Disk capacity in megabytes (10–1,048,576).
        type: Media type — HDD, SSD, or DVD.
        format: Image file format — VDI, VMDK, VHD, or RAW.
        variant: Allocation strategy — THIN (dynamic) or THICK (fixed).
        controller: Storage bus type the disk is attached to.
        controller_name: Exact VirtualBox controller name (e.g. ``"SATA Controller"``).
        port: Controller port number (0-based).
        device: Device number on the port (0 or 1).
        bootable: Mark this disk as a boot device.
        disk_path: Original image path on the source system (not exported).
        source: Existing medium to attach for removable devices (e.g. an ISO
            path for a DVD drive). Ignored for non-removable disks.
    """

    name: str
    size_mb: int = 20480  # 20GB default
    type: DiskType = DiskType.HDD
    format: DiskFormat = DiskFormat.VDI  # Disk image format
    variant: DiskVariant = DiskVariant.THIN  # Thin provisioned by default
    controller: StorageControllerType = StorageControllerType.SATA
    controller_name: str = "SATA"  # Actual controller name for VirtualBox
    port: int = 0
    device: int = 0
    bootable: bool = False
    disk_path: Optional[str] = None  # Original disk path (for reference)
    source: Optional[str] = None  # Existing medium to attach (ISO for DVD, etc.)

    @property
    def is_removable(self) -> bool:
        """True for devices whose medium is inserted, not created.

        A DVD or floppy drive is attached either empty or pointing at an
        existing image; vmctl must never create a medium for one.
        """
        return self.type in (DiskType.DVD, DiskType.FLOPPY)

    def to_dict(self) -> dict:
        """Return disk configuration as a plain dictionary.

        Enum values are serialised to their string representations.
        ``disk_path`` is excluded because it is host-specific.

        Returns:
            dict: Exportable fields with enum values as strings.
        """
        result = asdict(self)
        result['type'] = self.type.value
        result['format'] = self.format.value
        result['variant'] = self.variant.value
        result['controller'] = self.controller.value
        # Don't include disk_path in export (it's system-specific)
        result.pop('disk_path', None)
        # `source` is only meaningful for removable media; omit it otherwise so
        # exports of ordinary disks keep their 1.1.x shape.
        if not self.is_removable or self.source is None:
            result.pop('source', None)
        return result


@dataclass
class NetworkConfig:
    """Network adapter configuration.

    Attributes:
        adapter_type: NIC chipset emulation (e.g. ``"82540EM"`` for Intel PRO/1000 MT Desktop).
        network_type: Connection mode (NAT, BRIDGED, HOSTONLY, INTERNAL, NATNETWORK).
        adapter_name: Physical or virtual interface name used for BRIDGED / HOSTONLY modes.
        mac_address: Custom MAC address; ``None`` lets VirtualBox assign one automatically.
        promiscuous_mode: Allow the adapter to receive packets not addressed to it.
    """

    adapter_type: str = "82540EM"  # Default Intel PRO/1000 MT Desktop
    network_type: NetworkType = NetworkType.NAT
    adapter_name: Optional[str] = None  # For bridged/host-only
    mac_address: Optional[str] = None
    promiscuous_mode: bool = False

    def to_dict(self) -> dict:
        """Return network configuration as a plain dictionary.

        Returns:
            dict: Fields with ``network_type`` as a string value.
        """
        result = asdict(self)
        result['network_type'] = self.network_type.value
        return result


@dataclass
class BootConfig:
    """Boot configuration.

    Attributes:
        order: Ordered list of boot devices; valid values are
            ``"disk"``, ``"dvd"``, ``"floppy"``, ``"network"``, ``"none"``.
        boot1–boot4: Individual boot slots derived from ``order``.
        acpi: Enable ACPI support (required by most modern OSes).
        ioapic: Enable I/O APIC (required for more than one CPU or for Windows).
        hpet: Enable High Precision Event Timer.
    """

    order: List[str] = field(default_factory=lambda: ["disk", "dvd", "none"])
    boot1: str = "disk"
    boot2: str = "dvd"
    boot3: str = "none"
    boot4: str = "none"
    acpi: bool = True
    ioapic: bool = False
    hpet: bool = False

    def to_dict(self) -> dict:
        """Return boot configuration as a plain dictionary.

        Returns:
            dict: All fields with their current values.
        """
        return asdict(self)


@dataclass
class StorageControllerConfig:
    """Storage controller configuration.

    Attributes:
        name: Unique controller name as it appears in VirtualBox
            (e.g. ``"SATA Controller"``).
        controller_type: Bus/chipset type (IDE, SATA, SCSI, SAS).
        port_count: Number of available device ports.
        bootable: Mark this controller as capable of booting.
    """

    name: str
    controller_type: StorageControllerType
    port_count: int = 30
    bootable: bool = False

    def to_dict(self) -> dict:
        """Return storage controller configuration as a plain dictionary.

        Returns:
            dict: Fields with ``controller_type`` as a string value.
        """
        result = asdict(self)
        result['controller_type'] = self.controller_type.value
        return result


@dataclass
class VMConfig:
    """Canonical VM configuration - the center of gravity"""
    name: str
    cpu: CPUConfig
    memory: MemoryConfig
    firmware: FirmwareConfig
    disks: List[DiskConfig]
    networks: List[NetworkConfig]
    boot: BootConfig
    storage_controllers: List[StorageControllerConfig]
    ostype: str = "Ubuntu_64"
    description: Optional[str] = None
    audio_enabled: bool = False
    clipboard_mode: str = "disabled"
    draganddrop: str = "disabled"
    usb_enabled: bool = False
    rtc_utc: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self):
        """Ensure the VM has at least one disk after construction.

        If ``disks`` is empty a default system disk named ``<vm_name>_system``
        is created automatically with all default ``DiskConfig`` values.
        """
        # Ensure at least one disk exists
        if not self.disks:
            self.disks = [DiskConfig(name=f"{self.name}_system")]
    
    def to_dict(self) -> dict:
        """Convert VMConfig to dictionary for serialization"""
        result = {
            'name': self.name,
            'ostype': self.ostype,
            'description': self.description,
            'cpu': self.cpu.to_dict(),
            'memory': self.memory.to_dict(),
            'firmware': self.firmware.to_dict(),
            'disks': [disk.to_dict() for disk in self.disks],
            'networks': [net.to_dict() for net in self.networks],
            'boot': self.boot.to_dict(),
            'storage_controllers': [sc.to_dict() for sc in self.storage_controllers],
            'audio_enabled': self.audio_enabled,
            'clipboard_mode': self.clipboard_mode,
            'draganddrop': self.draganddrop,
            'usb_enabled': self.usb_enabled,
            'rtc_utc': self.rtc_utc,
            'metadata': self.metadata
        }
        return result
    
    @classmethod
    def from_dict(cls, data: dict) -> 'VMConfig':
        """Create VMConfig from dictionary"""
        # Convert string enums back to Enum types
        if 'firmware' in data and 'type' in data['firmware']:
            data['firmware']['type'] = FirmwareType(data['firmware']['type'])
        
        if 'disks' in data:
            for disk in data['disks']:
                if 'type' in disk:
                    disk['type'] = DiskType(disk['type'])
                if 'format' in disk:
                    disk['format'] = DiskFormat(disk['format'])
                if 'variant' in disk:
                    disk['variant'] = DiskVariant(disk['variant'])
                if 'controller' in disk:
                    disk['controller'] = StorageControllerType(disk['controller'])
        
        if 'networks' in data:
            for net in data['networks']:
                if 'network_type' in net:
                    net['network_type'] = NetworkType(net['network_type'])
        
        if 'storage_controllers' in data:
            for sc in data['storage_controllers']:
                if 'controller_type' in sc:
                    sc['controller_type'] = StorageControllerType(sc['controller_type'])
        
        # Create nested objects
        cpu = CPUConfig(**data.pop('cpu', {}))
        memory = MemoryConfig(**data.pop('memory', {}))
        firmware = FirmwareConfig(**data.pop('firmware', {}))
        boot = BootConfig(**data.pop('boot', {}))
        
        disks = [DiskConfig(**disk) for disk in data.pop('disks', [])]
        networks = [NetworkConfig(**net) for net in data.pop('networks', [])]
        storage_controllers = [StorageControllerConfig(**sc) for sc in data.pop('storage_controllers', [])]
        
        return cls(
            cpu=cpu,
            memory=memory,
            firmware=firmware,
            disks=disks,
            networks=networks,
            boot=boot,
            storage_controllers=storage_controllers,
            **data
        )
