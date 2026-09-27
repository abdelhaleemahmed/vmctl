"""libvirt provider tests (P-01).

Hermetic: the emitter is a pure function to XML and the parser a pure function
from XML, so none of this needs libvirt installed. The fixture
``libvirt_dumpxml_full.xml`` is a real domain that vmctl defined on libvirt
11.10.0 / QEMU 10.1.0 and libvirt then echoed back.
"""

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from vmctl.core.plan import StepKind
from vmctl.core.storage import directory
from vmctl.core.vmconfig import (
    BootConfig,
    CPUConfig,
    DiskConfig,
    DiskFormat,
    DeviceKind,
    FirmwareConfig,
    FirmwareType,
    MemoryConfig,
    NetworkConfig,
    NetworkType,
    BusType,
    VMConfig,
)
from vmctl.providers.libvirt.capabilities import LibvirtCapabilities
from vmctl.providers.libvirt.emitter import LibvirtEmitter
from vmctl.providers.libvirt.parser import LibvirtParser

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def emitter():
    return LibvirtEmitter(
        "demo",
        location=directory("/images"),
        definition_dir="/defs",
        emulator="/usr/libexec/qemu-kvm",
    )


@pytest.fixture
def parser():
    return LibvirtParser()


@pytest.fixture
def vm():
    return VMConfig(
        name="demo",
        cpu=CPUConfig(count=2, nested_virt=True),
        memory=MemoryConfig(mb=512),
        firmware=FirmwareConfig(type=FirmwareType.EFI64, tpm=True),
        disks=[
            DiskConfig(
                name="root",
                size_mb=64,
                format=DiskFormat.QCOW2,
                controller=BusType.VIRTIO_SCSI,
                bootable=True,
            ),
            DiskConfig(name="cd", type=DeviceKind.CDROM, controller=BusType.SATA, port=1),
        ],
        networks=[
            NetworkConfig(network_type=NetworkType.NAT, adapter_type="virtio"),
            NetworkConfig(
                network_type=NetworkType.BRIDGED, adapter_name="virbr0", adapter_type="82540EM"
            ),
        ],
        boot=BootConfig(order=["disk", "dvd", "none", "none"], acpi=True, ioapic=True, hpet=True),
        storage_controllers=[],
    )


# ---------------------------------------------------------------------------
# The plan shape: this is why Plan exists
# ---------------------------------------------------------------------------


def test_the_plan_is_declarative_not_a_command_sequence(emitter, vm):
    """VirtualBox is a sequence of CLI calls; libvirt is one document.

    A-01 exists because List[List[str]] cannot express this: the work is a file
    write plus a single define.
    """
    plan = emitter.emit_create_vm(vm)
    kinds = [s.kind for s in plan]
    assert StepKind.WRITE_FILE in kinds
    assert kinds[-1] is StepKind.EXEC
    assert plan.steps[-1].argv[:2] == ["virsh", "define"]
    assert len(plan.native_artifacts()) == 1


def test_media_are_created_before_the_domain_is_defined(emitter, vm):
    plan = emitter.emit_create_vm(vm)
    verbs = [s.argv[0] if s.argv else "write" for s in plan]
    assert verbs.index("qemu-img") < verbs.index("virsh")


def test_the_image_directory_is_created_as_a_visible_step(emitter, vm):
    plan = emitter.emit_create_vm(vm)
    assert plan.steps[0].argv == ["mkdir", "-p", "/images"]


def test_only_real_disks_get_an_image(emitter, vm):
    plan = emitter.emit_create_vm(vm)
    creates = [s for s in plan if s.argv and s.argv[0] == "qemu-img"]
    assert len(creates) == 1  # the DVD holds an existing medium
    assert "/images/demo_root.qcow2" in creates[0].argv


def test_defining_a_domain_can_be_undone(emitter, vm):
    plan = emitter.emit_create_vm(vm)
    define = plan.steps[-1]
    assert define.undo is not None
    assert define.undo.argv == ["virsh", "undefine", "demo"]
    assert define.undo.destructive


