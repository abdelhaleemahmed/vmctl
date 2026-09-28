"""VMConfig round-trips through dict + YAML, and the validator runs.

Kept from the 2.0.0 tree and adapted where this version deliberately differs: a saved
config writes ``storage:`` rather than ``disks:`` (M-02 -- the list was never only
disks, and ``disks:`` remains accepted as input forever), and the validator is
constructed with a typed :class:`~vmctl.core.capabilities.Capabilities` rather than a
bare dict (A-02), because a matrix of empty dicts cannot answer "can this bus carry a
CD-ROM".
"""

from vmctl.core.vmconfig import VMConfig
from vmctl.providers.virtualbox.capabilities import VirtualBoxCapabilities
from vmctl.serializers.yaml_serializer import YAMLSerializer
from vmctl.validators.vm_validator import VMValidator


def _sample(name="web-01"):
    # from_dict is the real load path and fills sensible defaults. `ostype:` is the
    # 1.1.x spelling of `guest_os:`, and still accepted.
    return VMConfig.from_dict({"name": name, "ostype": "Ubuntu_64"})


def test_vmconfig_dict_roundtrip():
    vm = _sample("web-01")
    data = vm.to_dict()
    assert data["name"] == "web-01"
    assert {"cpu", "memory", "storage"} <= set(data)
    vm2 = VMConfig.from_dict(data)
    assert vm2.name == vm.name
    assert vm2.ostype == vm.ostype


def test_the_older_key_is_still_accepted_as_input():
    """`disks:` in, `storage:` out -- the promise the compatibility table makes."""
    vm = VMConfig.from_dict({"name": "old-file", "disks": [{"name": "system", "size_mb": 1024}]})

    assert vm.storage[0].name == "system"
    assert "disks" not in vm.to_dict()


def test_yaml_serializer_roundtrip():
    vm = _sample("db-01")
    ser = YAMLSerializer()
    text = ser.to_string(vm)
    assert "db-01" in text
    vm2 = ser.from_dict(ser.to_dict(vm))
    assert vm2.name == "db-01"


def test_validator_returns_list():
    warnings = VMValidator(VirtualBoxCapabilities.get()).validate(_sample("ok-vm"))
    assert isinstance(warnings, list)
