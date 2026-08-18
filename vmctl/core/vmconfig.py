"""
Core VM configuration models - the center of gravity
"""
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict, Any
from enum import Enum


class FirmwareType(Enum):
    BIOS = "bios"
    EFI = "efi"
    EFI64 = "efi64"
    EFI32 = "efi32"


class DiskType(Enum):
    HDD = "hdd"
    SSD = "ssd"
    DVD = "dvd"


class DiskVariant(Enum):
    THIN = "thin"      # Dynamic/Standard - grows as needed
    THICK = "thick"    # Fixed - preallocated


class DiskFormat(Enum):
    VDI = "vdi"        # VirtualBox native format
    VMDK = "vmdk"      # VMware format (also supported by VirtualBox)
    VHD = "vhd"        # Microsoft Virtual Hard Disk
    RAW = "raw"        # Raw disk image


class NetworkType(Enum):
    NAT = "nat"
    BRIDGED = "bridged"
    HOSTONLY = "hostonly"
    INTERNAL = "internal"
    NATNETWORK = "natnetwork"


class StorageControllerType(Enum):
    IDE = "ide"
    SATA = "sata"
    SCSI = "scsi"
    SAS = "sas"


@dataclass
class CPUConfig:
    """CPU configuration"""
    count: int = 2
    hotplug: bool = False
    execution_cap: int = 100  # Percentage
    pae: bool = False  # Physical Address Extension
    nested_virt: bool = False
    
    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class MemoryConfig:
    """Memory configuration"""
    mb: int = 2048  # Memory in MB
    vram_mb: int = 16  # Video RAM in MB
    page_fusion: bool = False
    ballooning: bool = False
    
    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class FirmwareConfig:
    """Firmware configuration"""
    type: FirmwareType = FirmwareType.BIOS
    secure_boot: bool = False
    tpm: bool = False
    
    def to_dict(self) -> dict:
        result = asdict(self)
        result['type'] = self.type.value
        return result


@dataclass
class DiskConfig:
    """Disk configuration"""
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

    def to_dict(self) -> dict:
        result = asdict(self)
        result['type'] = self.type.value
        result['format'] = self.format.value
        result['variant'] = self.variant.value
        result['controller'] = self.controller.value
        # Don't include disk_path in export (it's system-specific)
        result.pop('disk_path', None)
        return result


@dataclass
class NetworkConfig:
    """Network adapter configuration"""
    adapter_type: str = "82540EM"  # Default Intel PRO/1000 MT Desktop
    network_type: NetworkType = NetworkType.NAT
    adapter_name: Optional[str] = None  # For bridged/host-only
    mac_address: Optional[str] = None
    promiscuous_mode: bool = False
    
    def to_dict(self) -> dict:
        result = asdict(self)
        result['network_type'] = self.network_type.value
        return result


@dataclass
class BootConfig:
    """Boot configuration"""
    order: List[str] = field(default_factory=lambda: ["disk", "dvd", "none"])
    boot1: str = "disk"
    boot2: str = "dvd"
    boot3: str = "none"
    boot4: str = "none"
    acpi: bool = True
    ioapic: bool = False
    hpet: bool = False
    
    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class StorageControllerConfig:
    """Storage controller configuration"""
    name: str
    controller_type: StorageControllerType
    port_count: int = 30
    bootable: bool = False
    
    def to_dict(self) -> dict:
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
