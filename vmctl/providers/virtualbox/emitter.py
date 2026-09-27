# providers/virtualbox/emitter.py
"""
Emit VirtualBox commands from VMConfig
"""
import os
import sys
from collections import OrderedDict
from typing import List, Dict, Optional
from ...core.vmconfig import (
    VMConfig,
    DeviceKind,
    Allocation,
    FirmwareType,
    NetworkConfig,
    NetworkType,
    StorageController,
    BusType,
    default_controller_id,
    resolve_controller,
)
from ...core.exceptions import ProviderError
from ...core.mapping import changed_flags, emit_flags
from ...core.naming import check_name, safe_filename
from ...core.plan import Plan
from ...core.storage import StorageLocation, directory
from ...core.slots import place
from ...core.oscatalog import family_of
from ...core.translate import Policy, Substitution, Translator
from ...core.capabilities import Capabilities
from .capabilities import VirtualBoxCapabilities
from .tables import GUEST_OS_TO_VBOX, FIELDS, MODIFIABLE, GuestOSCodec, generic_for


class VirtualBoxEmitter:
    """Emit VBoxManage commands to create/configure VMs"""

    #: Used only when no location is supplied. VirtualBox's own default, which
    #: is right for an untouched installation and wrong whenever the user has
    #: configured a different machine folder (F-13).
    FALLBACK_MACHINE_FOLDER = os.path.join(os.path.expanduser("~"), "VirtualBox VMs")

    def __init__(
        self,
        vm_name: str,
        location: Optional[StorageLocation] = None,
        capabilities: Optional[Capabilities] = None,
        policy: Policy = Policy.STRICT,
    ):
        """Initialise the emitter for a specific VM.

        Args:
            vm_name: Name of the VM every command will reference.
            location: Where new media go, and how paths are spelled there. The
                backend supplies the one that matches the target host -- the
                emitter must not look it up, because the target may not be the
                machine vmctl is running on (F-13, A-09).
            capabilities: Provider limits to emit within.
            policy: What to do about values VirtualBox does not support.
        """
        self.vm_name = vm_name
        self.capabilities = capabilities or VirtualBoxCapabilities.get()
        self.policy = policy
        self.location = location or directory(self.FALLBACK_MACHINE_FOLDER, nest_per_vm=True)
        self.commands: List[List[str]] = []

    def emit_create_vm(self, vm: VMConfig) -> Plan:
        """Generate the plan that creates a VM from a VMConfig.

        Args:
            vm: Configuration to realise.

        Returns:
            Plan: The steps to run, each with a human-readable description.
        """
        # The name goes into medium paths, so check it before building any.
        check_name(vm.name, self.capabilities)

        plan = Plan("virtualbox")
        # Anything that cannot be expressed exactly is recorded here and ends up
        # in the plan's warnings, rather than being changed quietly (A-04).
        translator = Translator(self.capabilities, self.policy)
        commands: List[List[str]] = []

        # Which OS type to create with. The mapping from what VirtualBox
        # *reports* to what it *accepts* is the provider's own field table now,
        # not a literal here: a hand-written subset meant any guest outside it
        # exported to a config that could not be imported (F-29, A-05).
        ostype = self._ostype(vm, translator)

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
        by_bus: Dict[BusType, StorageController] = {}
        for sc in controllers.values():
            by_bus.setdefault(sc.bus, sc)
            commands.append(self._create_storage_controller(sc))

        # Create and attach media.
        for index, (disk, placement) in enumerate(zip(vm.storage, placements)):
            controller = self._match_controller(disk, controllers, by_bus)
            if controller is None:
                raise ProviderError(
                    f"Device {disk.name!r} references controller "
                    f"{disk.controller or disk.bus.value!r}, which is "
                    f"not defined and could not be synthesised. Declared "
                    f"controllers: {', '.join(controllers) or '(none)'}."
                )

            attach_type = self.ATTACH_TYPES.get(disk.kind, "hdd")
            # The bus it really lands on: the controller it matched, which is not
            # always the bus the device asked for.
            placement_bus = controller.bus

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
                chosen_format = translator.format_for(disk, f"storage[{index}].format")
                fmt = self.capabilities.format_spec(chosen_format)
                medium = self.location.image_path(
                    vm.name, f"{vm.name}_{safe_filename(disk.name)}.{fmt.extension}"
                )

                allocation = translator.allocation_for(disk, chosen_format, f"storage[{index}]")
                vbox_variant = "Fixed" if allocation == Allocation.THICK else "Standard"

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

            attach = [
                "VBoxManage",
                "storageattach",
                vm.name,
                "--storagectl",
                self._storagectl_name(controller),
                "--port",
                str(placement.port),
                "--device",
                str(placement.unit),
                "--type",
                attach_type,
                "--medium",
                medium,
            ]
            if not disk.is_removable:
                # These three are properties of the *attachment*, not of the
                # medium -- which is why solid state was never a disk type
                # (M-01) -- and VirtualBox rejects all of them on a dvddrive.
                if disk.nonrotational:
                    attach += ["--nonrotational", "on"]
                if disk.discard:
                    attach += ["--discard", "on"]
                if disk.hotpluggable:
                    spec = self.capabilities.bus(placement_bus)
                    if spec is not None and spec.hotplug:
                        attach += ["--hotpluggable", "on"]
                    else:
                        # Measured: only SATA and USB accept the flag. Saying
                        # nothing would hand back a device the guest cannot
                        # detach under a config that asked for one.
                        translator.drop(
                            f"storage[{index}].hotpluggable",
                            True,
                            f"a {placement_bus.value} controller does not accept "
                            f"the hot-pluggable flag",
                        )
            commands.append(attach)

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

        # Everything a strict policy refused is reported together, so a user
        # sees all of it in one pass rather than one problem per run.
        translator.finish()

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
        DeviceKind.DISK: "hdd",
        DeviceKind.CDROM: "dvddrive",
        DeviceKind.FLOPPY: "fdd",
    }

    def _resolve_controllers(self, vm: VMConfig) -> Dict[str, StorageController]:
        """Return the controllers to create, keyed by logical id.

        A hand-written config names a bus (``bus: sata``) without declaring a
        controller, and the old code emitted no ``storagectl`` at all while still
        emitting ``storageattach --storagectl SATA`` -- which fails, because
        nothing by that name exists. Every bus a device references therefore gets
        a controller synthesised if the config did not declare one (F-01).

        Args:
            vm: The configuration being emitted.

        Returns:
            dict: controller id -> controller, in creation order (declared
            controllers first, then synthesised ones).
        """
        resolved: Dict[str, StorageController] = OrderedDict(
            (sc.id, sc) for sc in vm.storage_controllers
        )

        by_bus: Dict[BusType, StorageController] = {}
        for sc in vm.storage_controllers:
            by_bus.setdefault(sc.bus, sc)

        for disk in vm.storage:
            if self._match_controller(disk, resolved, by_bus) is not None:
                continue
            spec = self.capabilities.bus(disk.bus)
            if spec is None:
                raise ProviderError(
                    f"disk {disk.name!r} asks for the "
                    f"{disk.bus.value!r} bus, which this provider does "
                    f"not support"
                )
            identity = default_controller_id(disk.bus)
            if identity not in resolved:
                synthesized = StorageController(
                    id=identity,
                    bus=disk.bus,
                    native_name=spec.controller_name,
                    port_count=spec.default_ports,
                    bootable=spec.bootable,
                )
                resolved[identity] = synthesized
                by_bus.setdefault(disk.bus, synthesized)
        return resolved

    @staticmethod
    def _match_controller(disk, resolved, by_bus):
        """Find the controller a device should attach to, or None.

        Delegates to :func:`~vmctl.core.vmconfig.resolve_controller` so the
        emitter and the validator place devices identically (F-01/F-15). A device
        may name a controller by its logical id or by VirtualBox's own name for
        it -- a 1.1.x config did the latter, and still does.
        """
        by_native = {sc.native_name: sc for sc in resolved.values() if sc.native_name}
        return resolve_controller(disk, resolved, by_bus, by_native)

    def _ostype(self, vm: VMConfig, translator: Translator) -> str:
        """Return the OS type id ``createvm`` will accept.

        Args:
            vm: The configuration being emitted.
            translator: Records what could not be expressed exactly.

        Returns:
            A VirtualBox OS type id.

        Raises:
            ValidationError: Under ``strict``, when the guest OS is one
                VirtualBox does not know.
        """
        codec = GuestOSCodec()
        try:
            return codec.dump(vm.guest_os)
        except ValueError as exc:
            # Falling back silently would create the VM under the wrong guest
            # type, which changes the defaults VirtualBox picks for it.
            generic = generic_for(GUEST_OS_TO_VBOX.get(family_of(vm.guest_os).value, ""))
            if not translator.policy.may_substitute:
                translator._refuse("guest_os", vm.guest_os, str(exc), generic)
                return generic
            translator.report.substitutions.append(
                Substitution(
                    "guest_os",
                    vm.guest_os,
                    generic,
                    "VirtualBox has no OS type by that name",
                )
            )
            return generic

    @staticmethod
    def _storagectl_name(controller: StorageController) -> str:
        """Return the name VBoxManage addresses this controller by."""
        return controller.native_name or controller.id

    def _create_storage_controller(self, controller: StorageController) -> List[str]:
        """Generate the command that creates one storage controller."""
        spec = self.capabilities.bus(controller.bus)
        if spec is None:
            raise ProviderError(
                f"controller {self._storagectl_name(controller)!r} uses the "
                f"{controller.bus.value!r} bus, which this provider "
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
            self._storagectl_name(controller),
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
        ("storage", "storage layout"),
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
