"""
Emit a libvirt domain definition from a VMConfig.

This is the provider that justifies :class:`~vmctl.core.plan.Plan`. VirtualBox is
imperative -- a sequence of ``VBoxManage`` calls -- while libvirt is
**declarative**: you hand it one XML document. A plan here is therefore a few
``qemu-img create`` commands, a ``WRITE_FILE`` step carrying the document, and a
single ``virsh define``.

Two deliberate differences from the VirtualBox emitter:

* **Under-specify.** libvirt assigns PCI addresses, a CPU model and much else
  itself, and re-emits the domain with those filled in. Naming them here would
  fight it. The VirtualBox emitter chooses port and device numbers because
  VirtualBox does not.
* **Report what does not translate.** Settings libvirt has no equivalent for go
  into :attr:`Plan.warnings` rather than being dropped silently, which is the
  lossy-translation report from A-04 in miniature.
"""

import copy
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, Optional

from ...core.capabilities import Capabilities
from ...core.exceptions import ProviderError
from ...core.mapping import asks_for_something
from ...core.naming import check_name
from ...core.plan import Plan, Step, StepKind
from ...core.platform import CPU_HOST_MODEL, CPU_HOST_PASSTHROUGH, CPU_MODEL_KEYWORDS
from ...core.storage import StorageLocation, directory
from ...core.translate import Policy, Substitution, Translator
from ...core.vmconfig import (
    DEFAULT_DISK_MB,
    DeviceKind,
    NetworkConfig,
    VMConfig,
    keeping_existing_images,
)
from .capabilities import LibvirtCapabilities
from .tables import (
    BOOT_DEVICE,
    BUS_CONTROLLER_MODEL,
    BUS_TO_LIBVIRT,
    DISCARD_ON,
    FIRMWARE_TO_LIBVIRT,
    FORMAT_TO_DRIVER,
    GUEST_OS_APPROXIMATE,
    GUEST_OS_FROM_OSINFO,
    GUEST_OS_TO_OSINFO,
    KIND_TO_DEVICE,
    NETWORK_TO_LIBVIRT,
    NIC_MODEL_TO_LIBVIRT,
    OSINFO_NS,
    ROTATION_RATE_BUSES,
    SSD_ROTATION_RATE,
    TARGET_PREFIX,
)

#: Settings vmctl's model carries that a libvirt domain has no direct equivalent
#: for. Reported once per plan instead of disappearing.
UNTRANSLATABLE = (
    ("memory.vram_mb", "video memory is a device property in libvirt, not a VM setting"),
    ("cpu.execution_cap", "a CPU cap needs <cputune> quota, which vmctl does not emit yet"),
    ("cpu.hotplug", "CPU hotplug is expressed as a vcpu 'current' count"),
    ("memory.page_fusion", "libvirt has no page-fusion equivalent (KSM is host-wide)"),
    ("clipboard_mode", "clipboard sharing needs a SPICE agent channel"),
    ("draganddrop", "drag and drop needs a SPICE agent channel"),
)

#: Settings libvirt decides for itself, whatever a configuration says -- so a VM read
#: back disagrees with the file that made it, in these fields and only these. Answered
#: to callers through :meth:`LibvirtBackend.unexpressible_fields`, which is what
#: ``vmctl selftest`` consults (E-19 found this).
#:
#: Measured, both of them. A domain vmctl emits contains no USB at all, and libvirt's own
#: copy of it has ``<controller type='usb' model='qemu-xhci'/>``. And a domain asking for
#: ``machine='q35'`` comes back as ``machine='pc-q35-rhel9.8.0'``: libvirt resolves the
#: alias to the versioned type it picked, so the two are the same request and reporting
#: the difference as drift would make ``apply`` exit 1 for a VM that is exactly what was
#: asked for.
#:
#: Declared rather than reported per create: the value that cannot be honoured is
#: ``usb_enabled: false``, which is the *model's default*, so a warning would appear for
#: every VM nobody asked to have USB switched off -- and a line in every report is how
#: people learn to skip reports.
DECIDED_BY_LIBVIRT = ("usb_enabled", "machine")

