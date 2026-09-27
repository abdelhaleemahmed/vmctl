"""Validator tests: errors raise, warnings are returned, nothing is mutated."""

import copy

import pytest

from vmctl.core.exceptions import ValidationError
from vmctl.core.vmconfig import (
    StorageDevice,
    DeviceKind,
    FirmwareType,
    NetworkConfig,
    NetworkType,
    StorageController,
    BusType,
)
from vmctl.providers.virtualbox.capabilities import VirtualBoxCapabilities
from vmctl.validators.vm_validator import VMValidator

from conftest import VM_LABELS, parse_label


@pytest.fixture
def validator():
    return VMValidator(VirtualBoxCapabilities.get())


# ---------------------------------------------------------------------------
# Real configurations must pass cleanly
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("label", VM_LABELS)
def test_captured_vms_validate_without_warnings(validator, label):
    """A warning that fires on correct input is worse than no warning.

    Every fixture is a VM VirtualBox itself created, so all of them must come
    back clean. This is the guard against re-introducing noisy rules.
    """
    assert validator.validate(parse_label(label)) == []


def test_validator_does_not_modify_the_configuration(validator):
    """F-08 - it used to flip disks[0].bootable behind the caller's back."""
    vm = parse_label("multidisk")
    before = copy.deepcopy(vm)
    validator.validate(vm)
    assert vm == before


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mutate,needle",
    [
        (lambda vm: setattr(vm, "name", ""), "name"),
        (lambda vm: setattr(vm.cpu, "count", 0), "cpu"),
        (lambda vm: setattr(vm.cpu, "execution_cap", 0), "execution cap"),
        (lambda vm: setattr(vm.cpu, "execution_cap", 101), "execution cap"),
        (lambda vm: setattr(vm.memory, "mb", 2), "memory"),
        (lambda vm: setattr(vm.memory, "vram_mb", 0), "vram"),
        (lambda vm: setattr(vm.cpu, "count", 999), "exceeds"),
        (lambda vm: setattr(vm.memory, "vram_mb", 512), "exceeds"),
    ],
)
def test_invalid_values_raise(validator, vm_minimal, mutate, needle):
    mutate(vm_minimal)
    with pytest.raises(ValidationError) as excinfo:
        validator.validate(vm_minimal)
    assert needle.lower() in str(excinfo.value).lower()


def test_disk_too_small_is_rejected(validator, vm_minimal):
    vm_minimal.storage[0].size_mb = 1
    with pytest.raises(ValidationError, match="at least 10 MB"):
        validator.validate(vm_minimal)


def test_removable_devices_are_exempt_from_size_limits(validator, vm_minimal):
    """A DVD drive has no size of its own, so 0 must not be an error."""
    vm_minimal.storage.append(
        StorageDevice(
            name="cd",
            kind=DeviceKind.CDROM,
            size_mb=0,
            bus=BusType.IDE,
            controller="IDE Controller",
            slot=0,
        )
    )
    validator.validate(vm_minimal)  # must not raise


def test_duplicate_disk_names_rejected(validator, vm_minimal):
    vm_minimal.storage.append(StorageDevice(name="system", slot=1))
    with pytest.raises(ValidationError, match="unique"):
        validator.validate(vm_minimal)


def test_duplicate_controller_names_rejected(validator, vm_minimal):
    vm_minimal.storage_controllers = [
        StorageController(name="SATA", bus=BusType.SATA),
        StorageController(name="SATA", bus=BusType.SATA),
    ]
    with pytest.raises(ValidationError, match="unique"):
        validator.validate(vm_minimal)


def test_two_disks_pinned_to_the_same_slot_are_rejected(validator, vm_minimal):
    """F-08 - this used to pass and then fail inside VBoxManage mid-create.

    Both positions have to be *stated* for this to be a conflict: a device that
    does not say where it goes is placed somewhere free instead (A-06).
    """
    vm_minimal.storage[0].slot = 0
    vm_minimal.storage[0].unit = 0
    vm_minimal.storage.append(
        StorageDevice(
            name="second",
            controller=vm_minimal.storage[0].controller,
            slot=0,
            unit=0,
        )
    )
    with pytest.raises(ValidationError, match="both attached to"):
        validator.validate(vm_minimal)


