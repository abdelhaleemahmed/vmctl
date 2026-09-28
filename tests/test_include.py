"""Configs built out of several files (E-11).

A lab is mostly one machine repeated, and the fields that matter get lost among the
ones that are only in a file because YAML has no inheritance. ``extends:`` is the fix;
these tests pin the merge rules, which are deliberately the same rules ``apply`` uses
against a live VM -- a mapping merges, a list is stated whole.
"""

import json

import pytest

from vmctl.core.exceptions import SerializationError
from vmctl.core.include import bases_of, load_mapping, merge, resolve


def _write(tmp_path, name, text):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


# ---------------------------------------------------------------------------
# Merging
# ---------------------------------------------------------------------------


def test_a_mapping_merges_and_the_child_wins():
    assert merge({"memory": {"mb": 2048, "vram_mb": 16}}, {"memory": {"mb": 4096}}) == {
        "memory": {"mb": 4096, "vram_mb": 16}
    }


def test_a_list_is_replaced_not_appended():
    """A list has no key to merge on, and a file that writes `storage:` is stating all
    of it -- the same rule `apply` uses against a live VM (E-02)."""
    assert merge({"storage": [{"name": "a"}, {"name": "b"}]}, {"storage": [{"name": "c"}]}) == {
        "storage": [{"name": "c"}]
    }


def test_merging_modifies_neither_side():
    base = {"cpu": {"count": 2}}
    override = {"cpu": {"count": 4}}
    merge(base, override)
    assert base == {"cpu": {"count": 2}} and override == {"cpu": {"count": 4}}


# ---------------------------------------------------------------------------
# Resolving a file
# ---------------------------------------------------------------------------


def test_a_child_inherits_what_it_does_not_say(tmp_path):
    _write(tmp_path, "base.yaml", "guest_os: ubuntu22.04\ncpu:\n  count: 2\nmemory:\n  mb: 2048\n")
    child = _write(tmp_path, "web.yaml", "extends: base.yaml\nname: web\nmemory:\n  mb: 4096\n")

    data = resolve(child)

    assert data["name"] == "web"
    assert data["guest_os"] == "ubuntu22.04"
    assert data["cpu"] == {"count": 2}
    assert data["memory"] == {"mb": 4096}
    assert "extends" not in data


def test_a_base_is_found_next_to_the_file_that_names_it(tmp_path, monkeypatch):
    """Relative to the file, not to the working directory: a directory of configs has
    to keep working wherever it is checked out."""
    _write(tmp_path, "lab/base.yaml", "cpu:\n  count: 8\n")
    child = _write(tmp_path, "lab/web.yaml", "extends: base.yaml\nname: web\n")
    monkeypatch.chdir(tmp_path.parent)

    assert resolve(child)["cpu"] == {"count": 8}


def test_a_chain_of_three_resolves_in_order(tmp_path):
    _write(tmp_path, "a.yaml", "cpu:\n  count: 1\nmemory:\n  mb: 512\n")
    _write(tmp_path, "b.yaml", "extends: a.yaml\ncpu:\n  count: 2\n")
    child = _write(tmp_path, "c.yaml", "extends: b.yaml\nname: c\nmemory:\n  mb: 1024\n")

    data = resolve(child)

    assert (data["cpu"], data["memory"], data["name"]) == ({"count": 2}, {"mb": 1024}, "c")


def test_several_bases_are_applied_left_to_right(tmp_path):
    """The last word wins, which is the same direction as the child winning."""
    _write(tmp_path, "one.yaml", "cpu:\n  count: 1\nmemory:\n  mb: 512\n")
    _write(tmp_path, "two.yaml", "cpu:\n  count: 4\n")
    child = _write(tmp_path, "vm.yaml", "extends: [one.yaml, two.yaml]\nname: vm\n")

    data = resolve(child)

    assert data["cpu"] == {"count": 4}
    assert data["memory"] == {"mb": 512}