#: The same, but only for particular *values*: libvirt resolves ``host`` and
#: ``host-model`` into a concrete CPU at define time, and the domain does not remember
#: which was asked for -- measured, a domain asking for ``host-model`` comes back as
#: ``<cpu mode='custom'><model>EPYC</model>``. A model named outright is a different
#: matter: if libvirt does not honour that, it is a real disagreement.
DECIDED_BY_LIBVIRT_FOR = {"cpu.model": (CPU_HOST_PASSTHROUGH, CPU_HOST_MODEL)}


class LibvirtEmitter:
    """Build a libvirt domain document and the plan that defines it."""

    def __init__(
        self,
        vm_name: str,
        location: Optional[StorageLocation] = None,
        definition_dir: Optional[str] = None,
        domain_type: str = "qemu",
        machine: Optional[str] = None,
        emulator: Optional[str] = None,
        capabilities: Optional[Capabilities] = None,
        policy: Policy = Policy.STRICT,
    ):
        """Initialise the emitter.

        Args:
            vm_name: Domain name.
            location: Where new images go. The backend supplies one matching the
                connection in use, since a session connection cannot write to the
                system image store (A-09).
            definition_dir: Where the XML document is written before defining it.
            domain_type: ``kvm`` when hardware acceleration is available,
                ``qemu`` for emulation. The backend decides.
            machine: Machine type to use when a configuration does not name one;
                None takes the capability declaration's default. q35 has no IDE
                controller at all, which is why that bus is not declared.
            emulator: Path to the QEMU binary, when it must be stated.
            capabilities: Provider limits to emit within.
            policy: What to do about values libvirt does not support.
        """
        self.vm_name = vm_name
        self.capabilities = capabilities or LibvirtCapabilities.get()
        self.policy = policy
        self.location = location or directory("/var/lib/libvirt/images")
        self.definition_dir = definition_dir or "/tmp"
        self.domain_type = domain_type
        # The fallback machine type comes from the capability declaration, so the
        # emitter does not hold a second opinion about what this build offers.
        self.machine = machine or self.capabilities.default_machine or "q35"
        self.emulator = emulator

    # -- helpers -------------------------------------------------------------

    def _libvirt_bus_for(self, model_bus, disk) -> str:
        """Return the libvirt ``<target bus=...>`` value for a model bus.

        Args:
            model_bus: The bus actually being used, after translation.
            disk: The device, for the error message.

        Returns:
            The libvirt bus name.

        Raises:
            ProviderError: If libvirt has no way to express the bus.
        """
        bus = BUS_TO_LIBVIRT.get(model_bus)
        if bus is None:
            raise ProviderError(
                f"disk {disk.name!r} uses the {model_bus.value!r} bus, which "
                f"this provider cannot express"
            )
        return bus

    def _nic_model(self, model, where: str, translator) -> str:
        """Return the libvirt name for a NIC model, substituting when it has none.

        Args:
            model: The neutral model asked for.
            where: Field path, for the report.
            translator: Records a substitution.

        Returns:
            The ``<model type=...>`` value to emit.
        """
        native = NIC_MODEL_TO_LIBVIRT.get(model)
        if native is not None:
            return native
        fallback = self.capabilities.nic_model_fallback() or model
        if translator is not None:
            translator.report.substitutions.append(
                Substitution(
                    where,
                    model.value,
                    fallback.value,
                    "libvirt has no such network chipset",
                )
            )
        return NIC_MODEL_TO_LIBVIRT.get(fallback, "virtio")

    # -- the document --------------------------------------------------------

    def build_domain_xml(self, vm: VMConfig, translator: Optional[Translator] = None) -> str:
        """Render *vm* as a libvirt domain document.

        Args:
            vm: Configuration to express.
            translator: Records anything that does not carry over exactly. One is
                created for the caller when omitted.

        Returns:
            str: The XML document.
        """
        if translator is None:
            translator = Translator(self.capabilities, self.policy)
        domain = ET.Element("domain", type=self.domain_type)
        ET.SubElement(domain, "name").text = vm.name
        # Redefining a domain requires its existing UUID: libvirt rejects a
        # definition that reuses a name with a different identity.
        self._add_guest_os(vm, domain, translator)

        existing_uuid = vm.metadata.get("libvirt_uuid") if vm.metadata else None
        if existing_uuid:
            ET.SubElement(domain, "uuid").text = str(existing_uuid)
        if vm.description:
            ET.SubElement(domain, "description").text = vm.description

        ET.SubElement(domain, "memory", unit="MiB").text = str(vm.memory.mb)
        ET.SubElement(domain, "currentMemory", unit="MiB").text = str(vm.memory.mb)
        ET.SubElement(domain, "vcpu").text = str(vm.cpu.count)

        os_el = ET.SubElement(domain, "os")
        firmware = FIRMWARE_TO_LIBVIRT.get(vm.firmware.type, "")
        if firmware:
            os_el.set("firmware", firmware)
        ET.SubElement(
            os_el,
            "type",
            arch=self._arch(vm, translator),
            machine=self._machine(vm, translator),
        ).text = "hvm"
        for device in vm.boot.order:
            mapped = BOOT_DEVICE.get(device)
            if mapped:
                ET.SubElement(os_el, "boot", dev=mapped)

        features = ET.SubElement(domain, "features")
        if vm.boot.acpi:
            ET.SubElement(features, "acpi")
        if vm.boot.ioapic:
            # libvirt's `<apic/>` is the *local* APIC feature flag, not the I/O APIC
            # -- that is `<ioapic driver=.../>`, which only selects the component
            # that emulates one. Neither device can be removed: both are measured
            # present on q35 and pc with nothing asking for them (see
            # capabilities.py). So this field switches a feature flag rather than a
            # device's existence, which is why it is not warned about any more. The
            # mapping is kept as it is because it round-trips: `<apic/>` is written
            # when the field is set and read back into it, and a VM's `diff` against
            # the file it came from is clean either way.
            ET.SubElement(features, "apic")
        if vm.firmware.secure_boot:
            # Secure boot needs an SMM-capable machine as well as EFI firmware.
            ET.SubElement(features, "smm", state="on")
            os_el.set("firmware", "efi")

        clock = ET.SubElement(domain, "clock", offset="utc" if vm.rtc_utc else "localtime")
        ET.SubElement(clock, "timer", name="hpet", present="yes" if vm.boot.hpet else "no")

        self._add_cpu(vm, domain, translator)

        devices = ET.SubElement(domain, "devices")
        if self.emulator:
            ET.SubElement(devices, "emulator").text = self.emulator

        self._add_storage(vm, devices, translator)
        self._add_networks(vm, devices, translator)

        if vm.firmware.tpm:
            tpm = ET.SubElement(devices, "tpm", model="tpm-crb")
            ET.SubElement(tpm, "backend", type="emulator", version="2.0")

        if vm.audio_enabled:
            ET.SubElement(devices, "sound", model="ich9")

        ET.indent(domain, space="  ")
        return ET.tostring(domain, encoding="unicode") + "\n"

    def _add_storage(
        self, vm: VMConfig, devices: ET.Element, translator: Optional[Translator]
    ) -> None:
        """Add controllers and disks, letting libvirt place them."""
        needed_controllers: Dict[str, str] = {}
        counters: Dict[str, int] = {}

        # libvirt assigns PCI addresses itself, so a device's port and unit are
        # not expressed here at all -- but the *order* devices are declared in
        # decides their target names, so it must be stable. Configuration order
        # already is, and is what a reader expects, so it is used directly.
        for index, disk in enumerate(vm.storage):
            # Resolve the bus through the translator, not directly: a config from
            # VirtualBox routinely names IDE, which q35 has no controller for at
            # all. Mapping it straight through produced a domain libvirt would
            # reject, with nothing said about it (A-04).
            model_bus = (
                translator.bus_for(disk, f"storage[{index}].bus")
                if translator is not None
                else disk.bus
            )
            bus = self._libvirt_bus_for(model_bus, disk)
            model = BUS_CONTROLLER_MODEL.get(model_bus)
            if model:
                needed_controllers[bus] = model

            prefix = TARGET_PREFIX.get(bus, "sd")
            # Not `index`: reusing the loop variable here made every message
            # emitted below name the wrong disk -- a nonrotational virtio-blk
            # disk was reported as `disks[0]` because it was the first on its
            # target prefix (F-26).
            position = counters.get(prefix, 0)
            counters[prefix] = position + 1
            target_dev = f"{prefix}{chr(ord('a') + position)}"

            device_kind = KIND_TO_DEVICE.get(disk.kind, "disk")
            source: Optional[str] = None
            if disk.is_removable or disk.source:
                # Removable media are inserted; an image that already exists is
                # attached rather than created, which is what a migration needs.
                source = disk.source
                chosen = disk.format or self.capabilities.native_format
            else:
                chosen = (
                    translator.format_for(disk, f"storage[{index}].format")
                    if translator is not None
                    else disk.format or self.capabilities.native_format
                )
                spec = self.capabilities.format_spec(chosen)
                source = self.location.image_path(
                    vm.name, f"{vm.name}_{disk.name}.{spec.extension}"
                )

            disk_el = ET.SubElement(devices, "disk", type="file", device=device_kind)
            if not disk.is_removable:
                driver = {"name": "qemu", "type": FORMAT_TO_DRIVER.get(chosen, "qcow2")}
                if disk.discard:
                    driver["discard"] = DISCARD_ON
                ET.SubElement(disk_el, "driver", driver)
            if source:
                ET.SubElement(disk_el, "source", file=source)
            target_attrs = {"dev": target_dev, "bus": bus}
            if disk.nonrotational and not disk.is_removable:
                if bus in ROTATION_RATE_BUSES:
                    target_attrs["rotation_rate"] = SSD_ROTATION_RATE
                elif translator is not None:
                    # virtio-blk has no rotation rate at all, so saying nothing
                    # would quietly present a solid-state disk as spinning.
                    translator.drop(
                        f"disks[{index}].nonrotational",
                        True,
                        f"libvirt accepts a rotation rate only on "
                        f"{'/'.join(ROTATION_RATE_BUSES)}, not {bus}",
                    )
            ET.SubElement(disk_el, "target", target_attrs)
            # An optical drive is read-only whether or not the config says so;
            # a disk only when it asks.
            if disk.readonly or disk.kind is DeviceKind.CDROM:
                ET.SubElement(disk_el, "readonly")
            if disk.hotpluggable:
                # libvirt has no per-device hot-plug flag: whether a device can be
                # detached follows from its bus, and `virsh detach-device` is how
                # it is done. Nothing to emit, so say so rather than let the
                # setting look applied.
                translator_drop = translator.drop if translator is not None else None
                if translator_drop is not None:
                    translator_drop(
                        f"storage[{index}].hotpluggable",
                        True,
                        "libvirt has no per-device hot-plug flag; use "
                        "`virsh detach-device` on a bus that supports it",
                    )

        # Controllers come after the disks in document order only because
        # ElementTree appends; libvirt does not care, and re-emits them sorted.
        for bus, model in sorted(needed_controllers.items()):
            ET.SubElement(devices, "controller", type=bus, index="0", model=model)

        if translator is not None:
            for path, reason in UNTRANSLATABLE:
                # A field at its default asks for nothing, so reporting it would
                # put a line in every report and teach people to skip them.
                if asks_for_something(vm, path):
                    translator.drop(path, _get(vm, path), reason)

    def _arch(self, vm: VMConfig, translator: Optional[Translator]) -> str:
        """Return the architecture to emit, reporting one this build cannot run.

        libvirt requires an architecture in every domain; VirtualBox has no such
        field, so a config that came from there says ``x86_64`` by default (A-10).
        """
        if vm.arch in self.capabilities.arches:
            return vm.arch.value
        fallback = self.capabilities.arches[0]
        if translator is not None:
            translator.report.substitutions.append(
                Substitution(
                    "arch",
                    vm.arch.value,
                    fallback.value,
                    "this libvirt connection cannot run that architecture",
                )
            )
        return fallback.value

    def _machine(self, vm: VMConfig, translator: Optional[Translator]) -> str:
        """Return the machine type to emit.

        The set on offer depends on the QEMU build -- ``virt`` is ARM-only and
        refused on x86_64 -- so an unknown one is reported and the provider's own
        default used instead of letting ``virsh define`` fail.
        """
        default = self.machine
        wanted = vm.machine
        if not wanted:
            return default
        known = self.capabilities.machine_types
        if not known or wanted in known:
            return wanted
        if translator is not None:
            translator.report.substitutions.append(
                Substitution(
                    "machine",
                    wanted,
                    default,
                    f"this build offers {', '.join(known)}",
                )
            )
        return default

    def _add_cpu(self, vm: VMConfig, domain: ET.Element, translator: Optional[Translator]) -> None:
        """Add the ``<cpu>`` element: model, and topology when one is stated.

        Nested virtualisation needs the host's own CPU exposed to the guest, which
        is why it implies ``host-passthrough`` -- and why asking for a *named*
        model at the same time cannot be honoured.
        """
        cpu = ET.Element("cpu")
        model = (vm.cpu.model or "").strip()
        if vm.cpu.nested_virt or model == CPU_HOST_PASSTHROUGH:
            cpu.set("mode", "host-passthrough")
            if model and model not in CPU_MODEL_KEYWORDS and translator is not None:
                translator.drop(
                    "cpu.model",
                    model,
                    "nested virtualisation needs the host CPU passed through, "
                    "which cannot be combined with a named model",
                )
        elif model == CPU_HOST_MODEL:
            cpu.set("mode", "host-model")
        elif model:
            cpu.set("mode", "custom")
            ET.SubElement(cpu, "model").text = model

        if vm.cpu.has_topology:
            ET.SubElement(
                cpu,
                "topology",
                sockets=str(vm.cpu.sockets or 1),
                cores=str(vm.cpu.cores or 1),
                threads=str(vm.cpu.threads or 1),
            )

        if cpu.get("mode") or len(cpu):
            domain.append(cpu)

    def _add_guest_os(
        self, vm: VMConfig, domain: ET.Element, translator: Optional[Translator]
    ) -> None:
        """Record which OS the guest runs, the way libvirt tooling does.

        libvirt has no field for it, so the shared convention is a libosinfo id in
        ``<metadata>`` -- which libvirt stores and returns unchanged, so this is
        what makes the guest OS survive a round trip here.

        A family without a version -- vmctl's ``ubuntu`` rather than ``ubuntu22.04``,
        which is also the model's default -- has no exact libosinfo id, so the closest
        true one is used and reported as the substitution it is. Dropping it instead
        meant every VM created from a configuration that never mentioned a guest OS
        was reported as having lost a setting nobody asked for.

        A guest vmctl cannot name neutrally (a VirtualBox id carried through, say) has
        no libosinfo id at all, so there is nothing to write and it is reported.
        """
        wanted = (vm.guest_os or "").strip().lower()
        osinfo = GUEST_OS_TO_OSINFO.get(wanted)
        if not osinfo:
            approximate = GUEST_OS_APPROXIMATE.get(wanted)
            if approximate and translator is not None:
                translator.report.substitutions.append(
                    Substitution(
                        "guest_os",
                        vm.guest_os,
                        GUEST_OS_FROM_OSINFO.get(approximate, approximate),
                        "libosinfo has no id for a family without a version",
                    )
                )
            osinfo = approximate
        if not osinfo:
            if translator is not None and vm.guest_os:
                translator.drop(
                    "guest_os",
                    vm.guest_os,
                    "libvirt records a guest OS as a libosinfo id, and there is "
                    "none for this one",
                )
            return
        ET.register_namespace("libosinfo", OSINFO_NS)
        metadata = domain.find("metadata")
        if metadata is None:
            metadata = ET.SubElement(domain, "metadata")
        holder = ET.SubElement(metadata, f"{{{OSINFO_NS}}}libosinfo")
        ET.SubElement(holder, f"{{{OSINFO_NS}}}os", id=osinfo)

    def _add_networks(
        self, vm: VMConfig, devices: ET.Element, translator: Optional[Translator] = None
    ) -> None:
        """Add interfaces."""
        for index, net in enumerate(vm.networks):
            kind = NETWORK_TO_LIBVIRT.get(net.network_type, "network")
            iface = ET.SubElement(devices, "interface", type=kind)
            if kind == "bridge":
                ET.SubElement(iface, "source", bridge=net.adapter_name or "br0")
            elif kind == "user":
                # QEMU user networking needs no host-side object at all, which is
                # why it is the right analogue for a VirtualBox NAT adapter.
                pass
            else:
                ET.SubElement(iface, "source", network=net.adapter_name or "default")
            ET.SubElement(
                iface,
                "model",
                type=self._nic_model(net.model, f"networks[{index}].model", translator),
            )
            if net.mac_address:
                ET.SubElement(iface, "mac", address=_format_mac(net.mac_address))
            self._add_port_forwards(net, iface, kind, index, translator)

    def _add_port_forwards(
        self,
        net: NetworkConfig,
        iface: ET.Element,
        kind: str,
        index: int,
        translator: Optional[Translator],
    ) -> None:
        """Add ``<portForward>`` elements, which need the passt backend (E-10).

        Measured on libvirt 11.10: the element is refused anywhere else, with *"The
        <portForward> element can only be used with the 'passt' backend of interface
        type='user' or type='vhostuser'"*. So asking for a forward on a NAT adapter
        also decides its backend, from slirp to passt -- a change within user-mode
        networking, and one that needs passt installed. The declaration says whether
        it is (``port_forwards``, refined by ``probe()``), so this reports rather than
        emitting a domain libvirt will reject.

        ``<portForward>`` has no attribute for a guest *address*: passt forwards to the
        guest, whatever address it has. A rule that names one is reported.
        """
        if not net.port_forwards:
            return
        where = f"networks[{index}].port_forwards"
        rules = "; ".join(rule.label for rule in net.port_forwards)
        if kind != "user":
            if translator is not None:
                translator.drop(
                    where,
                    rules,
                    "libvirt forwards ports only on a user-mode interface; a bridged "
                    "or network-backed guest is reachable directly",
                )
            return
        if not self.capabilities.port_forwards.usable:
            if translator is not None:
                translator.drop(
                    where,
                    rules,
                    "this libvirt can only forward ports through the passt backend, "
                    "and passt is not installed",
                )
            return

        ET.SubElement(iface, "backend", type="passt")
        for rule in net.port_forwards:
            if rule.guest_ip and translator is not None:
                translator.drop(
                    f"{where}[{rule.name}].guest_ip",
                    rule.guest_ip,
                    "libvirt's <portForward> forwards to the guest itself and has no "
                    "field for a guest address",
                )
            attributes = {"proto": rule.protocol}
            if rule.host_ip:
                attributes["address"] = rule.host_ip
            forward = ET.SubElement(iface, "portForward", attributes)
            ET.SubElement(forward, "range", start=str(rule.host_port), to=str(rule.guest_port))

    # -- plans ---------------------------------------------------------------

    def emit_create_vm(self, vm: VMConfig) -> Plan:
        """Return the plan that creates *vm* as a libvirt domain.

        Media are created first, then the document is written, then defined --
        so a failure part-way leaves no half-defined domain.

        Args:
            vm: Configuration to create.

        Returns:
            Plan: qemu-img steps, a WRITE_FILE step for the domain XML, and the
            ``virsh define`` that consumes it.
        """
        # The name names the definition file and every image, so a separator in
        # it would redirect those writes. Checked here rather than relying on the
        # validator, which a direct create_vm() call never runs (A-08).
        check_name(vm.name, self.capabilities)

        plan = Plan("libvirt")
        translator = Translator(self.capabilities, self.policy)

        # A disk that already has an image is attached, not created.
        creatable = [d for d in vm.storage if not d.is_removable and not d.source]
        if creatable:
            # A session connection's image store does not exist until something
            # makes it, and `qemu-img create` will not. Doing it as a visible
            # step keeps the plan a complete description of the work.
            plan.add(
                Step(
                    kind=StepKind.EXEC,
                    description=(
                        f"ensure the image directory "
                        f"{self.location.directory_for(vm.name)} exists"
                    ),
                    argv=["mkdir", "-p", self.location.directory_for(vm.name)],
                )
            )

        for index, disk in enumerate(creatable):
            chosen = translator.format_for(disk, f"storage[{index}].format")
            spec = self.capabilities.format_spec(chosen)
            path = self.location.image_path(vm.name, f"{vm.name}_{disk.name}.{spec.extension}")
            argv = [
                "qemu-img",
                "create",
                "-f",
                FORMAT_TO_DRIVER.get(chosen, "qcow2"),
                path,
                f"{disk.size_mb or DEFAULT_DISK_MB}M",
            ]
            plan.add(
                Step(
                    kind=StepKind.EXEC,
                    description=f"create the {disk.name} image",
                    argv=argv,
                    undo=Step(
                        kind=StepKind.EXEC,
                        description=f"remove the {disk.name} image",
                        argv=["rm", "-f", path],
                        destructive=True,
                    ),
                )
            )

        # A create is a new domain, so it must not claim an identity read from another
        # one. An exported configuration carries the UUID of the domain it was read
        # from -- which is what makes redefining *that* domain work -- and importing it
        # under a new name then asked libvirt to define a second domain with the first
        # one's UUID, which it refuses:
        #
        #   error: operation failed: domain 'x' is already defined with uuid ...
        #
        # So `export` followed by `import --new-name` could not work on libvirt at all
        # while the original still existed, which is the tool's central promise. The
        # rename path in emit_modify_vm already dropped it for the same reason; this is
        # the other half. libvirt assigns a fresh UUID, which is right even when the
        # original is gone: as far as libvirt is concerned this domain is new.
        if vm.metadata and "libvirt_uuid" in vm.metadata:
            vm = copy.deepcopy(vm)
            vm.metadata.pop("libvirt_uuid", None)

        xml = self.build_domain_xml(vm, translator)
        definition = Path(self.definition_dir) / f"{vm.name}.xml"
        plan.add(
            Step(
                kind=StepKind.WRITE_FILE,
                description=f"write the domain definition for {vm.name}",
                path=definition,
                content=xml,
            )
        )
        plan.add(
            Step(
                kind=StepKind.EXEC,
                description=f"define the domain {vm.name}",
                argv=["virsh", "define", str(definition)],
                undo=Step(
                    kind=StepKind.EXEC,
                    description=f"undefine the domain {vm.name}",
                    argv=["virsh", "undefine", vm.name],
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
        """Return the plan that changes an existing domain.

        libvirt has no per-setting edit command: the domain is redefined from a
        new document. Nothing is destroyed by that -- the disks are untouched --
        but it is a different shape from VirtualBox's targeted ``modifyvm``.

        Args:
            current: The domain as it is now.
            desired: The domain as it should be.

        Returns:
            Plan: empty when nothing differs, otherwise a redefinition.
        """
        plan = Plan("libvirt")
        if _same(current, desired):
            return plan

        if desired.name != current.name:
            plan.warn(
                "libvirt cannot rename a defined domain in place; the new name "
                "is defined as a separate domain and the old one is left alone"
            )
            # A new name is a new domain, so it must not claim the old identity.
            desired = copy.deepcopy(desired)
            desired.metadata.pop("libvirt_uuid", None)

        # The domain keeps the images it has: recomputing their paths would
        # quietly repoint it at files named after the VM, which is not where a
        # hand-attached or migrated image lives (F-39).
        xml = self.build_domain_xml(keeping_existing_images(current, desired))
        definition = Path(self.definition_dir) / f"{desired.name}.xml"
        plan.add(
            Step(
                kind=StepKind.WRITE_FILE,
                description=f"write the updated definition for {desired.name}",
                path=definition,
                content=xml,
            )
        )
        plan.add(
            Step(
                kind=StepKind.EXEC,
                description=f"redefine the domain {desired.name}",
                argv=["virsh", "define", str(definition)],
            )
        )
        return plan


def _get(obj: object, dotted: str) -> object:
    for part in dotted.split("."):
        obj = getattr(obj, part)
    return obj


def _is_set(value: object) -> bool:
    """Whether a value differs from "not configured"."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value not in (0, 100)  # 100 is an unrestricted execution cap
    if isinstance(value, str):
        return value not in ("", "disabled", "none")
    return value is not None


def _format_mac(mac: str) -> str:
    """Render a MAC address with colons, as libvirt expects."""
    bare = mac.replace(":", "").replace("-", "").strip()
    if len(bare) == 12:
        return ":".join(bare[i : i + 2] for i in range(0, 12, 2)).lower()
    return mac.lower()


def _same(a: VMConfig, b: VMConfig) -> bool:
    """Whether two configurations would produce the same domain."""
    return a.to_dict() == b.to_dict() and a.name == b.name
