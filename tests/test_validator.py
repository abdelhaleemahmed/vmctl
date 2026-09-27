"""Validator tests — limits, structural rules, and the warnings pathway."""
import pytest

from vmctl.core.exceptions import ValidationError
from vmctl.core.vmconfig import DiskConfig, FirmwareType, StorageControllerConfig, \
    StorageControllerType
from vmctl.providers.virtualbox.capabilities import VirtualBoxCapabilities
from vmctl.validators.vm_validator import VMValidator

from conftest import parse_label


@pytest.fixture
def validator():
    return VMValidator(VirtualBoxCapabilities.get_capabilities())


def test_captured_vms_validate(validator):
    for label in ("bios_minimal", "multidisk", "multinic"):
        assert validator.validate(parse_label(label)) == []


@pytest.mark.parametrize("mutate,message", [
    (lambda vm: setattr(vm, "name", ""), "name"),
    (lambda vm: setattr(vm.cpu, "count", 0), "CPU"),
    (lambda vm: setattr(vm.memory, "mb", 2), "Memory"),
    (lambda vm: setattr(vm.memory, "vram_mb", 0), "VRAM"),
    (lambda vm: setattr(vm.cpu, "count", 999), "exceeds"),
    (lambda vm: setattr(vm.memory, "vram_mb", 512), "exceeds"),
])
def test_invalid_values_raise(validator, vm_minimal, mutate, message):
    mutate(vm_minimal)
    with pytest.raises(ValidationError) as excinfo:
        validator.validate(vm_minimal)
    assert message.lower() in str(excinfo.value).lower()


def test_disk_size_bounds(validator, vm_minimal):
    vm_minimal.disks[0].size_mb = 1
    with pytest.raises(ValidationError):
        validator.validate(vm_minimal)


def test_duplicate_disk_names_rejected(validator, vm_minimal):
    vm_minimal.disks.append(DiskConfig(name="system"))
    with pytest.raises(ValidationError, match="unique"):
        validator.validate(vm_minimal)


def test_duplicate_controller_names_rejected(validator, vm_minimal):
    vm_minimal.storage_controllers = [
        StorageControllerConfig(name="SATA",
                                controller_type=StorageControllerType.SATA),
        StorageControllerConfig(name="SATA",
                                controller_type=StorageControllerType.SATA),
    ]
    with pytest.raises(ValidationError, match="unique"):
        validator.validate(vm_minimal)


def test_invalid_boot_device_rejected(validator, vm_minimal):
    vm_minimal.boot.order = ["disk", "usb"]
    with pytest.raises(ValidationError, match="boot device"):
        validator.validate(vm_minimal)


def test_efi_is_accepted_when_the_provider_supports_it(validator, vm_minimal):
    vm_minimal.firmware.type = FirmwareType.EFI
    assert validator.validate(vm_minimal) == []


# ---------------------------------------------------------------------------
# Known-broken behaviour, pinned
# ---------------------------------------------------------------------------

def test_validator_currently_mutates_the_config(validator, vm_minimal):
    """F-08 — the validator silently flips bootable instead of warning."""
    vm_minimal.disks[0].bootable = False
    validator.validate(vm_minimal)
    assert vm_minimal.disks[0].bootable is True, "documented current behaviour"


@pytest.mark.documents_bug
@pytest.mark.xfail(strict=True, reason="F-08: the validator mutates the config instead of warning")
def test_validator_is_pure(validator, vm_minimal):
    vm_minimal.disks[0].bootable = False
    validator.validate(vm_minimal)
    assert vm_minimal.disks[0].bootable is False


@pytest.mark.documents_bug
@pytest.mark.xfail(strict=True, reason="F-08: validate() never appends to its warnings list")
def test_unaligned_memory_produces_a_warning(validator, vm_minimal):
    vm_minimal.memory.mb = 2051
    assert validator.validate(vm_minimal), "expected at least one warning"


@pytest.mark.documents_bug
@pytest.mark.xfail(strict=True, reason="F-08: supported_os_types is never consulted")
def test_unknown_ostype_produces_a_warning(validator, vm_minimal):
    vm_minimal.ostype = "NotARealOsType_64"
    assert validator.validate(vm_minimal)


@pytest.mark.documents_bug
@pytest.mark.xfail(strict=True, reason="F-08: no port/device collision check exists")
def test_two_disks_on_the_same_port_are_rejected(validator, vm_minimal):
    vm_minimal.disks.append(
        DiskConfig(name="second", controller_name="SATA", port=0, device=0)
    )
    with pytest.raises(ValidationError):
        validator.validate(vm_minimal)
