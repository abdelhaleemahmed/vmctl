"""
Write a ``.vmx``, and the plan that creates the VM around it.

The native artifact is a key/value file, which makes this the third distinct shape
of provider: VirtualBox is a sequence of CLI calls, libvirt is one document handed
to a daemon, QEMU is a command line, and VMware is a file plus a tool per disk.

Two rules come from the product rather than from the model, and both were measured:

* **every key exactly once** -- a duplicate makes VMware refuse to read the file at
  all, so the emitter builds an ordered mapping and writes it, rather than appending
  lines as it goes;
* **the PCI bridges have to be there** or a PCIe device (NVMe, pvscsi) is refused.
"""

import os
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, Optional

from ...core.capabilities import Capabilities
from ...core.devices import DeviceKind
from ...core.exceptions import ProviderError
from ...core.mapping import asks_for_something
from ...core.naming import check_name, safe_filename
from ...core.plan import Plan, Step, StepKind
from ...core.platform import describe_topology
from ...core.slots import place
from ...core.storage import StorageLocation, directory
from ...core.translate import Policy, Substitution, TranslationReport, Translator
from ...core.vmconfig import (
    DEFAULT_DISK_MB,
    NetworkConfig,
    VMConfig,
    keeping_existing_images,
)
from .capabilities import VMwareCapabilities
from .tables import (
    ALLOCATION_TO_DISK_TYPE,
    BOOT_DEVICE,
    BUS_KEYS,
    CONFIG_VERSION,
    EMPTY_CDROM_DEVICE_TYPE,
    FIRMWARE_TO_VMWARE,
    GUEST_OS_FROM_VMWARE,
    GUEST_OS_TO_VMWARE,
    KIND_TO_DEVICE_TYPE,
    NETWORK_TO_CONNECTION,
    NIC_MODEL_TO_VMWARE,
    PCI_BOILERPLATE,
    VIRTUAL_HW_VERSION,
    address,
)

#: Settings VMware has no key for. Reported rather than dropped in silence (A-04).
UNTRANSLATABLE = (
    ("cpu.execution_cap", "VMware limits CPU through resource shares, not a percentage"),
    ("cpu.model", "a .vmx names no CPU model; it masks CPUID features instead"),
    ("machine", "the chipset follows virtualHW.version rather than a machine type"),
    ("memory.page_fusion", "page sharing is a host-wide setting in VMware"),
    ("cpu.pae", "PAE is decided by the guest OS type"),
    # No key for it: VMware's chipset provides one and decides. Reported so a config
    # asking for it is told, rather than reading back as off for ever.
    ("boot.ioapic", "VMware's chipset provides an I/O APIC; there is no setting"),
)

#: The tool that makes a VMDK. Not the same binary as the one that runs VMs.
VDISKMANAGER = "vmware-vdiskmanager"


