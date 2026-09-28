"""Converging a VM towards a file (E-02).

The whole risk in ``apply`` is doing *too much*: a configuration file is full of
defaults nobody asked for, and the hypervisor knows things the file cannot. So most
of these tests are about what converging leaves alone.
"""

import pytest

from vmctl.core.apply import Action, overlay, plan_convergence
from vmctl.core.diff import stated_paths
from vmctl.core.vmconfig import (
    BootConfig,
    CPUConfig,
    DeviceKind,
    DiskFormat,
    FirmwareConfig,
    MemoryConfig,
    NetworkConfig,
    NetworkType,
    StorageDevice,
    VMConfig,
)


def _vm(**overrides):
    base = dict(
        name="web",
        cpu=CPUConfig(count=2),
        memory=MemoryConfig(mb=1024, vram_mb=16),
        firmware=FirmwareConfig(),
        storage=[
            StorageDevice(
                name="root",
                size_mb=20480,
                format=DiskFormat.QCOW2,
                disk_path="/images/web_root.qcow2",
            )
        ],
        networks=[NetworkConfig(mac_address="080027AABBCC")],
        boot=BootConfig(),
        storage_controllers=[],
        metadata={"libvirt_uuid": "abc-123"},
    )
    base.update(overrides)
    return VMConfig(**base)


@pytest.fixture
def live():
    return _vm()


def _from_file(mapping):
    """Load a config the way ``apply`` does: as a VM, and as what the file states."""
    return VMConfig.from_dict(dict(mapping)), stated_paths(mapping)


# ---------------------------------------------------------------------------
# What a file is allowed to decide
# ---------------------------------------------------------------------------


def test_a_stated_field_is_applied(live):
    desired, stated = _from_file({"name": "web", "cpu": {"count": 4}})

    assert overlay(live, desired, stated).cpu.count == 4


def test_a_field_the_file_never_mentions_is_left_alone(live):
    """The destructive reading of a config file, and the reason `stated` exists: a
    file that says only `cpu.count` would otherwise reset the memory to a default
    nobody wrote down."""
    desired, stated = _from_file({"name": "web", "cpu": {"count": 4}})

    result = overlay(live, desired, stated)

    assert result.memory.mb == 1024
    assert result.memory.vram_mb == 16
    assert result.firmware.type is live.firmware.type


def test_a_sibling_in_the_same_section_is_left_alone(live):
    """`memory: {mb: 2048}` must not take `vram_mb` down with it."""
    desired, stated = _from_file({"name": "web", "memory": {"mb": 2048}})

    result = overlay(live, desired, stated)

    assert (result.memory.mb, result.memory.vram_mb) == (2048, 16)


def test_the_hypervisors_own_identifiers_are_never_taken_from_the_file(live):
    """A libvirt domain's UUID is in metadata; redefining it with a different one is
    refused by libvirt, and inventing one is not something a file should do."""
    desired, stated = _from_file({"name": "web", "cpu": {"count": 4}})

    assert overlay(live, desired, stated).metadata == {"libvirt_uuid": "abc-123"}


# ---------------------------------------------------------------------------
# What only the hypervisor can know
# ---------------------------------------------------------------------------


def test_a_disks_image_survives_being_converged(live):
    """The safety property that F-39 was the other half of: libvirt and QEMU redefine
    a VM wholesale, so a disk whose path was dropped would come back pointing at a
    path computed from its name -- which is not where the data is."""
    desired, stated = _from_file({"name": "web", "storage": [{"name": "root", "size_mb": 20480}]})

    result = overlay(live, desired, stated)

    assert result.storage[0].disk_path == "/images/web_root.qcow2"


def test_a_generated_mac_survives_being_converged(live):
    """A file rarely states a MAC, and dropping one gives the guest a new network
    card -- which is a reboot into a machine with no network configuration."""
    desired, stated = _from_file({"name": "web", "networks": [{"network_type": "bridged"}]})

    result = overlay(live, desired, stated)

    assert result.networks[0].mac_address == "080027AABBCC"
    assert result.networks[0].network_type is NetworkType.BRIDGED


def test_a_mac_the_file_does_state_is_applied(live):
    """Carrying a value over is not the same as overriding one."""
    desired, stated = _from_file({"name": "web", "networks": [{"mac_address": "080027000001"}]})

    assert overlay(live, desired, stated).networks[0].mac_address == "080027000001"


def test_the_file_decides_the_whole_device_list_when_it_states_one(live):
    """A list is replaced, not merged: writing `storage:` is stating all of it."""
    desired, stated = _from_file(
        {
            "name": "web",
            "storage": [
                {"name": "root", "size_mb": 20480},
                {"name": "data", "size_mb": 4096, "slot": 1},
            ],
        }
    )

    result = overlay(live, desired, stated)

    assert [d.name for d in result.storage] == ["root", "data"]
    # ...and the one that already existed keeps its image.
    assert result.storage[0].disk_path == "/images/web_root.qcow2"
    assert result.storage[1].disk_path is None


def test_a_file_with_no_storage_section_leaves_the_devices_alone(live):
    desired, stated = _from_file({"name": "web", "cpu": {"count": 4}})

    result = overlay(live, desired, stated)

    assert len(result.storage) == 1
    assert result.storage[0].disk_path == "/images/web_root.qcow2"


