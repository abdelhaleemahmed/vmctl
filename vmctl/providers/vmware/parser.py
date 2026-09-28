"""
Read a ``.vmx`` into a :class:`VMConfig`.

A ``.vmx`` is ``key = "value"`` lines, which is the same shape as VirtualBox's
``showvminfo --machinereadable`` output -- so the shared field table in
:mod:`vmctl.core.mapping` reads the scalar settings with no VMware-specific code at
all. That was A-11's promise and this is where it is cheapest to see: the whole of
the CPU, memory, firmware and guest OS reading is one declaration in
:mod:`vmctl.providers.vmware.tables`.

What remains here is the part no field table can express: devices, whose keys carry
their address in the *name* (``sata0:2.fileName``), and adapters, which are numbered
the same way.
"""

import os
import re
from typing import Dict, List, Optional, Tuple

from ...core.capabilities import Capabilities
from ...core.devices import Allocation, BusType, DeviceKind, DiskFormat
from ...core.exceptions import ProviderError
from ...core.mapping import read_into
from ...core.platform import Arch, NicModel
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
from ..base import MediumProbe
from .capabilities import VMwareCapabilities
from .tables import (
    BOOT_FROM_DEVICE,
    CONNECTION_TO_NETWORK,
    DEVICE_TYPE_TO_KIND,
    FIELDS,
    GUEST_OS_FROM_VMWARE,
    NIC_MODEL_FROM_VMWARE,
    VIRTUAL_DEV_TO_BUS,
)

#: ``sata0:2.fileName`` -> ("sata", 0, 2, "fileName").
DEVICE_KEY = re.compile(r"^(ide|sata|scsi|nvme)(\d+):(\d+)\.(.+)$")
#: ``floppy0.fileName`` -> ("floppy", 0, "fileName").
FLOPPY_KEY = re.compile(r"^floppy(\d+)\.(.+)$")
#: ``ethernet0.virtualDev`` -> (0, "virtualDev").
NIC_KEY = re.compile(r"^ethernet(\d+)\.(.+)$")


