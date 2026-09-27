"""The JSON Schema for a config file (E-06).

Generated from the dataclasses, because a hand-written schema is a second description
of the model and a second description drifts -- the same argument that produced the
field tables (A-11) and the capability declarations (M-03).

These tests are mostly one assertion in different clothes: **the schema must accept
exactly what vmctl accepts**. A schema that rejects a working file is worse than no
schema, and one that accepts a broken file teaches people to ignore their editor.
"""

import json
from pathlib import Path

import pytest
import yaml

from vmctl.core.schema import build

CONFIGS = Path(__file__).parent / "fixtures" / "configs"
EXAMPLES = Path(__file__).parent.parent / "examples"
COMMITTED = Path(__file__).parent.parent / "vmctl.schema.json"

jsonschema = pytest.importorskip("jsonschema", reason="jsonschema is a dev dependency")


@pytest.fixture(scope="module")
def validator():
    schema = build()
    jsonschema.Draft202012Validator.check_schema(schema)
    return jsonschema.Draft202012Validator(schema)


def _errors(validator, data):
    return [error.message for error in validator.iter_errors(data)]


# ---------------------------------------------------------------------------
# It accepts what vmctl accepts
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    sorted(EXAMPLES.glob("*.yaml"))
    + sorted(CONFIGS.glob("*.yaml"))
    + sorted(CONFIGS.glob("*.json")),
    ids=lambda p: p.name,
)
def test_every_config_in_this_repository_validates(validator, path):
    """Including the 1.1.9 fixtures. A schema that refused those would be describing a
    tool that does not exist."""
    assert _errors(validator, yaml.safe_load(path.read_text())) == []


def test_a_1_1_x_chipset_name_is_offered_not_merely_tolerated(validator):
    """`adapter_type: "82540EM"` is what a 1.1.9 export really says, in that case."""
    assert _errors(validator, {"name": "v", "networks": [{"adapter_type": "82540EM"}]}) == []
    schema = build()
    spellings = schema["$defs"]["NetworkConfig"]["properties"]["adapter_type"]["enum"]
    assert "82540EM" in spellings, "an editor should suggest the spelling people wrote"


def test_a_minimal_config_validates(validator):
    """Only the name is required: a hand-written config omits almost everything."""
    assert _errors(validator, {"name": "minimal"}) == []


def test_a_batch_file_validates(validator):
    """Two kinds of file live side by side in a directory, so the schema covers both."""
    assert (
        _errors(
            validator,
            {
                "name": "lab",
                "base_vm": {"name": "base", "cpu": {"count": 2}},
                "instances": [{"name": "one", "memory": 4096}, {"name": "two", "cpu": 4}],
            },
        )
        == []
    )


def test_a_batch_instance_may_use_the_bare_number_shorthand(validator):
    """`memory: 4096` rather than `memory: {mb: 4096}` is what makes a batch file
    short enough to be worth writing, and it lives in the creator rather than the
    model -- so the schema states it."""
    assert (
        _errors(
            validator,
            {"base_vm": "template-vm", "instances": [{"name": "one", "cpu": 8, "memory": 2048}]},
        )
        == []
    )


# ---------------------------------------------------------------------------
# And rejects what vmctl rejects
# ---------------------------------------------------------------------------


def test_a_config_with_no_name_is_rejected(validator):
    assert _errors(validator, {"cpu": {"count": 2}})


def test_a_misspelled_field_is_rejected(validator):
    """The loader says "Unknown field 'nmae'. Did you mean 'name'?"; the editor should
    not wait until then."""
    assert _errors(validator, {"name": "v", "storage": [{"nmae": "root"}]})


def test_a_value_of_the_wrong_type_is_rejected(validator):
    assert _errors(validator, {"name": "v", "cpu": {"count": "four"}})


def test_a_device_kind_that_does_not_exist_is_rejected(validator):
    assert _errors(validator, {"name": "v", "storage": [{"name": "d", "kind": "tape"}]})


def test_a_batch_instance_with_no_name_is_rejected(validator):
    """The creator refuses it too -- instances without names collide silently."""
    assert _errors(validator, {"base_vm": "x", "instances": [{"memory": 1024}]})


# ---------------------------------------------------------------------------
# It comes from the model
# ---------------------------------------------------------------------------


def test_the_enums_come_from_the_enums():
    """Not from a literal that has to be remembered when a member is added."""
    from vmctl.core.devices import BusType, DeviceKind

    schema = build()
    device = schema["$defs"]["StorageDevice"]["properties"]
    assert set(device["bus"]["enum"]) == {bus.value for bus in BusType}
    assert {kind.value for kind in DeviceKind} <= set(device["kind"]["enum"])


def test_field_descriptions_come_from_the_docstrings():
    """The Attributes: sections already explain every field; asking for the same
    sentence twice is how the two come to disagree."""
    schema = build()
    assert "virtual CPUs" in schema["$defs"]["CPUConfig"]["properties"]["count"]["description"]


def test_every_model_field_is_in_the_schema():
    """The property that makes generation worth it: add a field to the model and it is
    here, with no edit."""
    from dataclasses import fields

    from vmctl.core.vmconfig import StorageDevice, VMConfig

    schema = build()
    root = set(schema["$defs"]["VMConfig"]["properties"])
    assert {f.name for f in fields(VMConfig)} <= root
    device = set(schema["$defs"]["StorageDevice"]["properties"])
    assert {f.name for f in fields(StorageDevice)} <= device


# ---------------------------------------------------------------------------
# The committed copy
# ---------------------------------------------------------------------------


def test_the_committed_schema_is_current():
    """Otherwise a user's editor validates against a model vmctl no longer has. CI
    runs this, so the answer to a drifted file is `vmctl schema -o vmctl.schema.json`.
    """
    expected = json.dumps(build(), indent=2, sort_keys=True) + "\n"
    assert COMMITTED.exists(), "run: vmctl schema -o vmctl.schema.json"
    assert COMMITTED.read_text() == expected, "run: vmctl schema -o vmctl.schema.json"
