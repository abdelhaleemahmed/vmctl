"""Command-line overrides, merged at the mapping layer (E-20).

The point of this layer is that nothing below it changes: an overridden field is
validated, translated and emitted exactly as a written one. So these tests are about
the *merge* and its precedence, and the provider-facing consequences are covered by
the ordinary emitter and validator tests, which cannot tell the difference.
"""

import pytest

from vmctl.core import overrides
from vmctl.core.exceptions import ValidationError


def _config():
    return {
        "name": "demo",
        "memory": {"mb": 128, "vram_mb": 16},
        "storage": [
            {"name": "system", "size_mb": 64, "format": "qcow2"},
            {"name": "dvd", "kind": "cdrom"},
        ],
    }


# ---------------------------------------------------------------------------
# --set
# ---------------------------------------------------------------------------


def test_a_value_has_the_type_it_is_written_as():
    """The shell hands over a string, and the model rejects "4096" for an int field.
    YAML's own scalar rules decide, so a value means on the command line what it
    would mean in the file."""
    merged, _ = overrides.apply(_config(), sets=["memory.mb=4096", "name=other"])

    assert merged["memory"]["mb"] == 4096
    assert merged["name"] == "other"


def test_a_field_the_file_never_mentioned_is_created():
    merged, _ = overrides.apply({"name": "demo"}, sets=["memory.mb=2048"])

    assert merged["memory"] == {"mb": 2048}


def test_setting_every_element_at_once():
    merged, applied = overrides.apply(_config(), sets=["storage[*].size_mb=100"])

    assert [device["size_mb"] for device in merged["storage"]] == [100, 100]
    assert [record.path for record in applied] == [
        "storage[0].size_mb",
        "storage[1].size_mb",
    ]


def test_a_device_that_does_not_exist_is_refused_rather_than_invented():
    """A third disk conjured up to satisfy `storage[2]` would hide the typo."""
    with pytest.raises(ValidationError) as caught:
        overrides.apply(_config(), sets=["storage[7].size_mb=1"])

    assert "does not exist" in str(caught.value)
    assert "2" in str(caught.value)  # says how many there are


@pytest.mark.parametrize("text", ["memory.mb", "=4096", "memory..mb=1", "9bad=1"])
def test_malformed_set_is_reported_not_guessed(text):
    with pytest.raises(ValidationError):
        overrides.apply(_config(), sets=[text])


def test_the_callers_mapping_is_not_modified():
    original = _config()
    overrides.apply(original, sets=["memory.mb=9999"], disks=["name=d,size_mb=16"])

    assert original["memory"]["mb"] == 128
    assert len(original["storage"]) == 2


# ---------------------------------------------------------------------------
# --disk-format, as a layer rather than a special case
# ---------------------------------------------------------------------------


def test_the_blanket_leaves_removable_devices_alone():
    """A DVD drive holds a medium that already exists, so it has no format to choose."""
    merged, _ = overrides.apply(_config(), disk_format="raw")

    assert merged["storage"][0]["format"] == "raw"
    assert "format" not in merged["storage"][1]


def test_a_per_device_format_outranks_the_blanket_and_the_pair_is_reportable():
    """The case that had no answer before: `--disk-format vdi --add-disk format=qcow2`.
    The blanket reaches the added disk too -- it is a disk -- and the device's own word
    replaces it, so the winner falls out of the ordering rather than from precedence
    code in an emitter, and `outranked` has the pair to show."""
    merged, applied = overrides.apply(
        _config(), disk_format="vdi", disks=["name=data,size_mb=32,format=qcow2"]
    )

    assert merged["storage"][0]["format"] == "vdi"  # from the file, blanketed
    assert merged["storage"][2]["format"] == "qcow2"  # said so itself

    pairs = overrides.outranked(applied)
    assert len(pairs) == 1
    earlier, later = pairs[0]
    assert (earlier.source, earlier.value) == ("--disk-format", "vdi")
    assert (later.source, later.value) == ("--add-disk", "qcow2")
    assert later.path == "storage[2].format"


def test_set_is_the_last_word():
    merged, _ = overrides.apply(_config(), disk_format="vdi", sets=["storage[0].format=raw"])

    assert merged["storage"][0]["format"] == "raw"


# ---------------------------------------------------------------------------
# --add-disk / --add-nic
# ---------------------------------------------------------------------------


def test_a_disk_is_appended_with_what_it_states():
    merged, _ = overrides.apply(
        _config(), disks=["name=data,size_mb=40960,bus=virtio-blk,format=qcow2"]
    )

    assert merged["storage"][2] == {
        "name": "data",
        "size_mb": 40960,
        "bus": "virtio-blk",
        "format": "qcow2",
    }


def test_a_nic_is_appended_to_a_file_that_had_none():
    merged, _ = overrides.apply({"name": "demo"}, nics=["network_type=bridged,model=virtio"])

    assert merged["networks"] == [{"network_type": "bridged", "model": "virtio"}]


def test_an_added_cdrom_is_not_given_a_format_by_the_blanket():
    """`kind` is placed before the blanket runs, so it can tell a drive from a disk."""
    merged, _ = overrides.apply(_config(), disk_format="raw", disks=["kind=cdrom,name=d2"])

    assert "format" not in merged["storage"][2]


@pytest.mark.parametrize("text", ["size_mb", "", "size_mb=1,size_mb=2", "=1"])
def test_malformed_add_disk_is_reported(text):
    with pytest.raises(ValidationError):
        overrides.apply(_config(), disks=[text])


# ---------------------------------------------------------------------------
# --patch
# ---------------------------------------------------------------------------


def test_a_patch_merges_mappings_and_replaces_lists():
    """The `extends:` rule, because it is the same merge -- there is one walk."""
    merged, _ = overrides.apply(
        _config(), patches=[{"memory": {"mb": 512}, "storage": [{"name": "only", "size_mb": 8}]}]
    )

    assert merged["memory"] == {"mb": 512, "vram_mb": 16}  # merged, vram kept
    assert merged["storage"] == [{"name": "only", "size_mb": 8}]  # replaced


def test_a_patch_says_what_it_changed():
    """A fragment that quietly halved the memory is the silent disagreement this
    ordering exists to refuse, so a patch reports like every other layer."""
    _, applied = overrides.apply(_config(), patches=[{"memory": {"mb": 512}}])

    assert [(r.path, r.value, r.replaced, r.source) for r in applied] == [
        ("memory.mb", 512, 128, "--patch")
    ]


def test_a_record_is_a_snapshot_not_a_live_reference():
    """`--patch` puts a list in and `--add-disk` then appends to it; a record holding
    the reference would describe a state that never existed when it was made."""
    _, applied = overrides.apply(
        _config(),
        patches=[{"storage": [{"name": "one", "size_mb": 16}]}],
        disks=["name=two,size_mb=16"],
    )

    patched = next(r for r in applied if r.path == "storage")
    assert len(patched.value) == 1
    assert str(patched) == "storage = 1 item (from --patch, was 2 items)"


def test_the_callers_patch_is_not_modified():
    patch = {"storage": [{"name": "one", "size_mb": 16}]}
    overrides.apply(_config(), patches=[patch], disks=["name=two,size_mb=16"])

    assert len(patch["storage"]) == 1


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------


def test_restating_a_value_is_recorded_but_marked_unchanged():
    _, applied = overrides.apply(_config(), sets=["memory.mb=128"])

    assert applied[0].changed is False
    assert "unchanged" in str(applied[0])
    assert overrides.outranked(applied) == []
