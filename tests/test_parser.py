"""Parser tests — native VirtualBox output in, canonical VMConfig out.

These pin *current* behaviour, including the bugs the audit found. Tests that
document a bug are marked ``documents_bug`` and carry a strict xfail, so the
Phase 1 fix flips them green and they can never silently regress afterwards.
"""
import pytest

from vmctl.core.vmconfig import (
    DiskFormat, DiskType, DiskVariant, FirmwareType, NetworkType,
    StorageControllerType,
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


@pytest.mark.documents_bug
@pytest.mark.xfail(strict=True, reason="F-03: the key regex uses \\w+, so hyphenated keys are dropped")
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
    assert vm.cpu.count == 2
    assert vm.cpu.pae is True
    assert vm.memory.mb == 2048
    assert vm.memory.vram_mb == 16
    assert vm.firmware.type == FirmwareType.BIOS
    assert vm.ostype == "Ubuntu (64-bit)"
    assert vm.boot.acpi is True
    assert vm.boot.ioapic is True
    assert vm.rtc_utc is True
    assert [sc.name for sc in vm.storage_controllers] == ["SATA"]
    assert vm.storage_controllers[0].controller_type == StorageControllerType.SATA
    assert len(vm.disks) == 1
    assert vm.disks[0].size_mb == 20480
    assert vm.disks[0].format == DiskFormat.VDI
    assert vm.disks[0].variant == DiskVariant.THIN
    assert len(vm.networks) == 1
    assert vm.networks[0].network_type == NetworkType.NAT


def test_disk_metadata_keys_are_not_mistaken_for_disks():
    """ImageUUID / nonrotational / discard entries must not become disks."""
    vm = parse_label("bios_minimal")
    assert len(vm.disks) == 1, [d.disk_path for d in vm.disks]


def test_controller_name_containing_dashes_is_split_correctly():
    vm = parse_label("multidisk")
    names = {sc.name for sc in vm.storage_controllers}
    assert "SAS-II Controller" in names
    archive = [d for d in vm.disks if d.disk_path.endswith("archive.vhd")][0]
    assert archive.controller_name == "SAS-II Controller"
    assert archive.controller == StorageControllerType.SAS
    assert archive.port == 0 and archive.device == 0


def test_multidisk_formats_come_from_the_medium_probe():
    vm = parse_label("multidisk")
    by_name = {d.disk_path.rsplit("-", 1)[-1]: d for d in vm.disks}
    assert by_name["system.vdi"].format == DiskFormat.VDI
    assert by_name["system.vdi"].size_mb == 51200
    assert by_name["data.vmdk"].format == DiskFormat.VMDK
    assert by_name["data.vmdk"].size_mb == 204800
    assert by_name["archive.vhd"].format == DiskFormat.VHD
    assert by_name["archive.vhd"].variant == DiskVariant.THICK


def test_iso_is_identified_as_optical_media():
    vm = parse_label("iso_attached")
    optical = [d for d in vm.disks if d.type == DiskType.DVD]
    assert len(optical) == 1
    assert optical[0].disk_path.endswith(".iso")
    assert optical[0].controller_name == "IDE"


def test_emptydrive_and_none_attachments_are_skipped():
    vm = parse_label("iso_attached")
    paths = [d.disk_path for d in vm.disks]
    assert "none" not in paths
    assert "emptydrive" not in paths
    assert len(vm.disks) == 2  # one vdi + one iso


def test_all_network_modes_read_their_adapter_name_from_the_right_key():
    vm = parse_label("multinic")
    assert len(vm.networks) == 4
    kinds = [(n.network_type, n.adapter_name) for n in vm.networks]
    assert kinds == [
        (NetworkType.BRIDGED, "enp0s31f6"),
        (NetworkType.HOSTONLY, "vboxnet0"),
        (NetworkType.INTERNAL, "lab-backend"),
        (NetworkType.NATNETWORK, "LabNatNetwork"),
    ]
    assert vm.networks[2].adapter_type == "virtio"
    assert vm.networks[0].mac_address == "080027AA0001"


def test_rtc_localtime_is_read():
    assert parse_label("multinic").rtc_utc is False


# ---------------------------------------------------------------------------
# Known-broken: firmware and nested virtualisation
# ---------------------------------------------------------------------------

@pytest.mark.documents_bug
@pytest.mark.xfail(strict=True, reason="F-02: 'EFI' is compared against lowercase 'efi'")
def test_efi_vm_is_parsed_as_efi():
    """F-02 — an EFI VM currently round-trips as BIOS and will not boot."""
    vm = parse_label("efi_secureboot")
    assert vm.firmware.type in (FirmwareType.EFI, FirmwareType.EFI64)


def test_efi_vm_currently_parses_as_bios():
    """The wrong behaviour, pinned so the fix is visible as a diff."""
    assert parse_label("efi_secureboot").firmware.type == FirmwareType.BIOS


@pytest.mark.documents_bug
@pytest.mark.xfail(strict=True, reason="F-03: hyphenated key never decoded")
def test_nested_virt_is_read():
    assert parse_label("efi_secureboot").cpu.nested_virt is True


def test_secure_boot_is_read():
    assert parse_label("efi_secureboot").firmware.secure_boot is True


# ---------------------------------------------------------------------------
# Medium decoding (pure)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path,expected", [
    ("/x/d.vdi", DiskFormat.VDI),
    ("/x/d.vmdk", DiskFormat.VMDK),
    ("/x/d.vhd", DiskFormat.VHD),
    ("/x/d.raw", DiskFormat.RAW),
    ("/x/d.img", DiskFormat.RAW),
    ("/x/noext", DiskFormat.VDI),
    ("/x/d.iso", DiskFormat.VDI),  # no ISO format in the model yet
])
def test_default_format_from_extension(parser, path, expected):
    assert parser.default_format_for(path) == expected