# ---------------------------------------------------------------------------
# The document
# ---------------------------------------------------------------------------


def test_the_document_is_a_valid_domain(emitter, vm):
    root = ET.fromstring(emitter.build_domain_xml(vm))
    assert root.tag == "domain"
    assert root.findtext("name") == "demo"
    assert root.find("memory").text == "512"
    assert root.findtext("vcpu") == "2"


def test_efi_firmware_is_expressed_as_an_os_attribute(emitter, vm):
    root = ET.fromstring(emitter.build_domain_xml(vm))
    assert root.find("os").get("firmware") == "efi"


def test_bios_needs_no_firmware_attribute(emitter, vm):
    vm.firmware.type = FirmwareType.BIOS
    root = ET.fromstring(emitter.build_domain_xml(vm))
    assert root.find("os").get("firmware") is None


def test_features_follow_the_configuration(emitter, vm):
    root = ET.fromstring(emitter.build_domain_xml(vm))
    assert root.find("features/acpi") is not None
    assert root.find("features/apic") is not None
    assert root.find("clock/timer[@name='hpet']").get("present") == "yes"
    assert root.find("clock").get("offset") == "utc"


def test_nat_is_user_networking_not_the_default_network(emitter, vm):
    """A VirtualBox NAT adapter has no host-side object, and neither does this.

    Mapping NAT onto libvirt's `default` network made a domain fail to start on a
    session connection, where that network does not exist.
    """
    root = ET.fromstring(emitter.build_domain_xml(vm))
    nat = root.findall("devices/interface")[0]
    assert nat.get("type") == "user"
    assert nat.find("source") is None


def test_a_bridged_adapter_names_its_bridge(emitter, vm):
    root = ET.fromstring(emitter.build_domain_xml(vm))
    bridged = root.findall("devices/interface")[1]
    assert bridged.get("type") == "bridge"
    assert bridged.find("source").get("bridge") == "virbr0"


def test_a_virtualbox_nic_chipset_is_translated(emitter, vm):
    """A config exported from VirtualBox carries 82540EM, which libvirt calls e1000."""
    root = ET.fromstring(emitter.build_domain_xml(vm))
    models = [m.get("type") for m in root.findall("devices/interface/model")]
    assert models == ["virtio", "e1000"]


def test_a_virtio_scsi_disk_gets_its_controller(emitter, vm):
    root = ET.fromstring(emitter.build_domain_xml(vm))
    controller = root.find("devices/controller[@type='scsi']")
    assert controller is not None
    assert controller.get("model") == "virtio-scsi"


def test_target_devices_use_the_prefix_for_their_bus(emitter, vm):
    root = ET.fromstring(emitter.build_domain_xml(vm))
    targets = {t.get("bus"): t.get("dev") for t in root.findall("devices/disk/target")}
    assert targets["scsi"] == "sda"
    assert targets["sata"].startswith("sd")


def test_the_emitter_does_not_assign_addresses(emitter, vm):
    """libvirt allocates PCI addresses itself and re-emits the domain with them.

    Naming them here would fight it -- the opposite of the VirtualBox emitter,
    which must choose port and device numbers because VirtualBox does not (A-06).
    """
    xml = emitter.build_domain_xml(vm)
    assert "<address" not in xml


def test_a_tpm_becomes_a_device(emitter, vm):
    root = ET.fromstring(emitter.build_domain_xml(vm))
    assert root.find("devices/tpm/backend").get("version") == "2.0"


def test_a_name_with_xml_metacharacters_is_escaped(emitter, vm):
    """A-08 - the moment a provider emits a document, names become injectable."""
    vm.name = 'evil"><name>pwned</name><x y="'
    xml = emitter.build_domain_xml(vm)
    root = ET.fromstring(xml)  # must still parse
    assert root.findtext("name") == vm.name
    assert "pwned</name>" not in xml.replace("&lt;", "<").split("<name>")[1][:0] + ""


