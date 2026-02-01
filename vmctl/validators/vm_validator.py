"""
Validation layer for VM configurations
"""
from typing import List, Dict, Any
from enum import Enum
from ..core.exceptions import ValidationError
from ..core.vmconfig import VMConfig, FirmwareType


class VMValidator:
    """Validate VM configurations against schema and provider constraints"""
    
    def __init__(self, provider_capabilities: Dict[str, Any]):
        self.capabilities = provider_capabilities
    
    def validate(self, vm: VMConfig) -> List[str]:
        """Validate VM configuration, return list of warnings"""
        warnings = []
        
        try:
            self._validate_schema(vm)
            self._validate_provider_limits(vm)
            self._validate_logical_constraints(vm)
        except ValidationError as e:
            raise e
        
        return warnings
    
    def _validate_schema(self, vm: VMConfig):
        """Basic schema validation"""
        if not vm.name or not isinstance(vm.name, str):
            raise ValidationError("VM name must be a non-empty string")
        
        if vm.cpu.count < 1:
            raise ValidationError("CPU count must be >= 1")
        
        if vm.memory.mb < 128:
            raise ValidationError("Memory must be at least 128 MB")
        
        if vm.memory.vram_mb < 1:
            raise ValidationError("VRAM must be at least 1 MB")
        
        # Validate disk sizes
        for i, disk in enumerate(vm.disks):
            if disk.size_mb < 10:
                raise ValidationError(f"Disk {i} ({disk.name}): Size must be at least 10 MB")
            
            if disk.size_mb > 1024 * 1024:  # 1 TB
                raise ValidationError(f"Disk {i} ({disk.name}): Size exceeds 1 TB limit")
        
        # Validate network adapters
        for i, net in enumerate(vm.networks):
            if i >= 8:  # VirtualBox supports up to 8 adapters
                raise ValidationError(f"Maximum 8 network adapters supported, got {len(vm.networks)}")
    
    def _validate_provider_limits(self, vm: VMConfig):
        """Validate against provider (VirtualBox) limits"""
        caps = self.capabilities
        
        # CPU limits
        if vm.cpu.count > caps.get('max_cpus', 128):
            raise ValidationError(f"CPU count {vm.cpu.count} exceeds provider limit {caps.get('max_cpus', 128)}")
        
        # Memory limits
        max_memory = caps.get('max_memory_mb', 1_048_576)  # Default 1 TB
        if vm.memory.mb > max_memory:
            raise ValidationError(f"Memory {vm.memory.mb}MB exceeds provider limit {max_memory}MB")
        
        # VRAM limits
        max_vram = caps.get('max_vram_mb', 256)
        if vm.memory.vram_mb > max_vram:
            raise ValidationError(f"VRAM {vm.memory.vram_mb}MB exceeds provider limit {max_vram}MB")
        
        # Firmware support
        if vm.firmware.type == FirmwareType.EFI:
            if not caps.get('supports_efi', True):
                raise ValidationError("EFI firmware not supported by provider")
        
        # Storage controller limits
        for sc in vm.storage_controllers:
            max_ports = caps.get('max_ports_per_controller', {}).get(sc.controller_type.value, 30)
            if sc.port_count > max_ports:
                raise ValidationError(f"Port count {sc.port_count} exceeds limit {max_ports} for {sc.controller_type.value}")
    
    def _validate_logical_constraints(self, vm: VMConfig):
        """Validate logical constraints and best practices"""
        
        # Memory alignment (power of 2 is often better)
        if vm.memory.mb % 4 != 0:
            # Just a warning would go here, not an error
            pass
        
        # Disk naming uniqueness
        disk_names = [d.name for d in vm.disks]
        if len(disk_names) != len(set(disk_names)):
            raise ValidationError("Disk names must be unique")
        
        # Storage controller uniqueness
        sc_names = [sc.name for sc in vm.storage_controllers]
        if len(sc_names) != len(set(sc_names)):
            raise ValidationError("Storage controller names must be unique")
        
        # Boot order validation
        valid_boot_devices = ["none", "floppy", "dvd", "disk", "network"]
        for device in vm.boot.order:
            if device not in valid_boot_devices:
                raise ValidationError(f"Invalid boot device: {device}. Must be one of {valid_boot_devices}")
        
        # Ensure at least one bootable disk if booting from disk
        if "disk" in vm.boot.order:
            bootable_disks = [d for d in vm.disks if d.bootable]
            if not bootable_disks:
                # Make first disk bootable automatically
                if vm.disks:
                    vm.disks[0].bootable = True
                else:
                    raise ValidationError("No disks defined, but boot order includes 'disk'")
