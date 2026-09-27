"""Serializer tests — file in, file out, no surprises."""

import json

import pytest
import yaml

from vmctl.core.exceptions import SerializationError, ValidationError
from vmctl.serializers.json_serializer import JSONSerializer
from vmctl.serializers.yaml_serializer import YAMLSerializer

from conftest import parse_label


@pytest.fixture(params=["yaml", "json"])
def serializer(request):
    return YAMLSerializer() if request.param == "yaml" else JSONSerializer()


def test_save_then_load_is_stable(serializer, tmp_path, vm_full):
    path = tmp_path / ("cfg.yaml" if isinstance(serializer, YAMLSerializer) else "cfg.json")
    serializer.save(vm_full, path)
    loaded = serializer.load(path)
    assert serializer.to_dict(loaded) == serializer.to_dict(vm_full)


def test_saved_yaml_is_plain_data(tmp_path):
    """No python-specific tags: the file must be readable by any YAML parser."""
    vm = parse_label("multidisk")
    path = tmp_path / "cfg.yaml"
    YAMLSerializer().save(vm, path)
    text = path.read_text()
    assert "!!python" not in text
    assert yaml.safe_load(text)["name"] == vm.name


def test_saved_json_is_plain_data(tmp_path):
    vm = parse_label("multidisk")
    path = tmp_path / "cfg.json"
    JSONSerializer().save(vm, path)
    assert json.loads(path.read_text())["name"] == vm.name


def test_enums_are_serialised_as_strings(vm_full):
    data = YAMLSerializer().to_dict(vm_full)
    assert data["firmware"]["type"] == "efi64"
    assert data["disks"][0]["type"] == "disk"
    assert data["disks"][0]["format"] == "vdi"
    assert data["disks"][0]["variant"] == "thin"
    assert data["disks"][0]["controller"] == "sata"
    assert data["networks"][0]["network_type"] == "nat"
    assert data["storage_controllers"][0]["controller_type"] == "sata"


def test_missing_file_raises_serialization_error(tmp_path):
    with pytest.raises(SerializationError):
        YAMLSerializer().load(tmp_path / "nope.yaml")


def test_empty_file_is_reported_as_a_config_error(tmp_path):
    """F-06 - used to raise TypeError: argument of type 'NoneType'."""
    path = tmp_path / "empty.yaml"
    path.write_text("")
    with pytest.raises(ValidationError, match="empty"):
        YAMLSerializer().load(path)


def test_unknown_key_is_reported_as_a_config_error(tmp_path):
    """F-06 - used to raise TypeError from the VMConfig constructor."""
    path = tmp_path / "bad.yaml"
    path.write_text("name: v\nunknown_field: 5\n")
    with pytest.raises(ValidationError) as excinfo:
        YAMLSerializer().load(path)
    assert "unknown_field" in str(excinfo.value)


def test_bad_enum_value_names_the_field(tmp_path):
    """F-06 - used to raise a bare enum ValueError with no field context."""
    path = tmp_path / "bad.yaml"
    path.write_text("name: v\ndisks:\n  - name: d\n    controller: fibrechannel\n")
    with pytest.raises(ValidationError) as excinfo:
        YAMLSerializer().load(path)
    assert "disks[0].controller" in str(excinfo.value)
    # nvme is now a real bus, so it must load rather than fail
    path.write_text("name: v\ndisks:\n  - name: d\n    controller: nvme\n")
    YAMLSerializer().load(path)