def test_neither_argument_is_modified(live):
    desired, stated = _from_file({"name": "web", "cpu": {"count": 4}})

    overlay(live, desired, stated)

    assert live.cpu.count == 2
    assert desired.cpu.count == 4


# ---------------------------------------------------------------------------
# What applying turns out to mean
# ---------------------------------------------------------------------------


def test_no_such_vm_means_create_it(live):
    desired, stated = _from_file({"name": "web"})

    convergence = plan_convergence(None, desired, stated)

    assert convergence.action is Action.CREATE
    assert convergence.vm is desired
    assert "does not exist" in convergence.describe()


def test_a_vm_that_matches_means_nothing_to_do(live):
    desired, stated = _from_file({"name": "web", "cpu": {"count": 2}})

    convergence = plan_convergence(live, desired, stated)

    assert convergence.action is Action.NOTHING
    assert convergence.changes == []
    assert "nothing to do" in convergence.describe()


def test_drift_means_converge_and_says_where(live):
    desired, stated = _from_file({"name": "web", "cpu": {"count": 4}})

    convergence = plan_convergence(live, desired, stated)

    assert convergence.action is Action.CONVERGE
    assert [c.path for c in convergence.changes] == ["cpu.count"]
    assert convergence.vm.cpu.count == 4


# ---------------------------------------------------------------------------
# What converging cannot do, said out loud
# ---------------------------------------------------------------------------


def test_a_disk_the_file_adds_with_no_image_is_reported(live):
    desired, stated = _from_file(
        {
            "name": "web",
            "storage": [
                {"name": "root", "size_mb": 20480},
                {"name": "data", "size_mb": 4096, "slot": 1},
            ],
        }
    )

    convergence = plan_convergence(live, desired, stated)

    assert any("no image behind it" in w for w in convergence.warnings)


def test_a_size_change_is_reported_rather_than_attempted(live):
    """Resizing an image is not something 'apply' should quietly mean."""
    desired, stated = _from_file({"name": "web", "storage": [{"name": "root", "size_mb": 40960}]})

    convergence = plan_convergence(live, desired, stated)

    assert any("does not resize" in w for w in convergence.warnings)


def test_a_disk_the_file_drops_says_the_image_is_kept(live):
    """Detaching is what a declarative file means; deleting the data is not."""
    live.storage.append(
        StorageDevice(name="data", size_mb=4096, slot=1, disk_path="/images/web_data.qcow2")
    )
    desired, stated = _from_file({"name": "web", "storage": [{"name": "root", "size_mb": 20480}]})

    convergence = plan_convergence(live, desired, stated)

    assert any("left on disk" in w for w in convergence.warnings)
    assert [d.name for d in convergence.vm.storage] == ["root"]


def test_an_optical_drive_is_not_a_disk_to_worry_about(live):
    live.storage.append(
        StorageDevice(name="cd", kind=DeviceKind.CDROM, source="/iso/boot.iso", slot=1)
    )
    desired, stated = _from_file(
        {
            "name": "web",
            "storage": [
                {"name": "root", "size_mb": 20480},
                {"name": "cd", "kind": "cdrom", "slot": 1},
            ],
        }
    )

    assert plan_convergence(live, desired, stated).warnings == []


# ---------------------------------------------------------------------------
# F-46: a stated list must not reset the fields it does not mention
# ---------------------------------------------------------------------------


def test_a_stated_device_keeps_the_fields_the_file_does_not_mention(live):
    """The device-level version of "a default is not a request". A file saying
    `storage: [{name: system, size_mb: 20480}]` was replacing the whole device, so
    `bootable`, `discard`, `allocation` and the rest went back to the model's defaults
    -- and the first symptom was VirtualBox warning about a storage layout that had not
    changed at all."""
    live.storage[0].bootable = True
    live.storage[0].discard = True
    live.storage[0].nonrotational = True
    desired, stated = _from_file({"name": "web", "storage": [{"name": "system", "size_mb": 40960}]})

    device = overlay(live, desired, stated).storage[0]

    assert device.size_mb == 40960  # what the file asked for
    assert device.bootable is True and device.discard is True and device.nonrotational is True
    assert device.name == "system"  # the file's own label for it


def test_a_stated_adapter_keeps_the_rest_of_its_settings(live):
    live.networks[0].promiscuous_mode = True
    desired, stated = _from_file({"name": "web", "networks": [{"model": "virtio"}]})

    adapter = overlay(live, desired, stated).networks[0]

    assert adapter.model.value == "virtio"
    assert adapter.promiscuous_mode is True
    assert adapter.mac_address == "080027AABBCC"


def test_a_device_the_file_adds_is_taken_from_the_file_entirely(live):
    desired, stated = _from_file(
        {
            "name": "web",
            "storage": [
                {"name": "root", "size_mb": 20480},
                {"name": "data", "size_mb": 4096, "slot": 1, "discard": True},
            ],
        }
    )

    added = overlay(live, desired, stated).storage[1]

    assert added.name == "data" and added.discard is True
