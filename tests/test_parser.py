"""Parser tests — native VirtualBox output in, canonical VMConfig out.

These pin *current* behaviour, including the bugs the audit found. Tests that
document a bug are marked ``documents_bug`` and carry a strict xfail, so the
Phase 1 fix flips them green and they can never silently regress afterwards.
"""

import pytest

from vmctl.core.vmconfig import (
    DiskFormat,
    DeviceKind,
    Allocation,
    FirmwareType,
    NetworkType,
    BusType,
)

from conftest import parse_label, read_fixture


# ---------------------------------------------------------------------------
# Decoding the machine-readable key/value format
# ---------------------------------------------------------------------------


def test_decodes_simple_and_quoted_keys(parser):
    raw = 'cpus=4\nmemory="2048"\n"SATA Controller-0-0"="/vms/d.vdi"\n'
    out = parser._parse_machinereadable(raw)
    assert out["cpus"] == "4"
    assert out["memory"] == "2048"
    assert out["SATA Controller-0-0"] == "/vms/d.vdi"


def test_ignores_blank_and_comment_lines(parser):
    out = parser._parse_machinereadable("\n# comment\ncpus=2\n")
    assert out == {"cpus": "2"}


def test_decodes_hyphenated_keys(parser):
    """F-03 — ``nested-hw-virt`` never reaches the model."""
    out = parser._parse_machinereadable('nested-hw-virt="on"\n')
    assert out.get("nested-hw-virt") == "on"


# ---------------------------------------------------------------------------
# Whole-VM parsing, per captured fixture
# ---------------------------------------------------------------------------


def test_every_capture_parses(vbox_capture, parser):
    label, raw, probe = vbox_capture
    vm = parser.parse_text(label, raw, probe=probe)
    assert vm.name == label
    assert vm.cpu.count >= 1
    assert vm.memory.mb >= 4


def test_bios_minimal_shape():
    vm = parse_label("bios_minimal")
    assert vm.cpu.count == 1
    assert vm.cpu.pae is True
    assert vm.memory.mb == 128
    assert vm.memory.vram_mb == 16
    assert vm.firmware.type == FirmwareType.BIOS
    assert vm.guest_os == "ubuntu"
    assert vm.boot.acpi is True
    assert vm.boot.ioapic is True
    assert vm.rtc_utc is True
    assert [sc.name for sc in vm.storage_controllers] == ["SATA"]
    assert vm.storage_controllers[0].bus == BusType.SATA
    assert len(vm.storage) == 1
    assert vm.storage[0].size_mb == 64
    assert vm.storage[0].format == DiskFormat.VDI
    assert vm.storage[0].allocation == Allocation.THIN
    assert len(vm.networks) == 1
    assert vm.networks[0].network_type == NetworkType.NAT


def test_disk_metadata_keys_are_not_mistaken_for_disks():
    """ImageUUID / nonrotational / discard entries must not become disks."""
    vm = parse_label("bios_minimal")
    assert len(vm.storage) == 1, [d.disk_path for d in vm.storage]


def test_controller_name_containing_dashes_is_split_correctly():
    """The attachment key is `<controller name>-<port>-<device>`, and the name may
    contain dashes of its own -- so it is split from the right.

    Since M-02 the device references the controller by its logical id, and
    VirtualBox's own string stays on the controller, where it is what
    `--storagectl` takes and means nothing anywhere else.
    """
    vm = parse_label("multidisk")
    by_id = {sc.id: sc for sc in vm.storage_controllers}
    assert "SAS-II Controller" in {sc.native_name for sc in vm.storage_controllers}
    archive = [d for d in vm.storage if d.disk_path.endswith("arch.vhd")][0]
    assert by_id[archive.controller].native_name == "SAS-II Controller"
    assert archive.bus == BusType.SAS
    assert archive.slot == 0 and archive.unit == 0


def test_multidisk_formats_come_from_the_medium_probe():
    vm = parse_label("multidisk")
    by_name = {d.disk_path.replace("\\", "/").rsplit("/", 1)[-1]: d for d in vm.storage}
    assert by_name["sys.vdi"].format == DiskFormat.VDI
    assert by_name["sys.vdi"].size_mb == 64
    assert by_name["data.vmdk"].format == DiskFormat.VMDK
    assert by_name["data.vmdk"].size_mb == 96
    assert by_name["arch.vhd"].format == DiskFormat.VHD
    assert by_name["arch.vhd"].size_mb == 128
    # Created with --variant Fixed; the parser must say so (F-20).
    assert by_name["arch.vhd"].allocation == Allocation.THICK