def test_devices_that_do_not_state_a_position_do_not_collide(validator, vm_minimal):
    """The friction A-06 removes: for libvirt, port and device are vmctl's own
    bookkeeping, so a config that omits them used to collide in slot 0."""
    vm_minimal.storage.append(StorageDevice(name="second", size_mb=1024))
    vm_minimal.storage.append(StorageDevice(name="third", size_mb=1024))
    assert validator.validate(vm_minimal) == []


def test_port_beyond_the_controller_port_count_is_rejected(validator, vm_minimal):
    vm_minimal.storage_controllers = [
        StorageController(name="SATA Controller", bus=BusType.SATA, port_count=2)
    ]
    vm_minimal.storage[0].controller = "SATA Controller"
    vm_minimal.storage[0].slot = 5
    with pytest.raises(ValidationError, match="slot 5"):
        validator.validate(vm_minimal)


def test_ide_allows_two_devices_per_port_and_no_more(validator, vm_minimal):
    vm_minimal.storage_controllers = [
        StorageController(name="IDE Controller", bus=BusType.IDE, port_count=2)
    ]
    vm_minimal.storage[0].controller = "IDE Controller"
    vm_minimal.storage[0].bus = BusType.IDE
    vm_minimal.storage[0].unit = 1
    validator.validate(vm_minimal)  # master/slave is fine
    vm_minimal.storage[0].unit = 2
    with pytest.raises(ValidationError, match="unit 2"):
        validator.validate(vm_minimal)


def test_sata_allows_only_one_device_per_port(validator, vm_minimal):
    vm_minimal.storage_controllers = [
        StorageController(name="SATA Controller", bus=BusType.SATA, port_count=4)
    ]
    vm_minimal.storage[0].controller = "SATA Controller"
    vm_minimal.storage[0].unit = 1
    with pytest.raises(ValidationError, match="unit 1"):
        validator.validate(vm_minimal)


def test_secure_boot_with_bios_is_rejected(validator, vm_minimal):
    """F-17 - VirtualBox refuses the combination, so catch it at validate time."""
    vm_minimal.firmware.secure_boot = True
    vm_minimal.firmware.type = FirmwareType.BIOS
    with pytest.raises(ValidationError, match="requires EFI"):
        validator.validate(vm_minimal)


def test_secure_boot_with_efi_is_accepted(validator, vm_minimal):
    vm_minimal.firmware.secure_boot = True
    vm_minimal.firmware.type = FirmwareType.EFI64
    validator.validate(vm_minimal)


def test_too_many_network_adapters_uses_the_provider_limit(validator, vm_minimal):
    """F-08 - the limit comes from capabilities, not a hardcoded 8."""
    vm_minimal.networks = [NetworkConfig() for _ in range(9)]
    with pytest.raises(ValidationError) as excinfo:
        validator.validate(vm_minimal)
    assert "at most 8" in str(excinfo.value)


def test_unsupported_controller_bus_is_rejected(vm_minimal):
    """A bus the provider does not declare must be refused, not attempted."""
    caps = VirtualBoxCapabilities.get()
    caps.buses.pop(BusType.NVME)
    vm_minimal.storage_controllers = [StorageController(name="NVMe Controller", bus=BusType.NVME)]
    with pytest.raises(ValidationError, match="not supported"):
        VMValidator(caps).validate(vm_minimal)


def test_every_bus_phase_1_added_is_accepted(validator, vm_minimal):
    """NVMe, floppy, USB and virtio-scsi must all be valid buses now."""
    for bus, ports in (("nvme", 8), ("floppy", 1), ("usb", 8), ("virtio-scsi", 16)):
        vm_minimal.storage_controllers = [
            StorageController(name=f"{bus} ctl", controller_type=BusType(bus), port_count=ports)
        ]
        validator.validate(vm_minimal)