class VMwareEmitter:
    """Build a ``.vmx`` and the plan that puts a VM around it."""

    def __init__(
        self,
        vm_name: str,
        location: Optional[StorageLocation] = None,
        tools_dir: Optional[str] = None,
        capabilities: Optional[Capabilities] = None,
        policy: Policy = Policy.STRICT,
    ):
        """Initialise the emitter.

        Args:
            vm_name: The VM's name, which is also its directory and file name.
            location: Where the VM's directory goes. A VM is a directory here, as
                it is for VirtualBox -- and the separator belongs to the *target*
                host, which is what lets a plan built on Linux name Windows paths
                (A-09).
            tools_dir: Where ``vmware-vdiskmanager`` and ``vmrun`` live. On Windows
                they are not on PATH, so the backend passes the install directory.
            capabilities: What this VMware supports.
            policy: What to do about values VMware cannot express.
        """
        self.vm_name = vm_name
        self.capabilities = capabilities or VMwareCapabilities.get()
        self.policy = policy
        self.location = location or directory(os.path.expanduser("~/vmware"), nest_per_vm=True)
        self.tools_dir = tools_dir
        self.report: Optional[TranslationReport] = None

    # -- paths ---------------------------------------------------------------

    def tool(self, name: str) -> str:
        """Return how to invoke one of VMware's tools."""
        if not self.tools_dir:
            return name
        separator = self.location.separator
        return f"{self.tools_dir.rstrip(separator)}{separator}{name}"

    def vmx_path(self, vm_name: str) -> str:
        """Return the path of the VM's ``.vmx`` -- the VM, as far as VMware is concerned."""
        return self.location.image_path(vm_name, f"{safe_filename(vm_name)}.vmx")

    def disk_file(self, vm: VMConfig, device) -> str:
        """Return a device's image *file name*, relative to the ``.vmx``.

        Relative on purpose: a ``.vmx`` that names its disks by bare file name can
        be moved or copied, which is how VMware itself writes one.
        """
        return f"{safe_filename(vm.name)}_{safe_filename(device.name)}.vmdk"

    def disk_path(self, vm: VMConfig, device) -> str:
        """Return a device's image path, for the tool that creates it."""
        return self.location.image_path(vm.name, self.disk_file(vm, device))

    # -- the file ------------------------------------------------------------

    def build_vmx(
        self, vm: VMConfig, translator: Optional[Translator] = None
    ) -> "OrderedDict[str, str]":
        """Return the whole ``.vmx`` as an ordered mapping of key to value.

        A mapping rather than a list of lines, because VMware refuses a file with a
        repeated key -- "Cannot read the virtual machine configuration" -- so the
        one-key-one-value rule is enforced by the structure instead of by care.

        Args:
            vm: The configuration to express.
            translator: Records anything that could not be expressed exactly.

        Returns:
            The keys and values, in the order they will be written.
        """
        keys: "OrderedDict[str, str]" = OrderedDict()
        keys[".encoding"] = "UTF-8"
        keys["config.version"] = CONFIG_VERSION
        keys["virtualHW.version"] = VIRTUAL_HW_VERSION
        keys["displayName"] = vm.name
        keys["guestOS"] = self._guest_os(vm, translator)
        keys["memsize"] = str(vm.memory.mb)
        keys["numvcpus"] = str(vm.cpu.count)
        if vm.cpu.cores:
            keys["cpuid.coresPerSocket"] = str(vm.cpu.cores)
        keys["firmware"] = FIRMWARE_TO_VMWARE.get(
            translator.firmware_for(vm) if translator is not None else vm.firmware.type,
            "bios",
        )
        if vm.firmware.secure_boot:
            keys["uefi.secureBoot.enabled"] = "TRUE"
        if vm.firmware.tpm:
            keys["vtpm.present"] = "TRUE"
        if vm.cpu.nested_virt:
            keys["vhv.enable"] = "TRUE"
        keys["nvram"] = f"{safe_filename(vm.name)}.nvram"
        keys["svga.present"] = "TRUE"
        if vm.description:
            keys["annotation"] = vm.description

        order = [BOOT_DEVICE[d] for d in vm.boot.order if d in BOOT_DEVICE]
        if order:
            keys["bios.bootOrder"] = ",".join(order)

        for key, value in PCI_BOILERPLATE:
            keys[key] = value

        self._add_storage(vm, keys, translator)
        if not any(device.kind is DeviceKind.FLOPPY for device in vm.storage):
            # Said out loud, because VMware's defaults add a floppy drive pointed at
            # the host's A: and then report "Could not connect to floppy" on every
            # power-on. A VM vmctl creates has the devices the config asked for.
            keys["floppy0.present"] = "FALSE"
        self._add_networks(vm, keys, translator)

        if vm.usb_enabled:
            keys["usb.present"] = "TRUE"
            keys["ehci.present"] = "TRUE"
        if vm.audio_enabled:
            keys["sound.present"] = "TRUE"
            keys["sound.autodetect"] = "TRUE"

        if translator is not None:
            for path, reason in UNTRANSLATABLE:
                if asks_for_something(vm, path):
                    translator.drop(path, _get(vm, path), reason)
            if vm.cpu.has_topology and vm.cpu.sockets and not vm.cpu.cores:
                # VMware divides the total by cores-per-socket; there is no key
                # that states a socket count on its own.
                translator.drop(
                    "cpu.sockets",
                    describe_topology(vm.cpu.sockets, vm.cpu.cores, vm.cpu.threads),
                    "a .vmx states cores per socket and divides the vCPU count by it",
                )
        return keys

    def _guest_os(self, vm: VMConfig, translator: Optional[Translator]) -> str:
        """Return the ``guestOS`` id, substituting one VMware does not know.

        Measured: an unknown id is refused at power-on with "[msg.guestos.badname]
        Guest operating system 'x' is not supported", so passing one through would
        fail at the last possible moment.
        """
        wanted = (vm.guest_os or "").strip()
        native = GUEST_OS_TO_VMWARE.get(wanted.lower())
        if native:
            # VMware has one Ubuntu id for every Ubuntu, so a version vmctl knows
            # cannot be stored. Losing it is unavoidable; losing it silently is not.
            reverse = GUEST_OS_FROM_VMWARE.get(native, wanted.lower())
            if translator is not None and reverse != wanted.lower():
                translator.report.substitutions.append(
                    Substitution(
                        "guest_os",
                        wanted,
                        reverse,
                        f"VMware has one id for this family ({native}), so the "
                        f"version is not recorded",
                    )
                )
            return native
        if wanted in self.capabilities.supported_os_types:
            return wanted  # already a VMware id
        fallback = GUEST_OS_TO_VMWARE["other"]
        if translator is not None and wanted:
            translator.report.substitutions.append(
                Substitution("guest_os", wanted, fallback, "VMware has no such guest OS id")
            )
        return fallback

    def _add_storage(self, vm: VMConfig, keys: Dict[str, str], translator) -> None:
        """Add every device, its controller, and the keys they need."""
        placements = place(vm, self.capabilities)
        for index, (device, placement) in enumerate(zip(vm.storage, placements)):
            bus = (
                translator.bus_for(device, f"storage[{index}].bus")
                if translator is not None
                else device.bus
            )
            spec = BUS_KEYS.get(bus)
            if spec is None:
                raise ProviderError(
                    f"device {device.name!r} asks for the {bus.value!r} bus, which "
                    f"VMware has no key for"
                )

            controller = f"{spec.prefix}0"
            keys[f"{controller}.present"] = "TRUE"
            if spec.virtual_dev:
                keys[f"{controller}.virtualDev"] = spec.virtual_dev

            at = address(bus, 0, placement.port, placement.unit)
            keys[f"{at}.present"] = "TRUE"

            if device.is_removable:
                medium = device.source
                if medium:
                    keys[f"{at}.fileName"] = medium
                    keys[f"{at}.deviceType"] = KIND_TO_DEVICE_TYPE[device.kind]
                elif device.kind is DeviceKind.CDROM:
                    # A drive with nothing in it is still a drive (F-22) -- but not
                    # the *host's* drive. `autodetect = "TRUE"` makes VMware reach
                    # for the host's optical device, which on a real power-on said
                    # "Unable to process CD-ROM device 'Z:'" and would have shown
                    # whatever disc the host had in it to the guest (F-33).
                    keys[f"{at}.deviceType"] = EMPTY_CDROM_DEVICE_TYPE
                    keys[f"{at}.autodetect"] = "FALSE"
                    keys[f"{at}.startConnected"] = "FALSE"
                else:
                    keys[f"{at}.fileType"] = "file"
            else:
                keys[f"{at}.fileName"] = device.source or self.disk_file(vm, device)
                keys[f"{at}.deviceType"] = KIND_TO_DEVICE_TYPE[DeviceKind.DISK]
                if device.nonrotational:
                    keys[f"{at}.virtualSSD"] = "TRUE"
                if device.readonly:
                    keys[f"{at}.mode"] = "independent-nonpersistent"

    def _add_networks(self, vm: VMConfig, keys: Dict[str, str], translator) -> None:
        """Add the network adapters."""
        for index, net in enumerate(vm.networks):
            prefix = f"ethernet{index}"
            keys[f"{prefix}.present"] = "TRUE"
            keys[f"{prefix}.connectionType"] = NETWORK_TO_CONNECTION.get(net.network_type, "nat")
            keys[f"{prefix}.virtualDev"] = self._nic_model(
                net, f"networks[{index}].model", translator
            )
            if net.network_type.value == "internal" and net.adapter_name:
                # `custom` needs the VMnet named; VMware has no unnamed internal net.
                keys[f"{prefix}.vnet"] = net.adapter_name
            elif net.adapter_name and translator is not None:
                # Measured: adding `ethernetN.vnet` to a *hostonly* adapter makes
                # `vmrun start` answer "The operation was canceled" -- VMware's
                # host-only and NAT adapters use the vmnet it decides, and naming one
                # is only valid for a custom (internal) network. Reported rather than
                # written, since writing it stops the VM starting at all.
                translator.drop(
                    f"networks[{index}].adapter_name",
                    net.adapter_name,
                    f"VMware chooses the vmnet for a {net.network_type.value} adapter; "
                    f"only a custom (internal) network is named",
                )
            if net.mac_address:
                keys[f"{prefix}.addressType"] = "static"
                keys[f"{prefix}.address"] = _format_mac(net.mac_address)
            else:
                keys[f"{prefix}.addressType"] = "generated"
            if net.port_forwards and translator is not None:
                # Workstation configures NAT forwarding host-wide, in `vmnetnat.conf`,
                # not per VM -- so there is no `.vmx` key to write and vmctl is not
                # going to edit a host-wide file behind a user's back (E-10).
                translator.drop(
                    f"networks[{index}].port_forwards",
                    "; ".join(rule.label for rule in net.port_forwards),
                    "VMware has no per-VM port forwarding; its NAT forwards live in "
                    "the host-wide vmnetnat.conf",
                )

    def _nic_model(self, net: NetworkConfig, where: str, translator) -> str:
        """Return the ``virtualDev`` for a NIC model, substituting if it has none."""
        native = NIC_MODEL_TO_VMWARE.get(net.model)
        if native is not None:
            return native
        fallback = self.capabilities.nic_model_fallback()
        if translator is not None and fallback is not None:
            translator.report.substitutions.append(
                Substitution(where, net.model.value, fallback.value, "VMware has no such NIC")
            )
        return NIC_MODEL_TO_VMWARE.get(fallback, "e1000") if fallback else "e1000"

    @staticmethod
    def render(keys: Dict[str, str]) -> str:
        """Return the ``.vmx`` text for a mapping of keys."""
        return "".join(f'{key} = "{value}"\n' for key, value in keys.items())

    # -- plans ---------------------------------------------------------------

    def emit_create_vm(self, vm: VMConfig) -> Plan:
        """Return the plan that creates *vm*: its directory, disks and ``.vmx``."""
        check_name(vm.name, self.capabilities)
        plan = Plan("vmware")
        translator = Translator(self.capabilities, self.policy)

        target = self.location.directory_for(vm.name)
        plan.add(
            Step(
                kind=StepKind.EXEC,
                description=f"make the VM's directory {target}",
                argv=["mkdir", target],
            )
        )

        for index, device in enumerate(vm.storage):
            if device.is_removable or device.source:
                continue
            chosen = translator.format_for(device, f"storage[{index}].format")
            allocation = translator.allocation_for(device, chosen, f"storage[{index}]")
            path = self.disk_path(vm, device)
            plan.add(
                Step(
                    kind=StepKind.EXEC,
                    description=f"create the {device.name} disk",
                    argv=[
                        self.tool(VDISKMANAGER),
                        "-q",
                        "-c",
                        "-s",
                        f"{device.size_mb or DEFAULT_DISK_MB}MB",
                        # The adapter is the vmdk's own hint; VMware's help says to
                        # pass lsilogic for anything that is not IDE or BusLogic.
                        "-a",
                        "ide" if device.bus.value == "ide" else "lsilogic",
                        "-t",
                        ALLOCATION_TO_DISK_TYPE.get(allocation, "0"),
                        path,
                    ],
                    undo=Step(
                        kind=StepKind.EXEC,
                        description=f"remove the {device.name} disk",
                        argv=["rm", "-f", path],
                        destructive=True,
                    ),
                )
            )

        vmx = self.vmx_path(vm.name)
        plan.add(
            Step(
                kind=StepKind.WRITE_FILE,
                description=f"write the VM definition to {vmx}",
                path=Path(vmx),
                content=self.render(self.build_vmx(vm, translator)),
                undo=Step(
                    kind=StepKind.EXEC,
                    description="remove the VM definition",
                    argv=["rm", "-f", vmx],
                    destructive=True,
                ),
            )
        )

        translator.finish()
        for line in translator.report.lines():
            plan.warn(line)
        self.report = translator.report
        plan.report = translator.report
        return plan

    def emit_modify_vm(self, current: VMConfig, desired: VMConfig) -> Plan:
        """Return the plan that rewrites the ``.vmx``.

        As with QEMU there is nothing to modify in place -- the file *is* the
        configuration -- and as with libvirt the change takes effect at next boot.
        """
        # Never over the VM's own disks: the creation plan makes them (F-39).
        plan = self.emit_create_vm(keeping_existing_images(current, desired))
        if (current.memory.mb, current.cpu.count) != (desired.memory.mb, desired.cpu.count):
            plan.warn(
                "the definition changed; VMware reads it at power-on, so a running "
                "VM keeps its current settings until it is restarted"
            )
        return plan


def _format_mac(mac: str) -> str:
    """Return a MAC in the colon-separated form a ``.vmx`` takes.

    A config from VirtualBox carries ``080027AA0001``, which VMware will not parse.
    """
    clean = mac.replace(":", "").replace("-", "").strip()
    if len(clean) != 12:
        return mac
    return ":".join(clean[i : i + 2] for i in range(0, 12, 2)).lower()


def _get(source: VMConfig, path: str) -> Any:
    """Read a dotted path off a configuration, for the report."""
    value: Any = source
    for part in path.split("."):
        value = getattr(value, part, None)
        if value is None:
            return None
    return value
