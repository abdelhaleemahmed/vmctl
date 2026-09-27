"""Translation policy and the lossiness report (A-04).

The report is the deliverable, not a side effect. A multi-hypervisor tool that
quietly produces a VM different from the one asked for is not trustworthy, so
every substitution, drop and conversion is recorded and shown before anything
runs.
"""

import pytest

from vmctl.core.exceptions import ValidationError
from vmctl.core.translate import (
    Conversion,
    Drop,
    Policy,
    Substitution,
    TranslationReport,
    Translator,
)
from vmctl.core.vmconfig import (
    DiskConfig,
    DiskFormat,
    DiskType,
    DiskVariant,
    FirmwareType,
    StorageControllerType,
)
from vmctl.providers.libvirt.capabilities import LibvirtCapabilities
from vmctl.providers.virtualbox.capabilities import VirtualBoxCapabilities


@pytest.fixture
def vbox():
    return VirtualBoxCapabilities.get()


@pytest.fixture
def libvirt():
    return LibvirtCapabilities.get()


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------


def test_strict_refuses_at_the_end_and_says_how_to_proceed(vbox):
    """An error that does not say what to do instead is half an error.

    Nothing is raised while resolving: a refusal is recorded and resolution
    continues, so every problem is found in one pass.
    """
    t = Translator(vbox, Policy.STRICT)
    disk = DiskConfig(name="d", size_mb=1024, format=DiskFormat.VHDX)
    assert t.format_for(disk, "disks[0].format") is DiskFormat.VDI  # does not raise
    with pytest.raises(ValidationError) as excinfo:
        t.finish()
    assert "--policy nearest" in (excinfo.value.recovery_hint or "")
    assert "vdi would be used instead" in str(excinfo.value)


def test_every_refusal_is_reported_in_one_error(vbox):
    """Refusing on the first problem makes a user fix one thing, run again, and
    find the next. One error listing all of them is the same information at once.
    """
    t = Translator(vbox, Policy.STRICT)
    for i, fmt in enumerate((DiskFormat.VHDX, DiskFormat.VHDX)):
        t.format_for(DiskConfig(name=f"d{i}", size_mb=1024, format=fmt), f"disks[{i}].format")
    with pytest.raises(ValidationError) as excinfo:
        t.finish()
    assert "2 settings are not supported" in str(excinfo.value)
    assert len(excinfo.value.constraints) == 2


def test_finish_is_quiet_when_nothing_was_refused(vbox):
    t = Translator(vbox, Policy.NEAREST)
    t.format_for(DiskConfig(name="d", size_mb=1024, format=DiskFormat.VHDX), "disks[0].format")
    t.finish()  # must not raise: nearest substituted rather than refused


def test_nearest_substitutes_and_records(vbox):
    t = Translator(vbox, Policy.NEAREST)
    disk = DiskConfig(name="d", size_mb=1024, format=DiskFormat.VHDX)
    assert t.format_for(disk, "disks[0].format") is DiskFormat.VDI
    assert len(t.report.substitutions) == 1
    assert t.report.substitutions[0].requested == "vhdx"
    assert t.report.substitutions[0].used == "vdi"


def test_convert_records_a_conversion_when_there_is_an_image(vbox):
    """The data is kept: an existing VHDX can be read, so it can be converted."""
    t = Translator(vbox, Policy.CONVERT)
    disk = DiskConfig(name="d", size_mb=1024, format=DiskFormat.VHDX, disk_path="/images/d.vhdx")
    assert t.format_for(disk, "disks[0].format") is DiskFormat.VDI
    assert t.report.conversions and not t.report.substitutions


def test_convert_falls_back_to_substitution_with_nothing_to_convert(vbox):
    """A config that only *describes* a disk has no image yet.

    Calling that a conversion would promise something nothing can do, so it is a
    substitution: the disk is simply created in a format the provider supports.
    """
    t = Translator(vbox, Policy.CONVERT)
    disk = DiskConfig(name="d", size_mb=1024, format=DiskFormat.VHDX)
    assert t.format_for(disk, "disks[0].format") is DiskFormat.VDI
    assert t.report.substitutions and not t.report.conversions


def test_a_format_the_provider_cannot_even_read_is_substituted_not_converted(libvirt):
    """Converting needs something readable to convert from."""
    libvirt.formats.pop(DiskFormat.VDI)
    t = Translator(libvirt, Policy.CONVERT)
    disk = DiskConfig(name="d", size_mb=1024, format=DiskFormat.VDI)
    assert t.format_for(disk, "disks[0].format") is DiskFormat.QCOW2
    assert t.report.substitutions and not t.report.conversions


def test_a_supported_value_is_left_alone(vbox):
    t = Translator(vbox, Policy.NEAREST)
    disk = DiskConfig(name="d", size_mb=1024, format=DiskFormat.VMDK)
    assert t.format_for(disk, "disks[0].format") is DiskFormat.VMDK
    assert not t.report


# ---------------------------------------------------------------------------
# Buses
# ---------------------------------------------------------------------------


def test_an_unsupported_bus_is_refused_under_strict(libvirt):
    t = Translator(libvirt, Policy.STRICT)
    disk = DiskConfig(name="d", size_mb=1024, controller=StorageControllerType.IDE)
    # The bus it *would* use is returned, so resolution can carry on and collect
    # every other problem before reporting.
    assert t.bus_for(disk, "disks[0].controller") is StorageControllerType.VIRTIO_SCSI
    with pytest.raises(ValidationError, match="not supported"):
        t.finish()


