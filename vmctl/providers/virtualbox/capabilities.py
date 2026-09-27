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
            # Minimum VirtualBox this provider targets. 7.0 is the floor because
            # the emitter relies on things that do not exist earlier:
            # `modifynvram enrollmssignatures` for secure boot, `--tpm-type`,
            # and the `virtio-scsi` controller bus. Everything vmctl emits was
            # verified against 7.1.18 on a real host.
            "min_version": "7.0",
            "max_cpus": 128,
            "max_memory_mb": 1_048_576,  # 1 TB
            "max_vram_mb": 256,
            "max_network_adapters": 8,
            "max_disks": 255,  # Theoretical limit per controller
            "supports_efi": True,
            "supports_secure_boot": True,
            "supports_tpm": True,
            "supported_os_types": [
                "Ubuntu_64",
                "Ubuntu",
                "Windows10_64",
                "Windows10",
                "RedHat_64",
                "RedHat",
                "Debian_64",
                "Debian",
                "Fedora_64",
                "Fedora",
                "CentOS_64",
                "CentOS",
                "Linux_64",
                "Linux",
                "Other_64",
                "Other",
            ],
            # Maximum ports per bus. The floppy and USB figures were verified
            # on VirtualBox 7.1.18 by creating controllers on a live host: a
            # USB controller accepts exactly 8 ports ("must be in range
            # [8, 8]"), and only one floppy controller may exist.
            "max_ports_per_controller": {
                "ide": 2,
                "sata": 30,
                "scsi": 16,
                "sas": 255,
                "nvme": 255,
                "floppy": 1,
                "usb": 8,
                "virtio-scsi": 256,
            },
            "devices_per_port": {"ide": 2},  # master/slave; every other bus is 1
            "supported_network_types": ["nat", "bridged", "hostonly", "internal", "natnetwork"],
            # Verified against `VBoxManage storagectl --help` on 7.1.18. Note
            # that virtio-scsi is a valid --add value even though the help text
            # omits it.
            "supported_storage_controllers": [
                "ide",
                "sata",
                "scsi",
                "sas",
                "nvme",
                "floppy",
                "usb",
                "virtio-scsi",
            ],
        }