def test_iso_is_identified_as_optical_media():
    vm = parse_label("iso_attached")
    optical = [d for d in vm.storage if d.kind == DeviceKind.CDROM]
    assert len(optical) == 1
    assert optical[0].disk_path.endswith(".iso")
    assert vm.controller_for(optical[0]).native_name == "IDE Controller"
    # Removable media are inserted, not created (F-04/F-18).
    assert optical[0].source == optical[0].disk_path
    # A drive has no capacity of its own. 0 was a number standing in for "not
    # applicable", which the validator then had to explain away (M-02).
    assert optical[0].size_mb is None


def test_empty_and_absent_attachments_are_told_apart():
    """ "emptydrive" is a drive with no medium; "none" is no drive at all.

    The fixture VM has a disk, an ISO and an empty floppy drive. The floppy used
    to be discarded twice over -- once as an "emptydrive" value and once because
    floppy controllers were skipped outright -- so a round trip lost it (F-22).
    """
    vm = parse_label("iso_attached")
    paths = [d.disk_path for d in vm.storage]
    assert "none" not in paths
    assert "emptydrive" not in paths

    kinds = sorted(d.kind.value for d in vm.storage)
    assert kinds == ["cdrom", "disk", "floppy"]

    floppy = [d for d in vm.storage if d.kind == DeviceKind.FLOPPY][0]
    assert floppy.source is None and floppy.disk_path is None


def test_all_network_modes_read_their_adapter_name_from_the_right_key():
    vm = parse_label("multinic")
    assert len(vm.networks) == 4
    kinds = [(n.network_type, n.adapter_name) for n in vm.networks]
    assert kinds == [
        (NetworkType.BRIDGED, "Intel(R) Dual Band Wireless-AC 7265"),
        # VirtualBox 7.1 emits hostonlyadapter<n>, not hostonlyif<n> (F-19).
        (NetworkType.HOSTONLY, "VirtualBox Host-Only Ethernet Adapter #4"),
        (NetworkType.INTERNAL, "lab-backend"),
        (NetworkType.NAT, None),
    ]
    assert vm.networks[2].adapter_type == "virtio"
    assert vm.networks[3].adapter_type == "82545EM"


def test_rtc_localtime_is_read():
    assert parse_label("multinic").rtc_utc is False


# ---------------------------------------------------------------------------
# Known-broken: firmware and nested virtualisation
# ---------------------------------------------------------------------------


def test_efi_vm_is_parsed_as_efi():
    """F-02 — an EFI VM currently round-trips as BIOS and will not boot."""
    vm = parse_label("efi_secureboot")
    assert vm.firmware.type in (FirmwareType.EFI, FirmwareType.EFI64)


def test_nested_virt_is_read():
    assert parse_label("efi_secureboot").cpu.nested_virt is True


def test_secure_boot_cannot_be_read_from_virtualbox():
    """F-17 - VirtualBox 7.x reports no secure-boot state at all.

    The VM this fixture came from has EFI64 firmware, yet no `secureboot` key
    appears in machine-readable output, so the field can only ever come from a
    config file. Pinned so the limitation is explicit rather than assumed.
    """
    vm = parse_label("efi_secureboot")
    assert vm.firmware.type == FirmwareType.EFI64
    assert vm.firmware.secure_boot is False


# ---------------------------------------------------------------------------
# Medium decoding (pure)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path,expected",
    [
        ("/x/d.vdi", DiskFormat.VDI),
        ("/x/d.vmdk", DiskFormat.VMDK),
        ("/x/d.vhd", DiskFormat.VHD),
        ("/x/d.raw", DiskFormat.RAW),
        ("/x/d.img", DiskFormat.RAW),
        ("/x/noext", DiskFormat.VDI),
        ("/x/d.iso", DiskFormat.VDI),  # no ISO format in the model yet
    ],
)
def test_default_format_from_extension(parser, path, expected):
    assert parser.default_format_for(path) == expected


