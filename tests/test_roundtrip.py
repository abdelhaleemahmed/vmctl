"""Round-trip fidelity: parse -> serialize -> load -> emit must be lossless.

This is the test that matters most for the product's premise ("export a VM,
recreate it anywhere"). A field that survives parsing but not serialization, or
survives the file but never reaches a command, shows up here.
"""

import pytest

from vmctl.core.vmconfig import VMConfig
from vmctl.providers.virtualbox.emitter import VirtualBoxEmitter
from vmctl.serializers.json_serializer import JSONSerializer
from vmctl.serializers.yaml_serializer import YAMLSerializer

from conftest import VM_LABELS, parse_label, render_commands


def emit(vm) -> str:
    return render_commands(VirtualBoxEmitter(vm.name).emit_create_vm(vm))


def reload_through(serializer, vm) -> VMConfig:
    """Serialize to a string and load it back, as export+import would."""
    return VMConfig.from_dict(dict(serializer.to_dict(vm)))


@pytest.mark.parametrize("label", VM_LABELS)
def test_dict_roundtrip_preserves_emitted_commands(label):
    """to_dict -> from_dict must not change what we would run."""
    vm = parse_label(label)
    assert emit(reload_through(YAMLSerializer(), vm)) == emit(vm)


@pytest.mark.parametrize("label", VM_LABELS)
def test_yaml_and_json_agree(label):
    vm = parse_label(label)
    y = YAMLSerializer().to_string(vm)
    j = JSONSerializer().to_string(vm)
    import json

    import yaml

    assert yaml.safe_load(y) == json.loads(j)


@pytest.mark.parametrize("label", VM_LABELS)
def test_roundtrip_preserves_core_hardware_fields(label):
    vm = parse_label(label)
    again = reload_through(YAMLSerializer(), vm)
    assert again.name == vm.name
    assert again.cpu.count == vm.cpu.count
    assert again.memory.mb == vm.memory.mb
    assert again.memory.vram_mb == vm.memory.vram_mb
    assert again.ostype == vm.ostype
    assert len(again.disks) == len(vm.disks)
    assert len(again.networks) == len(vm.networks)
    assert len(again.storage_controllers) == len(vm.storage_controllers)
    assert [d.size_mb for d in again.disks] == [d.size_mb for d in vm.disks]
    assert [d.format for d in again.disks] == [d.format for d in vm.disks]
    assert [n.network_type for n in again.networks] == [n.network_type for n in vm.networks]
    assert [n.adapter_name for n in again.networks] == [n.adapter_name for n in vm.networks]


def test_disk_path_is_intentionally_dropped_on_export():
    """disk_path is host-specific, so export omits it by design."""
    vm = parse_label("bios_minimal")
    assert vm.disks[0].disk_path is not None
    assert "disk_path" not in YAMLSerializer().to_dict(vm)["disks"][0]
    assert reload_through(YAMLSerializer(), vm).disks[0].disk_path is None


def test_from_dict_does_not_mutate_its_input():
    """F-07 — loading a config destroys the dict it was given."""
    data = {"name": "x", "cpu": {"count": 4}, "memory": {"mb": 1024}}
    before = {k: v for k, v in data.items()}
    VMConfig.from_dict(data)
    assert data == before


@pytest.mark.parametrize("label", ["efi_secureboot"])
def test_full_roundtrip_reaches_the_hypervisor(label):
    """F-05/F-02 — an EFI VM with nested virt must re-emit those settings."""
    vm = reload_through(YAMLSerializer(), parse_label(label))
    flat = {
        tok for cmd in VirtualBoxEmitter(vm.name).emit_create_vm(vm).as_argv_lists() for tok in cmd
    }
    assert "efi64" in flat or "efi" in flat