def test_a_yaml_file_can_extend_a_json_base(tmp_path):
    _write(tmp_path, "base.json", json.dumps({"cpu": {"count": 3}}))
    child = _write(tmp_path, "vm.yaml", "extends: base.json\nname: vm\n")

    assert resolve(child)["cpu"] == {"count": 3}


def test_a_file_with_no_extends_is_returned_as_it_is(tmp_path):
    path = _write(tmp_path, "vm.yaml", "name: vm\ncpu:\n  count: 1\n")
    assert resolve(path) == {"name": "vm", "cpu": {"count": 1}}


def test_an_empty_file_is_an_empty_mapping(tmp_path):
    assert resolve(_write(tmp_path, "empty.yaml", "")) == {}


# ---------------------------------------------------------------------------
# Saying what is wrong
# ---------------------------------------------------------------------------


def test_a_base_that_does_not_exist_says_where_it_looked(tmp_path):
    child = _write(tmp_path, "vm.yaml", "extends: missing.yaml\nname: vm\n")

    with pytest.raises(SerializationError) as raised:
        resolve(child)

    assert "missing.yaml" in str(raised.value)
    assert "looked for" in (raised.value.recovery_hint or "")


def test_a_cycle_is_reported_rather_than_looped(tmp_path):
    _write(tmp_path, "a.yaml", "extends: b.yaml\n")
    _write(tmp_path, "b.yaml", "extends: a.yaml\n")

    with pytest.raises(SerializationError, match="extends itself"):
        resolve(tmp_path / "a.yaml")


def test_a_file_extending_itself_is_reported(tmp_path):
    path = _write(tmp_path, "self.yaml", "extends: self.yaml\n")
    with pytest.raises(SerializationError, match="extends itself"):
        resolve(path)


def test_extends_must_name_files(tmp_path):
    path = _write(tmp_path, "vm.yaml", "extends:\n  base: yes\n")
    with pytest.raises(SerializationError, match="must be a file name"):
        resolve(path)


def test_a_top_level_that_is_not_a_mapping_is_reported(tmp_path):
    path = _write(tmp_path, "list.yaml", "- one\n- two\n")
    with pytest.raises(SerializationError, match="does not describe a VM"):
        load_mapping(path)


def test_unparseable_yaml_names_the_file(tmp_path):
    path = _write(tmp_path, "bad.yaml", "name: [unclosed\n")
    with pytest.raises(SerializationError, match="cannot parse"):
        load_mapping(path)


def test_bases_are_reported_without_loading_them(tmp_path):
    path = _write(tmp_path, "vm.yaml", "extends: [a.yaml, b.yaml]\nname: vm\n")
    assert bases_of(path) == ["a.yaml", "b.yaml"]
    assert bases_of(_write(tmp_path, "plain.yaml", "name: x\n")) == []


# ---------------------------------------------------------------------------
# The rest of vmctl sees the merged file
# ---------------------------------------------------------------------------


def test_the_serializer_loads_an_extended_config(tmp_path):
    from vmctl.serializers.yaml_serializer import YAMLSerializer

    _write(tmp_path, "base.yaml", "cpu:\n  count: 4\nmemory:\n  mb: 2048\n")
    child = _write(tmp_path, "vm.yaml", "extends: base.yaml\nname: vm\n")

    vm = YAMLSerializer().load(child)

    assert vm.name == "vm" and vm.cpu.count == 4 and vm.memory.mb == 2048


def test_an_inherited_field_counts_as_stated(tmp_path):
    """The point of resolving at the *mapping* level: `diff` and `apply` decide what a
    file asks for from what it states, so a field inherited from a base has to be part
    of that -- otherwise a base could never change anything (E-01, E-02)."""
    from vmctl.core.diff import stated_paths

    _write(tmp_path, "base.yaml", "memory:\n  mb: 2048\n")
    child = _write(tmp_path, "vm.yaml", "extends: base.yaml\nname: vm\n")

    stated = stated_paths(resolve(child))

    assert "memory.mb" in stated