def test_medium_info_decodes_capacity_format_and_variant(parser):
    """VirtualBox 7.x prints "Format variant:", not "Variant:" (F-20)."""
    info = parser.parse_medium_info(read_fixture("showmediuminfo_multidisk_0.txt"), DiskFormat.VDI)
    assert info == {
        "size_mb": 128,
        "format": DiskFormat.VHD,
        "variant": Allocation.THICK,
    }


def test_medium_info_accepts_the_legacy_variant_prefix(parser):
    """Older VirtualBox printed "Variant: fixed"; keep reading it."""
    info = parser.parse_medium_info(
        "Capacity: 100 MBytes\nStorage format: VDI\nVariant: fixed default\n",
        DiskFormat.VDI,
    )
    assert info["variant"] == Allocation.THICK


def test_medium_info_falls_back_to_the_supplied_default(parser):
    info = parser.parse_medium_info("nothing useful here\n", DiskFormat.VMDK)
    assert info["format"] == DiskFormat.VMDK
    assert info["size_mb"] == 20480
    assert info["variant"] == Allocation.THIN


def test_parser_never_touches_the_hypervisor(parser):
    """The guard in conftest would fail this test if parse_text shelled out."""
    vm = parser.parse_text(
        "x",
        read_fixture("showvminfo_bios_minimal.txt"),
        probe=lambda p: {"size_mb": 1, "format": DiskFormat.VDI, "variant": Allocation.THIN},
    )
    assert vm.storage[0].size_mb == 1


# ---------------------------------------------------------------------------
# Determinism and controller typing (found by the golden tests, see PLAN.md)
# ---------------------------------------------------------------------------


def test_controller_order_is_deterministic():
    """F-14 — controller order was set-iteration order, so it varied per process.

    It fed the emitter's controller resolution, so the same config could attach
    the system disk to a different controller on different runs. Controllers
    must come back in VirtualBox's own index order.
    """
    names = [sc.name for sc in parse_label("iso_attached").storage_controllers]
    assert names == ["IDE Controller", "Floppy"]


def test_controller_order_is_stable_across_repeated_parses():
    first = [sc.name for sc in parse_label("multidisk").storage_controllers]
    for _ in range(5):
        assert [sc.name for sc in parse_label("multidisk").storage_controllers] == first


def test_floppy_controller_is_not_typed_as_sata():
    """F-15 — an unmapped controller type silently becomes SATA."""
    vm = parse_label("floppy_first")
    floppy = [sc for sc in vm.storage_controllers if sc.name == "Floppy"][0]
    assert floppy.bus != BusType.SATA


# ---------------------------------------------------------------------------
# Findings from testing against a real host (VirtualBox 7.1.18)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "reported,expected",
    [
        ("BIOS", FirmwareType.BIOS),
        ("EFI", FirmwareType.EFI),
        ("EFI32", FirmwareType.EFI32),
        ("EFI64", FirmwareType.EFI64),
        ("bios", FirmwareType.BIOS),
        ("efi64", FirmwareType.EFI64),
    ],
)
def test_every_firmware_value_virtualbox_reports(parser, reported, expected):
    """F-02 - VirtualBox reports these four, upper case. All must map."""
    vm = parser.parse_text("x", f'firmware="{reported}"\ncpus=1\nmemory=128\n')
    assert vm.firmware.type == expected


def test_unknown_firmware_falls_back_to_bios(parser):
    vm = parser.parse_text("x", 'firmware="SOMETHINGNEW"\ncpus=1\nmemory=128\n')
    assert vm.firmware.type == FirmwareType.BIOS


def test_windows_paths_are_unescaped(parser):
    r"""F-16 - machine-readable output escapes '\' as '\\'."""
    raw = (
        'storagecontrollername0="SATA"\nstoragecontrollertype0="IntelAhci"\n'
        r'"SATA-0-0"="C:\\vms\\disk.vdi"' + "\n"
    )
    decoded = parser._parse_machinereadable(raw)
    assert decoded["SATA-0-0"] == r"C:\vms\disk.vdi"


def test_escaped_quotes_in_values_are_decoded(parser):
    decoded = parser._parse_machinereadable('"IDE-0-0"="C:\\\\a b\\\\x.vdi"\n')
    assert decoded["IDE-0-0"] == r"C:\a b\x.vdi"


