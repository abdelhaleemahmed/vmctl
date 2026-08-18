"""VMConfig round-trips through dict + YAML, and the validator runs."""
from vmctl.core.vmconfig import VMConfig
from vmctl.serializers.yaml_serializer import YAMLSerializer
from vmctl.validators.vm_validator import VMValidator


def _sample(name="web-01"):
    # from_dict is the real load path and fills sensible defaults.
    return VMConfig.from_dict({"name": name, "ostype": "Ubuntu_64"})


def test_vmconfig_dict_roundtrip():
    vm = _sample("web-01")
    data = vm.to_dict()
    assert data["name"] == "web-01"
    assert {"cpu", "memory", "disks"} <= set(data)
    vm2 = VMConfig.from_dict(data)
    assert vm2.name == vm.name
    assert vm2.ostype == vm.ostype


def test_yaml_serializer_roundtrip():
    vm = _sample("db-01")
    ser = YAMLSerializer()
    text = ser.to_string(vm)
    assert "db-01" in text
    vm2 = ser.from_dict(ser.to_dict(vm))
    assert vm2.name == "db-01"


def test_validator_returns_list():
    # VMValidator is constructed with the target provider's capabilities.
    errors = VMValidator(provider_capabilities={}).validate(_sample("ok-vm"))
    assert isinstance(errors, list)
