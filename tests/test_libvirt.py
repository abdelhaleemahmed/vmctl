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
from vmctl.core.translate import Policy, Translator
from vmctl.core.vmconfig import (
    BootConfig,
    CPUConfig,
    StorageDevice,
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
        storage=[
            StorageDevice(
                name="root",
                size_mb=64,
                format=DiskFormat.QCOW2,
                bus=BusType.VIRTIO_SCSI,
                bootable=True,
            ),
            StorageDevice(name="cd", kind=DeviceKind.CDROM, bus=BusType.SATA, slot=1),
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

    vm.storage[0].format = DiskFormat.VHDX
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

    vm.storage[0].format = DiskFormat.VHDX
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
    kinds = sorted(d.kind.value for d in vm.storage)
    assert kinds == ["cdrom", "disk", "disk"]
    buses = {d.bus for d in vm.storage}
    assert BusType.VIRTIO_SCSI in buses
    assert BusType.SATA in buses


def test_formats_are_recovered_from_the_driver(parser, real_domain):
    vm = parser.parse_text("lv-fixture", real_domain)
    formats = {d.format for d in vm.storage if not d.is_removable}
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
    assert all(d.size_mb == 0 for d in vm.storage if not d.is_removable)

    sizes = {"qcow2": 64, "raw": 32}

    def probe(path):
        ext = path.rsplit(".", 1)[-1]
        return {"size_mb": sizes.get(ext, 0), "format": DiskFormat.QCOW2, "variant": None}

    vm = parser.parse_text("lv-fixture", real_domain, probe=probe)
    assert sorted(d.size_mb for d in vm.storage if not d.is_removable) == [32, 64]


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
    vm.storage[0].nonrotational = True
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

    vm.storage[0].bus = BusType.VIRTIO_BLK
    vm.storage[0].nonrotational = True
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
    vm.storage = [vm.storage[0]]
    vm.storage[0].bus = BusType.VIRTIO_BLK
    once = parser.parse_text("demo", emitter.build_domain_xml(vm))
    assert once.storage[0].bus is BusType.VIRTIO_BLK
    assert _target(emitter.build_domain_xml(once), "vd") is not None


def test_a_rotation_rate_is_read_back(parser, emitter, vm):
    vm.storage[0].nonrotational = True
    once = parser.parse_text("demo", emitter.build_domain_xml(vm))
    assert once.storage[0].nonrotational is True
    assert not once.storage[1].nonrotational


def test_a_report_names_the_disk_it_is_about(emitter, vm):
    """F-26 -- found by reading the output of a real run.

    The device loop reused its index for a per-bus counter, so every message it
    emitted afterwards named the wrong disk: a nonrotational virtio-blk disk in
    second place was reported as `disks[0]`, because it was the first device on
    its own target prefix. A report that points at the wrong device is worse than
    no report.
    """
    from vmctl.core.translate import Policy, Translator

    vm.storage[1].bus = BusType.VIRTIO_BLK
    vm.storage[1].kind = DeviceKind.DISK
    vm.storage[1].nonrotational = True
    translator = Translator(LibvirtCapabilities.get(), Policy.NEAREST)
    emitter.build_domain_xml(vm, translator)
    assert [d.field for d in translator.report.drops if "nonrotational" in d.field] == [
        "disks[1].nonrotational"
    ]


def test_trim_passthrough_is_a_driver_attribute(emitter, vm):
    """libvirt says discard on the driver, not on the device (measured: accepted
    on sata, virtio and scsi on 11.10.0, and echoed back by dumpxml)."""
    vm.storage[0].discard = True
    root = ET.fromstring(emitter.build_domain_xml(vm))
    driver = root.find("devices/disk/driver")
    assert driver.get("discard") == "unmap"


def test_trim_passthrough_is_read_back(parser, emitter, vm):
    vm.storage[0].discard = True
    once = parser.parse_text("demo", emitter.build_domain_xml(vm))
    assert once.storage[0].discard is True


def test_hotplug_has_no_libvirt_equivalent_and_says_so(emitter, vm):
    """libvirt has no per-device hot-plug flag: it follows from the bus, and
    `virsh detach-device` is how a device is removed. Nothing to emit, so the
    setting is reported rather than left looking applied."""
    from vmctl.core.translate import Policy, Translator

    vm.storage[0].hotpluggable = True
    translator = Translator(LibvirtCapabilities.get(), Policy.NEAREST)
    emitter.build_domain_xml(vm, translator)
    assert any("hot-plug" in d.reason for d in translator.report.drops)


def test_a_device_with_no_format_gets_the_providers_native_one(emitter, vm):
    """An unset format is what keeps a config portable: the same file makes a
    qcow2 here and a VDI on VirtualBox (M-04)."""
    vm.storage[0].format = None
    root = ET.fromstring(emitter.build_domain_xml(vm))
    assert root.find("devices/disk/driver").get("type") == "qcow2"


def test_the_boot_disk_survives_a_round_trip(parser, emitter, vm):
    """libvirt refuses per-device <boot> alongside a VM-level boot order, so which
    disk is the boot disk is not stated in the document at all.

    Read it the way the VirtualBox parser does -- the first non-removable device,
    and only when the VM boots from disk (L-03) -- rather than losing the flag.
    """
    assert vm.storage[0].bootable
    once = parser.parse_text("demo", emitter.build_domain_xml(vm))
    assert once.storage[0].bootable
    assert not once.storage[1].bootable, "an optical drive is not the boot disk"


def test_a_read_only_disk_round_trips_and_a_drive_does_not_claim_to(parser, emitter, vm):
    """`<readonly/>` on an optical drive is implied by the kind, not a request."""
    vm.storage[0].readonly = True
    once = parser.parse_text("demo", emitter.build_domain_xml(vm))
    assert once.storage[0].readonly is True
    assert once.storage[1].readonly is False


# ---------------------------------------------------------------------------
# Deleting a domain (F-28)
# ---------------------------------------------------------------------------


def test_delete_removes_the_images_we_made_and_leaves_the_rest(tmp_path, monkeypatch):
    """F-28 -- `virsh undefine --remove-all-storage` only removes volumes libvirt
    can resolve inside a storage *pool*, and vmctl writes images into a plain
    directory. So it reported success and left every image on disk.

    Only images in our own directory are removed: one the user attached from
    elsewhere was not vmctl's to create, so it is not vmctl's to delete.
    """
    from vmctl.core.storage import directory as storage_directory
    from vmctl.providers.libvirt.backend import LibvirtBackend

    images = tmp_path / "images"
    images.mkdir()
    ours = images / "d_root.qcow2"
    ours.write_bytes(b"")
    theirs = tmp_path / "elsewhere" / "base.qcow2"
    theirs.parent.mkdir()
    theirs.write_bytes(b"")

    backend = LibvirtBackend()
    monkeypatch.setattr(
        LibvirtBackend, "storage_location", lambda self: storage_directory(str(images))
    )
    monkeypatch.setattr(LibvirtBackend, "vm_exists", lambda self, name: True)
    monkeypatch.setattr(LibvirtBackend, "get_vm_status", lambda self, name: "stopped")
    monkeypatch.setattr(LibvirtBackend, "list_vms", lambda self: [])
    monkeypatch.setattr(LibvirtBackend, "_virsh", lambda self, *a, **k: "")

    vm = VMConfig(
        name="d",
        cpu=CPUConfig(),
        memory=MemoryConfig(),
        firmware=FirmwareConfig(),
        storage=[
            StorageDevice(name="root", size_mb=32, disk_path=str(ours)),
            StorageDevice(name="shared", size_mb=32, disk_path=str(theirs)),
        ],
        networks=[],
        boot=BootConfig(),
        storage_controllers=[],
    )
    monkeypatch.setattr(LibvirtBackend, "read_vm", lambda self, name: vm)

    assert backend.delete_vm("d") is True
    assert not ours.exists(), "the image vmctl created should be gone"
    assert theirs.exists(), "an image from elsewhere is not ours to delete"


def test_the_guest_os_is_recorded_as_a_libosinfo_id(emitter, vm):
    """libvirt has no guest OS field; the convention virt-install and Boxes use is
    a libosinfo id in <metadata>, which libvirt stores and echoes back (A-05)."""
    vm.guest_os = "ubuntu22.04"
    root = ET.fromstring(emitter.build_domain_xml(vm))
    element = root.find(
        "metadata/{http://libosinfo.org/xmlns/libvirt/domain/1.0}libosinfo/"
        "{http://libosinfo.org/xmlns/libvirt/domain/1.0}os"
    )
    assert element is not None
    assert element.get("id") == "http://ubuntu.com/ubuntu/22.04"


def test_the_guest_os_round_trips(parser, emitter, vm):
    vm.guest_os = "win11"
    assert parser.parse_text("demo", emitter.build_domain_xml(vm)).guest_os == "win11"


def test_a_guest_os_libvirt_cannot_record_is_reported(emitter, vm):
    """A VirtualBox id carried into a migration has no libosinfo id, so there is
    nothing to write -- and saying nothing would look like it had been applied."""
    from vmctl.core.translate import Policy, Translator

    vm.guest_os = "Windows31"
    translator = Translator(LibvirtCapabilities.get(), Policy.NEAREST)
    emitter.build_domain_xml(vm, translator)
    assert any(d.field == "guest_os" for d in translator.report.drops)


def test_the_machine_type_is_not_read_as_a_guest_os(parser, real_domain):
    """It used to be: `ostype` was mapped to <os type machine=...>, so guest_os
    held "pc-q35-rhel9.8.0" -- a category error the catalogue fixes (A-05)."""
    vm = parser.parse_text("lv-fixture", real_domain)
    assert "q35" not in vm.guest_os


# ---------------------------------------------------------------------------
# Architecture, machine type, CPU and NIC (A-10)
# ---------------------------------------------------------------------------


def test_the_architecture_and_machine_type_are_stated(emitter, vm):
    """libvirt requires both in every domain; VirtualBox has neither, which is why
    they were missing from the model until a second provider existed."""
    root = ET.fromstring(emitter.build_domain_xml(vm))
    type_el = root.find("os/type")
    assert type_el.get("arch") == "x86_64"
    assert type_el.get("machine") == "q35"


def test_a_machine_type_from_the_config_wins(emitter, vm):
    vm.machine = "pc"
    root = ET.fromstring(emitter.build_domain_xml(vm))
    assert root.find("os/type").get("machine") == "pc"


def test_a_machine_type_this_build_lacks_is_substituted_and_reported(emitter, vm):
    """Measured: `machine='virt'` is ARM-only and libvirt refuses it on x86_64."""
    from vmctl.core.translate import Policy, Translator

    vm.machine = "virt"
    translator = Translator(LibvirtCapabilities.get(), Policy.NEAREST)
    root = ET.fromstring(emitter.build_domain_xml(vm, translator))
    assert root.find("os/type").get("machine") == "q35"
    assert any(s.field == "machine" for s in translator.report.substitutions)


def test_a_cpu_topology_is_emitted(emitter, vm):
    vm.cpu.count = 4
    vm.cpu.sockets, vm.cpu.cores, vm.cpu.threads = 2, 2, 1
    root = ET.fromstring(emitter.build_domain_xml(vm))
    topology = root.find("cpu/topology")
    assert (topology.get("sockets"), topology.get("cores"), topology.get("threads")) == (
        "2",
        "2",
        "1",
    )


def test_a_named_cpu_model_becomes_a_custom_cpu(emitter, vm):
    vm.cpu.nested_virt = False
    vm.cpu.model = "Skylake-Client"
    root = ET.fromstring(emitter.build_domain_xml(vm))
    assert root.find("cpu").get("mode") == "custom"
    assert root.findtext("cpu/model") == "Skylake-Client"


def test_host_passthrough_and_host_model_are_the_two_keywords(emitter, vm):
    for keyword, mode in (("host", "host-passthrough"), ("host-model", "host-model")):
        vm.cpu.nested_virt = False
        vm.cpu.model = keyword
        root = ET.fromstring(emitter.build_domain_xml(vm))
        assert root.find("cpu").get("mode") == mode, keyword


def test_nested_virtualisation_needs_the_host_cpu_and_says_so(emitter, vm):
    """It is expressed *as* host-passthrough, so a named model cannot also apply."""
    from vmctl.core.translate import Policy, Translator

    vm.cpu.nested_virt = True
    vm.cpu.model = "Skylake-Client"
    translator = Translator(LibvirtCapabilities.get(), Policy.NEAREST)
    root = ET.fromstring(emitter.build_domain_xml(vm, translator))
    assert root.find("cpu").get("mode") == "host-passthrough"
    assert any(d.field == "cpu.model" for d in translator.report.drops)


def test_the_cpu_and_the_architecture_round_trip(parser, emitter, vm):
    vm.cpu.count = 4
    vm.cpu.sockets, vm.cpu.cores, vm.cpu.threads = 2, 2, 1
    vm.cpu.nested_virt = False
    vm.cpu.model = "host-model"
    once = parser.parse_text("demo", emitter.build_domain_xml(vm))
    assert (once.cpu.sockets, once.cpu.cores, once.cpu.threads) == (2, 2, 1)
    assert once.cpu.model == "host-model"
    assert once.arch.value == "x86_64"
    assert once.machine == "q35"


def test_nic_models_are_named_neutrally(emitter, vm):
    from vmctl.core.platform import NicModel

    vm.networks[0].model = NicModel.E1000E
    vm.networks[1].model = NicModel.RTL8139
    models = [
        m.get("type")
        for m in ET.fromstring(emitter.build_domain_xml(vm)).findall("devices/interface/model")
    ]
    assert models == ["e1000e", "rtl8139"]


def test_a_nic_model_the_qemu_build_lacks_is_substituted(emitter, vm):
    """F-37 -- libvirt *defines* a domain with vmxnet3 and then cannot start it:
    "'vmxnet3' is not a valid device model name". For libvirt, define-time acceptance
    is not evidence, which is the same lesson F-31 taught about disk formats."""
    from vmctl.core.platform import NicModel
    from vmctl.core.translate import Policy, Translator

    vm.networks[0].model = NicModel.VMXNET3
    translator = Translator(LibvirtCapabilities.get(), Policy.NEAREST)
    root = ET.fromstring(emitter.build_domain_xml(vm, translator))
    assert root.find("devices/interface/model").get("type") == "e1000"
    assert any(s.field.endswith("model") for s in translator.report.substitutions)


def test_a_nic_model_round_trips(parser, emitter, vm):
    from vmctl.core.platform import NicModel

    vm.networks[0].model = NicModel.RTL8139
    once = parser.parse_text("demo", emitter.build_domain_xml(vm))
    assert once.networks[0].model is NicModel.RTL8139


def test_a_cpu_model_choice_is_not_read_as_nested_virtualisation(parser, emitter, vm):
    """host-passthrough is how nested virt is expressed, so reading it back that way
    is right. host-model is a model choice, and inferring nested virt from it made a
    round trip report both."""
    vm.cpu.nested_virt = False
    vm.cpu.model = "host-model"
    once = parser.parse_text("demo", emitter.build_domain_xml(vm))
    assert once.cpu.model == "host-model"
    assert once.cpu.nested_virt is False


def test_only_the_formats_this_build_can_write_are_creatable():
    """F-31 -- the declaration claimed VMDK, VDI, VHD and QED read-write from
    memory of QEMU in general. Measured: `-drive format=help` reports qcow2 and raw
    read-write and the rest read-only, and qed is not in the build at all. The
    error it hid was the worst kind -- libvirt defines such a domain and then fails
    to start it."""
    caps = LibvirtCapabilities.get()
    creatable = {f.value for f, spec in caps.formats.items() if spec.support.creatable}
    assert creatable == {"qcow2", "raw"}
    assert not caps.format_spec(DiskFormat.QED).support.usable
    # Still attachable, which is what makes converting one possible.
    assert caps.format_spec(DiskFormat.VMDK).support.usable


# ---------------------------------------------------------------------------
# F-39: a redefinition keeps the disks the domain has
# ---------------------------------------------------------------------------


def test_redefining_a_domain_keeps_the_image_it_has(emitter, vm):
    """libvirt has no per-setting edit: the domain is redefined from a new document.
    Recomputing each disk's path while doing so would silently repoint the domain at a
    file named after the VM -- which is not where a migrated or hand-attached image is,
    so the guest would boot from a path that does not exist (F-39)."""
    import copy

    current = copy.deepcopy(vm)
    current.storage[0].disk_path = "/srv/elsewhere/original.qcow2"
    desired = copy.deepcopy(current)
    desired.memory.mb = 1024

    plan = emitter.emit_modify_vm(current, desired)
    document = [step for step in plan if step.kind is StepKind.WRITE_FILE][0].content
    sources = [el.get("file") for el in ET.fromstring(document).iter("source") if el.get("file")]

    assert "/srv/elsewhere/original.qcow2" in sources


def test_a_renamed_domain_does_not_claim_the_old_ones_images(emitter, vm):
    """A rename defines a *separate* domain, and two domains sharing one image file is
    how both of them get a corrupted filesystem."""
    import copy

    current = copy.deepcopy(vm)
    current.storage[0].disk_path = "/images/demo_root.qcow2"
    desired = copy.deepcopy(current)
    desired.name = "renamed"

    plan = emitter.emit_modify_vm(current, desired)
    document = [step for step in plan if step.kind is StepKind.WRITE_FILE][0].content
    sources = [el.get("file") for el in ET.fromstring(document).iter("source") if el.get("file")]

    assert "/images/demo_root.qcow2" not in sources


# ---------------------------------------------------------------------------
# F-44: the guest-OS ids are claims about libosinfo's database
# ---------------------------------------------------------------------------


def _libosinfo_ids():
    """Return the ids this host's libosinfo really has, from the committed capture."""
    from conftest import read_fixture

    return {
        line.strip()
        for line in read_fixture("libosinfo_ids.txt").splitlines()
        if line.strip() and not line.startswith("#")
    }


def test_every_guest_os_id_exists_in_libosinfo():
    """Measured, not remembered. Five of these were neither: ``linux/2019`` (this
    database has 2016, 2018, 2020, 2022 and 2024), ``alpine/3.19`` (the path is
    ``alpinelinux``), ``macos/10.15``, ``solaris/11.4``. An id libosinfo does not know
    is stored happily by libvirt and then resolves to nothing in virt-manager."""
    from vmctl.providers.libvirt.tables import GUEST_OS_APPROXIMATE, GUEST_OS_TO_OSINFO

    known = _libosinfo_ids()
    unknown = {
        name: url
        for name, url in {**GUEST_OS_TO_OSINFO, **GUEST_OS_APPROXIMATE}.items()
        if url not in known
    }
    assert not unknown, unknown


def test_a_family_without_a_version_is_approximated_not_dropped(emitter, vm):
    """`guest_os: ubuntu` is the model's own default, so dropping it reported a lost
    setting on every VM created from a file that never mentioned a guest OS."""
    vm.guest_os = "ubuntu"
    translator = Translator(emitter.capabilities, Policy.STRICT)

    xml = emitter.build_domain_xml(vm, translator)

    assert "http://libosinfo.org/linux/2022" in xml
    assert not translator.report.drops
    assert [(s.field, s.used) for s in translator.report.substitutions] == [("guest_os", "linux")]


def test_an_id_that_means_exactly_unknown_is_used_as_is(emitter, vm):
    """libosinfo really has "unknown version of RHEL", which is what `rhel` means."""
    vm.guest_os = "rhel"
    translator = Translator(emitter.capabilities, Policy.STRICT)

    assert "http://redhat.com/rhel/unknown" in emitter.build_domain_xml(vm, translator)


def test_the_reverse_map_stays_unambiguous():
    """Three ids pointing at one url would make a round trip return a different guest
    OS than it was given, which is worse than saying "approximated"."""
    from vmctl.providers.libvirt.tables import (
        GUEST_OS_APPROXIMATE,
        GUEST_OS_FROM_OSINFO,
        GUEST_OS_TO_OSINFO,
    )

    assert len(GUEST_OS_FROM_OSINFO) == len(GUEST_OS_TO_OSINFO)
    for url in GUEST_OS_APPROXIMATE.values():
        if url in GUEST_OS_FROM_OSINFO:
            assert GUEST_OS_FROM_OSINFO[url] in GUEST_OS_TO_OSINFO


def test_deleting_a_vm_with_snapshots_is_not_refused(monkeypatch, tmp_path):
    """F-45: libvirt answers "cannot delete inactive domain with 3 snapshots" unless
    the snapshot metadata goes too. Found the moment vmctl could take snapshots: a VM
    vmctl had snapshotted could not then be deleted by vmctl, which makes the feature
    a trap. Deleting a VM means deleting what belonged to it."""
    from vmctl.core.storage import directory as storage_directory
    from vmctl.providers.libvirt.backend import LibvirtBackend

    calls = []
    backend = LibvirtBackend()
    monkeypatch.setattr(
        LibvirtBackend, "storage_location", lambda self: storage_directory(str(tmp_path))
    )
    monkeypatch.setattr(LibvirtBackend, "vm_exists", lambda self, name: True)
    monkeypatch.setattr(LibvirtBackend, "get_vm_status", lambda self, name: "stopped")
    monkeypatch.setattr(LibvirtBackend, "list_vms", lambda self: [])
    monkeypatch.setattr(LibvirtBackend, "_images_under_our_directory", lambda self, name: [])
    monkeypatch.setattr(LibvirtBackend, "_virsh", lambda self, *a, **k: calls.append(a) or "")

    backend.delete_vm("d")

    undefine = [args for args in calls if args and args[0] == "undefine"][0]
    assert "--snapshots-metadata" in undefine
