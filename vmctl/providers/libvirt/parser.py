"""
Parse a libvirt domain definition into a VMConfig.

The input is an XML document rather than line-oriented key/value text, so the
*decoder* differs from VirtualBox's -- but everything after it is shared. The
document is flattened into the same ``key -> value`` shape, and the scalar
settings are then read by the same field-table engine
(:mod:`vmctl.core.mapping`), against this provider's own table.

Pure with respect to the hypervisor: nothing here runs a command. The backend
acquires the XML; this turns XML into a model.
"""

import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Tuple

from ...core.capabilities import Capabilities
from ...core.exceptions import ProviderError
from ...core.mapping import read_into
from ..base import MediumProbe
from ...core.devices import BusType, DeviceKind
from ...core.platform import CPU_HOST_MODEL, CPU_HOST_PASSTHROUGH, Arch, NicModel
from ...core.oscatalog import DEFAULT_ID as DEFAULT_GUEST_OS
from ...core.vmconfig import (
    BootConfig,
    CPUConfig,
    FirmwareConfig,
    MemoryConfig,
    NetworkConfig,
    NetworkType,
    PortForward,
    StorageController,
    StorageDevice,
    VMConfig,
    default_controller_id,
)
from .capabilities import LibvirtCapabilities
from .tables import (
    BOOT_DEVICE,
    GUEST_OS_FROM_OSINFO,
    NIC_MODEL_FROM_LIBVIRT,
    OSINFO_NS,
    DISCARD_ON,
    DRIVER_TO_FORMAT,
    FIELDS,
    NETWORK_TO_LIBVIRT,
    SSD_ROTATION_RATE,
)

#: libvirt bus -> model bus. Ambiguous cases are resolved by the controller
#: model: ``bus='scsi'`` is virtio-scsi or plain SCSI depending on it.
LIBVIRT_TO_BUS = {
    "sata": BusType.SATA,
    "ide": BusType.IDE,
    "usb": BusType.USB,
    "fdc": BusType.FLOPPY,
    "nvme": BusType.NVME,
    "scsi": BusType.VIRTIO_SCSI,
    "virtio": BusType.VIRTIO_BLK,
}

DEVICE_TO_KIND = {
    "disk": DeviceKind.DISK,
    "cdrom": DeviceKind.CDROM,
    "floppy": DeviceKind.FLOPPY,
    "lun": DeviceKind.DISK,
}

#: Reverse of the boot map, for reading a domain's boot order.
BOOT_FROM_LIBVIRT = {v: k for k, v in BOOT_DEVICE.items()}