def test_invalid_boot_device_rejected(validator, vm_minimal):
    vm_minimal.boot.order = ["disk", "usb"]
    with pytest.raises(ValidationError, match="boot device"):
        validator.validate(vm_minimal)


def test_boot_from_disk_with_no_disks_is_an_error(validator, vm_minimal):
    vm_minimal.storage = []
    vm_minimal.boot.order = ["disk"]
    with pytest.raises(ValidationError, match="no disks"):
        validator.validate(vm_minimal)


# ---------------------------------------------------------------------------
# Warnings
# ---------------------------------------------------------------------------


def test_unaligned_memory_warns(validator, vm_minimal):
    """F-08 - validate() used to build a warnings list and never append to it."""
    vm_minimal.memory.mb = 2051
    warnings = validator.validate(vm_minimal)
    assert any("multiple of 4" in w for w in warnings)


def test_nothing_bootable_warns(validator, vm_minimal):
    vm_minimal.storage = [StorageDevice(name="cd", kind=DeviceKind.CDROM, size_mb=0)]
    vm_minimal.boot.order = ["floppy", "none"]
    warnings = validator.validate(vm_minimal)
    assert any("will not boot" in w for w in warnings)


def test_default_boot_order_without_floppy_or_dvd_does_not_warn(validator, vm_minimal):
    """VirtualBox's own default order is floppy, dvd, disk -- warning per
    missing device would fire on almost every VM."""
    vm_minimal.boot.order = ["floppy", "dvd", "disk", "none"]
    assert validator.validate(vm_minimal) == []


def test_size_on_a_removable_device_warns(validator, vm_minimal):
    vm_minimal.storage.append(StorageDevice(name="cd", kind=DeviceKind.CDROM, size_mb=4096, slot=1))
    warnings = validator.validate(vm_minimal)
    assert any("is ignored" in w for w in warnings)


@pytest.mark.parametrize(
    "mode", [NetworkType.BRIDGED, NetworkType.HOSTONLY, NetworkType.NATNETWORK]
)
def test_adapter_mode_without_a_name_warns(validator, vm_minimal, mode):
    vm_minimal.networks = [NetworkConfig(network_type=mode)]
    warnings = validator.validate(vm_minimal)
    assert any("names no adapter" in w for w in warnings)


def test_internal_network_without_a_name_does_not_warn(validator, vm_minimal):
    """VirtualBox defaults an unnamed internal network to 'intnet'."""
    vm_minimal.networks = [NetworkConfig(network_type=NetworkType.INTERNAL)]
    assert validator.validate(vm_minimal) == []


def test_smp_without_ioapic_warns(validator, vm_minimal):
    vm_minimal.cpu.count = 4
    vm_minimal.boot.ioapic = False
    warnings = validator.validate(vm_minimal)
    assert any("I/O APIC" in w for w in warnings)


def test_a_made_up_guest_os_is_now_warned_about(validator, vm_minimal):
    """This test used to assert the opposite, and said why: VirtualBox reported
    display names while the capability list held ids, so any static check flagged
    every real VM. A-05 removed the reason -- there is a neutral catalogue, and the
    provider's own list is generated from `VBoxManage list ostypes` -- so the check
    is possible and this expectation flips."""
    vm_minimal.ostype = "CompletelyMadeUp_64"
    assert any("guest_os" in w for w in validator.validate(vm_minimal))


# ---------------------------------------------------------------------------
# Provider contract
# ---------------------------------------------------------------------------


