"""
A JSON Schema for the configuration format, generated from the model.

Config-as-code users expect their editor to autocomplete and validate the file they
are writing, and a schema is how that is asked for. The only honest way to publish one
is to derive it from the dataclasses (E-06 in PLAN.md): a hand-written schema is a
second description of the model, and a second description drifts -- which is the same
argument that produced the field tables (A-11) and the capability declarations (M-03).

So this walks the model with :mod:`dataclasses` and :mod:`typing`, exactly as the
loader does, and every enum's members come from the enum. A field added to
:class:`~vmctl.core.vmconfig.VMConfig` appears here on the next run with no edit at
all, and CI asserts the committed copy is current.

The 1.1.x names are included as accepted aliases, because a schema that rejected files
vmctl accepts would be worse than none.
"""

from dataclasses import MISSING, fields, is_dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Union, get_args, get_origin, get_type_hints

from .vmconfig import (
    LEGACY_CONTROLLER_FIELDS,
    LEGACY_NIC_MODELS,
    LEGACY_DEVICE_FIELDS,
    LEGACY_NETWORK_FIELDS,
    LEGACY_VM_FIELDS,
    VMConfig,
)

#: Where the schema says it lives, so an editor can reference it by URL.
SCHEMA_ID = "https://github.com/ahmedhal/vmctl/blob/main/vmctl.schema.json"
SCHEMA_VERSION = "https://json-schema.org/draft/2020-12/schema"

#: Old names accepted for each dataclass, by class name.
LEGACY_BY_CLASS = {
    "VMConfig": LEGACY_VM_FIELDS,
    "StorageDevice": LEGACY_DEVICE_FIELDS,
    "NetworkConfig": LEGACY_NETWORK_FIELDS,
    "StorageController": LEGACY_CONTROLLER_FIELDS,
}

#: Values 1.1.x wrote that are no longer the model's own, per field. Without these the
#: schema would reject files vmctl accepts -- a 1.1.9 export really does say
#: ``adapter_type: "82540EM"``, and the loader really does understand it.
LEGACY_VALUES = {
    "kind": ("hdd", "ssd", "dvd"),
    "model": tuple(LEGACY_NIC_MODELS),
}


def build() -> Dict[str, Any]:
    """Return the JSON Schema for a vmctl configuration file.

    Both kinds of file are described: one VM, or a batch definition whose ``base_vm``
    is one. An editor pointed at a directory of configs then validates both, which is
    what a user has in front of them.

    Returns:
        dict: a draft 2020-12 schema, ready to serialise.
    """
    definitions: Dict[str, Any] = {}
    vm = _for_dataclass(VMConfig, definitions)
    definitions["VMConfig"] = vm
    definitions["BatchDefinition"] = _batch_definition()
    return {
        "$schema": SCHEMA_VERSION,
        "$id": SCHEMA_ID,
        "title": "vmctl configuration",
        "description": (
            "A virtual machine, or a batch that creates several from one base -- in "
            "terms no hypervisor owns. Generated from vmctl's own model, so it cannot "
            "describe a file vmctl would reject, and it accepts the 1.1.x field names."
        ),
        "oneOf": [
            {"$ref": "#/$defs/VMConfig"},
            {"$ref": "#/$defs/BatchDefinition"},
        ],
        "$defs": definitions,
    }


def _batch_definition() -> Dict[str, Any]:
    """Return the schema for a batch file.

    Its instances are *overrides*, not VMs: each may restate any part of the base, and
    ``cpu``/``memory`` additionally accept a bare number, which is what makes a batch
    file short enough to be worth writing. That shorthand lives in
    :mod:`vmctl.core.batch` rather than in the model, so it is stated here rather than
    derived.
    """
    number_or_mapping = [{"type": "integer"}, {"type": "object"}]
    return {
        "type": "object",
        "description": "Several VMs from one base configuration.",
        "properties": {
            # A batch file names itself in the committed example and in the docs, and
            # the creator ignores it -- so the schema accepts it rather than making
            # every existing batch file fail validation.
            "name": {"type": "string", "description": "A name for the batch itself."},
            "description": {"type": "string"},
            "base_vm": {
                "description": (
                    "The configuration every instance starts from: inline, a path to a "
                    "config file, or the name of an existing VM to copy."
                ),
                "anyOf": [{"$ref": "#/$defs/VMConfig"}, {"type": "string"}],
            },
            "instances": {
                "type": "array",
                "minItems": 1,
                "description": "One entry per VM, each overriding part of the base.",
                "items": {
                    "type": "object",
                    "required": ["name"],
                    "properties": {
                        "name": {"type": "string"},
                        "cpu": {"anyOf": number_or_mapping},
                        "memory": {"anyOf": number_or_mapping},
                        "storage": {"type": "array", "items": {"type": "object"}},
                        "disks": {
                            "type": "array",
                            "items": {"type": "object"},
                            "description": "Accepted older name for 'storage'.",
                            "deprecated": True,
                        },
                        "networks": {"type": "array", "items": {"type": "object"}},
                        "metadata": {"type": "object"},
                    },
                    "additionalProperties": False,
                },
            },
        },
        "required": ["base_vm", "instances"],
        "additionalProperties": False,
    }