def test_untranslatable_settings_are_reported(emitter, vm):
    """A-04 in miniature: what libvirt has no equivalent for is said out loud."""
    vm.memory.vram_mb = 128
    vm.cpu.execution_cap = 50
    vm.clipboard_mode = "bidirectional"
    plan = emitter.emit_create_vm(vm)
    reported = " ".join(plan.warnings)
    assert "vram" in reported
    assert "execution_cap" in reported
    assert "clipboard" in reported


def test_a_format_the_provider_cannot_create_is_refused(emitter, vm):
    """Under the default strict policy, an impossible format is an error."""
    from vmctl.core.exceptions import ValidationError

    vm.disks[0].format = DiskFormat.VHDX
    with pytest.raises(ValidationError) as excinfo:
        emitter.emit_create_vm(vm)

    # The refusal comes from the plan being completed rather than from the first
    # problem found, so everything is listed together.
    reported = str(excinfo.value) + " ".join(excinfo.value.constraints or [])
    assert "can be attached but not created" in reported
    assert "would be used instead" in reported
    # ...and the error says how to get the other behaviour.
    assert "--policy nearest" in (excinfo.value.recovery_hint or "")


def test_nearest_substitutes_a_format_and_reports_it(vm):
    """A-04 - "make it work here, and tell me what changed"."""
    from vmctl.core.translate import Policy
    from vmctl.providers.libvirt.emitter import LibvirtEmitter

    vm.disks[0].format = DiskFormat.VHDX
    emitter = LibvirtEmitter("demo", location=directory("/images"), policy=Policy.NEAREST)
    plan = emitter.emit_create_vm(vm)
    assert any("vhdx" in w and "qcow2" in w for w in plan.warnings)
    created = [s for s in plan if s.argv and s.argv[0] == "qemu-img"][0]
    assert "qcow2" in created.argv


# ---------------------------------------------------------------------------
# Reading a real domain back
# ---------------------------------------------------------------------------


@pytest.fixture
def real_domain():
    return (FIXTURES / "libvirt_dumpxml_full.xml").read_text()


def test_a_real_domain_parses(parser, real_domain):
    vm = parser.parse_text("lv-fixture", real_domain)
    assert vm.name == "lv-fixture"
    assert vm.cpu.count == 2
    assert vm.memory.mb == 512
    assert vm.firmware.type == FirmwareType.EFI64
    assert vm.boot.acpi and vm.boot.ioapic and vm.boot.hpet
    assert vm.firmware.tpm is True


def test_the_domains_identity_is_kept_as_a_native_hint(parser, real_domain):
    """libvirt refuses to redefine a domain under a different UUID, so `edit`
    needs the existing one (the provider_options idea from A-08)."""
    vm = parser.parse_text("lv-fixture", real_domain)
    assert vm.metadata.get("libvirt_uuid")


def test_devices_and_buses_are_recovered(parser, real_domain):
    vm = parser.parse_text("lv-fixture", real_domain)
    kinds = sorted(d.type.value for d in vm.disks)
    assert kinds == ["cdrom", "disk", "disk"]
    buses = {d.controller for d in vm.disks}
    assert BusType.VIRTIO_SCSI in buses
    assert BusType.SATA in buses


def test_formats_are_recovered_from_the_driver(parser, real_domain):
    vm = parser.parse_text("lv-fixture", real_domain)
    formats = {d.format for d in vm.disks if not d.is_removable}
    assert formats == {DiskFormat.QCOW2, DiskFormat.RAW}


def test_networks_are_recovered(parser, real_domain):
    vm = parser.parse_text("lv-fixture", real_domain)
    modes = [n.network_type for n in vm.networks]
    assert NetworkType.NAT in modes
    assert NetworkType.BRIDGED in modes


