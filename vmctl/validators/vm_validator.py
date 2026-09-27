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

from typing import List

from ..core.capabilities import Capabilities
from ..core.exceptions import ValidationError
from ..core.slots import place
from ..core.vmconfig import DiskType, FirmwareType, VMConfig


class VMValidator:
    """Validate VM configurations against schema and provider constraints."""

    # Note on DiskConfig.bootable: VirtualBox has no per-disk bootable flag --
    # boot selection is the VM's boot order plus the controller's --bootable
    # setting. The field is therefore decorative, nothing emits it, and warning
    # about it would be warning about our own artefact. M-02 should either give
    # it a meaning or drop it.

    def __init__(self, provider_capabilities: Capabilities):
        """Initialise the validator with a provider's capability declaration.

        Args:
            provider_capabilities: The provider's limits, bus rules, attach
                matrix and format support, e.g. from
                :meth:`VirtualBoxCapabilities.get`. Every limit checked below is
                read from here rather than restated as a literal (A-02).
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

        if vm.cpu.count > caps.max_cpus:
            raise ValidationError(
                f"CPU count {vm.cpu.count} exceeds the provider limit {caps.max_cpus}",
                field="cpu.count",
                value=vm.cpu.count,
                expected=f"<= {caps.max_cpus}",
            )

        if vm.memory.mb > caps.max_memory_mb:
            raise ValidationError(
                f"Memory {vm.memory.mb}MB exceeds the provider limit " f"{caps.max_memory_mb}MB",
                field="memory.mb",
                value=vm.memory.mb,
                expected=f"<= {caps.max_memory_mb}",
            )

        if vm.memory.vram_mb > caps.max_vram_mb:
            raise ValidationError(
                f"VRAM {vm.memory.vram_mb}MB exceeds the provider limit " f"{caps.max_vram_mb}MB",
                field="memory.vram_mb",
                value=vm.memory.vram_mb,
                expected=f"<= {caps.max_vram_mb}",
            )

        firmware_support = caps.firmware.get(vm.firmware.type)
        if firmware_support is not None and not firmware_support.usable:
            raise ValidationError(
                f"{vm.firmware.type.value} firmware is not supported by this " f"provider",
                field="firmware.type",
                value=vm.firmware.type.value,
            )

        if vm.firmware.tpm and not caps.supports_tpm:
            raise ValidationError("TPM is not supported by this provider", field="firmware.tpm")

        if len(vm.networks) > caps.max_network_adapters:
            raise ValidationError(
                f"{len(vm.networks)} network adapters configured, but the "
                f"provider supports at most {caps.max_network_adapters}",
                field="networks",
                value=len(vm.networks),
                expected=f"<= {caps.max_network_adapters}",
            )

        if caps.supported_network_types:
            for i, net in enumerate(vm.networks):
                if net.network_type.value not in caps.supported_network_types:
                    raise ValidationError(
                        f"Network type {net.network_type.value!r} is not "
                        f"supported by this provider",
                        field=f"networks[{i}].network_type",
                        value=net.network_type.value,
                        expected=" | ".join(caps.supported_network_types),
                    )

        if len(vm.disks) > caps.max_disks:
            raise ValidationError(
                f"{len(vm.disks)} disks configured, but the provider supports "
                f"at most {caps.max_disks}",
                field="disks",
                value=len(vm.disks),
                expected=f"<= {caps.max_disks}",
            )

        for i, sc in enumerate(vm.storage_controllers):
            spec = caps.bus(sc.controller_type)
            if spec is None:
                raise ValidationError(
                    f"Storage controller type {sc.controller_type.value!r} is "
                    f"not supported by this provider",
                    field=f"storage_controllers[{i}].controller_type",
                    value=sc.controller_type.value,
                    expected=" | ".join(sorted(b.value for b in caps.buses)),
                )
            if sc.port_count is None:
                continue
            fixed = spec.fixed_port_count
            if fixed is not None and sc.port_count != fixed:
                raise ValidationError(
                    f"Controller {sc.name!r}: a {sc.controller_type.value} "
                    f"controller must have exactly {fixed} port(s), not "
                    f"{sc.port_count}",
                    field=f"storage_controllers[{i}].port_count",
                    value=sc.port_count,
                    expected=str(fixed),
                )
            if not fixed and not spec.min_ports <= sc.port_count <= spec.max_ports:
                raise ValidationError(
                    f"Controller {sc.name!r}: port count {sc.port_count} is "
                    f"outside the {sc.controller_type.value} range "
                    f"{spec.min_ports}-{spec.max_ports}",
                    field=f"storage_controllers[{i}].port_count",
                    value=sc.port_count,
                    expected=f"{spec.min_ports}-{spec.max_ports}",
                )

        # Which device kinds each bus carries, and which formats the provider can
        # create, are measured facts about the hypervisor -- see the provider's
        # capability module (M-03).
        for i, disk in enumerate(vm.disks):
            if not caps.can_attach(disk.type, disk.controller):
                usable = caps.buses_for(disk.type)
                raise ValidationError(
                    f"disks[{i}] ({disk.name}) is a {disk.type.value} device on "
                    f"the {disk.controller.value} bus, which this provider does "
                    f"not support",
                    field=f"disks[{i}].controller",
                    value=disk.controller.value,
                    expected=" | ".join(sorted(b.value for b in usable)) or "(none)",
                )
            if disk.is_removable:
                continue
            fmt = caps.format_spec(disk.format)
            if not fmt.support.creatable:
                raise ValidationError(
                    f"disks[{i}] ({disk.name}) uses format "
                    f"{disk.format.value!r}, which this provider cannot create "
                    f"({fmt.support.value})",
                    field=f"disks[{i}].format",
                    value=disk.format.value,
                    expected=" | ".join(sorted(f.value for f in caps.creatable_formats())),
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

        # Placement -- collisions, ports a bus does not have, and devices per
        # port -- is resolved by the shared allocator, so the validator and the
        # emitters cannot disagree about where a device lands (A-06).
        place(vm, self.capabilities)

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
