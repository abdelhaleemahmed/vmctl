# providers/virtualbox/emitter.py
"""
Emit VirtualBox commands from VMConfig
"""
import os
import re
import sys
from collections import OrderedDict
from typing import List, Dict, Optional
from ...core.vmconfig import (
    VMConfig,
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
from ...core.mapping import changed_flags, emit_flags
from ...core.plan import Plan
from ...core.slots import place
from ...core.translate import Policy, Translator
from ...core.capabilities import Capabilities
from .capabilities import VirtualBoxCapabilities
from .tables import FIELDS, MODIFIABLE


class VirtualBoxEmitter:
    """Emit VBoxManage commands to create/configure VMs"""

    #: Used only when no machine folder is supplied. VirtualBox's own default,
    #: which is correct for an untouched installation but wrong whenever the
    #: user has configured a different default machine folder (F-13).
    FALLBACK_MACHINE_FOLDER = os.path.join(os.path.expanduser("~"), "VirtualBox VMs")

    def __init__(
        self,
        vm_name: str,
        machine_folder: Optional[str] = None,
        path_sep: Optional[str] = None,
        capabilities: Optional[Capabilities] = None,
        policy: Policy = Policy.STRICT,
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
            capabilities: Provider limits to emit within. Defaults to
                VirtualBox's own declaration.
            policy: What to do about values VirtualBox does not support. The
                default refuses; ``nearest`` substitutes and reports.
        """
        self.vm_name = vm_name
        # Bus rules, port limits and format support come from the provider's
        # capability declaration rather than a second copy kept here (A-02).
        self.capabilities = capabilities or VirtualBoxCapabilities.get()
        self.policy = policy
        self.machine_folder = machine_folder or self.FALLBACK_MACHINE_FOLDER
        self.path_sep = path_sep or os.sep
        self.commands: List[List[str]] = []

    def _medium_path(self, *parts: str) -> str:
        """Join a medium path using the *target* host's separator."""
        return self.path_sep.join([self.machine_folder.rstrip("/\\"), *parts])

    def emit_create_vm(self, vm: VMConfig) -> Plan:
        """Generate the plan that creates a VM from a VMConfig.

        Args:
            vm: Configuration to realise.

        Returns:
            Plan: The steps to run, each with a human-readable description.
        """
        plan = Plan("virtualbox")
        # Anything that cannot be expressed exactly is recorded here and ends up
        # in the plan's warnings, rather than being changed quietly (A-04).
        translator = Translator(self.capabilities, self.policy)
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

        # Basic configuration. Every flag below comes from the shared field
        # table, so a setting is declared once and read and written by the same
        # declaration (A-11). Table order is the emission order.
        commands.append(["VBoxManage", "modifyvm", vm.name] + emit_flags(vm, FIELDS))

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
        # Where each device sits, decided once and shared with the validator and
        # every other provider (A-06).
        placements = place(vm, self.capabilities)
        by_bus: Dict[StorageControllerType, StorageControllerConfig] = {}
        for sc in controllers.values():
            by_bus.setdefault(sc.controller_type, sc)
            commands.append(self._create_storage_controller(sc))

        # Create and attach media.
        for index, (disk, placement) in enumerate(zip(vm.disks, placements)):
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
            elif disk.source:
                # An image that already exists is attached, not created. This is
                # how a migration attaches the converted copy of a disk instead
                # of a blank one.
                medium = disk.source
            else:
                chosen_format = translator.format_for(disk, f"disks[{index}].format")
                fmt = self.capabilities.format_spec(chosen_format)
                medium = self._medium_path(
                    vm.name, f"{vm.name}_{self._slug(disk.name)}.{fmt.extension}"
                )

                allocation = translator.allocation_for(disk, chosen_format, f"disks[{index}]")
                vbox_variant = "Fixed" if allocation == DiskVariant.THICK else "Standard"

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
                        fmt.native_name,
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
                    str(placement.port),
                    "--device",
                    str(placement.unit),
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

        for cmd in commands:
            plan.exec(cmd, self._describe(cmd))
        for line in translator.report.lines():
            plan.warn(line)
        self.report = translator.report
        return plan

    @staticmethod
    def _describe(cmd: List[str]) -> str:
        """Return a one-line description of a VBoxManage command."""
        verb = cmd[1] if len(cmd) > 1 else "?"
        labels = {
            "createvm": "register the VM",
            "modifyvm": "apply VM settings",
            "storagectl": "add a storage controller",
            "createmedium": "create a medium",
            "storageattach": "attach a device",
            "modifynvram": "enrol secure-boot keys",
            "unregistervm": "unregister the VM",
            "controlvm": "change the VM's run state",
            "startvm": "start the VM",
        }
        detail = ""
        for flag in ("--name", "--storagectl", "--filename"):
            if flag in cmd:
                detail = f" ({cmd[cmd.index(flag) + 1]})"
                break
        return f"{labels.get(verb, verb)}{detail}"

    # -- storage topology ----------------------------------------------------

    #: Attachment ``--type`` per device kind.
    ATTACH_TYPES = {
        DiskType.HDD: "hdd",
        DiskType.SSD: "hdd",
        DiskType.DVD: "dvddrive",
        DiskType.FLOPPY: "fdd",
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
            spec = self.capabilities.bus(disk.controller)
            if spec is None:
                raise ProviderError(
                    f"disk {disk.name!r} asks for the "
                    f"{disk.controller.value!r} bus, which this provider does "
                    f"not support"
                )
            name = spec.controller_name
            if name not in resolved:
                synthesized = StorageControllerConfig(
                    name=name,
                    controller_type=disk.controller,
                    port_count=spec.default_ports,
                    bootable=spec.bootable,
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
        spec = self.capabilities.bus(controller.controller_type)
        if spec is None:
            raise ProviderError(
                f"controller {controller.name!r} uses the "
                f"{controller.controller_type.value!r} bus, which this provider "
                f"does not support"
            )
        # Several buses accept exactly one port count -- IDE 2, SCSI 16, USB 8,
        # floppy 1 -- so the requested value is clamped to what will be accepted
        # rather than passed through to fail.
        port_count = spec.clamp_ports(controller.port_count)

        return [
            "VBoxManage",
            "storagectl",
            self.vm_name,
            "--name",
            controller.name,
            "--add",
            spec.add,
            "--controller",
            spec.model,
            "--portcount",
            str(port_count),
            "--bootable",
            "on" if controller.bootable else "off",
        ]

    # -- editing an existing VM ---------------------------------------------

    #: Changes vmctl cannot apply in place, reported rather than silently ignored.
    UNSUPPORTED_EDITS = (
        ("disks", "storage layout"),
        ("storage_controllers", "storage controllers"),
        ("networks", "network adapters"),
        ("ostype", "guest OS type"),
    )

    def emit_modify_vm(self, current: VMConfig, desired: VMConfig) -> Plan:
        """Generate the plan that turns *current* into *desired*.

        Only differences are emitted, so editing one setting produces one
        command rather than re-applying everything. Requested changes that
        cannot be applied in place land in :attr:`Plan.warnings` rather than
        being dropped silently.

        Args:
            current: The VM as it exists now.
            desired: The VM as it should be.

        Returns:
            Plan: The steps to run, possibly empty, plus any warnings.
        """
        plan = Plan("virtualbox")
        commands = []

        # Renaming first: every later command addresses the VM by its new name
        # only if the rename has already happened.
        target = current.name
        if desired.name != current.name:
            commands.append(["VBoxManage", "modifyvm", current.name, "--name", desired.name])
            target = desired.name

        changed = changed_flags(current, desired, MODIFIABLE)
        if changed:
            commands.append(["VBoxManage", "modifyvm", target] + changed)

        for cmd in commands:
            plan.exec(cmd, self._describe(cmd))

        for attr, label in self.UNSUPPORTED_EDITS:
            if getattr(current, attr) != getattr(desired, attr):
                plan.warn(
                    f"{label} differs from the VM but cannot be changed in "
                    f"place; it was left alone"
                )

        return plan

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