class LibvirtParser:
    """Turn a libvirt domain document into a VMConfig."""

    def __init__(
        self,
        capabilities: Optional[Capabilities] = None,
        probe: Optional[MediumProbe] = None,
    ):
        """Initialise the parser.

        Args:
            capabilities: Provider declaration, used to map driver types back to
                model formats.
            probe: Medium lookup. A libvirt domain document does **not** record a
                disk's capacity -- libvirt reads it from the image -- so the size
                has to come from the image itself. This is the same
                :class:`~vmctl.providers.base.MediumProbe` seam the VirtualBox
                parser uses, which is the point of having defined it there (T-04).
        """
        self.capabilities = capabilities or LibvirtCapabilities.get()
        self.probe = probe

    def extension_to_format(self, native: str):
        """Map a qemu format name (``qcow2``, ``raw``, ...) to a model format.

        Args:
            native: The name ``qemu-img`` reports.

        Returns:
            The matching DiskFormat, or None when it is unrecognised.
        """
        return DRIVER_TO_FORMAT.get(native.strip().lower())

    # -- decoding ------------------------------------------------------------

    def flatten(self, root: ET.Element) -> Dict[str, str]:
        """Flatten the parts of a domain document the field table reads.

        Args:
            root: The ``<domain>`` element.

        Returns:
            dict: ``key -> value`` pairs matching this provider's field table.
        """
        flat: Dict[str, str] = {}

        name = root.findtext("name")
        if name:
            flat["name"] = name
        uuid = root.findtext("uuid")
        if uuid:
            flat["uuid"] = uuid.strip()
        description = root.findtext("description")
        if description:
            flat["description"] = description

        memory = root.find("memory")
        if memory is not None and memory.text:
            flat["memory_mib"] = str(_to_mib(memory.text, memory.get("unit", "KiB")))
        vcpu = root.findtext("vcpu")
        if vcpu:
            flat["vcpu"] = vcpu.strip()

        os_el = root.find("os")
        if os_el is not None:
            # firmware='efi' is the modern spelling; a <loader> element is the
            # older one, and means the same thing for our purposes.
            if os_el.get("firmware") == "efi" or os_el.find("loader") is not None:
                flat["firmware"] = "efi64"
            else:
                flat["firmware"] = "bios"

        features = root.find("features")
        flat["feature_acpi"] = "on" if _present(features, "acpi") else "off"
        flat["feature_apic"] = "on" if _present(features, "apic") else "off"

        clock = root.find("clock")
        if clock is not None:
            flat["clock_utc"] = "on" if clock.get("offset") == "utc" else "off"
            hpet = clock.find("timer[@name='hpet']")
            if hpet is not None:
                flat["timer_hpet"] = "on" if hpet.get("present") == "yes" else "off"

        return flat

    def parse_text(
        self,
        vm_name: str,
        xml_text: str,
        probe: Optional[MediumProbe] = None,
    ) -> VMConfig:
        """Parse a domain document into a VMConfig.

        Args:
            vm_name: Name to use when the document does not carry one.
            xml_text: The output of ``virsh dumpxml``.
            probe: Medium lookup for disk sizes, overriding the one given to the
                constructor.

        Returns:
            VMConfig: The parsed configuration.

        Raises:
            ProviderError: If the document is not a libvirt domain.
        """
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as exc:
            raise ProviderError(f"could not parse the domain definition: {exc}")
        if root.tag != "domain":
            raise ProviderError(f"expected a <domain> document, got <{root.tag}>")

        flat = self.flatten(root)
        controllers, disks = self._parse_storage(root, probe or self.probe)
        networks = self._parse_networks(root)
        boot = self._parse_boot(root)

        # With a VM-level boot order, libvirt refuses per-device <boot> elements
        # in the same domain, so "which disk is the boot disk" is not stated
        # anywhere. Read it the way the VirtualBox parser does -- the first
        # non-removable device, and only when the VM boots from disk at all
        # (L-03) -- so the flag survives a round trip instead of being lost.
        if "disk" in boot.order and not any(d.bootable for d in disks):
            for device in disks:
                if not device.is_removable:
                    device.bootable = True
                    break

        vm = VMConfig(
            name=flat.get("name") or vm_name,
            cpu=_cpu(root),
            memory=MemoryConfig(),
            firmware=FirmwareConfig(secure_boot=_secure_boot(root)),
            storage=disks,
            networks=networks,
            boot=boot,
            storage_controllers=controllers,
            arch=_arch(root),
            machine=_machine(root),
            audio_enabled=root.find("devices/sound") is not None,
            usb_enabled=root.find("devices/controller[@type='usb']") is not None,
            # A defined domain's UUID is part of its identity: libvirt refuses to
            # redefine a domain under a different one. Carrying it as a native
            # hint is what lets `vmctl edit` redefine rather than collide -- the
            # `provider_options` idea from A-08, in miniature.
            guest_os=_guest_os(root),
            metadata=({"libvirt_uuid": flat["uuid"]} if flat.get("uuid") else {}),
        )
        vm.firmware.tpm = root.find("devices/tpm") is not None
        read_into(vm, flat, FIELDS)
        return vm

    # -- assembly ------------------------------------------------------------

    def _parse_storage(
        self, root: ET.Element, probe: Optional[MediumProbe] = None
    ) -> Tuple[List[StorageController], List[StorageDevice]]:
        """Build controllers and devices from the document."""
        models: Dict[str, str] = {}
        for controller in root.findall("devices/controller"):
            ctype = controller.get("type", "")
            model = controller.get("model", "")
            if ctype and model:
                models.setdefault(ctype, model)

        disks: List[StorageDevice] = []
        buses_seen: Dict[BusType, StorageController] = {}

        for index, disk_el in enumerate(root.findall("devices/disk")):
            target = disk_el.find("target")
            if target is None:
                continue
            libvirt_bus = target.get("bus", "sata")
            bus = self._resolve_bus(libvirt_bus, models.get("scsi"))
            kind = DEVICE_TO_KIND.get(disk_el.get("device", "disk"), DeviceKind.DISK)

            source_el = disk_el.find("source")
            source = source_el.get("file") if source_el is not None else None

            driver = disk_el.find("driver")
            driver_type = driver.get("type") if driver is not None else None
            fmt = DRIVER_TO_FORMAT.get(driver_type or "", self.capabilities.native_format)

            # libvirt states solid-state as a rotation rate; anything but the
            # "not rotating" convention means a spinning disk.
            nonrotational = target.get("rotation_rate") == SSD_ROTATION_RATE

            removable = kind in (DeviceKind.CDROM, DeviceKind.FLOPPY)
            carrier = buses_seen.setdefault(bus, self._controller_for(bus))
            disks.append(
                StorageDevice(
                    name=target.get("dev") or f"disk{index}",
                    # A drive has no capacity; only a disk does.
                    size_mb=None if removable else _image_size_mb(source, probe),
                    kind=kind,
                    format=fmt,
                    nonrotational=nonrotational,
                    # libvirt spells TRIM passthrough as a driver attribute.
                    discard=(driver.get("discard") if driver is not None else None) == DISCARD_ON,
                    bus=bus,
                    controller=carrier.id,
                    slot=index,
                    unit=0,
                    bootable=disk_el.find("boot") is not None,
                    # `<readonly/>` on an optical drive says nothing: it is
                    # implied by the kind. On a disk it is a request.
                    readonly=disk_el.find("readonly") is not None and not removable,
                    disk_path=None if removable else source,
                    source=source if removable else None,
                )
            )

        return list(buses_seen.values()), disks

    def _controller_for(self, bus: BusType) -> StorageController:
        """Build the controller that carries devices on *bus*.

        libvirt declares a controller per bus at most, so the index is always 0
        and the logical id follows from the bus alone.
        """
        spec = self.capabilities.bus(bus)
        return StorageController(
            id=default_controller_id(bus),
            bus=bus,
            native_name=spec.controller_name if spec else bus.value,
            port_count=spec.default_ports if spec else 1,
            bootable=bus is not BusType.USB,
        )

    @staticmethod
    def _resolve_bus(libvirt_bus: str, scsi_model: Optional[str]) -> BusType:
        """Map a libvirt bus to a model bus.

        ``bus='scsi'`` is ambiguous: it is virtio-scsi or plain SCSI depending on
        the controller model in the same document.
        """
        if libvirt_bus == "scsi" and scsi_model and "virtio" not in scsi_model:
            return BusType.SCSI
        return LIBVIRT_TO_BUS.get(libvirt_bus, BusType.SATA)

    def _parse_networks(self, root: ET.Element) -> List[NetworkConfig]:
        """Build network adapters from the document."""
        reverse: Dict[str, NetworkType] = {}
        for mode, kind in NETWORK_TO_LIBVIRT.items():
            reverse.setdefault(kind, mode)

        networks = []
        for iface in root.findall("devices/interface"):
            kind = iface.get("type", "network")
            source = iface.find("source")
            if kind == "bridge":
                mode = NetworkType.BRIDGED
                name = source.get("bridge") if source is not None else None
            else:
                network = source.get("network") if source is not None else None
                # libvirt's stock network is NAT, which is what a VirtualBox NAT
                # adapter corresponds to; a named network is closer to internal.
                mode = NetworkType.NAT if network in (None, "default") else NetworkType.INTERNAL
                name = None if network == "default" else network
            model = iface.find("model")
            mac = iface.find("mac")
            native = (model.get("type") or "" if model is not None else "").strip().lower()
            networks.append(
                NetworkConfig(
                    # An unrecognised model keeps the guest's card working rather
                    # than failing the read: virtio is libvirt's own default.
                    model=NIC_MODEL_FROM_LIBVIRT.get(native, NicModel.VIRTIO),
                    network_type=mode,
                    adapter_name=name,
                    mac_address=(mac.get("address") if mac is not None else None),
                    port_forwards=_parse_port_forwards(iface),
                )
            )
        return networks

    @staticmethod
    def _parse_boot(root: ET.Element) -> BootConfig:
        """Build the boot order from the document."""
        order = []
        for boot in root.findall("os/boot"):
            mapped = BOOT_FROM_LIBVIRT.get(boot.get("dev", ""))
            if mapped:
                order.append(mapped)
        # Per-device <boot order='N'> is the other way libvirt expresses this.
        if not order:
            for disk in root.findall("devices/disk"):
                if disk.find("boot") is not None:
                    order.append("disk")
                    break
        while len(order) < 4:
            order.append("none")
        return BootConfig(order=order[:4])


