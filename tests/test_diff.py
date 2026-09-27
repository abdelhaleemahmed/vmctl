"""Drift between a VM and a file (E-01).

The command a config-as-code tool is not finished without. Every test here is really
about the same question -- *what is worth reporting?* -- because a diff that lists
everything is one nobody reads, and one that hides a real change is worse than none.
"""

from vmctl.core.devices import BusType
from vmctl.core.diff import ChangeKind, diff, stated_paths, summarise
from vmctl.core.platform import NicModel
from vmctl.core.vmconfig import (
    BootConfig,
    CPUConfig,
    FirmwareConfig,
    MemoryConfig,
    NetworkConfig,
    StorageDevice,
    VMConfig,
)


def _vm(**overrides):
    base = dict(
        name="web",
        cpu=CPUConfig(count=2),
        memory=MemoryConfig(mb=2048),
        firmware=FirmwareConfig(),
        storage=[StorageDevice(name="root", size_mb=20480, bus=BusType.SATA, slot=0)],
        networks=[NetworkConfig()],
        boot=BootConfig(),
        storage_controllers=[],
    )
    base.update(overrides)
    return VMConfig(**base)


def _paths(*paths):
    return set(paths)


# ---------------------------------------------------------------------------
# The same VM
# ---------------------------------------------------------------------------


def test_a_vm_that_matches_its_file_has_no_differences():
    assert diff(_vm(), _vm()) == []
    assert summarise([]) == "no differences"


def test_a_field_the_file_does_not_state_is_not_drift():
    """The decision the command's usefulness rests on: a default is not a request. A
    file that never mentions `bootable` would otherwise disagree with every VM whose
    first disk boots."""
    live = _vm()
    live.storage[0].bootable = True  # the hypervisor reports it; the file never said
    changes = diff(live, _vm(), stated=_paths("name", "cpu", "cpu.count"))
    assert changes == []


def test_the_same_field_is_drift_when_the_file_does_state_it():
    live = _vm()
    live.cpu.count = 8
    changes = diff(live, _vm(), stated=_paths("cpu", "cpu.count"))
    assert [c.path for c in changes] == ["cpu.count"]
    assert (changes[0].live, changes[0].desired) == (8, 2)
    assert changes[0].kind is ChangeKind.CHANGED


# ---------------------------------------------------------------------------
# What is not worth saying
# ---------------------------------------------------------------------------


def test_a_device_name_is_not_compared():
    """A file says `name: root`; VirtualBox has nowhere to store that, so its parser
    calls the same device `disk_SATA Controller_0_0`. Comparing names would report
    drift on every device of every VM."""
    live = _vm()
    live.storage[0].name = "disk_SATA Controller_0_0"
    assert diff(live, _vm(), stated=_paths("storage", "storage.name")) == []


def test_where_an_image_happens_to_live_is_not_compared():
    live = _vm()
    live.storage[0].disk_path = "/vms/web/root.vdi"
    assert diff(live, _vm(), stated=_paths("storage", "storage.disk_path")) == []


def test_a_generated_mac_is_not_compared():
    """The hypervisor's to choose, unless the file asked for one."""
    live = _vm()
    live.networks[0].mac_address = "080027AA0001"
    assert diff(live, _vm(), stated=_paths("networks", "networks.mac_address")) == []


def test_a_providers_native_hints_are_not_compared():
    """`metadata` carries things like a libvirt domain's UUID, which exist so that
    `edit` can redefine it -- not so that a diff can complain about them."""
    live = _vm()
    live.metadata = {"libvirt_uuid": "abc-123"}
    assert diff(live, _vm()) == []


def test_a_field_the_vm_cannot_report_is_not_drift():
    """QEMU records no device position, so a file asking for `slot: 0` is not in
    disagreement with a VM that has no answer to give."""
    live = _vm()
    live.storage[0].slot = None
    live.storage[0].unit = None
    assert diff(live, _vm(), stated=_paths("storage", "storage.slot")) == []


# ---------------------------------------------------------------------------
# Devices and adapters, where presence means something
# ---------------------------------------------------------------------------


def test_a_disk_the_file_does_not_have_is_reported():
    """The drift this command exists for."""
    live = _vm()
    live.storage.append(StorageDevice(name="extra", size_mb=4096, bus=BusType.SCSI))
    changes = diff(live, _vm())
    assert [(c.path, c.kind) for c in changes] == [("storage[scsi/0]", ChangeKind.REMOVED)]
    assert "4096 MB" in changes[0].render()