@pytest.mark.parametrize(
    "chipset,expected",
    [
        ("PIIX3", BusType.IDE),
        ("PIIX4", BusType.IDE),
        ("ICH6", BusType.IDE),
        ("IntelAhci", BusType.SATA),
        ("LsiLogic", BusType.SCSI),
        ("BusLogic", BusType.SCSI),
        ("LsiLogicSas", BusType.SAS),
        ("NVMe", BusType.NVME),
        ("I82078", BusType.FLOPPY),
        ("USB", BusType.USB),
        ("VirtioSCSI", BusType.VIRTIO_SCSI),
    ],
)
def test_every_controller_chipset_virtualbox_offers(parser, chipset, expected):
    """F-15 - the full chipset set from `storagectl --help` on 7.1.18."""
    raw = f'storagecontrollername0="C0"\nstoragecontrollertype0="{chipset}"\n'
    vm = parser.parse_text("x", raw)
    assert vm.storage_controllers[0].bus == expected


def test_unknown_controller_chipset_is_reported_not_guessed(parser):
    """F-15 - silently calling an unknown controller SATA caused disk mis-attachment."""
    from vmctl.core.exceptions import ProviderError

    raw = 'storagecontrollername0="C0"\nstoragecontrollertype0="FutureBus9000"\n'
    with pytest.raises(ProviderError, match="FutureBus9000"):
        parser.parse_text("x", raw)


def test_audio_default_driver_does_not_mean_audio_is_enabled(parser):
    """F-21 - VirtualBox 7.x reports audio="default" even with sound off."""
    vm = parser.parse_text("x", 'audio="default"\naudio_out="off"\naudio_in="off"\n')
    assert vm.audio_enabled is False


def test_audio_is_enabled_when_playback_or_recording_is_on(parser):
    assert parser.parse_text("x", 'audio="default"\naudio_out="on"\n').audio_enabled
    assert parser.parse_text("x", 'audio="default"\naudio_in="on"\n').audio_enabled


@pytest.mark.parametrize("key", ["hostonlyadapter2", "hostonlyif2"])
def test_hostonly_adapter_name_read_from_either_key(parser, key):
    """F-19 - 7.1 emits hostonlyadapter<n>; older releases used hostonlyif<n>."""
    raw = f'nic2="hostonly"\n{key}="vboxnet0"\n'
    vm = parser.parse_text("x", raw)
    assert vm.networks[0].adapter_name == "vboxnet0"


def test_empty_controller_slots_are_not_disks():
    """A 16-port SAS controller reports 15 "none" attachments."""
    vm = parse_label("multidisk")
    assert len(vm.storage) == 3
    assert all(d.disk_path and d.disk_path != "none" for d in vm.storage)


# ---------------------------------------------------------------------------
# Formats and removable devices (M-03/M-04 findings)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ext,expected",
    [
        ("vdi", DiskFormat.VDI),
        ("vmdk", DiskFormat.VMDK),
        ("vhd", DiskFormat.VHD),
        ("vhdx", DiskFormat.VHDX),
        ("qcow2", DiskFormat.QCOW2),
        ("qed", DiskFormat.QED),
        ("img", DiskFormat.RAW),
        ("raw", DiskFormat.RAW),
    ],
)
def test_a_disk_in_any_supported_format_is_recognised(parser, ext, expected):
    """F-24 - the attachment filter kept its own shorter list of extensions.

    A qcow2 disk was not recognised as a disk at all, so the VM looked diskless
    and VMConfig invented a default 20 GB VDI in its place. Both the filter and
    the format map now come from the provider's capability declaration.
    """
    raw = (
        'storagecontrollername0="SATA"\n'
        'storagecontrollertype0="IntelAhci"\n'
        f'"SATA-0-0"="C:\\\\vms\\\\d.{ext}"\n'
    )
    vm = parser.parse_text(
        "x",
        raw,
        probe=lambda p: {
            "size_mb": 64,
            "format": parser.default_format_for(p),
            "variant": Allocation.THIN,
        },
    )
    assert len(vm.storage) == 1, "the disk was filtered out and a default invented"
    assert vm.storage[0].format == expected
    assert vm.storage[0].size_mb == 64