def _present(parent: Optional[ET.Element], tag: str) -> bool:
    return parent is not None and parent.find(tag) is not None


def _cpu(root: ET.Element) -> CPUConfig:
    """Read the CPU: nested virtualisation, model and topology.

    ``count`` is filled by the field table from ``<vcpu>``; everything else lives
    in ``<cpu>``, which libvirt keeps exactly as it was given (A-10).
    """
    cpu_el = root.find("cpu")
    if cpu_el is None:
        return CPUConfig()
    mode = cpu_el.get("mode")
    model: Optional[str] = None
    if mode == "host-passthrough":
        model = CPU_HOST_PASSTHROUGH
    elif mode == "host-model":
        model = CPU_HOST_MODEL
    else:
        named = cpu_el.findtext("model")
        model = named.strip() if named else None

    topology = cpu_el.find("topology")
    return CPUConfig(
        # host-passthrough is exactly how the emitter expresses nested
        # virtualisation, so reading it back that way is what makes the setting
        # survive. host-model is not: it is a choice of CPU model, and inferring
        # nested virt from it turned `model: host-model` into both settings.
        nested_virt=mode == "host-passthrough",
        model=model,
        sockets=int(topology.get("sockets", 0)) or None if topology is not None else None,
        cores=int(topology.get("cores", 0)) or None if topology is not None else None,
        threads=int(topology.get("threads", 0)) or None if topology is not None else None,
    )