def test_a_disk_the_vm_does_not_have_is_reported():
    desired = _vm()
    desired.storage.append(StorageDevice(name="extra", size_mb=4096, bus=BusType.SCSI))
    changes = diff(_vm(), desired)
    assert [(c.path, c.kind) for c in changes] == [("storage[scsi/0]", ChangeKind.ADDED)]


def test_devices_are_matched_by_bus_and_order_not_by_raw_address():
    """A provider that assigns addresses itself reports none, so matching on the raw
    address made one device look like one added and one removed."""
    live = _vm()
    live.storage[0].slot = None  # QEMU-style: no recorded position
    desired = _vm()  # file-style: slot 0
    assert [c for c in diff(live, desired) if c.kind is not ChangeKind.CHANGED] == []


def test_an_adapter_the_file_does_not_have_is_reported():
    live = _vm()
    live.networks.append(NetworkConfig(model=NicModel.VIRTIO))
    changes = diff(live, _vm())
    assert [(c.path, c.kind) for c in changes] == [("networks[1]", ChangeKind.REMOVED)]
    assert "virtio" in changes[0].render()


def test_a_changed_adapter_model_is_reported():
    live = _vm()
    live.networks[0].model = NicModel.VIRTIO
    changes = diff(live, _vm(), stated=_paths("networks", "networks.model"))
    assert [c.path for c in changes] == ["networks[0].model"]


# ---------------------------------------------------------------------------
# Nested settings
# ---------------------------------------------------------------------------


def test_a_nested_setting_is_named_not_dumped():
    """Without descending into the mapping, one changed boot slot printed both whole
    `boot` dictionaries and left the reader to find it."""
    live = _vm()
    live.boot.ioapic = True
    changes = diff(live, _vm(), stated=_paths("boot", "boot.ioapic"))
    assert [c.path for c in changes] == ["boot.ioapic"]
    assert changes[0].render() == "~ boot.ioapic  vm true  file false"


def test_a_changed_disk_size_is_reported_with_its_address():
    live = _vm()
    live.storage[0].size_mb = 10240
    changes = diff(live, _vm(), stated=_paths("storage", "storage.size_mb"))
    assert [c.path for c in changes] == ["storage[sata/0].size_mb"]


# ---------------------------------------------------------------------------
# Which paths a file states
# ---------------------------------------------------------------------------


def test_stated_paths_reads_what_the_file_contains():
    found = stated_paths(
        {"name": "web", "cpu": {"count": 4}, "storage": [{"name": "root", "size_mb": 100}]}
    )
    assert "cpu.count" in found
    assert "storage.size_mb" in found
    assert "cpu.execution_cap" not in found


def test_stated_paths_drops_list_indices():
    """What matters is whether the file talks about a field, not about which item."""
    found = stated_paths({"storage": [{"size_mb": 1}, {"bus": "sata"}]})
    assert found >= {"storage", "storage.size_mb", "storage.bus"}
    assert not any("[" in path for path in found)


def test_stated_paths_understands_1_1_x_names():
    """So an older file's `disks:` and `type:` line up with today's fields."""
    found = stated_paths({"disks": [{"type": "ssd", "controller": "sata", "port": 0}]})
    assert found >= {"storage", "storage.kind", "storage.slot"}
    # `controller` meant the bus in 1.1.x and means the controller now, and only the
    # value tells them apart -- so a file that says it is taken to mean both.
    assert {"storage.bus", "storage.controller"} <= found


def test_the_summary_counts_each_kind():
    live = _vm()
    live.cpu.count = 8
    live.storage.append(StorageDevice(name="extra", size_mb=1, bus=BusType.SCSI))
    changes = diff(live, _vm(), stated=_paths("cpu", "cpu.count"))
    assert summarise(changes) == "1 changed, 1 removed"


# ---------------------------------------------------------------------------
# It is the round-trip check, too
# ---------------------------------------------------------------------------


def test_a_faithful_round_trip_shows_nothing(vbox_capture, parser):
    """The plan calls this the fastest way for a user to see that a round trip was
    faithful: parse a real VM, serialise it, load it back, and the diff should be
    empty."""
    from vmctl.serializers.yaml_serializer import YAMLSerializer

    label, raw, probe = vbox_capture
    live = parser.parse_text(label, raw, probe=probe)
    serializer = YAMLSerializer()
    reloaded = VMConfig.from_dict(serializer.to_dict(live))
    assert diff(live, reloaded, stated=stated_paths(serializer.to_dict(live))) == []
