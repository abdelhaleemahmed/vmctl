# providers/virtualbox/capabilities.py
"""
VirtualBox capabilities and constraints
"""
from typing import Dict, Any


class VirtualBoxCapabilities:
    """VirtualBox-specific capabilities and limits"""
    
    @staticmethod
    def get_capabilities() -> Dict[str, Any]:
        """Get VirtualBox capabilities"""
        return {
            'max_cpus': 128,
            'max_memory_mb': 1_048_576,  # 1 TB
            'max_vram_mb': 256,
            'max_network_adapters': 8,
            'max_disks': 255,  # Theoretical limit per controller
            'supports_efi': True,
            'supports_secure_boot': True,
            'supports_tpm': True,
            'supported_os_types': [
                'Ubuntu_64', 'Ubuntu', 'Windows10_64', 'Windows10',
                'RedHat_64', 'RedHat', 'Debian_64', 'Debian',
                'Fedora_64', 'Fedora', 'CentOS_64', 'CentOS',
                'Linux_64', 'Linux', 'Other_64', 'Other'
            ],
            'max_ports_per_controller': {
                'ide': 30,    # VirtualBox reports various counts
                'sata': 30,
                'scsi': 254,
                'sas': 255,
                'nvme': 255
            },
            'supported_network_types': [
                'nat', 'bridged', 'hostonly', 'internal', 'natnetwork'
            ],
            'supported_storage_controllers': ['ide', 'sata', 'scsi', 'sas']
        }
