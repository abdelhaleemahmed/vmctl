"""
Validation layer for VM configurations.

Two kinds of finding, kept strictly apart:

* **errors** raise :class:`~vmctl.core.exceptions.ValidationError` -- the
  configuration cannot be created as written;
* **warnings** are returned as a list of strings -- the configuration will be
  created, but probably not as the author intended.

The validator is **pure**: it never modifies the configuration it is given. It
used to silently flip ``disks[0].bootable`` instead of reporting anything, which
meant a caller's object changed under them and no warning was ever produced
(F-08).
"""

from typing import Any, Dict, List, Tuple

from ..core.exceptions import ValidationError
from ..core.vmconfig import (
    DiskType,
    FirmwareType,
    StorageControllerType,
    VMConfig,
)


class VMValidator:
    """Validate VM configurations against schema and provider constraints."""

    # Note on DiskConfig.bootable: VirtualBox has no per-disk bootable flag --
    # boot selection is the VM's boot order plus the controller's --bootable
    # setting. The field is therefore decorative, nothing emits it, and warning
    # about it would be warning about our own artefact. M-02 should either give
    # it a meaning or drop it.

    def __init__(self, provider_capabilities: Dict[str, Any]):
        """Initialise the validator with provider-specific capability limits.

        Args:
            provider_capabilities: Dictionary of provider limits (e.g. max CPUs,
                max memory, max VRAM). Typically obtained from
                :meth:`VirtualBoxCapabilities.get_capabilities`.
        """
        self.capabilities = provider_capabilities

    # -- entry point ---------------------------------------------------------

    def validate(self, vm: VMConfig) -> List[str]:
        """Validate a VM configuration.

        Args:
            vm: The configuration to check. Not modified.

        Returns:
            list[str]: Human-readable warnings, empty if there are none.

        Raises:
            ValidationError: If the configuration cannot be created.
        """
        warnings: List[str] = []
        self._validate_schema(vm)
        self._validate_provider_limits(vm)
        self._validate_storage_topology(vm)
        self._validate_logical_constraints(vm, warnings)
        return warnings

    # -- errors --------------------------------------------------------------

    def _validate_schema(self, vm: VMConfig) -> None:
        """Check the values the model itself requires."""
        if not vm.name or not isinstance(vm.name, str):
            raise ValidationError("VM name must be a non-empty string", field="name", value=vm.name)

        if vm.cpu.count < 1:
            raise ValidationError("CPU count must be >= 1", field="cpu.count", value=vm.cpu.count)

        if not 1 <= vm.cpu.execution_cap <= 100:
            raise ValidationError(
                "CPU execution cap must be a percentage between 1 and 100",
                field="cpu.execution_cap",
                value=vm.cpu.execution_cap,
                expected="1-100",
            )

        if vm.memory.mb < 4:
            raise ValidationError(
                "Memory must be at least 4 MB", field="memory.mb", value=vm.memory.mb
            )

        if vm.memory.vram_mb < 1:
            raise ValidationError(
                "VRAM must be at least 1 MB", field="memory.vram_mb", value=vm.memory.vram_mb
            )

        for i, disk in enumerate(vm.disks):
            where = f"disks[{i}] ({disk.name})"
            if disk.is_removable:
                # A DVD or floppy drive has no size of its own.
                continue
            if disk.size_mb < 10:
                raise ValidationError(
                    f"{where}: size must be at least 10 MB",
                    field=f"disks[{i}].size_mb",
                    value=disk.size_mb,
                )
            if disk.size_mb > 1024 * 1024:
                raise ValidationError(
                    f"{where}: size exceeds the 1 TB limit",
                    field=f"disks[{i}].size_mb",
                    value=disk.size_mb,
                )

    def _validate_provider_limits(self, vm: VMConfig) -> None:
        """Check the configuration against the provider's declared limits."""
        caps = self.capabilities

        max_cpus = caps.get("max_cpus", 128)
        if vm.cpu.count > max_cpus:
            raise ValidationError(
                f"CPU count {vm.cpu.count} exceeds the provider limit {max_cpus}",
                field="cpu.count",
                value=vm.cpu.count,
                expected=f"<= {max_cpus}",
            )

        max_memory = caps.get("max_memory_mb", 1_048_576)
        if vm.memory.mb > max_memory:
            raise ValidationError(
                f"Memory {vm.memory.mb}MB exceeds the provider limit {max_memory}MB",
                field="memory.mb",
                value=vm.memory.mb,
                expected=f"<= {max_memory}",
            )

        max_vram = caps.get("max_vram_mb", 256)
        if vm.memory.vram_mb > max_vram:
            raise ValidationError(
                f"VRAM {vm.memory.vram_mb}MB exceeds the provider limit {max_vram}MB",
                field="memory.vram_mb",
                value=vm.memory.vram_mb,
                expected=f"<= {max_vram}",
            )

        if vm.firmware.type != FirmwareType.BIOS and not caps.get("supports_efi", True):
            raise ValidationError(
                "EFI firmware is not supported by this provider",
                field="firmware.type",
                value=vm.firmware.type.value,
            )

        if vm.firmware.tpm and not caps.get("supports_tpm", True):
            raise ValidationError("TPM is not supported by this provider", field="firmware.tpm")

        # Network adapters: read the limit from capabilities rather than
        # hardcoding 8, and name the provider's number in the message.
        max_nics = caps.get("max_network_adapters", 8)
        if len(vm.networks) > max_nics:
            raise ValidationError(
                f"{len(vm.networks)} network adapters configured, but the "
                f"provider supports at most {max_nics}",
                field="networks",
                value=len(vm.networks),
                expected=f"<= {max_nics}",
            )

        supported_nets = caps.get("supported_network_types")
        if supported_nets:
            for i, net in enumerate(vm.networks):
                if net.network_type.value not in supported_nets:
                    raise ValidationError(
                        f"Network type {net.network_type.value!r} is not "
                        f"supported by this provider",
                        field=f"networks[{i}].network_type",
                        value=net.network_type.value,
                        expected=" | ".join(supported_nets),
                    )

        max_disks = caps.get("max_disks")
        if max_disks and len(vm.disks) > max_disks:
            raise ValidationError(
                f"{len(vm.disks)} disks configured, but the provider supports "
                f"at most {max_disks}",
                field="disks",
                value=len(vm.disks),
                expected=f"<= {max_disks}",
            )

        supported_ctls = caps.get("supported_storage_controllers")
        port_limits = caps.get("max_ports_per_controller", {})
        for i, sc in enumerate(vm.storage_controllers):
            bus = sc.controller_type.value
            if supported_ctls and bus not in supported_ctls:
                raise ValidationError(
                    f"Storage controller type {bus!r} is not supported by this " f"provider",
                    field=f"storage_controllers[{i}].controller_type",
                    value=bus,
                    expected=" | ".join(supported_ctls),
                )
            max_ports = port_limits.get(bus)
            if max_ports and sc.port_count and sc.port_count > max_ports:
                raise ValidationError(
                    f"Controller {sc.name!r}: port count {sc.port_count} "
                    f"exceeds the limit {max_ports} for {bus}",
                    field=f"storage_controllers[{i}].port_count",
                    value=sc.port_count,
                    expected=f"<= {max_ports}",
                )

    def _validate_storage_topology(self, vm: VMConfig) -> None:
        """Check that the storage layout is physically possible.

        A port/device clash used to pass validation and fail inside VBoxManage
        partway through a create, leaving a half-built VM.
        """
        disk_names = [d.name for d in vm.disks]
        if len(disk_names) != len(set(disk_names)):
            dupes = sorted({n for n in disk_names if disk_names.count(n) > 1})
            raise ValidationError(
                f"Disk names must be unique; repeated: {', '.join(dupes)}", field="disks"
            )

        sc_names = [sc.name for sc in vm.storage_controllers]
        if len(sc_names) != len(set(sc_names)):
            dupes = sorted({n for n in sc_names if sc_names.count(n) > 1})
            raise ValidationError(
                f"Storage controller names must be unique; repeated: " f"{', '.join(dupes)}",
                field="storage_controllers",
            )

        # Two devices cannot share one (controller, port, device) slot.
        seen: Dict[Tuple[str, int, int], Tuple[int, str]] = {}
        for i, disk in enumerate(vm.disks):
            # Compare the *resolved* controller, not the raw name: an unset name
            # means "whichever controller serves this bus", so two devices on
            # different buses must not look like a clash.
            resolved = vm.controller_for(disk)
            controller = resolved.name if resolved else disk.controller.value
            slot = (controller, disk.port, disk.device)
            if slot in seen:
                raise ValidationError(
                    f"disks[{i}] ({disk.name}) and disks[{seen[slot][0]}] "
                    f"({seen[slot][1]}) are both attached to controller "
                    f"{controller!r} port {disk.port} device {disk.device}",
                    field=f"disks[{i}]",
                    recovery_hint="Give each device its own port or device number.",
                )
            seen[slot] = (i, disk.name)

        # A device cannot sit on a port the controller does not have.
        for i, disk in enumerate(vm.disks):
            sc = vm.controller_for(disk)
            if sc is None or not sc.port_count:
                continue
            if disk.port >= sc.port_count:
                raise ValidationError(
                    f"disks[{i}] ({disk.name}) uses port {disk.port} on "
                    f"controller {sc.name!r}, which has {sc.port_count} "
                    f"port(s) (0-{sc.port_count - 1})",
                    field=f"disks[{i}].port",
                    value=disk.port,
                    expected=f"0-{sc.port_count - 1}",
                )
            units = 2 if sc.controller_type == StorageControllerType.IDE else 1
            if disk.device >= units:
                raise ValidationError(
                    f"disks[{i}] ({disk.name}) uses device {disk.device} on a "
                    f"{sc.controller_type.value} controller, which allows "
                    f"{units} device(s) per port",
                    field=f"disks[{i}].device",
                    value=disk.device,
                    expected=f"0-{units - 1}",
                )

        # Secure boot needs EFI; VirtualBox refuses the combination outright.
        if vm.firmware.secure_boot and vm.firmware.type == FirmwareType.BIOS:
            raise ValidationError(
                "Secure boot requires EFI firmware",
                field="firmware.secure_boot",
                recovery_hint="Set firmware.type to efi, efi32 or efi64.",
            )

        valid_boot_devices = ["none", "floppy", "dvd", "disk", "network"]
        for device in vm.boot.order:
            if device not in valid_boot_devices:
                raise ValidationError(
                    f"Invalid boot device: {device}",
                    field="boot.order",
                    value=device,
                    expected=" | ".join(valid_boot_devices),
                )

    # -- warnings ------------------------------------------------------------

    def _validate_logical_constraints(self, vm: VMConfig, warnings: List[str]) -> None:
        """Collect things that are legal but probably unintended."""
        if vm.memory.mb % 4 != 0:
            warnings.append(
                f"memory.mb is {vm.memory.mb}, which is not a multiple of 4; "
                f"VirtualBox may round it"
            )

        # ostype is deliberately NOT warned about. VirtualBox reports display
        # names ("Ubuntu (64-bit)") while the capability list holds internal ids
        # ("Ubuntu_64"), so any static comparison flags every real VM. Doing
        # this properly means asking the host via `VBoxManage list ostypes` --
        # E-05 in PLAN.md. A warning that fires on correct input is worse than
        # no warning.

        if "disk" in vm.boot.order and not vm.disks:
            raise ValidationError(
                "Boot order includes 'disk' but no disks are defined", field="boot.order"
            )

        # Warn only when *nothing* in the boot order can be satisfied. Warning
        # per-device would fire constantly: VirtualBox's default order is
        # floppy, dvd, disk, and most VMs have neither a floppy nor a DVD.
        available = {
            "disk": any(not d.is_removable for d in vm.disks),
            "dvd": any(d.type == DiskType.DVD for d in vm.disks),
            "floppy": any(d.type == DiskType.FLOPPY for d in vm.disks),
            "network": True,
        }
        wanted = [d for d in vm.boot.order if d != "none"]
        if wanted and not any(available.get(d) for d in wanted):
            warnings.append(
                f"boot order is {', '.join(wanted)} but the VM has none of "
                f"those devices; it will not boot"
            )

        for i, disk in enumerate(vm.disks):
            if disk.is_removable and disk.size_mb:
                warnings.append(
                    f"disks[{i}] ({disk.name}) is a {disk.type.value} drive, so "
                    f"size_mb={disk.size_mb} is ignored"
                )

        for i, net in enumerate(vm.networks):
            if net.needs_adapter_name and not net.adapter_name:
                warnings.append(
                    f"networks[{i}] is {net.network_type.value} but names no "
                    f"adapter; the VM will have no network until one is set"
                )

        if vm.cpu.count > 1 and not vm.boot.ioapic:
            warnings.append(
                "more than one CPU is configured but ioapic is off; "
                "VirtualBox requires I/O APIC for SMP"
            )