def _for_dataclass(dc: type, definitions: Dict[str, Any]) -> Dict[str, Any]:
    """Return the schema for one dataclass, registering nested ones."""
    hints = get_type_hints(dc)
    properties: Dict[str, Any] = {}
    required: List[str] = []
    for field_ in fields(dc):
        entry = _for_type(hints.get(field_.name, Any), definitions)
        doc = _field_doc(dc, field_.name)
        if doc:
            entry = {**entry, "description": doc}
        if isinstance(field_.default, Enum):
            entry = {**entry, "default": field_.default.value}
        elif field_.default is not MISSING:
            entry = {**entry, "default": field_.default}
        properties[field_.name] = entry
        if field_.default is MISSING and field_.default_factory is MISSING:  # type: ignore[misc]
            required.append(field_.name)
        for value in LEGACY_VALUES.get(field_.name, ()):
            enum = properties[field_.name].get("enum")
            if enum is not None and value not in enum:
                properties[field_.name] = {**properties[field_.name], "enum": [*enum, value]}

    # The old names, so a 1.1.x file validates too.
    for old, new in LEGACY_BY_CLASS.get(dc.__name__, {}).items():
        if new in properties and old not in properties:
            properties[old] = {
                **{k: v for k, v in properties[new].items() if k != "description"},
                "description": f"Accepted older name for '{new}'.",
                "deprecated": True,
            }

    schema: Dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        # Required only where the model has no default: a config file that omits
        # `firmware:` is normal, and a schema that called it missing would be wrong.
        schema["required"] = [name for name in required if name == "name"]
    schema["additionalProperties"] = False
    return schema


def _for_type(annotation: Any, definitions: Dict[str, Any]) -> Dict[str, Any]:
    """Return the schema for one annotation."""
    origin = get_origin(annotation)

    if origin is Union:  # Optional[X]
        inner = [a for a in get_args(annotation) if a is not type(None)]
        schema = _for_type(inner[0], definitions) if inner else {}
        return {"anyOf": [schema, {"type": "null"}]}

    if origin is list:
        item = (get_args(annotation) or (Any,))[0]
        return {"type": "array", "items": _for_type(item, definitions)}

    if origin is dict:
        return {"type": "object"}

    if is_dataclass(annotation) and isinstance(annotation, type):
        name = annotation.__name__
        if name not in definitions:
            definitions[name] = {}  # placeholder, so a cycle cannot recurse forever
            definitions[name] = _for_dataclass(annotation, definitions)
        return {"$ref": f"#/$defs/{name}"}

    if isinstance(annotation, type) and issubclass(annotation, Enum):
        return {"type": "string", "enum": [member.value for member in annotation]}

    return {
        bool: {"type": "boolean"},
        int: {"type": "integer"},
        float: {"type": "number"},
        str: {"type": "string"},
    }.get(annotation, {})


def _field_doc(dc: type, name: str) -> Optional[str]:
    """Return a field's description from its class docstring.

    The ``Attributes:`` section is where every field in this model is already
    explained, so the schema reuses it rather than asking for the same sentence twice.
    """
    doc = dc.__doc__ or ""
    wanted = f"{name}:"
    lines = doc.splitlines()
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith(wanted):
            continue
        parts = [stripped[len(wanted) :].strip()]
        indent = len(line) - len(line.lstrip())
        for follow in lines[index + 1 :]:
            if not follow.strip():
                break
            if len(follow) - len(follow.lstrip()) <= indent:
                break
            parts.append(follow.strip())
        return " ".join(parts) or None
    return None