def test_a_disk_size_needs_a_probe(parser, real_domain):
    """A libvirt domain does not record capacity; the image does.

    Without a probe the size is honestly unknown; the backend always supplies one
    (the MediumProbe seam from T-04, reused by a second provider).
    """
    vm = parser.parse_text("lv-fixture", real_domain)
    assert all(d.size_mb == 0 for d in vm.disks if not d.is_removable)

    sizes = {"qcow2": 64, "raw": 32}

    def probe(path):
        ext = path.rsplit(".", 1)[-1]
        return {"size_mb": sizes.get(ext, 0), "format": DiskFormat.QCOW2, "variant": None}

    vm = parser.parse_text("lv-fixture", real_domain, probe=probe)
    assert sorted(d.size_mb for d in vm.disks if not d.is_removable) == [32, 64]


def test_a_document_that_is_not_a_domain_is_rejected(parser):
    from vmctl.core.exceptions import ProviderError

    with pytest.raises(ProviderError, match="expected a <domain>"):
        parser.parse_text("x", "<pool><name>images</name></pool>")


def test_malformed_xml_is_reported_not_raised_raw(parser):
    from vmctl.core.exceptions import ProviderError

    with pytest.raises(ProviderError, match="could not parse"):
        parser.parse_text("x", "<domain><name>oops")


# ---------------------------------------------------------------------------
# The conformance property, formulated for a normalising provider
# ---------------------------------------------------------------------------


def test_parse_emit_parse_is_stable(emitter, parser, vm):
    """A-07's assertion, and why it is *not* "emit == original input".

    libvirt adds a UUID, PCI addresses, a CPU model and controllers of its own,
    so the document it returns is never the one it was given. The second
    generation onwards is fixed, which is what this checks.
    """
    first = emitter.build_domain_xml(vm)
    once = parser.parse_text("demo", first)
    second = emitter.build_domain_xml(once)
    twice = parser.parse_text("demo", second)
    third = emitter.build_domain_xml(twice)
    assert second == third, "the mapping is not idempotent from the second pass"


# ---------------------------------------------------------------------------
# The capability declaration against its probe
# ---------------------------------------------------------------------------


def test_the_declaration_records_that_it_was_measured():
    caps = LibvirtCapabilities.get()
    assert "11.10.0" in caps.evidence
    assert "libvirt_attach_matrix.json" in caps.evidence


def test_qcow2_is_the_native_format():
    """Unlike VirtualBox, whose native format is VDI."""
    caps = LibvirtCapabilities.get()
    assert caps.native_format is DiskFormat.QCOW2


def test_buses_carry_only_what_the_probe_said():
    caps = LibvirtCapabilities.get()
    assert caps.can_attach(DeviceKind.DISK, BusType.VIRTIO_SCSI)
    assert caps.can_attach(DeviceKind.FLOPPY, BusType.FLOPPY)
    assert not caps.can_attach(DeviceKind.FLOPPY, BusType.SATA)


def test_virtio_blk_carries_disks_and_no_removable_media():
    """The recording said `disk|virtio: true` from the day it was captured.

    The declaration could not repeat it until M-01 gave the model a name for
    virtio-blk, so the fastest disk bus KVM offers was undeclared. An optical
    drive on it is refused -- "disk type of 'vda' does not support ejectable
    media" -- which is a property of the bus, not of this QEMU build.
    """
    caps = LibvirtCapabilities.get()
    assert caps.can_attach(DeviceKind.DISK, BusType.VIRTIO_BLK)
    assert not caps.can_attach(DeviceKind.CDROM, BusType.VIRTIO_BLK)
    assert not caps.can_attach(DeviceKind.FLOPPY, BusType.VIRTIO_BLK)


def test_the_matrix_records_a_build_dependent_gap():
    """IDE and NVMe are absent because *this QEMU build and machine type* lack
    them, not because libvirt cannot express them.

    That is the difference from VirtualBox, whose matrix is a property of the
    product. It is why E-05 (asking the host) is a requirement for libvirt rather
    than a refinement, and the declaration says so.
    """
    caps = LibvirtCapabilities.get()
    assert BusType.IDE not in caps.buses
    assert BusType.NVME not in caps.buses
    assert "machine type" in caps.evidence