def _arch(root: ET.Element) -> Arch:
    """Return the architecture the domain declares, defaulting to x86_64."""
    type_el = root.find("os/type")
    raw = (type_el.get("arch") or "" if type_el is not None else "").strip()
    try:
        return Arch(raw)
    except ValueError:
        return Arch.X86_64


def _machine(root: ET.Element) -> Optional[str]:
    """Return the machine type the domain declares, if any.

    libvirt expands an alias into the exact versioned type it resolved
    (``q35`` becomes ``pc-q35-rhel9.8.0``), so what comes back is the real one.
    """
    type_el = root.find("os/type")
    return (type_el.get("machine") if type_el is not None else None) or None


def _guest_os(root: ET.Element) -> str:
    """Return the guest OS a domain records, or the model's default.

    libvirt keeps no guest OS field of its own; the convention is a libosinfo id
    in ``<metadata>``, which it stores and echoes back verbatim. An id vmctl does
    not name neutrally is returned as the libosinfo id itself -- a passthrough, so
    nothing is lost even when nothing can be translated (A-05).
    """
    element = root.find(f"metadata/{{{OSINFO_NS}}}libosinfo/{{{OSINFO_NS}}}os")
    osinfo = element.get("id", "") if element is not None else ""
    if not osinfo:
        return DEFAULT_GUEST_OS
    return GUEST_OS_FROM_OSINFO.get(osinfo, osinfo)


def _secure_boot(root: ET.Element) -> bool:
    smm = root.find("features/smm")
    loader = root.find("os/loader")
    if smm is not None and smm.get("state") == "on":
        return True
    return loader is not None and loader.get("secure") == "yes"


def _to_mib(value: str, unit: str) -> int:
    """Convert a libvirt memory value to MiB."""
    amount = int(float(value.strip()))
    factors = {
        "b": 1 / (1024 * 1024),
        "bytes": 1 / (1024 * 1024),
        "kib": 1 / 1024,
        "k": 1 / 1024,
        "kb": 1 / 1024,
        "mib": 1,
        "m": 1,
        "mb": 1,
        "gib": 1024,
        "g": 1024,
        "gb": 1024,
    }
    return max(1, int(amount * factors.get(unit.strip().lower(), 1 / 1024)))


def _image_size_mb(path: Optional[str], probe: Optional[MediumProbe]) -> int:
    """Return a disk's size in MB, asking the probe when there is one.

    A libvirt domain document does not record capacity, so without a probe the
    size is simply unknown. Returning 0 then is honest, but it makes the
    configuration fail validation, which is why the backend always supplies one.
    """
    if not path or probe is None:
        return 0
    try:
        return int(probe(path).get("size_mb") or 0)
    except Exception:
        return 0


def _parse_port_forwards(iface: ET.Element) -> List[PortForward]:
    """Return an interface's ``<portForward>`` rules (E-10).

    ``<range start='2222' to='22'/>``: the host port and the guest port. A range with
    an ``end`` covers several host ports at once, which the neutral model has no word
    for -- so it is expanded into one rule per port, which is the same thing said
    longhand and survives a round trip.
    """
    rules: List[PortForward] = []
    for forward in iface.findall("portForward"):
        protocol = (forward.get("proto") or "tcp").strip().lower()
        host_ip = forward.get("address") or ""
        for entry in forward.findall("range"):
            start = entry.get("start")
            if not start or not start.isdigit():
                continue
            first = int(start)
            last = int(entry.get("end") or start)
            guest = entry.get("to")
            guest_first = int(guest) if guest and guest.isdigit() else first
            for offset in range(max(0, last - first) + 1):
                rules.append(
                    PortForward(
                        host_port=first + offset,
                        guest_port=guest_first + offset,
                        protocol=protocol,
                        host_ip=host_ip,
                    )
                )
    return rules