def test_a_substituted_bus_lands_on_the_providers_idiomatic_one(libvirt):
    """virtio-scsi, not merely the first alphabetically valid bus.

    A substitution that is only *valid* is worse than one that is what a user of
    that hypervisor would have chosen.
    """
    t = Translator(libvirt, Policy.NEAREST)
    disk = DiskConfig(name="d", size_mb=1024, controller=StorageControllerType.IDE)
    assert t.bus_for(disk, "disks[0].controller") is StorageControllerType.VIRTIO_SCSI


def test_an_optical_drive_substitutes_onto_a_bus_that_carries_one(vbox):
    """NVMe carries disks only, so a CD-ROM on it has to move."""
    t = Translator(vbox, Policy.NEAREST)
    disk = DiskConfig(name="cd", type=DiskType.DVD, controller=StorageControllerType.NVME)
    used = t.bus_for(disk, "disks[0].controller")
    assert vbox.can_attach(DiskType.DVD, used)
    assert used is StorageControllerType.IDE  # VirtualBox's idiomatic optical bus


def test_a_device_kind_no_bus_carries_is_refused_whatever_the_policy(libvirt):
    """Substitution needs somewhere to substitute to."""
    for bus in list(libvirt.buses):
        libvirt.attach[(DiskType.FLOPPY, bus)] = False
    t = Translator(libvirt, Policy.NEAREST)
    disk = DiskConfig(name="f", type=DiskType.FLOPPY, controller=StorageControllerType.FLOPPY)
    with pytest.raises(ValidationError, match="no bus"):
        t.bus_for(disk, "disks[0].controller")


# ---------------------------------------------------------------------------
# Allocation: always adjusted, because there is only one possible answer
# ---------------------------------------------------------------------------


def test_an_impossible_allocation_is_adjusted_even_under_strict(vbox):
    """VirtualBox cannot create a dynamic RAW, so "thin" has no other answer.

    Refusing would be unhelpful when exactly one value is possible, so this
    always adjusts -- and records it.
    """
    t = Translator(vbox, Policy.STRICT)
    disk = DiskConfig(name="d", size_mb=1024, format=DiskFormat.RAW, variant=DiskVariant.THIN)
    assert t.allocation_for(disk, DiskFormat.RAW, "disks[0]") is DiskVariant.THICK
    assert t.report.substitutions[0].used == "thick"


def test_qcow2_is_the_mirror_image(vbox):
    t = Translator(vbox, Policy.STRICT)
    disk = DiskConfig(name="d", size_mb=1024, format=DiskFormat.QCOW2, variant=DiskVariant.THICK)
    assert t.allocation_for(disk, DiskFormat.QCOW2, "disks[0]") is DiskVariant.THIN


def test_a_possible_allocation_is_untouched(vbox):
    t = Translator(vbox, Policy.STRICT)
    disk = DiskConfig(name="d", size_mb=1024, format=DiskFormat.VDI, variant=DiskVariant.THICK)
    assert t.allocation_for(disk, DiskFormat.VDI, "disks[0]") is DiskVariant.THICK
    assert not t.report


# ---------------------------------------------------------------------------
# Firmware
# ---------------------------------------------------------------------------


def test_an_efi_variant_substitutes_onto_another_efi_variant(libvirt):
    """Falling back to BIOS would change how the guest boots, which is not a
    "nearest" value at all."""
    t = Translator(libvirt, Policy.NEAREST)
    from vmctl.core.vmconfig import (
        BootConfig,
        CPUConfig,
        FirmwareConfig,
        MemoryConfig,
        VMConfig,
    )

    vm = VMConfig(
        name="x",
        cpu=CPUConfig(),
        memory=MemoryConfig(),
        firmware=FirmwareConfig(type=FirmwareType.EFI32),
        disks=[],
        networks=[],
        boot=BootConfig(),
        storage_controllers=[],
    )
    used = t.firmware_for(vm)
    assert used is not FirmwareType.BIOS
    assert used in (FirmwareType.EFI, FirmwareType.EFI64)


# ---------------------------------------------------------------------------
# Drops, and the report itself
# ---------------------------------------------------------------------------


def test_a_setting_with_no_equivalent_is_recorded_not_refused(libvirt):
    """Strict refuses a value the provider cannot *do*; it does not refuse a VM
    because the provider has no concept of one of its settings."""
    t = Translator(libvirt, Policy.STRICT)
    t.drop("memory.vram_mb", 128, "video memory is a device property here")
    assert t.report.drops
    assert t.report.lossy


def test_an_empty_report_says_so():
    report = TranslationReport("virtualbox", Policy.STRICT)
    assert not report
    assert not report.lossy
    assert "maps exactly" in report.render()


def test_the_report_reads_as_a_block():
    report = TranslationReport("libvirt", Policy.CONVERT)
    report.conversions.append(Conversion("disks[0].format", "vdi", "qcow2", "why"))
    report.substitutions.append(Substitution("disks[0].controller", "ide", "sata", "why"))
    report.drops.append(Drop("memory.vram_mb", 128, "why"))
    text = report.render()
    assert "Translating for libvirt (policy: convert)" in text
    assert text.index("will be converted") < text.index("substituted") < text.index("not applied")
    assert len(report.lines()) == 3


def test_conversions_are_reported_before_substitutions():
    """A conversion keeps the data; a substitution does not. The one that
    preserves more should be read first."""
    report = TranslationReport("x", Policy.CONVERT)
    report.drops.append(Drop("d", 1, "r"))
    report.conversions.append(Conversion("c", "a", "b", "r"))
    assert report.lines()[0].startswith("c:")