# ---------------------------------------------------------------------------
# Solid state, and the buses that cannot say it
# ---------------------------------------------------------------------------


def _target(xml, dev_prefix):
    """Return the <target> element of the first disk whose dev has this prefix."""
    root = ET.fromstring(xml)
    for target in root.findall("devices/disk/target"):
        if (target.get("dev") or "").startswith(dev_prefix):
            return target
    raise AssertionError(f"no disk targeting {dev_prefix}* in:\n{xml}")


def test_solid_state_is_stated_as_a_rotation_rate(emitter, vm):
    """libvirt has no "ssd" flag: a disk that does not rotate has rate 1."""
    vm.disks[0].nonrotational = True
    target = _target(emitter.build_domain_xml(vm), "sd")
    assert target.get("rotation_rate") == "1"


def test_a_spinning_disk_says_nothing_at_all(emitter, vm):
    assert _target(emitter.build_domain_xml(vm), "sd").get("rotation_rate") is None


def test_a_bus_that_cannot_say_solid_state_reports_it_rather_than_lying(emitter, vm):
    """Measured: "rotation rate is only valid for SCSI/IDE/SATA bus".

    virtio-blk therefore cannot carry the flag. Emitting the disk anyway and
    saying nothing would hand back a spinning disk under a config that asked for
    an SSD, which is the silent lossy translation A-04 exists to prevent.
    """
    from vmctl.core.translate import Policy, Translator

    vm.disks[0].controller = BusType.VIRTIO_BLK
    vm.disks[0].nonrotational = True
    translator = Translator(LibvirtCapabilities.get(), Policy.NEAREST)
    xml = emitter.build_domain_xml(vm, translator)
    assert _target(xml, "vd").get("rotation_rate") is None
    assert any("rotation rate" in d.reason for d in translator.report.drops)


def test_a_virtio_blk_disk_is_not_read_back_as_virtio_scsi(parser, emitter, vm):
    """F-25 -- found while splitting the axes (M-01).

    `bus='virtio'` used to map onto VIRTIO_SCSI, the only virtio the model could
    name. Re-emitting that gave `bus='scsi'`, so a round trip moved the guest's
    disk from /dev/vda to /dev/sda -- enough to leave it unbootable.
    """
    vm.disks = [vm.disks[0]]
    vm.disks[0].controller = BusType.VIRTIO_BLK
    once = parser.parse_text("demo", emitter.build_domain_xml(vm))
    assert once.disks[0].controller is BusType.VIRTIO_BLK
    assert _target(emitter.build_domain_xml(once), "vd") is not None


def test_a_rotation_rate_is_read_back(parser, emitter, vm):
    vm.disks[0].nonrotational = True
    once = parser.parse_text("demo", emitter.build_domain_xml(vm))
    assert once.disks[0].nonrotational is True
    assert not once.disks[1].nonrotational


def test_a_report_names_the_disk_it_is_about(emitter, vm):
    """F-26 -- found by reading the output of a real run.

    The device loop reused its index for a per-bus counter, so every message it
    emitted afterwards named the wrong disk: a nonrotational virtio-blk disk in
    second place was reported as `disks[0]`, because it was the first device on
    its own target prefix. A report that points at the wrong device is worse than
    no report.
    """
    from vmctl.core.translate import Policy, Translator

    vm.disks[1].controller = BusType.VIRTIO_BLK
    vm.disks[1].type = DeviceKind.DISK
    vm.disks[1].nonrotational = True
    translator = Translator(LibvirtCapabilities.get(), Policy.NEAREST)
    emitter.build_domain_xml(vm, translator)
    assert [d.field for d in translator.report.drops if "nonrotational" in d.field] == [
        "disks[1].nonrotational"
    ]
