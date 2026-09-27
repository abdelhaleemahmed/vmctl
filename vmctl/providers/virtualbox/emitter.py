# providers/virtualbox/emitter.py
"""
Emit VirtualBox commands from VMConfig
"""
import os
import re
import sys
from collections import OrderedDict
from typing import List, Dict, Any, Optional
from ...core.vmconfig import (
    VMConfig,
    DiskFormat,
    DiskType,
    DiskVariant,
    FirmwareType,
    NetworkConfig,
    NetworkType,
    StorageControllerConfig,
    StorageControllerType,
    resolve_controller,
)
from ...core.exceptions import ProviderError


class VirtualBoxEmitter:
    """Emit VBoxManage commands to create/configure VMs"""

    #: Used only when no machine folder is supplied. VirtualBox's own default,
    #: which is correct for an untouched installation but wrong whenever the
    #: user has configured a different default machine folder (F-13).
    FALLBACK_MACHINE_FOLDER = os.path.join(os.path.expanduser("~"), "VirtualBox VMs")

    def __init__(
        self, vm_name: str, machine_folder: Optional[str] = None, path_sep: Optional[str] = None
    ):
        """Initialise the emitter for a specific VM.

        Args:
            vm_name: Name of the VM that will be referenced in every
                ``VBoxManage`` command this emitter generates.
            machine_folder: VirtualBox's default machine folder on the target
                host, as reported by ``VBoxManage list systemproperties``. The
                emitter must not query it itself -- that would make it depend on
                the host it runs on, when the target may be a different machine
                entirely. Defaults to ``~/VirtualBox VMs`` (F-13).
            path_sep: Separator to build medium paths with. Defaults to the
                local one; pass ``"\\"`` when emitting for a Windows target
                from a POSIX host.
        """
        self.vm_name = vm_name
        self.machine_folder = machine_folder or self.FALLBACK_MACHINE_FOLDER
        self.path_sep = path_sep or os.sep
        self.commands: List[List[str]] = []

    def _medium_path(self, *parts: str) -> str:
        """Join a medium path using the *target* host's separator."""
        return self.path_sep.join([self.machine_folder.rstrip("/\\"), *parts])

    def emit_create_vm(self, vm: VMConfig) -> List[List[str]]:
        """Generate commands to create a VM from VMConfig"""
        commands: List[List[str]] = []

        # Map display names to internal VirtualBox OS type names
        ostype_map = {
            # Red Hat / RHEL
            "Red Hat (64-bit)": "RedHat_64",
            "Red Hat (32-bit)": "RedHat",
            "Red Hat": "RedHat",
            # Rocky Linux (uses RedHat type)
            "Rocky Linux (64-bit)": "RedHat_64",
            "Rocky Linux": "RedHat_64",
            # CentOS
            "CentOS (64-bit)": "RedHat_64",
            "CentOS": "RedHat",
            # Fedora
            "Fedora (64-bit)": "Fedora_64",
            "Fedora (32-bit)": "Fedora",
            "Fedora": "Fedora",
            # Ubuntu
            "Ubuntu (64-bit)": "Ubuntu_64",
            "Ubuntu (32-bit)": "Ubuntu",
            "Ubuntu": "Ubuntu",
            # Debian
            "Debian (64-bit)": "Debian_64",
            "Debian (32-bit)": "Debian",
            "Debian": "Debian",
            # Linux Generic
            "Linux 2.6 / 3.x / 4.x (64-bit)": "Linux26_64",
            "Linux 2.6 / 3.x / 4.x (32-bit)": "Linux26",
            "Other Linux (64-bit)": "Linux_64",
            "Other Linux (32-bit)": "Linux",
            "Other Linux": "Linux",
            # Windows
            "Windows 10 (64-bit)": "Windows10_64",
            "Windows 10 (32-bit)": "Windows10",
            "Windows 11 (64-bit)": "Windows11_64",
            "Windows Server 2019 (64-bit)": "Windows2019_64",
            "Windows Server 2016 (64-bit)": "Windows2016_64",
            # Other
            "Other (64-bit)": "Other_64",
            "Other (32-bit)": "Other",
        }

        # Normalize ostype - use mapping or original if already internal format
        ostype = ostype_map.get(vm.ostype, vm.ostype)

        # Create VM
        commands.append(
            ["VBoxManage", "createvm", "--name", vm.name, "--ostype", ostype, "--register"]
        )

        # Basic configuration. Every field below used to be parsed, exported to
        # YAML, and then silently dropped on create (F-05).
        basic = [
            "VBoxManage",
            "modifyvm",
            vm.name,
            "--memory",
            str(vm.memory.mb),
            "--vram",
            str(vm.memory.vram_mb),
            "--cpus",
            str(vm.cpu.count),
            "--firmware",
            vm.firmware.type.value,
            "--acpi",
            "on" if vm.boot.acpi else "off",
            "--ioapic",
            "on" if vm.boot.ioapic else "off",
            "--rtcuseutc",
            "on" if vm.rtc_utc else "off",
            "--pae",
            "on" if vm.cpu.pae else "off",
            "--nested-hw-virt",
            "on" if vm.cpu.nested_virt else "off",
            "--cpuhotplug",
            "on" if vm.cpu.hotplug else "off",
            "--pagefusion",
            "on" if vm.memory.page_fusion else "off",
            "--hpet",
            "on" if vm.boot.hpet else "off",
        ]
        if vm.cpu.execution_cap != 100:
            basic += ["--cpuexecutioncap", str(vm.cpu.execution_cap)]
        if vm.clipboard_mode:
            basic += ["--clipboard-mode", vm.clipboard_mode]
        if vm.draganddrop:
            basic += ["--draganddrop", vm.draganddrop]
        commands.append(basic)

        # TPM. VirtualBox 7.x spells this --tpm-type; there is no boolean form.
        if vm.firmware.tpm:
            commands.append(["VBoxManage", "modifyvm", vm.name, "--tpm-type", "2.0"])

        # Secure boot. There is no `modifyvm --secureboot` option -- emitting one
        # made VBoxManage fail with "Unknown option: --secureboot" and aborted
        # the create partway through (F-17). On 7.x secure boot lives in NVRAM
        # and is enabled by enrolling the Microsoft signatures, which requires
        # EFI firmware.
        if vm.firmware.secure_boot:
            if vm.firmware.type == FirmwareType.BIOS:
                raise ProviderError(
                    f"VM {vm.name!r} requests secure boot with BIOS firmware. "
                    f"Secure boot requires an EFI firmware type "
                    f"(efi, efi32, efi64)."
                )
            commands.append(["VBoxManage", "modifynvram", vm.name, "enrollmssignatures"])

        # Add storage controllers. Any bus a disk references but the config
        # does not declare gets one synthesised, so a hand-written config works
        # instead of attaching to a controller that was never created (F-01).
        controllers = self._resolve_controllers(vm)
        by_bus: Dict[StorageControllerType, StorageControllerConfig] = {}
        for sc in controllers.values():
            by_bus.setdefault(sc.controller_type, sc)
            commands.append(self._create_storage_controller(sc))

        # Create and attach media.
        for disk in vm.disks:
            controller = self._match_controller(disk, controllers, by_bus)
            if controller is None:
                raise ProviderError(
                    f"Disk {disk.name!r} references controller "
                    f"{disk.controller_name or disk.controller.value!r}, which is "
                    f"not defined and could not be synthesised. Declared "
                    f"controllers: {', '.join(controllers) or '(none)'}."
                )

            attach_type = self.ATTACH_TYPES.get(disk.type, "hdd")

            if disk.is_removable:
                # A DVD or floppy drive is *inserted*, never created. The old
                # code ran createhd for one and then attached the resulting
                # blank image as a dvddrive, which VirtualBox rejects (F-04).
                medium = disk.source or "emptydrive"
            else:
                vbox_format, ext = self.FORMAT_SPECS.get(disk.format, ("VDI", "vdi"))
                medium = self._medium_path(vm.name, f"{vm.name}_{self._slug(disk.name)}.{ext}")

                vbox_variant = "Standard"  # Thin/dynamic by default
                if disk.variant == DiskVariant.THICK:
                    vbox_variant = "Fixed"
                # RAW format doesn't support dynamic storage - must use Fixed
                if disk.format == DiskFormat.RAW:
                    vbox_variant = "Fixed"

                commands.append(
                    [
                        "VBoxManage",
                        "createmedium",
                        "disk",
                        "--filename",
                        medium,
                        "--size",
                        str(disk.size_mb),
                        "--format",
                        vbox_format,
                        "--variant",
                        vbox_variant,
                    ]
                )

            commands.append(
                [
                    "VBoxManage",
                    "storageattach",
                    vm.name,
                    "--storagectl",
                    controller.name,
                    "--port",
                    str(disk.port),
                    "--device",
                    str(disk.device),
                    "--type",
                    attach_type,
                    "--medium",
                    medium,
                ]
            )

        # Configure network adapters
        for i, network in enumerate(vm.networks):
            commands.append(self._configure_network_adapter(i + 1, network))

        # Boot order
        for i, device in enumerate(vm.boot.order[:4], 1):
            if device != "none":
                commands.append(["VBoxManage", "modifyvm", vm.name, f"--boot{i}", device])

        # Audio - use platform-appropriate driver
        if vm.audio_enabled:
            if sys.platform == "win32":
                audio_driver = "dsound"  # DirectSound for Windows
            elif sys.platform == "darwin":
                audio_driver = "coreaudio"  # CoreAudio for macOS
            else:
                audio_driver = "pulse"  # PulseAudio for Linux

            commands.append(
                [
                    "VBoxManage",
                    "modifyvm",
                    vm.name,
                    "--audio-driver",
                    audio_driver,
                    "--audio-enabled",
                    "on",
                    "--audiocontroller",
                    "hda",
                ]
            )

        # USB
        if vm.usb_enabled:
            commands.append(["VBoxManage", "modifyvm", vm.name, "--usb", "on", "--usbehci", "on"])

        # Description
        if vm.description:
            commands.append(["VBoxManage", "modifyvm", vm.name, "--description", vm.description])

        return commands

    # -- storage topology ----------------------------------------------------

    #: Canonical bus/chipset pair and default port count per controller type.
    #: Verified against VirtualBox 7.1.18 by creating each controller on a live
    #: host: ``virtio-scsi`` is a valid ``--add`` value even though
    #: ``storagectl --help`` omits it, and a USB controller demands exactly 8
    #: ports (``Invalid port count: 1 (must be in range [8, 8])``).
    CONTROLLER_SPECS = {
        StorageControllerType.IDE: ("ide", "PIIX4", 2, "IDE Controller"),
        StorageControllerType.SATA: ("sata", "IntelAhci", 30, "SATA Controller"),
        StorageControllerType.SCSI: ("scsi", "LSILogic", 16, "SCSI Controller"),
        StorageControllerType.SAS: ("sas", "LSILogicSAS", 16, "SAS Controller"),
        StorageControllerType.NVME: ("pcie", "NVMe", 8, "NVMe Controller"),
        StorageControllerType.FLOPPY: ("floppy", "I82078", 1, "Floppy"),
        StorageControllerType.USB: ("usb", "USB", 8, "USB Controller"),
        StorageControllerType.VIRTIO_SCSI: ("virtio-scsi", "VirtIO", 16, "VirtIO SCSI Controller"),
    }

    #: Attachment ``--type`` per device kind.
    ATTACH_TYPES = {
        DiskType.HDD: "hdd",
        DiskType.SSD: "hdd",
        DiskType.DVD: "dvddrive",
        DiskType.FLOPPY: "fdd",
    }

    #: Image format -> (VBoxManage --format, file extension).
    FORMAT_SPECS = {
        DiskFormat.VDI: ("VDI", "vdi"),
        DiskFormat.VMDK: ("VMDK", "vmdk"),
        DiskFormat.VHD: ("VHD", "vhd"),
        DiskFormat.RAW: ("RAW", "img"),
    }

    @staticmethod
    def _slug(name: str) -> str:
        """Make a device name safe to use inside a filename.

        Parsed disk names carry the controller name, which routinely contains
        spaces ("disk_SATA Controller_0_0"), so medium filenames inherited them.
        The readable name stays in the config; only the filename is slugged
        (L-04).
        """
        cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_")
        return cleaned or "disk"

    def _resolve_controllers(self, vm: VMConfig) -> Dict[str, StorageControllerConfig]:
        """Return the controllers to create, keyed by the name disks will use.

        A hand-written config names a bus (``controller: sata``) without
        declaring a controller, and the old code emitted no ``storagectl`` at
        all while still emitting ``storageattach --storagectl SATA`` -- which
        fails, because nothing by that name exists. Every bus a disk references
        therefore gets a controller synthesised if the config did not declare
        one (F-01).

        Args:
            vm: The configuration being emitted.

        Returns:
            dict: controller name -> controller configuration, in creation
            order (declared controllers first, then synthesised ones).
        """
        resolved = OrderedDict()
        for sc in vm.storage_controllers:
            resolved[sc.name] = sc

        by_bus: Dict[StorageControllerType, StorageControllerConfig] = {}
        for sc in vm.storage_controllers:
            by_bus.setdefault(sc.controller_type, sc)

        for disk in vm.disks:
            if self._match_controller(disk, resolved, by_bus) is not None:
                continue
            bus_spec = self.CONTROLLER_SPECS.get(
                disk.controller, self.CONTROLLER_SPECS[StorageControllerType.SATA]
            )
            name = bus_spec[3]
            if name not in resolved:
                synthesized = StorageControllerConfig(
                    name=name,
                    controller_type=disk.controller,
                    port_count=bus_spec[2],
                    bootable=True,
                )
                resolved[name] = synthesized
                by_bus.setdefault(disk.controller, synthesized)
        return resolved

    @staticmethod
    def _match_controller(disk, resolved, by_bus):
        """Find the controller a disk should attach to, or None.

        Delegates to :func:`~vmctl.core.vmconfig.resolve_controller` so the
        emitter and the validator place devices identically (F-01/F-15).
        """
        return resolve_controller(disk, resolved, by_bus)

    def _create_storage_controller(self, controller: StorageControllerConfig) -> List[str]:
        """Generate the command that creates one storage controller."""
        bus_type, chipset, default_ports, _ = self.CONTROLLER_SPECS.get(
            controller.controller_type,
            self.CONTROLLER_SPECS[StorageControllerType.SATA],
        )
        port_count = controller.port_count or default_ports
        if controller.controller_type == StorageControllerType.USB:
            port_count = 8  # VirtualBox accepts nothing else
        elif controller.controller_type == StorageControllerType.FLOPPY:
            port_count = 1

        return [
            "VBoxManage",
            "storagectl",
            self.vm_name,
            "--name",
            controller.name,
            "--add",
            bus_type,
            "--controller",
            chipset,
            "--portcount",
            str(port_count),
            "--bootable",
            "on" if controller.bootable else "off",
        ]

    # -- editing an existing VM ---------------------------------------------

    #: Settings that can be changed with a single ``modifyvm`` flag, as
    #: (dotted config path, flag, renderer). Driving edits from a table means a
    #: field is supported in one place rather than in a hand-written if-chain,
    #: and read/write cannot drift apart. A-11 generalises this to both
    #: directions for every provider.
    MODIFIABLE = (
        ("memory.mb", "--memory", str),
        ("memory.vram_mb", "--vram", str),
        ("cpu.count", "--cpus", str),
        ("cpu.execution_cap", "--cpuexecutioncap", str),
        ("cpu.pae", "--pae", lambda v: "on" if v else "off"),
        ("cpu.nested_virt", "--nested-hw-virt", lambda v: "on" if v else "off"),
        ("cpu.hotplug", "--cpuhotplug", lambda v: "on" if v else "off"),
        ("memory.page_fusion", "--pagefusion", lambda v: "on" if v else "off"),
        ("boot.acpi", "--acpi", lambda v: "on" if v else "off"),
        ("boot.ioapic", "--ioapic", lambda v: "on" if v else "off"),
        ("boot.hpet", "--hpet", lambda v: "on" if v else "off"),
        ("rtc_utc", "--rtcuseutc", lambda v: "on" if v else "off"),
        ("usb_enabled", "--usb", lambda v: "on" if v else "off"),
        ("clipboard_mode", "--clipboard-mode", str),
        ("draganddrop", "--draganddrop", str),
        ("firmware.type", "--firmware", lambda v: v.value),
        ("description", "--description", str),
    )

    #: Changes vmctl cannot apply in place, reported rather than silently ignored.
    UNSUPPORTED_EDITS = (
        ("disks", "storage layout"),
        ("storage_controllers", "storage controllers"),
        ("networks", "network adapters"),
        ("ostype", "guest OS type"),
    )

    @staticmethod
    def _get(obj: Any, dotted: str) -> Any:
        for part in dotted.split("."):
            obj = getattr(obj, part)
        return obj

    def emit_modify_vm(self, current: VMConfig, desired: VMConfig):
        """Generate the commands that turn *current* into *desired*.

        Only differences are emitted, so editing one setting produces one
        command rather than re-applying everything.

        Args:
            current: The VM as it exists now.
            desired: The VM as it should be.

        Returns:
            tuple: ``(commands, unsupported)`` where ``commands`` is a list of
            argv lists and ``unsupported`` is a list of human-readable
            descriptions of requested changes that cannot be applied in place.
        """
        commands = []
        unsupported = []

        # Renaming first: every later command addresses the VM by its new name
        # only if the rename has already happened.
        target = current.name
        if desired.name != current.name:
            commands.append(["VBoxManage", "modifyvm", current.name, "--name", desired.name])
            target = desired.name

        changed = []
        for path, flag, render in self.MODIFIABLE:
            old = self._get(current, path)
            new = self._get(desired, path)
            if new is None or new == old:
                continue
            changed += [flag, render(new)]

        if changed:
            commands.append(["VBoxManage", "modifyvm", target] + changed)

        for attr, label in self.UNSUPPORTED_EDITS:
            if getattr(current, attr) != getattr(desired, attr):
                unsupported.append(label)

        return commands, unsupported

    def _configure_network_adapter(self, adapter_num: int, network: NetworkConfig) -> List[str]:
        """Generate command to configure network adapter"""
        # Map our NetworkType to VirtualBox network type names
        network_type_map = {
            NetworkType.NAT: "nat",
            NetworkType.BRIDGED: "bridged",
            NetworkType.HOSTONLY: "hostonly",
            NetworkType.INTERNAL: "intnet",
            NetworkType.NATNETWORK: "natnetwork",
        }
        vbox_net_type = network_type_map.get(network.network_type, "nat")

        cmd = [
            "VBoxManage",
            "modifyvm",
            self.vm_name,
            f"--nic{adapter_num}",
            vbox_net_type,
            f"--nictype{adapter_num}",
            network.adapter_type,
            f"--cableconnected{adapter_num}",
            "on",
        ]

        if network.adapter_name:
            if network.network_type == NetworkType.BRIDGED:
                cmd.extend([f"--bridgeadapter{adapter_num}", network.adapter_name])
            elif network.network_type == NetworkType.HOSTONLY:
                cmd.extend([f"--hostonlyadapter{adapter_num}", network.adapter_name])
            elif network.network_type == NetworkType.INTERNAL:
                cmd.extend([f"--intnet{adapter_num}", network.adapter_name])
            elif network.network_type == NetworkType.NATNETWORK:
                cmd.extend([f"--nat-network{adapter_num}", network.adapter_name])

        if network.mac_address:
            cmd.extend([f"--macaddress{adapter_num}", network.mac_address])

        if network.promiscuous_mode:
            cmd.extend([f"--promiscuous{adapter_num}", "allow-all"])

        return cmd
