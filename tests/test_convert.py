"""Medium conversion (M-05).

Deciding *whether* to convert is provider-neutral; only *how* differs. These tests
cover the decision, and each provider's mechanism, without running either tool.
"""

import pytest

from vmctl.core.convert import (
    ConversionRequest,
    conversions_for,
    convert,
    describe,
    plan_conversions,
)
from vmctl.core.exceptions import ProviderError
from vmctl.core.plan import StepKind
from vmctl.core.vmconfig import DiskConfig, DiskFormat, DeviceKind
from vmctl.providers.libvirt.capabilities import LibvirtCapabilities
from vmctl.providers.libvirt.convert import QemuImgConverter
from vmctl.providers.virtualbox.capabilities import VirtualBoxCapabilities
from vmctl.providers.virtualbox.convert import CloneMediumConverter


@pytest.fixture
def qemu():
    return QemuImgConverter()


@pytest.fixture
def clonemedium():
    return CloneMediumConverter()


def _request(**kwargs):
    base = dict(
        source="/in/root.vdi",
        target="/out/root.qcow2",
        target_format=DiskFormat.QCOW2,
        source_format=DiskFormat.VDI,
        label="root",
    )
    base.update(kwargs)
    return ConversionRequest(**base)


# ---------------------------------------------------------------------------
# What needs converting
# ---------------------------------------------------------------------------


def test_a_format_change_is_needed():
    assert _request().needed


def test_the_same_file_in_the_same_format_is_a_no_op():
    assert not _request(
        source="/x/d.qcow2", target="/x/d.qcow2", source_format=DiskFormat.QCOW2
    ).needed


def test_a_different_path_is_still_a_copy():
    """`--clone-disks` wants this: same format, different file."""
    assert _request(source="/a/d.qcow2", target="/b/d.qcow2", source_format=DiskFormat.QCOW2).needed


def test_a_disk_with_no_image_yet_is_not_converted(vm_minimal):
    """A configuration that merely describes a disk has nothing to convert from."""
    caps = LibvirtCapabilities.get()
    vm_minimal.disks = [DiskConfig(name="d", size_mb=1024, format=DiskFormat.VHDX)]
    assert conversions_for(vm_minimal, caps, lambda vm, d, ext: "/out/x") == []


def test_an_existing_image_the_target_cannot_create_is_converted(vm_minimal):
    caps = LibvirtCapabilities.get()
    vm_minimal.disks = [
        DiskConfig(name="d", size_mb=1024, format=DiskFormat.VHDX, disk_path="/in/d.vhdx")
    ]
    requests = conversions_for(vm_minimal, caps, lambda vm, disk, ext: f"/out/{disk.name}.{ext}")
    assert len(requests) == 1
    assert requests[0].source == "/in/d.vhdx"
    assert requests[0].target_format is DiskFormat.QCOW2  # libvirt's native
    assert requests[0].target == "/out/d.qcow2"


def test_an_image_the_target_can_create_is_left_alone(vm_minimal):
    caps = LibvirtCapabilities.get()
    vm_minimal.disks = [
        DiskConfig(name="d", size_mb=1024, format=DiskFormat.QCOW2, disk_path="/in/d.qcow2")
    ]
    assert conversions_for(vm_minimal, caps, lambda vm, d, ext: "/out/x") == []


def test_removable_media_are_never_converted(vm_minimal):
    """An ISO is inserted, not created, so its format is not ours to change."""
    caps = LibvirtCapabilities.get()
    vm_minimal.disks = [DiskConfig(name="cd", type=DeviceKind.CDROM, source="/iso/install.iso")]
    assert conversions_for(vm_minimal, caps, lambda vm, d, ext: "/out/x") == []


# ---------------------------------------------------------------------------
# qemu-img
# ---------------------------------------------------------------------------


def test_qemu_img_states_the_source_format_when_it_knows_it(qemu):
    """Letting qemu-img guess is a known way to be surprised by a file."""
    step = qemu.steps(_request())[0]
    assert step.argv[:4] == ["qemu-img", "convert", "-f", "vdi"]
    assert step.argv[-2:] == ["/in/root.vdi", "/out/root.qcow2"]


