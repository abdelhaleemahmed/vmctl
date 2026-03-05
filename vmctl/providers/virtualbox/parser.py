# providers/virtualbox/parser.py
"""
Parse VirtualBox VM configuration into VMConfig
"""
import subprocess
import re
from typing import Dict, Any, List, Optional
from ...core.vmconfig import (
    VMConfig, CPUConfig, MemoryConfig, FirmwareConfig, FirmwareType,
    DiskConfig, DiskType, DiskFormat, DiskVariant, NetworkConfig, NetworkType,
    BootConfig, StorageControllerConfig, StorageControllerType
)
from ...core.exceptions import ProviderError


class VirtualBoxParser:
    """Parse VirtualBox VM configuration"""
    
    def __init__(self):
        self.vboxmanage_cmd = "VBoxManage"
    
    def get_vm_info(self, vm_name: str) -> str:
        """Get raw VM info from VirtualBox"""
        try:
            result = subprocess.run(
                [self.vboxmanage_cmd, "showvminfo", vm_name, "--machinereadable"],
                capture_output=True,
                text=True,
                check=True
            )
            return result.stdout
        except subprocess.CalledProcessError as e:
            raise ProviderError(f"Failed to get VM info for {vm_name}: {e}")
        except FileNotFoundError:
            raise ProviderError("VBoxManage not found. Is VirtualBox installed?")

    def get_disk_info(self, disk_path: str) -> Dict[str, Any]:
        """Get disk size, format, and variant from VirtualBox"""
        # Detect format from file extension as fallback
        format_map = {
            '.vdi': DiskFormat.VDI,
            '.vmdk': DiskFormat.VMDK,
            '.vhd': DiskFormat.VHD,
            '.raw': DiskFormat.RAW,
            '.img': DiskFormat.RAW,
        }
        ext = disk_path.lower().rsplit('.', 1)[-1] if '.' in disk_path else ''
        default_format = format_map.get(f'.{ext}', DiskFormat.VDI)

        try:
            result = subprocess.run(
                [self.vboxmanage_cmd, "showmediuminfo", disk_path],
                capture_output=True,
                text=True,
                check=True
            )

            disk_info = {
                'size_mb': 20480,  # Default 20GB
                'format': default_format,
                'variant': DiskVariant.THIN
            }

            for line in result.stdout.splitlines():
                line = line.strip()
                # Capacity: 20480 MBytes
                if line.startswith('Capacity:'):
                    match = re.search(r'(\d+)\s*MBytes', line)
                    if match:
                        disk_info['size_mb'] = int(match.group(1))
                # Storage format: VMDK or VDI
                elif line.startswith('Storage format:'):
                    fmt_str = line.split(':', 1)[1].strip().upper()
                    if fmt_str == 'VMDK':
                        disk_info['format'] = DiskFormat.VMDK
                    elif fmt_str == 'VDI':
                        disk_info['format'] = DiskFormat.VDI
                    elif fmt_str == 'VHD':
                        disk_info['format'] = DiskFormat.VHD
                    elif fmt_str in ('RAW', 'IMG'):
                        disk_info['format'] = DiskFormat.RAW
                # Variant: Standard (dynamic)
                elif line.startswith('Variant:'):
                    variant_str = line.split(':', 1)[1].strip().lower()
                    if 'fixed' in variant_str:
                        disk_info['variant'] = DiskVariant.THICK
                    else:
                        disk_info['variant'] = DiskVariant.THIN

            return disk_info

        except subprocess.CalledProcessError:
            # If we can't get disk info, return defaults with format from extension
            return {'size_mb': 20480, 'format': default_format, 'variant': DiskVariant.THIN}
        except FileNotFoundError:
            return {'size_mb': 20480, 'format': default_format, 'variant': DiskVariant.THIN}

    def parse_vm(self, vm_name: str) -> VMConfig:
        """Parse VirtualBox VM into VMConfig"""
        raw_info = self.get_vm_info(vm_name)
        config_dict = self._parse_machinereadable(raw_info)
        
        # Convert to VMConfig
        return self._dict_to_vmconfig(vm_name, config_dict)
    
    def _parse_machinereadable(self, raw_info: str) -> Dict[str, str]:
        """Parse machine-readable output into dictionary"""
        config = {}

        for line in raw_info.splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue

            # Match two patterns:
            # 1. key="value" or key=value (simple keys)
            # 2. "key with spaces"="value" (quoted keys like disk attachments)

            # Try quoted key first: "key"="value"
            match = re.match(r'^"([^"]+)"="([^"]*)"$', line)
            if match:
                key, value = match.groups()
                config[key] = value
                continue

            # Try simple key: key="value" or key=value
            match = re.match(r'^(\w+)="?(.*?)"?$', line)
            if match:
                key, value = match.groups()
                # Remove quotes if present
                if value.startswith('"') and value.endswith('"'):
                    value = value[1:-1]
                config[key] = value

        return config
    
    def _dict_to_vmconfig(self, vm_name: str, config: Dict[str, str]) -> VMConfig:
        """Convert parsed dictionary to VMConfig"""
        
        # CPU
        cpu = CPUConfig(
            count=int(config.get('cpus', 2)),
            pae=config.get('pae', 'off') == 'on',
            nested_virt=config.get('nested-hw-virt', 'off') == 'on'
        )
        
        # Memory
        memory = MemoryConfig(
            mb=int(config.get('memory', 2048)),
            vram_mb=int(config.get('vram', 16))
        )
        
        # Firmware
        firmware_type = FirmwareType.BIOS
        if config.get('firmware', 'bios') == 'efi':
            firmware_type = FirmwareType.EFI
        
        firmware = FirmwareConfig(
            type=firmware_type,
            secure_boot=config.get('secureboot', 'off') == 'on'
        )
        
        # Boot
        boot = BootConfig(
            order=[
                config.get('boot1', 'disk'),
                config.get('boot2', 'dvd'),
                config.get('boot3', 'none'),
                config.get('boot4', 'none')
            ],
            acpi=config.get('acpi', 'on') == 'on',
            ioapic=config.get('ioapic', 'off') == 'on'
        )
        
        # Parse disks and storage controllers
        disks = []
        storage_controllers = []
        
        # Parse storage controllers
        controller_pattern = re.compile(r'^storagecontrollername(\d+)$')
        controller_indices = set()
        
        for key, value in config.items():
            match = controller_pattern.match(key)
            if match:
                idx = match.group(1)
                controller_indices.add(idx)
        
        # Map VirtualBox controller types to our types
        vbox_controller_map = {
            'piix4': StorageControllerType.IDE,
            'piix3': StorageControllerType.IDE,
            'ich6': StorageControllerType.IDE,
            'intelahci': StorageControllerType.SATA,
            'lsilogic': StorageControllerType.SCSI,
            'buslogic': StorageControllerType.SCSI,
            'lsilogicsas': StorageControllerType.SAS,
            'nvme': StorageControllerType.SCSI,  # Map NVMe to SCSI for now
        }

        for idx in controller_indices:
            name = config.get(f'storagecontrollername{idx}', f'Controller{idx}')
            controller_type = config.get(f'storagecontrollertype{idx}', 'PIIX4')

            # Map VirtualBox controller type to our enum
            sc_type = vbox_controller_map.get(controller_type.lower(), StorageControllerType.SATA)

            sc = StorageControllerConfig(
                name=name,
                controller_type=sc_type,
                port_count=int(config.get(f'storagecontrollerportcount{idx}', 30))
            )
            storage_controllers.append(sc)
        
        # Parse disks - keys are stored without surrounding quotes
        # Match patterns like: "SATA Controller-0-0" stored as "SATA Controller-0-0"
        # VirtualBox outputs many metadata keys per disk (ImageUUID, nonrotational, discard, etc.)
        # We only want actual disk paths, so filter by checking the value looks like a path
        disk_pattern = re.compile(r'^(.+)-(\d+)-(\d+)$')
        disk_extensions = ('.vdi', '.vmdk', '.vhd', '.img', '.raw', '.iso')
        disk_attachments = {}

        for key, value in config.items():
            match = disk_pattern.match(key)
            if match:
                # Only process if value looks like a disk path (has disk extension)
                # This filters out metadata entries like ImageUUID, nonrotational, discard, etc.
                if value and value.lower().endswith(disk_extensions):
                    controller_name, port, device = match.groups()
                    # Skip floppy controller - not a real disk to recreate
                    if controller_name.lower() == 'floppy':
                        continue
                    disk_attachments[f"{controller_name}-{port}-{device}"] = value
        
        # Build controller name to type mapping
        controller_name_to_type = {sc.name: sc.controller_type for sc in storage_controllers}

        # Create disk configurations
        for attachment, disk_path in disk_attachments.items():
            # Use rsplit to handle controller names with dashes (e.g., "SATA-II Controller-0-0")
            ctrl_name, port, device = attachment.rsplit('-', 2)

            # Find controller type by name
            ctrl_type = controller_name_to_type.get(ctrl_name, StorageControllerType.SATA)

            # Skip empty/none attachments
            if not disk_path or disk_path == 'none':
                continue

            # Get actual disk size and variant from VirtualBox
            disk_info = self.get_disk_info(disk_path)

            disk_type = DiskType.DVD if disk_path.lower().endswith('.iso') else DiskType.HDD
            disk = DiskConfig(
                name=f"disk_{ctrl_name}_{port}_{device}",
                size_mb=disk_info['size_mb'],
                type=disk_type,
                format=disk_info['format'],
                variant=disk_info['variant'],
                controller=ctrl_type,
                controller_name=ctrl_name,
                port=int(port),
                device=int(device),
                bootable=(boot.order[0] == 'disk'),
                disk_path=disk_path
            )
            disks.append(disk)
        
        # Parse networks
        # Map VirtualBox network types to our NetworkType enum
        vbox_network_map = {
            'nat': NetworkType.NAT,
            'bridged': NetworkType.BRIDGED,
            'hostonly': NetworkType.HOSTONLY,
            'intnet': NetworkType.INTERNAL,
            'natnetwork': NetworkType.NATNETWORK,
            'none': None,
        }

        networks = []
        for i in range(8):  # VirtualBox supports up to 8 adapters
            nic_type = config.get(f'nic{i+1}', 'none')
            if nic_type != 'none':
                network_type = vbox_network_map.get(nic_type, NetworkType.NAT)
                adapter_type = config.get(f'nictype{i+1}', '82540EM')
                
                network = NetworkConfig(
                    adapter_type=adapter_type,
                    network_type=network_type,
                    adapter_name=config.get(f'bridgeadapter{i+1}'),
                    mac_address=config.get(f'macaddress{i+1}')
                )
                networks.append(network)
        
        return VMConfig(
            name=vm_name,
            cpu=cpu,
            memory=memory,
            firmware=firmware,
            disks=disks,
            networks=networks,
            boot=boot,
            storage_controllers=storage_controllers,
            ostype=config.get('ostype', 'Ubuntu_64'),
            description=config.get('description'),
            audio_enabled=config.get('audio', 'none') != 'none',
            usb_enabled=config.get('usb', 'off') == 'on',
            rtc_utc=config.get('rtcuseutc', 'on') == 'on'
        )
