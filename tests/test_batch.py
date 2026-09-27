"""Batch creation: resolve and check everything before creating anything."""

import pytest

from vmctl.core.batch import BatchCreator
from vmctl.core.exceptions import ValidationError

BASE = (
    "base_vm:\n"
    "  name: base\n"
    "  cpu: {count: 1}\n"
    "  memory: {mb: 512}\n"
    "  disks:\n    - name: system\n      size_mb: 1024\n"
    "  networks:\n    - network_type: nat\n"
)


class FakeEngine:
    """Engine stand-in: real validation, fixed VM list, no hypervisor."""

    def __init__(self, existing=()):
        self.existing = list(existing)
        from vmctl.providers.virtualbox.capabilities import VirtualBoxCapabilities
        from vmctl.validators.vm_validator import VMValidator

        self._validator = VMValidator(VirtualBoxCapabilities.get())

    def list_vms(self):
        return self.existing

    def validate_vm(self, vm):
        return self._validator.validate(vm)

    def read_vm(self, name):  # pragma: no cover - not used here
        raise AssertionError("should not read from the provider")


def write(tmp_path, body):
    path = tmp_path / "batch.yaml"
    path.write_text(body)
    return path


def test_instances_are_built_in_file_order(tmp_path):
    path = write(tmp_path, BASE + "instances:\n  - name: a\n  - name: b\n")
    vms = BatchCreator(FakeEngine()).create_from_file(path)
    assert [vm.name for vm in vms] == ["a", "b"]


def test_overrides_apply_per_instance(tmp_path):
    path = write(
        tmp_path,
        BASE + ("instances:\n" "  - name: a\n    memory: 2048\n    cpu: 4\n" "  - name: b\n"),
    )
    a, b = BatchCreator(FakeEngine()).create_from_file(path)
    assert (a.memory.mb, a.cpu.count) == (2048, 4)
    assert (b.memory.mb, b.cpu.count) == (512, 1)


def test_instance_without_a_name_is_rejected(tmp_path):
    """F-12 - nameless instances all inherited the base name and collided."""
    path = write(tmp_path, BASE + "instances:\n  - memory: 1024\n  - memory: 2048\n")
    with pytest.raises(ValidationError, match="has no 'name'"):
        BatchCreator(FakeEngine()).create_from_file(path)


def test_duplicate_instance_names_are_rejected(tmp_path):
    path = write(tmp_path, BASE + "instances:\n  - name: dup\n  - name: dup\n")
    with pytest.raises(ValidationError, match="repeats the name"):
        BatchCreator(FakeEngine()).create_from_file(path)


def test_empty_instance_list_is_rejected(tmp_path):
    path = write(tmp_path, BASE + "instances: []\n")
    with pytest.raises(ValidationError, match="no instances"):
        BatchCreator(FakeEngine()).create_from_file(path)


def test_non_mapping_instance_is_rejected(tmp_path):
    path = write(tmp_path, BASE + "instances:\n  - just-a-string\n")
    with pytest.raises(ValidationError, match="must be a mapping"):
        BatchCreator(FakeEngine()).create_from_file(path)


def test_a_bad_last_instance_stops_the_whole_batch(tmp_path):
    """The point of resolving everything first: no VM exists yet when this fails."""
    path = write(
        tmp_path, BASE + ("instances:\n  - name: good\n  - name: bad\n    memory: {mb: -5}\n")
    )
    with pytest.raises(ValidationError):
        BatchCreator(FakeEngine()).create_from_file(path)


def test_preflight_reports_names_that_already_exist(tmp_path):
    path = write(tmp_path, BASE + "instances:\n  - name: a\n  - name: b\n")
    creator = BatchCreator(FakeEngine(existing=["b", "unrelated"]))
    vms = creator.create_from_file(path)
    assert creator.preflight(vms) == ["b"]


def test_preflight_is_quiet_when_the_provider_cannot_be_reached(tmp_path):
    class Broken(FakeEngine):
        def list_vms(self):
            raise RuntimeError("no hypervisor")

    path = write(tmp_path, BASE + "instances:\n  - name: a\n")
    creator = BatchCreator(Broken())
    assert creator.preflight(creator.create_from_file(path)) == []


def test_warnings_are_reported_per_instance(tmp_path):
    seen = []

    class Warns(FakeEngine):
        def validate_vm(self, vm):
            return ["something to note"]  # override the real validator

    path = write(tmp_path, BASE + "instances:\n  - name: a\n  - name: b\n")
    BatchCreator(Warns(), on_warning=seen.append).create_from_file(path)
    assert seen == ["a: something to note", "b: something to note"]


def test_loading_the_batch_file_does_not_consume_the_base_definition(tmp_path):
    """F-07 - from_dict used to pop() keys out of the caller's dict."""
    path = write(tmp_path, BASE + "instances:\n  - name: a\n  - name: b\n")
    vms = BatchCreator(FakeEngine()).create_from_file(path)
    # Both instances must inherit the base disk; the second used to come up
    # empty because building the first emptied the shared mapping.
    assert all(len(vm.disks) == 1 for vm in vms)
    assert all(vm.disks[0].size_mb == 1024 for vm in vms)
