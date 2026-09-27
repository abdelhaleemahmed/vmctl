"""Tests for the shared field-table engine (A-11).

The point of the table is that a setting is declared once and works in both
directions. The property test at the end is what enforces that: it holds for
every field in every provider's table, so a new field cannot be added in a way
that reads but does not write, or vice versa.
"""

import pytest

from vmctl.core.codecs import EnumCodec, Int, OnOff
from vmctl.core.mapping import (
    Field,
    changed_flags,
    check_table,
    emit_flags,
    get_path,
    read_into,
    set_path,
)
from vmctl.core.vmconfig import FirmwareType, VMConfig
from vmctl.providers.virtualbox.tables import FIELDS, MODIFIABLE

from conftest import VM_LABELS, parse_label


# ---------------------------------------------------------------------------
# Codecs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw", ["on", "ON", "true", "True", "yes", "1", "enabled"])
def test_onoff_accepts_every_true_spelling(raw):
    assert OnOff().load(raw) is True


@pytest.mark.parametrize("raw", ["off", "OFF", "false", "no", "0", "disabled", "none"])
def test_onoff_accepts_every_false_spelling(raw):
    assert OnOff().load(raw) is False


def test_onoff_rejects_nonsense():
    with pytest.raises(ValueError):
        OnOff().load("maybe")


def test_onoff_round_trips():
    codec = OnOff()
    for value in (True, False):
        assert codec.load(codec.dump(value)) is value


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("BIOS", FirmwareType.BIOS),
        ("EFI", FirmwareType.EFI),
        ("EFI32", FirmwareType.EFI32),
        ("efi64", FirmwareType.EFI64),
    ],
)
def test_enum_codec_is_case_insensitive(raw, expected):
    """F-02 came from deciding case per field; the codec decides it once."""
    from vmctl.providers.virtualbox.tables import FIRMWARE

    assert EnumCodec(FirmwareType, FIRMWARE).load(raw) is expected


def test_enum_codec_reports_the_accepted_values():
    from vmctl.providers.virtualbox.tables import FIRMWARE

    codec = EnumCodec(FirmwareType, FIRMWARE)
    with pytest.raises(ValueError, match="bios, efi, efi32, efi64"):
        codec.load("uefi")


def test_int_codec_enforces_bounds():
    assert Int(1, 100).load("90") == 90
    with pytest.raises(ValueError):
        Int(1, 100).load("0")
    with pytest.raises(ValueError):
        Int(1, 100).load("101")


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def test_dotted_paths_read_and_write(vm_minimal):
    assert get_path(vm_minimal, "memory.mb") == vm_minimal.memory.mb
    set_path(vm_minimal, "memory.vram_mb", 64)
    assert vm_minimal.memory.vram_mb == 64


# ---------------------------------------------------------------------------
# The table itself
# ---------------------------------------------------------------------------


def test_virtualbox_table_is_internally_consistent():
    """A table that names a setting or a flag twice is a declaration bug."""
    assert check_table(FIELDS) == []


def test_check_table_catches_a_duplicated_path():
    bad = (
        Field("cpu.count", "cpus", Int(), "--cpus"),
        Field("cpu.count", "ncpu", Int(), "--numcpu"),
    )
    assert any("repeats path" in p for p in check_table(bad))


def test_check_table_catches_a_duplicated_flag():
    bad = (
        Field("cpu.count", "cpus", Int(), "--cpus"),
        Field("memory.mb", "memory", Int(), "--cpus"),
    )
    assert any("repeats flag" in p for p in check_table(bad))


def test_check_table_catches_a_field_that_does_nothing():
    assert any("neither" in p for p in check_table((Field("cpu.count", "", Int()),)))


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def test_read_into_leaves_absent_keys_alone(vm_minimal):
    before = vm_minimal.memory.mb
    read_into(vm_minimal, {"cpus": "4"}, FIELDS)
    assert vm_minimal.cpu.count == 4
    assert vm_minimal.memory.mb == before