def test_medium_info_decodes_capacity_format_and_variant(parser):
    info = parser.parse_medium_info(
        read_fixture("showmediuminfo_multidisk_2.txt"), DiskFormat.VDI
    )
    assert info == {
        "size_mb": 102400,
        "format": DiskFormat.VHD,
        "variant": DiskVariant.THICK,
    }


def test_medium_info_falls_back_to_the_supplied_default(parser):
    info = parser.parse_medium_info("nothing useful here\n", DiskFormat.VMDK)
    assert info["format"] == DiskFormat.VMDK
    assert info["size_mb"] == 20480
    assert info["variant"] == DiskVariant.THIN


def test_parser_never_touches_the_hypervisor(parser):
    """The guard in conftest would fail this test if parse_text shelled out."""
    vm = parser.parse_text(
        "x", read_fixture("showvminfo_bios_minimal.txt"),
        probe=lambda p: {"size_mb": 1, "format": DiskFormat.VDI,
                         "variant": DiskVariant.THIN},
    )
    assert vm.disks[0].size_mb == 1


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
    assert names == ["IDE", "SATA", "Floppy"]


def test_controller_order_is_stable_across_repeated_parses():
    first = [sc.name for sc in parse_label("multidisk").storage_controllers]
    for _ in range(5):
        assert [sc.name for sc in parse_label("multidisk").storage_controllers] == first


@pytest.mark.documents_bug
@pytest.mark.xfail(strict=True, reason="F-15: I82078 is absent from vbox_controller_map, so a floppy controller is typed SATA")
def test_floppy_controller_is_not_typed_as_sata():
    """F-15 — an unmapped controller type silently becomes SATA."""
    vm = parse_label("floppy_first")
    floppy = [sc for sc in vm.storage_controllers if sc.name == "Floppy"][0]
    assert floppy.controller_type != StorageControllerType.SATA


def test_floppy_controller_currently_reports_itself_as_sata():
    """The wrong behaviour, pinned."""
    vm = parse_label("floppy_first")
    floppy = [sc for sc in vm.storage_controllers if sc.name == "Floppy"][0]
    assert floppy.controller_type == StorageControllerType.SATA