def test_qemu_img_omits_the_source_format_when_unknown(qemu):
    step = qemu.steps(_request(source_format=None))[0]
    assert "-f" not in step.argv


def test_qemu_img_conversions_can_be_undone(qemu):
    step = qemu.steps(_request())[0]
    assert step.undo is not None
    assert step.undo.argv == ["rm", "-f", "/out/root.qcow2"]
    assert step.undo.destructive


def test_qemu_img_reads_other_hypervisors_formats(qemu):
    """The reason qemu-img is the right tool for moving a VM onto libvirt."""
    for source in (DiskFormat.VDI, DiskFormat.VMDK, DiskFormat.VHD, DiskFormat.VHDX):
        assert qemu.can_convert(source, DiskFormat.QCOW2), source


def test_qemu_img_will_not_write_a_format_it_cannot(qemu):
    assert not qemu.can_convert(DiskFormat.VDI, DiskFormat.VHDX)


# ---------------------------------------------------------------------------
# VBoxManage clonemedium
# ---------------------------------------------------------------------------


def test_clonemedium_uses_the_providers_own_format_name(clonemedium):
    """qcow2 is 'QCOW' to VirtualBox; the name comes from the declaration."""
    step = clonemedium.steps(_request())[0]
    assert step.argv[:3] == ["VBoxManage", "clonemedium", "disk"]
    assert step.argv[-2:] == ["--format", "QCOW"]


def test_clonemedium_targets_only_formats_virtualbox_can_create(clonemedium):
    caps = VirtualBoxCapabilities.get()
    for fmt in DiskFormat:
        expected = caps.format_spec(fmt).support.creatable
        assert clonemedium.can_convert(DiskFormat.VDI, fmt) is expected, fmt


def test_clonemedium_conversions_can_be_undone(clonemedium):
    step = clonemedium.steps(_request(target="/out/root.vmdk", target_format=DiskFormat.VMDK))[0]
    assert step.undo.argv[:4] == ["VBoxManage", "closemedium", "disk", "/out/root.vmdk"]


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------


def test_a_conversion_comes_back_as_a_plan(qemu):
    plan = convert(
        "/in/d.vdi", "/out/d.qcow2", DiskFormat.QCOW2, qemu, "libvirt", source_format=DiskFormat.VDI
    )
    assert len(plan) == 1
    assert plan.steps[0].kind is StepKind.EXEC
    assert "convert" in plan.steps[0].description


def test_a_no_op_produces_an_empty_plan(qemu):
    plan = plan_conversions(
        [_request(source="/x/d.qcow2", target="/x/d.qcow2", source_format=DiskFormat.QCOW2)],
        qemu,
        "libvirt",
    )
    assert not plan


def test_a_provider_with_no_converter_is_reported(qemu):
    with pytest.raises(ProviderError, match="cannot convert disk images"):
        plan_conversions([_request()], None, "someprovider")


def test_a_pair_the_converter_cannot_handle_is_reported(clonemedium):
    with pytest.raises(ProviderError, match="cannot convert vdi to vhdx"):
        plan_conversions(
            [_request(target="/out/d.vhdx", target_format=DiskFormat.VHDX)],
            clonemedium,
            "virtualbox",
        )


def test_an_unknown_source_format_says_it_cannot_write_the_target(clonemedium):
    with pytest.raises(ProviderError, match="cannot write vhdx"):
        plan_conversions(
            [_request(source_format=None, target="/o/d.vhdx", target_format=DiskFormat.VHDX)],
            clonemedium,
            "virtualbox",
        )


def test_descriptions_distinguish_a_copy_from_a_conversion():
    assert describe(_request()).startswith("convert root from vdi to qcow2")
    same = _request(source="/a/d.qcow2", target="/b/d.qcow2", source_format=DiskFormat.QCOW2)
    assert describe(same).startswith("copy root")