@pytest.mark.parametrize(
    "native,expected",
    [
        ("VDI", DiskFormat.VDI),
        ("VMDK", DiskFormat.VMDK),
        ("VHD", DiskFormat.VHD),
        ("VHDX", DiskFormat.VHDX),
        ("QCOW", DiskFormat.QCOW2),
        ("QED", DiskFormat.QED),
        ("Parallels", DiskFormat.PARALLELS),
        ("RAW", DiskFormat.RAW),
    ],
)
def test_every_reported_medium_format_is_understood(parser, native, expected):
    info = parser.parse_medium_info(
        f"Capacity: 16 MBytes\nStorage format: {native}\n", DiskFormat.VDI
    )
    assert info["format"] == expected


def test_an_empty_optical_drive_is_kept(parser):
    """F-22 - discarding "emptydrive" lost the drive, not just the medium."""
    raw = (
        'storagecontrollername0="IDE"\n'
        'storagecontrollertype0="PIIX4"\n'
        '"IDE-1-0"="emptydrive"\n'
    )
    vm = parser.parse_text("x", raw)
    optical = [d for d in vm.storage if d.kind == DeviceKind.CDROM]
    assert len(optical) == 1
    assert optical[0].source is None
    assert optical[0].disk_path is None
    assert optical[0].port == 1


def test_an_empty_floppy_drive_is_kept_as_a_floppy(parser):
    raw = (
        'storagecontrollername0="Floppy"\n'
        'storagecontrollertype0="I82078"\n'
        '"Floppy-0-0"="emptydrive"\n'
    )
    vm = parser.parse_text("x", raw)
    assert [d.kind for d in vm.storage] == [DeviceKind.FLOPPY]


def test_a_slot_holding_nothing_is_still_skipped(parser):
    """ "none" means the slot is empty of a drive, unlike "emptydrive"."""
    raw = (
        'storagecontrollername0="SATA"\n'
        'storagecontrollertype0="IntelAhci"\n'
        '"SATA-0-0"="none"\n'
    )
    vm = parser.parse_text("x", raw)
    # VMConfig supplies a default disk when a VM genuinely has none.
    assert all(d.disk_path is None for d in vm.storage)


def test_solid_state_is_read_from_the_attachment_not_invented():
    """`type: ssd` was a stand-in for a flag VirtualBox reports per attachment.

    The captured VMs all report ``nonrotational="off"``, so this flips one on in
    the capture: the model has to follow what the hypervisor says, not a field
    the user happened to write (M-01).
    """
    from vmctl.providers.virtualbox.parser import VirtualBoxParser
    from conftest import make_fixture_probe

    raw = read_fixture("showvminfo_multidisk.txt")
    assert '"SATA Controller-nonrotational-0-0"="off"' in raw
    flipped = raw.replace(
        '"SATA Controller-nonrotational-0-0"="off"',
        '"SATA Controller-nonrotational-0-0"="on"',
    )
    vm = VirtualBoxParser().parse_text("multidisk", flipped, probe=make_fixture_probe("multidisk"))
    by_slot = {(vm.controller_for(d).native_name, d.slot, d.unit): d for d in vm.storage}
    assert by_slot[("SATA Controller", 0, 0)].nonrotational is True
    assert all(
        not d.nonrotational for k, d in by_slot.items() if k != ("SATA Controller", 0, 0)
    ), "only the attachment that says so is solid state"


def test_a_bootable_controller_is_read_as_bootable():
    """F-27 -- found by reading a real capture field by field during M-02.

    `storagecontrollerbootable<n>` has been in every capture all along and was
    never read, so a parsed controller was always "not bootable" and the emitter
    re-created it with `--bootable off`. An exported VM could therefore come back
    unable to boot from the controller its original booted from.
    """
    vm = parse_label("bios_minimal")
    assert all(sc.bootable for sc in vm.storage_controllers)


def test_a_non_bootable_controller_stays_that_way():
    raw = read_fixture("showvminfo_bios_minimal.txt").replace(
        'storagecontrollerbootable0="on"', 'storagecontrollerbootable0="off"'
    )
    from vmctl.providers.virtualbox.parser import VirtualBoxParser
    from conftest import make_fixture_probe

    vm = VirtualBoxParser().parse_text(
        "bios-minimal", raw, probe=make_fixture_probe("bios_minimal")
    )
    assert not vm.storage_controllers[0].bootable
