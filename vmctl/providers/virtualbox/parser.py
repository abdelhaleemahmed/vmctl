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
from ..base import MediumProbe


class VirtualBoxParser:
    """Parse VirtualBox VM configuration"""
    
    def __init__(self):
        """Initialise the parser.

        Sets ``vboxmanage_cmd`` to ``"VBoxManage"``, which must be available
        on the system ``PATH``.
        """
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

    # -- pure decoding -------------------------------------------------------

    EXTENSION_FORMATS = {
        'vdi': DiskFormat.VDI,
        'vmdk': DiskFormat.VMDK,
        'vhd': DiskFormat.VHD,
        'raw': DiskFormat.RAW,
        'img': DiskFormat.RAW,
    }

    MEDIUM_FORMATS = {
        'VDI': DiskFormat.VDI,
        'VMDK': DiskFormat.VMDK,
        'VHD': DiskFormat.VHD,
        'RAW': DiskFormat.RAW,
        'IMG': DiskFormat.RAW,
    }

    FIRMWARE_TYPES = {
        'bios': FirmwareType.BIOS,
        'efi': FirmwareType.EFI,
        'efi32': FirmwareType.EFI32,
        'efi64': FirmwareType.EFI64,
    }

    #: Reported controller chipset -> canonical bus type. Verified against
    #: ``VBoxManage storagectl --help`` on VirtualBox 7.1.18: the full chipset
    #: set is BusLogic, I82078, ICH6, IntelAhci, LSILogic, LSILogicSAS, NVMe,
    #: PIIX3, PIIX4, USB, VirtIO.
    CONTROLLER_TYPES = {
        'piix3': StorageControllerType.IDE,
        'piix4': StorageControllerType.IDE,
        'ich6': StorageControllerType.IDE,
        'intelahci': StorageControllerType.SATA,
        'lsilogic': StorageControllerType.SCSI,
        'buslogic': StorageControllerType.SCSI,
        'lsilogicsas': StorageControllerType.SAS,
        'nvme': StorageControllerType.NVME,
        'i82078': StorageControllerType.FLOPPY,
        'usb': StorageControllerType.USB,
        'virtioscsi': StorageControllerType.VIRTIO_SCSI,
    }

    TRUTHY = {'on', 'true', 'yes', '1', 'enabled'}
    FALSY = {'off', 'false', 'no', '0', 'disabled'}

    @classmethod
    def _flag(cls, config: Dict[str, str], key: str, default: bool = False) -> bool:
        """Read a boolean flag, tolerating case and VirtualBox's spellings.

        Args:
            config: Decoded machine-readable values.
            key: Key to read.
            default: Value to use when the key is absent or unrecognised.

        Returns:
            bool: The flag's value.
        """
        raw = config.get(key)
        if raw is None:
            return default
        raw = raw.strip().lower()
        if raw in cls.TRUTHY:
            return True
        if raw in cls.FALSY:
            return False
        return default

    def default_format_for(self, disk_path: str) -> DiskFormat:
        """Guess a medium's format from its file extension.

        Used as the fallback when VirtualBox cannot be asked about the medium.

        Args:
            disk_path: Path to the medium file.

        Returns:
            DiskFormat: The format implied by the extension, or VDI.
        """
        ext = disk_path.lower().rsplit('.', 1)[-1] if '.' in disk_path else ''
        return self.EXTENSION_FORMATS.get(ext, DiskFormat.VDI)

    def parse_medium_info(self, raw_info: str, default_format: DiskFormat) -> Dict[str, Any]:
        """Decode ``VBoxManage showmediuminfo`` output.

        Pure function: takes the command's text and returns the medium's
        properties. Kept separate from :meth:`get_disk_info` so the same
        decoding runs in production and against captured fixtures in tests.

        Args:
            raw_info: Raw ``showmediuminfo`` output.
            default_format: Format to assume when the output does not state one.

        Returns:
            dict: ``size_mb``, ``format`` and ``variant`` keys.
        """
        disk_info = {
            'size_mb': 20480,  # Default 20GB
            'format': default_format,
            'variant': DiskVariant.THIN
        }

        for line in raw_info.splitlines():
            line = line.strip()
            lower = line.lower()
            # Capacity: 20480 MBytes
            if lower.startswith('capacity:'):
                match = re.search(r'(\d+)\s*MBytes', line)
                if match:
                    disk_info['size_mb'] = int(match.group(1))
            # Storage format: VMDK or VDI
            elif lower.startswith('storage format:'):
                fmt_str = line.split(':', 1)[1].strip().upper()
                if fmt_str in self.MEDIUM_FORMATS:
                    disk_info['format'] = self.MEDIUM_FORMATS[fmt_str]
            # VirtualBox 7.x prints "Format variant: fixed default"; older
            # releases printed "Variant: ...". Accept both (F-20).
            elif lower.startswith('format variant:') or lower.startswith('variant:'):
                variant_str = line.split(':', 1)[1].strip().lower()
                if 'fixed' in variant_str:
                    disk_info['variant'] = DiskVariant.THICK
                else:
                    disk_info['variant'] = DiskVariant.THIN

        return disk_info

    # -- transport -----------------------------------------------------------

    def get_disk_info(self, disk_path: str) -> Dict[str, Any]:
        """Get disk size, format, and variant from VirtualBox.

        This is the production :class:`~vmctl.providers.base.MediumProbe`: it
        runs ``showmediuminfo`` and hands the output to :meth:`parse_medium_info`.

        Args:
            disk_path: Path to the medium file.

        Returns:
            dict: ``size_mb``, ``format`` and ``variant`` keys. Falls back to
            defaults (with the format guessed from the extension) when the
            medium cannot be queried.
        """
        default_format = self.default_format_for(disk_path)

        try:
            result = subprocess.run(
                [self.vboxmanage_cmd, "showmediuminfo", disk_path],
                capture_output=True,
                text=True,
                check=True
            )
            return self.parse_medium_info(result.stdout, default_format)
        except subprocess.CalledProcessError:
            # If we can't get disk info, return defaults with format from extension
            return {'size_mb': 20480, 'format': default_format, 'variant': DiskVariant.THIN}
        except FileNotFoundError:
            return {'size_mb': 20480, 'format': default_format, 'variant': DiskVariant.THIN}

    def parse_vm(self, vm_name: str) -> VMConfig:
        """Read a VM from VirtualBox and parse it into a VMConfig.

        Acquires the text (transport) and delegates to :meth:`parse_text`.

        Args:
            vm_name: Name of the VM to read.

        Returns:
            VMConfig: The parsed configuration.

        Raises:
            ProviderError: If the VM cannot be read.
        """
        raw_info = self.get_vm_info(vm_name)
        return self.parse_text(vm_name, raw_info)

    def parse_text(
        self,
        vm_name: str,
        raw_info: str,
        probe: Optional[MediumProbe] = None,
    ) -> VMConfig:
        """Parse ``showvminfo --machinereadable`` text into a VMConfig.

        Pure with respect to the hypervisor: nothing here runs a command, so
        this is the entry point tests use against captured fixtures.

        Args:
            vm_name: Name to assign to the resulting configuration.
            raw_info: Raw ``showvminfo --machinereadable`` output.
            probe: Medium lookup to use. Defaults to :meth:`get_disk_info`,
                which shells out to VirtualBox.

        Returns:
            VMConfig: The parsed configuration.
        """
        config_dict = self._parse_machinereadable(raw_info)
        return self._dict_to_vmconfig(vm_name, config_dict, probe=probe)
    
    @staticmethod
    def _unescape(value: str) -> str:
        """Undo the escaping VBoxManage applies inside quoted values.

        ``showvminfo --machinereadable`` escapes backslashes and double quotes,
        so a Windows path arrives as ``C:\\\\vms\\\\disk.vdi``. Taking the value
        verbatim doubles every separator (F-16).
        """
        return value.replace('\\"', '"').replace('\\\\', '\\')

    def _parse_machinereadable(self, raw_info: str) -> Dict[str, str]:
        """Parse machine-readable output into dictionary"""
        config = {}

        for line in raw_info.splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue

            # Quoted key: "key"="value" -- disk attachments and anything whose
            # key contains spaces. Values may contain escaped quotes.
            match = re.match(r'^"((?:[^"\\]|\\.)+)"="((?:[^"\\]|\\.)*)"$', line)
            if match:
                key, value = match.groups()
                config[self._unescape(key)] = self._unescape(value)
                continue

            # Bare key: key="value" or key=value. VirtualBox uses hyphens and
            # dots in several keys (nested-hw-virt, cpu-profile, tpm-type), so
            # the key class cannot be \w+ alone (F-03).
            match = re.match(r'^([\w.\-]+)=(.*)$', line)
            if match:
                key, value = match.groups()
                if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
                    value = self._unescape(value[1:-1])
                config[key] = value

        return config
    
    def _dict_to_vmconfig(
        self,
        vm_name: str,
        config: Dict[str, str],
        probe: Optional[MediumProbe] = None,
    ) -> VMConfig:
        """Convert a parsed machine-readable dictionary into a VMConfig.

        Args:
            vm_name: Name to assign to the resulting configuration.
            config: Decoded ``key -> value`` pairs.
            probe: Medium lookup used for disk size/format/variant. Defaults to
                :meth:`get_disk_info`.

        Returns:
            VMConfig: The parsed configuration.
        """
        if probe is None:
            probe = self.get_disk_info
        
        # CPU
        cpu = CPUConfig(
            count=int(config.get('cpus', 2)),
            pae=self._flag(config, 'pae'),
            nested_virt=self._flag(config, 'nested-hw-virt')
        )
        
        # Memory
        memory = MemoryConfig(
            mb=int(config.get('memory', 2048)),
            vram_mb=int(config.get('vram', 16))
        )
        
        # Firmware. VirtualBox reports BIOS / EFI / EFI32 / EFI64 in upper case,
        # so the comparison must be case-insensitive and cover every value --
        # matching only lowercase 'efi' made every EFI VM look like BIOS (F-02).
        firmware = FirmwareConfig(
            type=self.FIRMWARE_TYPES.get(
                config.get('firmware', 'bios').strip().lower(),
                FirmwareType.BIOS,
            ),
            # VirtualBox 7.x does not report secure-boot state in
            # machine-readable output at all, so this is always False on read;
            # see F-17.
            secure_boot=self._flag(config, 'secureboot'),
        )
        
        # Boot
        boot = BootConfig(
            order=[
                config.get('boot1', 'disk'),
                config.get('boot2', 'dvd'),
                config.get('boot3', 'none'),
                config.get('boot4', 'none')
            ],
            acpi=self._flag(config, 'acpi', True),
            ioapic=self._flag(config, 'ioapic')
        )
        
        # Parse disks and storage controllers
        disks = []
        storage_controllers = []
        
        # Parse storage controllers.
        #
        # Indices are collected into a *sorted* list, not a set: VirtualBox
        # numbers controllers in a meaningful order, and downstream code
        # resolves a disk's controller by scanning this list. Iterating a set of
        # strings varies with PYTHONHASHSEED, which made both the emitted
        # command order and the controller a disk got attached to
        # non-deterministic between runs (F-14 in PLAN.md).
        controller_pattern = re.compile(r'^storagecontrollername(\d+)$')
        controller_indices = []

        for key, value in config.items():
            match = controller_pattern.match(key)
            if match:
                controller_indices.append(match.group(1))

        controller_indices = sorted(set(controller_indices), key=int)
        

        for idx in controller_indices:
            name = config.get(f'storagecontrollername{idx}', f'Controller{idx}')
            controller_type = config.get(f'storagecontrollertype{idx}', 'PIIX4')

            # Map the reported chipset to a bus type. An unrecognised chipset
            # must not silently become SATA: that made a floppy controller
            # (I82078) claim to be SATA and absorb disk attachments (F-15).
            sc_type = self.CONTROLLER_TYPES.get(controller_type.strip().lower())
            if sc_type is None:
                raise ProviderError(
                    f"Unknown storage controller type {controller_type!r} on "
                    f"controller {name!r}. Known types: "
                    f"{', '.join(sorted(self.CONTROLLER_TYPES))}."
                )

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

            # Decide what kind of device this is before probing. A floppy
            # controller carries floppy drives; an .iso is an optical medium.
            if ctrl_type == StorageControllerType.FLOPPY:
                disk_type = DiskType.FLOPPY
            elif disk_path.lower().endswith('.iso'):
                disk_type = DiskType.DVD
            else:
                disk_type = DiskType.HDD

            if disk_type in (DiskType.DVD, DiskType.FLOPPY):
                # Removable media are *inserted*, not created, so their size,
                # format and allocation carry no meaning for recreation. Do not
                # probe them either: `showmediuminfo <path>` defaults to a HDD
                # device type and fails outright for an ISO, which silently
                # yielded a bogus 20 GB VDI (F-18, feeding F-04).
                disk = DiskConfig(
                    name=f"disk_{ctrl_name}_{port}_{device}",
                    size_mb=0,
                    type=disk_type,
                    format=self.default_format_for(disk_path),
                    controller=ctrl_type,
                    controller_name=ctrl_name,
                    port=int(port),
                    device=int(device),
                    bootable=False,
                    disk_path=disk_path,
                    source=disk_path,
                )
            else:
                disk_info = probe(disk_path)
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
                    disk_path=disk_path,
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
                
                # Read the adapter/network name from the correct key for each type
                n = i + 1
                if nic_type == 'bridged':
                    adapter_name = config.get(f'bridgeadapter{n}')
                elif nic_type == 'hostonly':
                    # VirtualBox 7.1 emits hostonlyadapter<n>; hostonlyif<n> is
                    # kept as a fallback for older releases (F-19).
                    adapter_name = (config.get(f'hostonlyadapter{n}')
                                    or config.get(f'hostonlyif{n}'))
                elif nic_type == 'intnet':
                    adapter_name = config.get(f'intnet{n}')
                elif nic_type == 'natnetwork':
                    # A plain NAT adapter reports natnet<n>="nat", so a NAT
                    # network reports its name there too; natnetwork<n> is a
                    # fallback (F-19).
                    adapter_name = (config.get(f'natnet{n}')
                                    or config.get(f'natnetwork{n}'))
                else:
                    adapter_name = None

                network = NetworkConfig(
                    adapter_type=adapter_type,
                    network_type=network_type,
                    adapter_name=adapter_name,
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
            # `audio` names the *driver* (VirtualBox 7.x reports
            # audio="default" even when sound is off), so it says nothing about
            # whether audio is enabled. The playback/recording flags do (F-21).
            audio_enabled=(self._flag(config, 'audio_out')
                           or self._flag(config, 'audio_in')),
            usb_enabled=self._flag(config, 'usb'),
            rtc_utc=self._flag(config, 'rtcuseutc', True),
            clipboard_mode=config.get('clipboard', 'disabled'),
            draganddrop=config.get('draganddrop', 'disabled'),
        )