def test_read_into_reports_a_value_it_cannot_parse(vm_minimal):
    problems = []
    read_into(vm_minimal, {"cpus": "lots"}, FIELDS, on_warning=problems.append)
    assert problems and "cpus" in problems[0]
    assert vm_minimal.cpu.count == 1  # left alone


@pytest.mark.parametrize(
    "native,path,expected",
    [
        ("hpet", "boot.hpet", True),
        ("pagefusion", "memory.page_fusion", True),
        ("cpuexecutioncap", "cpu.execution_cap", 90),
    ],
)
def test_fields_that_were_emitted_but_never_read(vm_minimal, native, path, expected):
    """F-23 - VirtualBox reports these, the emitter wrote them, nothing read them.

    Export then import silently reset each one. The table closes the gap by
    construction: one declaration serves both directions.
    """
    raw = {"hpet": "on", "pagefusion": "on", "cpuexecutioncap": "90"}
    read_into(vm_minimal, {native: raw[native]}, FIELDS)
    assert get_path(vm_minimal, path) == expected


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def test_emit_flags_follows_table_order(vm_minimal):
    flags = emit_flags(vm_minimal, FIELDS)
    assert flags[:2] == ["--memory", str(vm_minimal.memory.mb)]
    assert "--firmware" in flags


def test_an_unrestricted_execution_cap_is_not_emitted(vm_minimal):
    vm_minimal.cpu.execution_cap = 100
    assert "--cpuexecutioncap" not in emit_flags(vm_minimal, FIELDS)
    vm_minimal.cpu.execution_cap = 75
    assert "--cpuexecutioncap" in emit_flags(vm_minimal, FIELDS)


def test_changed_flags_emits_only_differences(vm_minimal):
    import copy

    desired = copy.deepcopy(vm_minimal)
    assert changed_flags(vm_minimal, desired, MODIFIABLE) == []
    desired.memory.mb = 4096
    assert changed_flags(vm_minimal, desired, MODIFIABLE) == ["--memory", "4096"]


# ---------------------------------------------------------------------------
# The property that keeps the two directions honest
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "entry", [f for f in FIELDS if f.readable and f.writable], ids=lambda f: f.path
)
def test_every_field_round_trips_through_its_codec(entry, vm_minimal):
    """For every field: dump(load(x)) == x, and load(dump(v)) == v.

    One test covering the whole table. It is what would have caught F-02 (a
    firmware value that read as something else than it was written) and F-05/F-23
    (a field that moved in one direction only) on the day they were introduced.
    """
    value = get_path(vm_minimal, entry.path)
    if value is None:
        pytest.skip(f"{entry.path} has no default value to round-trip")
    native = entry.codec.dump(value)
    assert entry.codec.load(native) == value, f"{entry.path}: dump/load lost the value"


@pytest.mark.parametrize("label", VM_LABELS)
def test_reading_a_real_vm_then_writing_it_is_stable(label):
    """Parse a captured VM, emit its flags, read them back: nothing drifts."""
    vm = parse_label(label)
    flags = emit_flags(vm, FIELDS)

    # Turn the emitted flags back into native key/value pairs and re-read them.
    flag_to_field = {f.flag: f for f in FIELDS if f.writable}
    native = {}
    for flag, value in zip(flags[::2], flags[1::2]):
        native[flag_to_field[flag].native] = value

    again = VMConfig(
        name=vm.name,
        cpu=type(vm.cpu)(),
        memory=type(vm.memory)(),
        firmware=type(vm.firmware)(),
        disks=list(vm.disks),
        networks=list(vm.networks),
        boot=type(vm.boot)(order=list(vm.boot.order)),
        storage_controllers=list(vm.storage_controllers),
    )
    read_into(again, native, FIELDS)

    for entry in FIELDS:
        if not entry.writable:
            continue
        expected = get_path(vm, entry.path)
        if expected is None:
            continue
        if entry.emit_when is not None and not entry.emit_when(expected):
            continue
        assert get_path(again, entry.path) == expected, entry.path
