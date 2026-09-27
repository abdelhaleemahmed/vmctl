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
from ...core.plan import Plan, Step, StepKind
from ...core.vmconfig import DiskType, VMConfig
from .capabilities import LibvirtCapabilities
from .tables import (
    BOOT_DEVICE,
    BUS_CONTROLLER_MODEL,
    BUS_TO_LIBVIRT,
    FIRMWARE_TO_LIBVIRT,
    FORMAT_TO_DRIVER,
    KIND_TO_DEVICE,
    NETWORK_TO_LIBVIRT,
    NIC_MODEL_FROM_NATIVE,
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
    ("usb_enabled", "a USB controller is added on demand by device, not by a flag"),
)


class LibvirtEmitter:
    """Build a libvirt domain document and the plan that defines it."""

    def __init__(
        self,
        vm_name: str,
        image_dir: Optional[str] = None,
        definition_dir: Optional[str] = None,
        domain_type: str = "qemu",
        machine: str = "q35",
        arch: str = "x86_64",
        emulator: Optional[str] = None,
        capabilities: Optional[Capabilities] = None,
    ):
        """Initialise the emitter.

        Args:
            vm_name: Domain name.
            image_dir: Where new disk images go. The provider passes the
                directory that matches the connection it is using, since a
                session connection cannot write to the system image store.
            definition_dir: Where the XML document is written before it is
                defined.
            domain_type: ``kvm`` when hardware acceleration is available,
                ``qemu`` for emulation. The backend decides.
            machine: Machine type. q35 has no IDE controller at all, which is why
                the capability declaration does not offer that bus.
            arch: Guest architecture.
            emulator: Path to the QEMU binary, when it must be stated.
            capabilities: Provider limits to emit within.
        """
        self.vm_name = vm_name
        self.capabilities = capabilities or LibvirtCapabilities.get()
        self.image_dir = image_dir or "/var/lib/libvirt/images"
        self.definition_dir = definition_dir or "/tmp"
        self.domain_type = domain_type
        self.machine = machine
        self.arch = arch
        self.emulator = emulator

    # -- helpers -------------------------------------------------------------

    def _image_path(self, vm: VMConfig, disk, extension: str) -> str:
        return f"{self.image_dir.rstrip('/')}/{vm.name}_{disk.name}.{extension}"

    def _libvirt_bus(self, disk) -> str:
        bus = BUS_TO_LIBVIRT.get(disk.controller)
        if bus is None:
            raise ProviderError(
                f"disk {disk.name!r} uses the {disk.controller.value!r} bus, "
                f"which this provider cannot express"
            )
        return bus

    @staticmethod
    def _nic_model(adapter_type: str) -> str:
        """Translate a NIC chipset name into a libvirt model."""
        return NIC_MODEL_FROM_NATIVE.get(adapter_type.strip().lower(), "virtio")

    # -- the document --------------------------------------------------------

    def build_domain_xml(self, vm: VMConfig, plan: Optional[Plan] = None) -> str:
        """Render *vm* as a libvirt domain document.

        Args:
            vm: Configuration to express.
            plan: Plan to record translation warnings on, if any.

        Returns:
            str: The XML document.
        """
        domain = ET.Element("domain", type=self.domain_type)
        ET.SubElement(domain, "name").text = vm.name
        # Redefining a domain requires its existing UUID: libvirt rejects a
        # definition that reuses a name with a different identity.
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
        ET.SubElement(os_el, "type", arch=self.arch, machine=self.machine).text = "hvm"
        for device in vm.boot.order:
            mapped = BOOT_DEVICE.get(device)
            if mapped:
                ET.SubElement(os_el, "boot", dev=mapped)

        features = ET.SubElement(domain, "features")
        if vm.boot.acpi:
            ET.SubElement(features, "acpi")
        if vm.boot.ioapic:
            ET.SubElement(features, "apic")
        if vm.firmware.secure_boot:
            # Secure boot needs an SMM-capable machine as well as EFI firmware.
            ET.SubElement(features, "smm", state="on")
            os_el.set("firmware", "efi")

        clock = ET.SubElement(domain, "clock", offset="utc" if vm.rtc_utc else "localtime")
        ET.SubElement(clock, "timer", name="hpet", present="yes" if vm.boot.hpet else "no")

        if vm.cpu.nested_virt:
            ET.SubElement(domain, "cpu", mode="host-passthrough")

        devices = ET.SubElement(domain, "devices")
        if self.emulator:
            ET.SubElement(devices, "emulator").text = self.emulator

        self._add_storage(vm, devices, plan)
        self._add_networks(vm, devices)

        if vm.firmware.tpm:
            tpm = ET.SubElement(devices, "tpm", model="tpm-crb")
            ET.SubElement(tpm, "backend", type="emulator", version="2.0")

        if vm.audio_enabled:
            ET.SubElement(devices, "sound", model="ich9")

        ET.indent(domain, space="  ")
        return ET.tostring(domain, encoding="unicode") + "\n"

    def _add_storage(self, vm: VMConfig, devices: ET.Element, plan: Optional[Plan]) -> None:
        """Add controllers and disks, letting libvirt place them."""
        needed_controllers: Dict[str, str] = {}
        counters: Dict[str, int] = {}

        for disk in vm.disks:
            bus = self._libvirt_bus(disk)
            model = BUS_CONTROLLER_MODEL.get(disk.controller)
            if model:
                needed_controllers[bus] = model

            prefix = TARGET_PREFIX.get(bus, "sd")
            index = counters.get(prefix, 0)
            counters[prefix] = index + 1
            target_dev = f"{prefix}{chr(ord('a') + index)}"

            device_kind = KIND_TO_DEVICE.get(disk.type, "disk")
            source: Optional[str] = None
            if disk.is_removable:
                source = disk.source
            else:
                spec = self.capabilities.format_spec(disk.format)
                if not spec.support.creatable:
                    raise ProviderError(
                        f"disk {disk.name!r} uses format {disk.format.value!r}, "
                        f"which this provider cannot create ({spec.support.value})"
                    )
                source = self._image_path(vm, disk, spec.extension)

            disk_el = ET.SubElement(devices, "disk", type="file", device=device_kind)
            if not disk.is_removable:
                driver_type = FORMAT_TO_DRIVER.get(disk.format, "qcow2")
                ET.SubElement(disk_el, "driver", name="qemu", type=driver_type)
            if source:
                ET.SubElement(disk_el, "source", file=source)
            ET.SubElement(disk_el, "target", dev=target_dev, bus=bus)
            if disk.readonly if hasattr(disk, "readonly") else disk.type == DiskType.DVD:
                ET.SubElement(disk_el, "readonly")

        # Controllers come after the disks in document order only because
        # ElementTree appends; libvirt does not care, and re-emits them sorted.
        for bus, model in sorted(needed_controllers.items()):
            ET.SubElement(devices, "controller", type=bus, index="0", model=model)

        if plan is not None:
            for path, reason in UNTRANSLATABLE:
                value = _get(vm, path)
                if _is_set(value):
                    plan.warn(f"{path} is set but {reason}")

    def _add_networks(self, vm: VMConfig, devices: ET.Element) -> None:
        """Add interfaces."""
        for net in vm.networks:
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
            ET.SubElement(iface, "model", type=self._nic_model(net.adapter_type))
            if net.mac_address:
                ET.SubElement(iface, "mac", address=_format_mac(net.mac_address))

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
        plan = Plan("libvirt")

        creatable = [d for d in vm.disks if not d.is_removable]
        if creatable:
            # A session connection's image store does not exist until something
            # makes it, and `qemu-img create` will not. Doing it as a visible
            # step keeps the plan a complete description of the work.
            plan.add(
                Step(
                    kind=StepKind.EXEC,
                    description=f"ensure the image directory {self.image_dir} exists",
                    argv=["mkdir", "-p", self.image_dir],
                )
            )

        for disk in creatable:
            spec = self.capabilities.format_spec(disk.format)
            path = self._image_path(vm, disk, spec.extension)
            argv = [
                "qemu-img",
                "create",
                "-f",
                FORMAT_TO_DRIVER.get(disk.format, "qcow2"),
                path,
                f"{disk.size_mb}M",
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

        xml = self.build_domain_xml(vm, plan)
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

        xml = self.build_domain_xml(desired, plan)
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
