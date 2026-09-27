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

from typing import List, Optional

from ..core.capabilities import Capabilities
from ..core.exceptions import ValidationError
from ..core import oscatalog
from ..core.naming import check_name
from ..core.platform import describe_topology, topology_product
from ..core.slots import place
from ..core.translate import Policy, Translator
from ..core.vmconfig import BusType, DeviceKind, FirmwareType, VMConfig


class VMValidator:
    """Validate VM configurations against schema and provider constraints."""

    # Note on StorageDevice.bootable: VirtualBox has no per-disk bootable flag --
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

    def validate(self, vm: VMConfig, policy: Policy = Policy.STRICT) -> List[str]:
        """Validate a VM configuration.

        Args:
            vm: The configuration to check. Not modified.
            policy: What the caller intends to do about unsupported values. Under
                ``strict`` an unsupported bus or format is an error here; under
                ``nearest`` it is a warning, because the emitter's translator
                will substitute a supported value and report what it changed.
                Without this the validator refused first and ``--policy nearest``
                could never take effect.

        Returns:
            list[str]: Human-readable warnings, empty if there are none.

        Raises:
            ValidationError: If the configuration cannot be created.
        """
        warnings: List[str] = []
        self._validate_schema(vm)
        self._validate_provider_limits(vm, policy, warnings)
        self._validate_storage_topology(vm)
        self._validate_logical_constraints(vm, warnings)
        return warnings

    # -- errors --------------------------------------------------------------

    def _validate_schema(self, vm: VMConfig) -> None:
        """Check the values the model itself requires."""
        # A name ends up in file paths and, for some providers, inside a
        # document, so a separator in one redirects where files are written. The
        # same function the emitters call, so there is one rule (A-08).
        check_name(vm.name, self.capabilities)

        if vm.cpu.count < 1:
            raise ValidationError("CPU count must be >= 1", field="cpu.count", value=vm.cpu.count)

        # libvirt refuses a domain whose topology does not multiply out to its
        # vCPU count ("CPU topology doesn't match maximum vcpu count"), so this is
        # a real constraint rather than a stylistic one. It is checked here for
        # every provider, because a topology that contradicts the count is wrong
        # regardless of whether the target happens to enforce it.
        if vm.cpu.has_topology:
            described = topology_product(vm.cpu.sockets, vm.cpu.cores, vm.cpu.threads)
            if described != vm.cpu.count:
                raise ValidationError(
                    f"cpu topology describes {described} vCPU(s) "
                    f"({describe_topology(vm.cpu.sockets, vm.cpu.cores, vm.cpu.threads)}) "
                    f"but cpu.count is {vm.cpu.count}",
                    field="cpu",
                    value=described,
                    expected=str(vm.cpu.count),
                    recovery_hint="Set cpu.count to the product, or drop the topology "
                    "and let the provider choose one.",
                )

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

        for i, disk in enumerate(vm.storage):
            where = f"storage[{i}] ({disk.name})"
            if disk.is_removable or disk.size_mb is None:
                # A DVD or floppy drive has no size of its own.
                continue
            if disk.size_mb < 10:
                raise ValidationError(
                    f"{where}: size must be at least 10 MB",
                    field=f"storage[{i}].size_mb",
                    value=disk.size_mb,
                )
            if disk.size_mb > 1024 * 1024:
                raise ValidationError(
                    f"{where}: size exceeds the 1 TB limit",
                    field=f"storage[{i}].size_mb",
                    value=disk.size_mb,
                )

    def _validate_provider_limits(
        self,
        vm: VMConfig,
        policy: Policy = Policy.STRICT,
        warnings: Optional[List[str]] = None,
    ) -> None:
        """Check the configuration against the provider's declared limits.

        Args:
            vm: The configuration to check.
            policy: Decides whether a substitutable value is an error or a
                warning here.
            warnings: Where to record substitutable findings.
        """

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

        if len(vm.storage) > caps.max_disks:
            raise ValidationError(
                f"{len(vm.storage)} disks configured, but the provider supports "
                f"at most {caps.max_disks}",
                field="storage",
                value=len(vm.storage),
                expected=f"<= {caps.max_disks}",
            )

        for i, sc in enumerate(vm.storage_controllers):
            spec = caps.bus(sc.bus)
            if spec is None:
                raise ValidationError(
                    f"Storage controller type {sc.bus.value!r} is "
                    f"not supported by this provider",
                    field=f"storage_controllers[{i}].bus",
                    value=sc.bus.value,
                    expected=" | ".join(sorted(b.value for b in caps.buses)),
                )
            if sc.port_count is None:
                continue
            fixed = spec.fixed_port_count
            if fixed is not None and sc.port_count != fixed:
                raise ValidationError(
                    f"Controller {sc.native_name or sc.id!r}: a {sc.bus.value} "
                    f"controller must have exactly {fixed} port(s), not "
                    f"{sc.port_count}",
                    field=f"storage_controllers[{i}].port_count",
                    value=sc.port_count,
                    expected=str(fixed),
                )
            if not fixed and not spec.min_ports <= sc.port_count <= spec.max_ports:
                raise ValidationError(
                    f"Controller {sc.native_name or sc.id!r}: port count {sc.port_count} is "
                    f"outside the {sc.bus.value} range "
                    f"{spec.min_ports}-{spec.max_ports}",
                    field=f"storage_controllers[{i}].port_count",
                    value=sc.port_count,
                    expected=f"{spec.min_ports}-{spec.max_ports}",
                )

        # Which bus carries which device kind, and which formats can be created,
        # are the translator's questions: it knows the policy, and it collects
        # every problem before reporting them together. Checking here as well
        # meant refusing on the first one, and made --policy nearest impossible.
        translator = Translator(caps, policy)
        for i, disk in enumerate(vm.storage):
            translator.bus_for(disk, f"storage[{i}].bus")
            if not disk.is_removable:
                translator.format_for(disk, f"storage[{i}].format")
        translator.finish()

    def _validate_storage_topology(self, vm: VMConfig) -> None:
        """Check that the storage layout is physically possible.

        A port/device clash used to pass validation and fail inside VBoxManage
        partway through a create, leaving a half-built VM.
        """
        disk_names = [d.name for d in vm.storage]
        if len(disk_names) != len(set(disk_names)):
            dupes = sorted({n for n in disk_names if disk_names.count(n) > 1})
            raise ValidationError(
                f"Disk names must be unique; repeated: {', '.join(dupes)}", field="storage"
            )

        for key, label in (("id", "ids"), ("native_name", "names")):
            values = [getattr(sc, key) for sc in vm.storage_controllers if getattr(sc, key)]
            if len(values) != len(set(values)):
                dupes = sorted({n for n in values if values.count(n) > 1})
                raise ValidationError(
                    f"Storage controller {label} must be unique; repeated: " f"{', '.join(dupes)}",
                    field="storage_controllers",
                    recovery_hint=(
                        (
                            "Two controllers on the same bus get the same default id; "
                            "give one of them an explicit 'id'."
                        )
                        if key == "id"
                        else None
                    ),
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
                f"{self.capabilities.provider} may round it"
            )

        # The guest OS used to be unwarnable: VirtualBox reports descriptions
        # ("Ubuntu (64-bit)") and accepts ids ("Ubuntu_64"), so any static
        # comparison flagged every real VM. Since A-05 there is a neutral
        # catalogue and each provider lists what it accepts besides, so a typo
        # can be told from a deliberate passthrough.
        if vm.guest_os and oscatalog.get(vm.guest_os) is None:
            known = self.capabilities.supported_os_types
            if known and vm.guest_os not in known:
                warnings.append(
                    f"guest_os is {vm.guest_os!r}, which is neither one of vmctl's "
                    f"ids ({', '.join(oscatalog.ids()[:4])}, ...) nor a guest OS "
                    f"{self.capabilities.provider} knows"
                )

        if "disk" in vm.boot.order and not vm.storage:
            raise ValidationError(
                "Boot order includes 'disk' but no disks are defined", field="boot.order"
            )

        # Warn only when *nothing* in the boot order can be satisfied. Warning
        # per-device would fire constantly: VirtualBox's default order is
        # floppy, dvd, disk, and most VMs have neither a floppy nor a DVD.
        available = {
            "disk": any(not d.is_removable for d in vm.storage),
            "dvd": any(d.kind == DeviceKind.CDROM for d in vm.storage),
            "floppy": any(d.kind == DeviceKind.FLOPPY for d in vm.storage),
            "network": True,
        }
        wanted = [d for d in vm.boot.order if d != "none"]
        if wanted and not any(available.get(d) for d in wanted):
            warnings.append(
                f"boot order is {', '.join(wanted)} but the VM has none of "
                f"those devices; it will not boot"
            )

        for i, disk in enumerate(vm.storage):
            if disk.is_removable and disk.size_mb:
                warnings.append(
                    f"storage[{i}] ({disk.name}) is a {disk.kind.value} drive, so "
                    f"size_mb={disk.size_mb} is ignored"
                )

        # A device may name a controller that nothing declares. That is
        # deliberate -- "controller: SATA" means "whichever controller serves
        # SATA", which is what made the old magic default work (F-01) -- but it
        # is also what a typo looks like, so it is said out loud. The two
        # readings are named in the message because `controller` meant a bus
        # before M-02 and means a controller now.
        declared = {sc.id for sc in vm.storage_controllers}
        declared |= {sc.native_name for sc in vm.storage_controllers if sc.native_name}
        buses = {b.value for b in BusType}
        for i, disk in enumerate(vm.storage):
            named = disk.controller
            if not named or named in declared:
                continue
            landing = disk.bus.value
            if named.strip().lower() in buses:
                warnings.append(
                    f"storage[{i}] ({disk.name}) names controller {named!r}, "
                    f"which nothing declares; it will go on the first "
                    f"{landing} controller"
                )
            else:
                warnings.append(
                    f"storage[{i}] ({disk.name}) names controller {named!r}, "
                    f"which is neither a declared controller nor a bus, so it "
                    f"will go on the first {landing} controller. Declared: "
                    f"{', '.join(sorted(declared)) or '(none)'}; buses: "
                    f"{', '.join(sorted(buses))}"
                )

        for i, net in enumerate(vm.networks):
            if net.needs_adapter_name and not net.adapter_name:
                warnings.append(
                    f"networks[{i}] is {net.network_type.value} but names no "
                    f"adapter; the VM will have no network until one is set"
                )

        if vm.cpu.count > 1 and not vm.boot.ioapic:
            # x86 SMP needs an I/O APIC to route interrupts to more than one CPU.
            # The message used to name VirtualBox, which then turned up while
            # creating a libvirt domain -- a provider's name in shared code (F-30).
            warnings.append(
                f"more than one CPU is configured but ioapic is off; an x86 guest "
                f"needs an I/O APIC to use them, and {self.capabilities.provider} "
                f"will not give it one"
            )
