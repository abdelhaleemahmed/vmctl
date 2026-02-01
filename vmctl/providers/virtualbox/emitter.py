# providers/virtualbox/emitter.py
"""
Emit VirtualBox commands from VMConfig
"""
from typing import List, Dict, Any
from ...core.vmconfig import (
    VMConfig, DiskConfig, DiskFormat, DiskVariant, NetworkConfig, NetworkType,
    StorageControllerConfig, StorageControllerType
)
from ...core.exceptions import ProviderError


class VirtualBoxEmitter:
    """Emit VBoxManage commands to create/configure VMs"""
    
    def __init__(self, vm_name: str):
        self.vm_name = vm_name
        self.commands = []
    
    def emit_create_vm(self, vm: VMConfig) -> List[List[str]]:
        """Generate commands to create a VM from VMConfig"""
        commands = []

        # Map display names to internal VirtualBox OS type names
        ostype_map = {
            # Red Hat / RHEL
            'Red Hat (64-bit)': 'RedHat_64',
            'Red Hat (32-bit)': 'RedHat',
            'Red Hat': 'RedHat',
            # Rocky Linux (uses RedHat type)
            'Rocky Linux (64-bit)': 'RedHat_64',
            'Rocky Linux': 'RedHat_64',
            # CentOS
            'CentOS (64-bit)': 'RedHat_64',
            'CentOS': 'RedHat',
            # Fedora
            'Fedora (64-bit)': 'Fedora_64',
            'Fedora (32-bit)': 'Fedora',
            'Fedora': 'Fedora',
            # Ubuntu
            'Ubuntu (64-bit)': 'Ubuntu_64',
            'Ubuntu (32-bit)': 'Ubuntu',
            'Ubuntu': 'Ubuntu',
            # Debian
            'Debian (64-bit)': 'Debian_64',
            'Debian (32-bit)': 'Debian',
            'Debian': 'Debian',
            # Linux Generic
            'Linux 2.6 / 3.x / 4.x (64-bit)': 'Linux26_64',
            'Linux 2.6 / 3.x / 4.x (32-bit)': 'Linux26',
            'Other Linux (64-bit)': 'Linux_64',
            'Other Linux (32-bit)': 'Linux',
            'Other Linux': 'Linux',
            # Windows
            'Windows 10 (64-bit)': 'Windows10_64',
            'Windows 10 (32-bit)': 'Windows10',
            'Windows 11 (64-bit)': 'Windows11_64',
            'Windows Server 2019 (64-bit)': 'Windows2019_64',
            'Windows Server 2016 (64-bit)': 'Windows2016_64',
            # Other
            'Other (64-bit)': 'Other_64',
            'Other (32-bit)': 'Other',
        }

        # Normalize ostype - use mapping or original if already internal format
        ostype = ostype_map.get(vm.ostype, vm.ostype)

        # Create VM
        commands.append([
            "VBoxManage", "createvm",
            "--name", vm.name,
            "--ostype", ostype,
            "--register"
        ])
        
        # Basic configuration
        commands.append([
            "VBoxManage", "modifyvm", vm.name,
            "--memory", str(vm.memory.mb),
            "--vram", str(vm.memory.vram_mb),
            "--cpus", str(vm.cpu.count),
            "--firmware", vm.firmware.type.value,
            "--acpi", "on" if vm.boot.acpi else "off",
            "--ioapic", "on" if vm.boot.ioapic else "off",
            "--rtcuseutc", "on" if vm.rtc_utc else "off"
        ])
        
        # Firmware settings
        if vm.firmware.secure_boot:
            commands.append([
                "VBoxManage", "modifyvm", vm.name,
                "--secureboot", "on"
            ])
        
        # Add storage controllers
        for controller in vm.storage_controllers:
            commands.append(self._create_storage_controller(controller))
        
        # Create and attach disks
        # Disks are created in the VM's folder using VirtualBox's path resolution
        for disk in vm.disks:
            # Map disk format to VirtualBox format and file extension
            format_map = {
                DiskFormat.VDI: ('VDI', 'vdi'),
                DiskFormat.VMDK: ('VMDK', 'vmdk'),
                DiskFormat.VHD: ('VHD', 'vhd'),
                DiskFormat.RAW: ('RAW', 'img'),
            }
            vbox_format, ext = format_map.get(disk.format, ('VDI', 'vdi'))

            # Use VM name as folder to place disk in VM's directory
            # VirtualBox will resolve this to the full path in the default machine folder
            disk_filename = f"{vm.name}/{vm.name}_{disk.name}.{ext}"

            # Map our variant to VirtualBox variant
            vbox_variant = "Standard"  # Thin/dynamic by default
            if disk.variant == DiskVariant.THICK:
                vbox_variant = "Fixed"

            commands.append([
                "VBoxManage", "createhd",
                "--filename", disk_filename,
                "--size", str(disk.size_mb),
                "--format", vbox_format,
                "--variant", vbox_variant
            ])

            # Attach disk - use controller_name if set, otherwise find matching controller
            ctrl_name = disk.controller_name
            if ctrl_name == "SATA":  # Default value, try to find actual controller
                for sc in vm.storage_controllers:
                    if sc.controller_type == disk.controller:
                        ctrl_name = sc.name
                        break

            commands.append([
                "VBoxManage", "storageattach", vm.name,
                "--storagectl", ctrl_name,
                "--port", str(disk.port),
                "--device", str(disk.device),
                "--type", "hdd" if disk.type.value == "hdd" else "dvddrive",
                "--medium", disk_filename
            ])
        
        # Configure network adapters
        for i, network in enumerate(vm.networks):
            commands.append(self._configure_network_adapter(i + 1, network))
        
        # Boot order
        for i, device in enumerate(vm.boot.order[:4], 1):
            if device != "none":
                commands.append([
                    "VBoxManage", "modifyvm", vm.name,
                    f"--boot{i}", device
                ])
        
        # Audio
        if vm.audio_enabled:
            commands.append([
                "VBoxManage", "modifyvm", vm.name,
                "--audio", "pulse",
                "--audiocontroller", "hda"
            ])
        
        # USB
        if vm.usb_enabled:
            commands.append([
                "VBoxManage", "modifyvm", vm.name,
                "--usb", "on",
                "--usbehci", "on"
            ])
        
        # Description
        if vm.description:
            commands.append([
                "VBoxManage", "modifyvm", vm.name,
                "--description", f'"{vm.description}"'
            ])
        
        return commands
    
    def _create_storage_controller(self, controller: StorageControllerConfig) -> List[str]:
        """Generate command to create storage controller"""
        # Map our controller types to VirtualBox controller types
        controller_type_map = {
            StorageControllerType.IDE: ('ide', 'PIIX4'),
            StorageControllerType.SATA: ('sata', 'IntelAhci'),
            StorageControllerType.SCSI: ('scsi', 'LsiLogic'),
            StorageControllerType.SAS: ('sas', 'LsiLogicSas'),
        }
        bus_type, vbox_controller = controller_type_map.get(
            controller.controller_type,
            ('sata', 'IntelAhci')
        )

        return [
            "VBoxManage", "storagectl", self.vm_name,
            "--name", controller.name,
            "--add", bus_type,
            "--controller", vbox_controller,
            "--portcount", str(controller.port_count),
            "--bootable", "on" if controller.bootable else "off"
        ]
    
    def _configure_network_adapter(self, adapter_num: int, network: NetworkConfig) -> List[str]:
        """Generate command to configure network adapter"""
        # Map our NetworkType to VirtualBox network type names
        network_type_map = {
            NetworkType.NAT: 'nat',
            NetworkType.BRIDGED: 'bridged',
            NetworkType.HOSTONLY: 'hostonly',
            NetworkType.INTERNAL: 'intnet',
            NetworkType.NATNETWORK: 'natnetwork',
        }
        vbox_net_type = network_type_map.get(network.network_type, 'nat')

        cmd = [
            "VBoxManage", "modifyvm", self.vm_name,
            f"--nic{adapter_num}", vbox_net_type,
            f"--nictype{adapter_num}", network.adapter_type,
            f"--cableconnected{adapter_num}", "on"
        ]

        if network.network_type == NetworkType.BRIDGED and network.adapter_name:
            cmd.extend([f"--bridgeadapter{adapter_num}", network.adapter_name])
        
        if network.mac_address:
            cmd.extend([f"--macaddress{adapter_num}", network.mac_address])
        
        if network.promiscuous_mode:
            cmd.extend([f"--promiscuous{adapter_num}", "allow-all"])
        
        return cmd