class VMwareParser:
    """Parse a ``.vmx`` into the canonical model."""

    def __init__(self, capabilities: Optional[Capabilities] = None):
        """Initialise the parser.

        Args:
            capabilities: Used for the native format and port counts.
        """
        self.capabilities = capabilities or VMwareCapabilities.get()

    # -- the file ------------------------------------------------------------

    @staticmethod
    def parse_keys(text: str) -> Dict[str, str]:
        """Return a ``.vmx``'s keys and values.

        Args:
            text: The file's contents.

        Returns:
            dict: keys in the file's own case, values unquoted.

        Raises:
            ProviderError: If the file holds a key twice, which is what VMware
                itself refuses to read ("Cannot read the virtual machine
                configuration") -- so vmctl does not quietly pick one.
        """
        keys: Dict[str, str] = {}
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "=" not in stripped:
                continue
            key, _, value = stripped.partition("=")
            key = key.strip()
            value = value.strip().strip('"')
            if key in keys and keys[key] != value:
                raise ProviderError(
                    f"this .vmx sets {key!r} twice ({keys[key]!r} and {value!r}); "
                    f"VMware refuses to read a file with a repeated key"
                )
            keys[key] = value
        if not keys:
            raise ProviderError("this file contains no .vmx settings")
        return keys

    def parse_text(
        self,
        vm_name: str,
        text: str,
        probe: Optional[MediumProbe] = None,
        base_dir: Optional[str] = None,
    ) -> VMConfig:
        """Parse a ``.vmx``.

        Args:
            vm_name: Fallback name, when the file states none.
            text: The file's contents.
            probe: Reads a disk's real size, which a ``.vmx`` does not state -- the
                same injected transport the other parsers take (T-04).
            base_dir: The directory the ``.vmx`` is in. A ``.vmx`` names its disks by
                bare file name, so without this they resolve against whatever the
                working directory happens to be: the probe then failed, the size came
                back as the model's 20 GB default, and a migration reported the image
                as unreachable (F-35).

        Returns:
            The configuration the file describes.
        """
        keys = self.parse_keys(text)
        lowered = {key.lower(): value for key, value in keys.items()}

        vm = VMConfig(
            name=keys.get("displayName") or vm_name,
            cpu=CPUConfig(),
            memory=MemoryConfig(),
            firmware=FirmwareConfig(
                secure_boot=lowered.get("uefi.secureboot.enabled", "").upper() == "TRUE",
                tpm=lowered.get("vtpm.present", "").upper() == "TRUE",
            ),
            storage=[],
            networks=[],
            boot=self._boot(lowered),
            storage_controllers=[],
            arch=Arch.X86_64,
        )
        # The scalar settings come from the shared field table, in one line.
        read_into(vm, keys, FIELDS)
        vm.cpu.nested_virt = lowered.get("vhv.enable", "").upper() == "TRUE"
        if vm.cpu.cores and vm.cpu.count:
            vm.cpu.sockets = max(1, vm.cpu.count // vm.cpu.cores)
            # Stated, not left empty: VMware has no SMT control, so a topology it
            # reports is always one thread per core -- and a config asking for
            # `threads: 1` otherwise read back as unset, which is a round trip
            # disagreeing about a number with only one possible value.
            vm.cpu.threads = 1
        vm.guest_os = GUEST_OS_FROM_VMWARE.get(vm.guest_os, vm.guest_os)
        vm.audio_enabled = lowered.get("sound.present", "").upper() == "TRUE"
        vm.usb_enabled = lowered.get("usb.present", "").upper() == "TRUE"

        controllers, devices = self._storage(keys, probe, base_dir)
        vm.storage_controllers = controllers
        vm.storage = devices
        vm.networks = self._networks(keys)

        # `bios.bootOrder` says the VM boots from disk; which disk is not stated, so
        # it is read the way the other providers read it (L-03).
        if "disk" in vm.boot.order:
            for device in vm.storage:
                if not device.is_removable:
                    device.bootable = True
                    break
        return vm

    # -- pieces --------------------------------------------------------------

    def _boot(self, lowered: Dict[str, str]) -> BootConfig:
        """Read ``bios.bootOrder``, keeping the model's four slots."""
        order = [
            BOOT_FROM_DEVICE[word.strip()]
            for word in lowered.get("bios.bootorder", "").split(",")
            if word.strip() in BOOT_FROM_DEVICE
        ]
        while len(order) < 4:
            order.append("none")
        return BootConfig(order=order[:4])

    def _storage(
        self,
        keys: Dict[str, str],
        probe: Optional[MediumProbe],
        base_dir: Optional[str] = None,
    ) -> Tuple[List[StorageController], List[StorageDevice]]:
        """Build controllers and devices from the addressed keys."""
        # Which bus each controller is: `scsi0.virtualDev` decides between plain
        # SCSI, SAS and the paravirtual adapter, all of which are keyed `scsi`.
        buses: Dict[str, BusType] = {}
        for key, value in keys.items():
            if key.endswith(".virtualDev"):
                prefix = key.split(".")[0]
                bus = VIRTUAL_DEV_TO_BUS.get(value.strip().lower())
                if bus is not None:
                    buses[prefix] = bus

        devices: Dict[str, Dict[str, str]] = {}
        for key, value in keys.items():
            match = DEVICE_KEY.match(key)
            if match:
                prefix, index, port, attribute = match.groups()
                devices.setdefault(f"{prefix}{index}:{port}", {})[attribute.lower()] = value
                continue
            match = FLOPPY_KEY.match(key)
            if match and match.group(2).lower() != "present":
                devices.setdefault(f"floppy{match.group(1)}", {})[match.group(2).lower()] = value

        seen: Dict[BusType, StorageController] = {}
        out: List[StorageDevice] = []
        for at, attributes in sorted(devices.items()):
            if attributes.get("present", "TRUE").upper() != "TRUE":
                continue
            bus, port, unit = self._address_of(at, buses)
            if bus is None:
                continue
            device_type = attributes.get("devicetype", "").lower()
            kind = DEVICE_TYPE_TO_KIND.get(
                device_type, DeviceKind.FLOPPY if at.startswith("floppy") else DeviceKind.DISK
            )
            source = attributes.get("filename") or None
            if source and base_dir and not os.path.isabs(source):
                source = os.path.join(base_dir, source)
            removable = kind is not DeviceKind.DISK
            size = 0
            if not removable and source and probe is not None:
                size = int(probe(source).get("size_mb") or 0)

            controller = seen.setdefault(bus, self._controller_for(bus))
            out.append(
                StorageDevice(
                    name=at.replace(":", "_"),
                    kind=kind,
                    bus=bus,
                    controller=controller.id,
                    slot=port,
                    unit=unit,
                    size_mb=None if removable else (size or None),
                    format=None if removable else DiskFormat.VMDK,
                    allocation=Allocation.THIN,
                    source=source if removable else None,
                    disk_path=None if removable else source,
                    nonrotational=attributes.get("virtualssd", "").upper() == "TRUE",
                    readonly=attributes.get("mode", "").startswith("independent-non"),
                )
            )
        return list(seen.values()), out

    @staticmethod
    def _address_of(at: str, buses: Dict[str, BusType]):
        """Return ``(bus, port, unit)`` for a device key prefix."""
        if at.startswith("floppy"):
            return (BusType.FLOPPY, int(at[len("floppy") :] or 0), 0)
        prefix, _, port = at.partition(":")
        name = prefix.rstrip("0123456789")
        index = int(prefix[len(name) :] or 0)
        if name == "ide":
            # IDE addresses a channel and a device on it, so the *key's* numbers
            # mean something different from every other bus.
            return (BusType.IDE, index, int(port))
        if name == "scsi":
            return (buses.get(prefix, BusType.SCSI), int(port), 0)
        bus = {"sata": BusType.SATA, "nvme": BusType.NVME}.get(name)
        return (bus, int(port), 0)

    def _controller_for(self, bus: BusType) -> StorageController:
        spec = self.capabilities.bus(bus)
        return StorageController(
            id=default_controller_id(bus),
            bus=bus,
            native_name=spec.controller_name if spec else bus.value,
            port_count=spec.default_ports if spec else 1,
            bootable=True,
        )

    def _networks(self, keys: Dict[str, str]) -> List[NetworkConfig]:
        """Build the adapters."""
        found: Dict[int, Dict[str, str]] = {}
        for key, value in keys.items():
            match = NIC_KEY.match(key)
            if match:
                found.setdefault(int(match.group(1)), {})[match.group(2).lower()] = value

        networks: List[NetworkConfig] = []
        for index in sorted(found):
            attributes = found[index]
            if attributes.get("present", "TRUE").upper() != "TRUE":
                continue
            mode = CONNECTION_TO_NETWORK.get(
                attributes.get("connectiontype", "nat").lower(), NetworkType.NAT
            )
            networks.append(
                NetworkConfig(
                    model=NIC_MODEL_FROM_VMWARE.get(
                        attributes.get("virtualdev", "").lower(), NicModel.E1000
                    ),
                    network_type=mode,
                    adapter_name=attributes.get("vnet"),
                    mac_address=attributes.get("address"),
                )
            )
        return networks
