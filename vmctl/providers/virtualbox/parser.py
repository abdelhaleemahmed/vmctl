# providers/virtualbox/parser.py
"""
Parse VirtualBox VM configuration into VMConfig
"""
import subprocess
import re
from typing import Any, Callable, Dict, Optional
from ...core.devices import Allocation, BusType, DeviceKind, DiskFormat
from ...core.platform import NicModel
from ...core.vmconfig import (
    BootConfig,
    CPUConfig,
    FirmwareConfig,
    MemoryConfig,
    NetworkConfig,
    NetworkType,
    StorageController,
    StorageDevice,
    VMConfig,
    default_controller_id,
)
from ...core.exceptions import DependencyError, ProviderError
from ...core.capabilities import Capabilities
from ...core.mapping import read_into
from ..base import MediumProbe
from .capabilities import VirtualBoxCapabilities
from .tables import CONTROLLER_CHIPSETS, FIELDS, NIC_MODEL_FROM_VBOX, NIC_MODEL_TO_VBOX


class VirtualBoxParser:
    """Parse VirtualBox VM configuration"""

    def __init__(self, capabilities: Optional[Capabilities] = None):
        """Initialise the parser.

        Args:
            capabilities: The provider's capability declaration, used to map
                reported medium formats and file extensions back to the model.
                Deriving those from the same declaration the emitter writes with
                is what stops vmctl creating a format it cannot then read: a
                qcow2 disk came back as VDI, because the parser kept its own
                shorter list of formats (F-24).
        """
        self.vboxmanage_cmd = "VBoxManage"
        self.capabilities = capabilities or VirtualBoxCapabilities.get()
        # Extensions that mark an attachment as a real medium.
        self.medium_extensions = tuple("." + e for e in self.capabilities.medium_extensions())
        # Reported "Storage format:" value -> model format.
        self.medium_formats = {
            spec.native_name.upper(): fmt for fmt, spec in self.capabilities.formats.items()
        }
        # File extension -> model format. ISO and the floppy extensions are
        # removable media, whose format is not meaningful for recreation.
        # Every extension a format owns, not just the canonical one: the RAW
        # backend answers for both `img` and `raw`.
        self.extension_formats = {
            ext.lower(): fmt
            for fmt, spec in self.capabilities.formats.items()
            for ext in spec.extensions
        }

    def get_vm_info(self, vm_name: str) -> str:
        """Get raw VM info from VirtualBox"""
        try:
            result = subprocess.run(
                [self.vboxmanage_cmd, "showvminfo", vm_name, "--machinereadable"],
                capture_output=True,
                text=True,
                check=True,
            )
            return result.stdout
        except subprocess.CalledProcessError as e:
            raise ProviderError(f"Failed to get VM info for {vm_name}: {e}")
        except FileNotFoundError:
            raise DependencyError(
                "VBoxManage",
                reason="not found on PATH",
                install_hint="Install VirtualBox and make sure VBoxManage is on " "your PATH.",
            )

    # -- pure decoding -------------------------------------------------------

    TRUTHY = {"on", "true", "yes", "1", "enabled"}
    FALSY = {"off", "false", "no", "0", "disabled"}

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
        ext = disk_path.lower().rsplit(".", 1)[-1] if "." in disk_path else ""
        return self.extension_formats.get(ext, self.capabilities.native_format)

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
            "size_mb": 20480,  # Default 20GB
            "format": default_format,
            "variant": Allocation.THIN,
        }

        for line in raw_info.splitlines():
            line = line.strip()
            lower = line.lower()
            # Capacity: 20480 MBytes
            if lower.startswith("capacity:"):
                match = re.search(r"(\d+)\s*MBytes", line)
                if match:
                    disk_info["size_mb"] = int(match.group(1))
            # Storage format: VMDK or VDI
            elif lower.startswith("storage format:"):
                fmt_str = line.split(":", 1)[1].strip().upper()
                if fmt_str in self.medium_formats:
                    disk_info["format"] = self.medium_formats[fmt_str]
            # VirtualBox 7.x prints "Format variant: fixed default"; older
            # releases printed "Variant: ...". Accept both (F-20).
            elif lower.startswith("format variant:") or lower.startswith("variant:"):
                variant_str = line.split(":", 1)[1].strip().lower()
                if "fixed" in variant_str:
                    disk_info["variant"] = Allocation.THICK
                else:
                    disk_info["variant"] = Allocation.THIN

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
                check=True,
            )
            return self.parse_medium_info(result.stdout, default_format)
        except subprocess.CalledProcessError:
            # If we can't get disk info, return defaults with format from extension
            return {"size_mb": 20480, "format": default_format, "variant": Allocation.THIN}
        except FileNotFoundError:
            return {"size_mb": 20480, "format": default_format, "variant": Allocation.THIN}

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
        return value.replace('\\"', '"').replace("\\\\", "\\")

    def _parse_machinereadable(self, raw_info: str) -> Dict[str, str]:
        """Parse machine-readable output into dictionary"""
        config = {}

        for line in raw_info.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
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
            match = re.match(r"^([\w.\-]+)=(.*)$", line)
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
        lookup: Callable[[str], Dict[str, Any]] = probe if probe is not None else self.get_disk_info

        # Scalar settings come from the shared field table below; only the
        # structural parts are assembled by hand. Reading and writing a setting
        # are now the same declaration, so a field cannot be parsed but not
        # emitted (F-05) or emitted but not parsed (F-23) -- which is how hpet,
        # cpuexecutioncap and pagefusion were being lost.
        cpu = CPUConfig()
        memory = MemoryConfig()
        firmware = FirmwareConfig(
            # VirtualBox 7.x does not report secure-boot state in
            # machine-readable output at all, so it can never be read back; see
            # F-17. It is deliberately absent from the field table.
            secure_boot=False,
        )
        boot = BootConfig(
            order=[
                config.get("boot1", "disk"),
                config.get("boot2", "dvd"),
                config.get("boot3", "none"),
                config.get("boot4", "none"),
            ]
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
        controller_pattern = re.compile(r"^storagecontrollername(\d+)$")
        controller_indices = []

        for key, value in config.items():
            match = controller_pattern.match(key)
            if match:
                controller_indices.append(match.group(1))

        controller_indices = sorted(set(controller_indices), key=int)

        per_bus: Dict[BusType, int] = {}
        for idx in controller_indices:
            name = config.get(f"storagecontrollername{idx}", f"Controller{idx}")
            controller_type = config.get(f"storagecontrollertype{idx}", "PIIX4")

            # Map the reported chipset to a bus type. An unrecognised chipset
            # must not silently become SATA: that made a floppy controller
            # (I82078) claim to be SATA and absorb disk attachments (F-15).
            sc_type = CONTROLLER_CHIPSETS.get(controller_type.strip().lower())
            if sc_type is None:
                raise ProviderError(
                    f"Unknown storage controller type {controller_type!r} on "
                    f"controller {name!r}. Known types: "
                    f"{', '.join(sorted(CONTROLLER_CHIPSETS))}."
                )

            # The logical id is derived from the bus and how many controllers on
            # that bus came before, by the same rule every provider uses -- so
            # the same VM read through two hypervisors names its controllers
            # alike. VirtualBox's own string is kept separately, because that is
            # what `--storagectl` takes and it means nothing anywhere else (M-02).
            index = per_bus.get(sc_type, 0)
            per_bus[sc_type] = index + 1
            sc = StorageController(
                id=default_controller_id(sc_type, index),
                bus=sc_type,
                native_name=name,
                port_count=int(config.get(f"storagecontrollerportcount{idx}", 30)),
                # Reported all along and never read, so every re-created VM got
                # `--bootable off` and could not boot from the controller its
                # source booted from (F-27).
                bootable=config.get(f"storagecontrollerbootable{idx}", "off").strip().lower()
                == "on",
            )
            storage_controllers.append(sc)

        # Parse disks - keys are stored without surrounding quotes
        # Match patterns like: "SATA Controller-0-0" stored as "SATA Controller-0-0"
        # VirtualBox outputs many metadata keys per disk (ImageUUID, nonrotational, discard, etc.)
        # We only want actual disk paths, so filter by checking the value looks like a path
        disk_pattern = re.compile(r"^(.+)-(\d+)-(\d+)$")
        disk_attachments = {}

        for key, value in config.items():
            match = disk_pattern.match(key)
            if match:
                # Keep real media, and also empty removable drives: discarding
                # "emptydrive" meant a VM whose installer DVD had been ejected
                # lost the drive itself on the next round trip (F-22).
                if value and (
                    value.lower().endswith(self.medium_extensions) or value.lower() == "emptydrive"
                ):
                    controller_name, port, device = match.groups()
                    # Floppy attachments used to be skipped here as "not a real
                    # disk to recreate". A floppy drive is a device kind vmctl
                    # supports now, so dropping it would lose it on export.
                    disk_attachments[f"{controller_name}-{port}-{device}"] = value

        # VirtualBox addresses an attachment by the controller's own name, so
        # that is the key here; the device stores the logical id it maps to.
        by_native = {sc.native_name: sc for sc in storage_controllers}

        # Create disk configurations
        for attachment, disk_path in disk_attachments.items():
            # Use rsplit to handle controller names with dashes (e.g., "SATA-II Controller-0-0")
            ctrl_name, port, device = attachment.rsplit("-", 2)

            controller = by_native.get(ctrl_name)
            ctrl_type = controller.bus if controller else BusType.SATA

            # "none" means the slot exists but holds nothing at all.
            if not disk_path or disk_path == "none":
                continue

            empty = disk_path.lower() == "emptydrive"

            # Decide what kind of device this is before probing. A floppy
            # controller carries floppy drives; an .iso is an optical medium.
            if ctrl_type == BusType.FLOPPY:
                disk_type = DeviceKind.FLOPPY
            elif disk_path.lower().endswith(".iso"):
                disk_type = DeviceKind.CDROM
            elif empty:
                # A hard disk attachment is never empty, so an empty drive on a
                # disk bus is an optical one.
                disk_type = DeviceKind.CDROM
            else:
                disk_type = DeviceKind.DISK

            if disk_type in (DeviceKind.CDROM, DeviceKind.FLOPPY):
                # Removable media are *inserted*, not created, so their size,
                # format and allocation carry no meaning for recreation. Do not
                # probe them either: `showmediuminfo <path>` defaults to a HDD
                # device type and fails outright for an ISO, which silently
                # yielded a bogus 20 GB VDI (F-18, feeding F-04).
                disk = StorageDevice(
                    name=f"disk_{ctrl_name}_{port}_{device}",
                    # A drive has no capacity of its own; 0 read as a number that
                    # then had to be explained away (M-02).
                    size_mb=None,
                    kind=disk_type,
                    format=self.default_format_for(disk_path),
                    bus=ctrl_type,
                    controller=controller.id if controller else ctrl_name,
                    slot=int(port),
                    unit=int(device),
                    bootable=False,
                    disk_path=None if empty else disk_path,
                    source=None if empty else disk_path,
                )
            else:
                disk_info = lookup(disk_path)
                disk = StorageDevice(
                    name=f"disk_{ctrl_name}_{port}_{device}",
                    size_mb=disk_info["size_mb"],
                    kind=disk_type,
                    # Reported per attachment as "<controller>-nonrotational-<port>-<device>".
                    # This is what `type: ssd` used to stand in for (M-01).
                    nonrotational=config.get(f"{ctrl_name}-nonrotational-{port}-{device}", "off")
                    .strip()
                    .lower()
                    == "on",
                    discard=config.get(f"{ctrl_name}-discard-{port}-{device}", "off")
                    .strip()
                    .lower()
                    == "on",
                    format=disk_info["format"],
                    allocation=disk_info["variant"],
                    bus=ctrl_type,
                    controller=controller.id if controller else ctrl_name,
                    slot=int(port),
                    unit=int(device),
                    bootable=False,  # set below for the first disk only
                    disk_path=disk_path,
                )
            disks.append(disk)

        # Mark only the first non-removable disk bootable, and only when the VM
        # boots from disk at all. Every disk used to be marked bootable whenever
        # boot1 was 'disk', including optical drives (L-03).
        if "disk" in boot.order:
            for disk in disks:
                if not disk.is_removable:
                    disk.bootable = True
                    break

        # Parse networks
        # Map VirtualBox network types to our NetworkType enum
        vbox_network_map = {
            "nat": NetworkType.NAT,
            "bridged": NetworkType.BRIDGED,
            "hostonly": NetworkType.HOSTONLY,
            "intnet": NetworkType.INTERNAL,
            "natnetwork": NetworkType.NATNETWORK,
        }

        networks = []
        for i in range(8):  # VirtualBox supports up to 8 adapters
            nic_type = config.get(f"nic{i+1}", "none")
            if nic_type != "none":
                network_type = vbox_network_map.get(nic_type, NetworkType.NAT)
                native = config.get(f"nictype{i+1}", "82540EM").strip().lower()
                # 82540EM is VirtualBox's default and its Intel PRO/1000 MT
                # Desktop, which the model calls e1000 (A-10).
                nic_model = NIC_MODEL_FROM_VBOX.get(native, NicModel.E1000)

                # Read the adapter/network name from the correct key for each type
                n = i + 1
                if nic_type == "bridged":
                    adapter_name = config.get(f"bridgeadapter{n}")
                elif nic_type == "hostonly":
                    # VirtualBox 7.1 emits hostonlyadapter<n>; hostonlyif<n> is
                    # kept as a fallback for older releases (F-19).
                    adapter_name = config.get(f"hostonlyadapter{n}") or config.get(f"hostonlyif{n}")
                elif nic_type == "intnet":
                    adapter_name = config.get(f"intnet{n}")
                elif nic_type == "natnetwork":
                    # A plain NAT adapter reports natnet<n>="nat", so a NAT
                    # network reports its name there too; natnetwork<n> is a
                    # fallback (F-19).
                    adapter_name = config.get(f"natnet{n}") or config.get(f"natnetwork{n}")
                else:
                    adapter_name = None

                # Keep the exact chipset when it is not the canonical one for
                # this model: 82540EM, 82543GC and 82545EM are all e1000 to the
                # model, and a guest bound to one should get that one back (A-10).
                canonical = NIC_MODEL_TO_VBOX.get(nic_model, "").lower()
                hint = (
                    {"virtualbox": {"nictype": config.get(f"nictype{i+1}")}}
                    if native != canonical
                    else {}
                )
                network = NetworkConfig(
                    model=nic_model,
                    provider_options=hint,
                    network_type=network_type,
                    adapter_name=adapter_name,
                    mac_address=config.get(f"macaddress{i+1}"),
                )
                networks.append(network)

        vm = VMConfig(
            name=vm_name,
            cpu=cpu,
            memory=memory,
            firmware=firmware,
            storage=disks,
            networks=networks,
            boot=boot,
            storage_controllers=storage_controllers,
            # `audio` names the driver (VirtualBox 7.x reports audio="default"
            # even when sound is off), so the playback/recording flags decide
            # it, not the driver name (F-21).
            audio_enabled=(self._flag(config, "audio_out") or self._flag(config, "audio_in")),
            usb_enabled=self._flag(config, "usb"),
        )
        read_into(vm, config, FIELDS)
        return vm