def test_base_provider_refuses_to_edit_rather_than_deleting(vm_minimal):
    """F-11 - the default edit_vm was delete_vm() + create_vm(), and delete_vm
    passes --delete, so it destroyed the disks of any provider that did not
    override it. It must now refuse instead."""
    from vmctl.providers.base import BaseProvider

    destroyed = []

    class Incomplete(BaseProvider):
        name = "incomplete"
        capabilities = {}

        def list_vms(self):
            return ["vm1"]

        def read_vm(self, vm_name):
            raise NotImplementedError

        def create_vm(self, vm, execute=True):
            raise AssertionError("must not recreate the VM")

        def delete_vm(self, vm_name):
            destroyed.append(vm_name)
            raise AssertionError("must not delete the VM to edit it")

        def start_vm(self, vm_name):
            raise NotImplementedError

        def stop_vm(self, vm_name, force=False, wait=0):
            raise NotImplementedError

        def get_vm_status(self, vm_name):
            return "stopped"

        def storage_location(self):
            from vmctl.core.storage import directory

            return directory("/nowhere")

    with pytest.raises(NotImplementedError):
        Incomplete().edit_vm("vm1", vm_minimal)
    assert destroyed == []


def test_a_device_with_no_format_is_not_refused(validator, vm_minimal):
    """No format means "whichever this provider creates", so there is nothing to
    check and nothing to substitute -- the portable default (M-04)."""
    vm_minimal.storage[0].format = None
    assert isinstance(validator.validate(vm_minimal), list)


def test_two_controllers_on_one_bus_need_distinct_ids(validator, vm_minimal):
    """Both would otherwise derive `sata0`, and a device naming it would be
    ambiguous -- which is the whole reason the id is separate (M-02)."""
    vm_minimal.storage_controllers = [
        StorageController(bus=BusType.SATA, native_name="First"),
        StorageController(bus=BusType.SATA, native_name="Second"),
    ]
    with pytest.raises(ValidationError, match="ids must be unique"):
        validator.validate(vm_minimal)


def test_naming_an_undeclared_controller_warns_and_falls_back(validator, vm_minimal):
    """ "Whichever controller serves SATA" is how this has always been written, so
    it stays legal (F-01) -- but it is said out loud, because it is also what a
    typo looks like."""
    vm_minimal.storage[0].controller = "SATA"
    warnings = validator.validate(vm_minimal)
    assert any("nothing declares" in w for w in warnings)


def test_a_misspelled_guest_os_warns(validator, vm_minimal):
    """Unwarnable before A-05: VirtualBox reports descriptions and accepts ids, so
    any static comparison flagged every real VM. With a neutral catalogue and a
    per-provider list, a typo can be told from a deliberate passthrough."""
    vm_minimal.guest_os = "Ubunto"
    assert any("guest_os" in w for w in validator.validate(vm_minimal))


def test_a_native_or_neutral_guest_os_does_not_warn(validator, vm_minimal):
    for value in ("ubuntu22.04", "Ubuntu_64", "Ubuntu (64-bit)"):
        vm_minimal.guest_os = value
        assert not [w for w in validator.validate(vm_minimal) if "guest_os" in w], value


def test_a_cpu_topology_must_multiply_out_to_the_count(validator, vm_minimal):
    """Measured on libvirt: "CPU topology doesn't match maximum vcpu count". It is a
    real constraint, so it is checked for every provider rather than left to the one
    that happens to enforce it."""
    vm_minimal.cpu.count = 4
    vm_minimal.cpu.sockets, vm_minimal.cpu.cores = 1, 2
    with pytest.raises(ValidationError, match="describes 2 vCPU"):
        validator.validate(vm_minimal)


def test_a_consistent_topology_is_accepted(validator, vm_minimal):
    vm_minimal.cpu.count = 4
    vm_minimal.cpu.sockets, vm_minimal.cpu.cores, vm_minimal.cpu.threads = 2, 2, 1
    assert isinstance(validator.validate(vm_minimal), list)


def test_a_partial_topology_counts_the_unset_parts_as_one(validator, vm_minimal):
    vm_minimal.cpu.count = 2
    vm_minimal.cpu.sockets = 2
    assert isinstance(validator.validate(vm_minimal), list)
