"""
Changes stated on the command line, merged the way ``extends:`` merges (E-20).

A configuration file describes a machine, and the command line says "like that,
but". That is the same sentence :mod:`vmctl.core.include` already answers for
``extends:``, so it gets the same answer rather than a second one: an override is a
*fragment merged over the mapping*, before any of it becomes a
:class:`~vmctl.core.vmconfig.VMConfig`.

Everything below the mapping follows from that and needs no changes at all. The
model validates an overridden field exactly as it validates a written one, the
validator checks it against the provider's measured capabilities, the translator
applies ``--policy`` to it, and the emitters place it. So ``--add-disk
bus=virtio-blk,format=vdi`` against VMware is refused under ``strict`` and
substituted under ``nearest`` without one line of new provider code -- and a
provider cannot be told about a device in a way that bypasses the checks, because
there is no path that reaches it except the ordinary one.

Precedence, most specific last, which is the ``extends:`` rule (the child wins)
applied to how narrowly a flag speaks:

1. the file, with its own ``extends:`` chain already resolved
2. ``--patch`` files, in the order given -- whole mappings, merged
3. ``--add-disk`` / ``--add-nic`` put the new devices in place, so that everything
   after this point can see them. Only the structure, not yet what they say.
4. ``--disk-format`` -- the blanket: every disk, the added ones included
5. the fields each ``--add-*`` states -- that one device
6. ``--set`` -- one field, the last word

``--disk-format`` is no longer special: it is the blanket layer of this ordering,
which is why a per-device ``format=`` beats it without any precedence code in the
emitter. ``--disk-format vdi --add-disk format=qcow2`` is answerable now, and the
answer is visible: the blanket sets the added disk to vdi at step 4 and the device's
own word puts it back to qcow2 at step 5, so :func:`outranked` has a pair to show
and the plan can say which flag won and which lost.

This layer never touches what a disk image on disk *is*, only what the new VM is
being asked for. That separation is F-38, and it is why ``--clone-disks`` still
reads the source format from the image rather than from the request.
"""

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

import yaml

from .devices import DeviceKind
from .exceptions import ValidationError
from .include import merge_tracked

#: One segment of a field path: a name, optionally indexed. ``storage[0]``,
#: ``networks[*]``, ``memory``.
_SEGMENT = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)(?:\[(\d+|\*)\])?$")

#: The keys that name a list of devices, and what a device added to each is called
#: in a message. Only these accept ``--add-*``.
DEVICE_LISTS = {"storage": "disk", "networks": "network adapter"}


@dataclass(frozen=True)
class Override:
    """One value a command-line flag put into the configuration.

    Kept as data for the reason a substitution is kept as data (A-01): two flags can
    disagree, and the user has to be able to see which one won without running the
    command a second time to find out.

    Attributes:
        path: Where it landed, spelled the way ``diff`` spells a field.
        value: What it was set to.
        source: The flag it came from, e.g. ``"--disk-format"``.
        replaced: What was there before, when something was.
    """

    path: str
    value: Any
    source: str
    replaced: Any = None

    def __post_init__(self) -> None:
        """Snapshot the values, because a later layer may mutate the originals.

        ``--patch`` replacing ``storage:`` puts a list into the mapping, and
        ``--add-disk`` then appends to that same list -- so a record holding the
        reference would afterwards describe a state that never existed at the moment
        it was made. A report has to say what happened, not what the object became.
        """
        object.__setattr__(self, "value", _deep_copy(self.value))
        object.__setattr__(self, "replaced", _deep_copy(self.replaced))

    @property
    def changed(self) -> bool:
        """Whether this actually altered anything, rather than restating it."""
        return bool(self.value != self.replaced)

    def __str__(self) -> str:
        if not self.changed:
            note = ", unchanged"
        elif self.replaced is None:
            note = ""
        else:
            note = f", was {_brief(self.replaced)}"
        return f"{self.path} = {_brief(self.value)} (from {self.source}{note})"


def _brief(value: Any) -> str:
    """Render a value for a report line.

    A whole device list printed inline is a line nobody reads, and ``--patch``
    replacing ``storage:`` is exactly that -- so a list says how many it now holds and
    leaves the detail to the plan below, which lists every command anyway.
    """
    if isinstance(value, list):
        return f"{len(value)} item{'' if len(value) == 1 else 's'}"
    if isinstance(value, dict):
        return "{" + ", ".join(sorted(value)) + "}"
    return repr(value)


def _scalar(text: str) -> Any:
    """Read a command-line value as the type it is written as.

    The shell hands over a string, and ``memory.mb=4096`` has to become an integer
    or the model rejects it. YAML's own scalar rules are used rather than a private
    guess, so a value means on the command line what it would mean in the file --
    including YAML's surprises, such as ``on`` being true.
    """
    if text == "":
        return ""
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError:
        return text


def parse_assignment(text: str, source: str = "--set") -> Tuple[str, Any]:
    """Split ``field.path=value`` into its path and its typed value.

    Args:
        text: The option's value, as typed.
        source: The flag, for the error message.

    Raises:
        ValidationError: If there is no ``=``, or nothing before it.
    """
    if "=" not in text:
        raise ValidationError(
            f"{source} takes field=value, not {text!r}",
            recovery_hint=f"{source} memory.mb=4096",
        )
    path, _, raw = text.partition("=")
    path = path.strip()
    if not path:
        raise ValidationError(f"{source} has no field name in {text!r}")
    return path, _scalar(raw)


def parse_fields(text: str, source: str) -> Dict[str, Any]:
    """Split ``k=v,k=v`` into a mapping of typed values.

    The keys are the model's own field names, deliberately: a friendlier spelling
    would be a second vocabulary to keep in step with the schema, and the schema is
    already what ``vmctl schema`` and the generated documentation describe.

    Raises:
        ValidationError: On an empty value, a part with no ``=``, or a repeated key.
    """
    fields: Dict[str, Any] = {}
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise ValidationError(
                f"{source} takes comma-separated field=value pairs; {part!r} has no '='",
                recovery_hint=f"{source} size_mb=20480,bus=virtio-blk,format=qcow2",
            )
        key, _, raw = part.partition("=")
        key = key.strip()
        if not key:
            raise ValidationError(f"{source} has a value with no field name in {part!r}")
        if key in fields:
            raise ValidationError(f"{source} states {key!r} twice")
        fields[key] = _scalar(raw)
    if not fields:
        raise ValidationError(
            f"{source} needs at least one field=value",
            recovery_hint=f"{source} size_mb=20480,bus=virtio-blk",
        )
    return fields


def _segments(path: str, source: str) -> List[Tuple[str, Optional[str]]]:
    """Parse ``storage[0].format`` into ``[("storage", "0"), ("format", None)]``."""
    parsed: List[Tuple[str, Optional[str]]] = []
    for raw in path.split("."):
        match = _SEGMENT.match(raw.strip())
        if not match:
            raise ValidationError(
                f"{source}: {raw!r} is not a field name, optionally indexed",
                field=path,
                recovery_hint="storage[0].size_mb, networks[*].model, memory.mb",
            )
        parsed.append((match.group(1), match.group(2)))
    return parsed


def _descend(
    node: Any, name: str, index: Optional[str], path: str, source: str
) -> List[Tuple[Any, str]]:
    """Step one segment inwards, returning the containers reached and their paths.

    A missing mapping is created, because ``--set memory.mb=4096`` on a file that
    never mentioned memory is an ordinary thing to ask. A missing *list element* is
    refused: ``storage[3]`` on a file with two disks is a mistake, and inventing a
    third disk to satisfy it would hide it.
    """
    if not isinstance(node, dict):
        raise ValidationError(f"{source}: {path} is not a mapping", field=path)
    if index is None:
        child = node.get(name)
        if child is None:
            child = node[name] = {}
        return [(child, name)]

    items = node.get(name)
    if not isinstance(items, list):
        raise ValidationError(
            f"{source}: {name} is not a list, so {name}[{index}] means nothing",
            field=path,
        )
    if index == "*":
        return [(item, f"{name}[{n}]") for n, item in enumerate(items)]
    position = int(index)
    if position >= len(items):
        raise ValidationError(
            f"{source}: {name}[{position}] does not exist -- there "
            f"{'is' if len(items) == 1 else 'are'} {len(items)}",
            field=path,
        )
    return [(items[position], f"{name}[{position}]")]


def set_path(data: Dict[str, Any], path: str, value: Any, source: str) -> List[Override]:
    """Set one field, in place, returning what was applied.

    ``storage[*].format`` sets it on every element and so returns one
    :class:`Override` each -- which is what makes ``--disk-format`` expressible here
    rather than as its own mechanism.
    """
    segments = _segments(path, source)
    reached: List[Tuple[Any, str]] = [(data, "")]
    for name, index in segments[:-1]:
        stepped: List[Tuple[Any, str]] = []
        for node, prefix in reached:
            for child, spelled in _descend(node, name, index, path, source):
                stepped.append((child, f"{prefix}.{spelled}" if prefix else spelled))
        reached = stepped

    name, index = segments[-1]
    applied: List[Override] = []
    for node, prefix in reached:
        if index is not None:
            for child, spelled in _descend(node, name, index, path, source):
                where = f"{prefix}.{spelled}" if prefix else spelled
                raise ValidationError(f"{source}: {where} is a device, not a field", field=path)
        if not isinstance(node, dict):
            raise ValidationError(f"{source}: {prefix or path} is not a mapping", field=path)
        before = node.get(name)
        node[name] = value
        applied.append(
            Override(
                path=f"{prefix}.{name}" if prefix else name,
                value=value,
                source=source,
                replaced=before,
            )
        )
    return applied


def _is_disk(entry: Any) -> bool:
    """Whether a storage entry is a disk, by the model's own rule.

    A DVD or floppy holds a medium that already exists, so it has no format to
    choose and ``--disk-format`` must leave it alone. ``DeviceKind`` decides, rather
    than a second list of removable kinds here.
    """
    if not isinstance(entry, dict):
        return False
    kind = entry.get("kind", DeviceKind.DISK.value)
    try:
        return not bool(DeviceKind(kind).is_removable)
    except ValueError:
        # An unknown kind is the model's error to report, not this layer's.
        return False


def blanket_disk_format(data: Dict[str, Any], disk_format: str) -> List[Override]:
    """Apply ``--disk-format`` to every disk in the mapping.

    This is the whole of what that flag now is: a fragment naming one field on each
    disk. It used to rewrite the built configuration, which made it the only thing
    in the tool with its own precedence -- and made ``--disk-format vdi --add-disk
    format=qcow2`` a question nobody could answer from the code.
    """
    applied: List[Override] = []
    for position, entry in enumerate(data.get("storage") or []):
        if _is_disk(entry):
            applied.extend(
                set_path(data, f"storage[{position}].format", disk_format, "--disk-format")
            )
    return applied


def append_device(data: Dict[str, Any], key: str, fields: Dict[str, Any], source: str) -> int:
    """Put a new device in place and return where it landed.

    Only the structure, and ``kind`` when the flag states one -- because the blanket
    that runs next has to know whether this is a disk or a DVD drive, and nothing
    else about it yet. What the device says about itself is applied afterwards, by
    :func:`device_fields`, so that it outranks the blanket.
    """
    if key not in DEVICE_LISTS:
        raise ValidationError(f"{source}: {key} is not a device list", field=key)
    devices = data.get(key)
    if not isinstance(devices, list):
        devices = data[key] = []
    position = len(devices)
    placed: Dict[str, Any] = {}
    if "kind" in fields:
        placed["kind"] = fields["kind"]
    devices.append(placed)
    return position


def device_fields(
    data: Dict[str, Any], key: str, position: int, fields: Dict[str, Any], source: str
) -> List[Override]:
    """Apply what one added device states about itself, as ordinary overrides.

    Each field is *set*, so it is reported the way any other override is and so a
    ``format=`` here replaces whatever the blanket put there a step earlier.
    """
    applied: List[Override] = []
    for field, value in fields.items():
        applied.extend(set_path(data, f"{key}[{position}].{field}", value, source))
    return applied


def apply(
    mapping: Dict[str, Any],
    *,
    patches: Sequence[Dict[str, Any]] = (),
    disk_format: Optional[str] = None,
    disks: Sequence[str] = (),
    nics: Sequence[str] = (),
    sets: Sequence[str] = (),
) -> Tuple[Dict[str, Any], List[Override]]:
    """Lay the command line's fragments over *mapping*, in precedence order.

    Args:
        mapping: The configuration as loaded, ``extends:`` already resolved.
        patches: Mappings from ``--patch`` files, merged in the order given.
        disk_format: ``--disk-format``, applied to every disk.
        disks: ``--add-disk`` values, as typed.
        nics: ``--add-nic`` values, as typed.
        sets: ``--set`` values, as typed.

    Returns:
        The merged mapping (a new one; *mapping* is not modified) and every override
        that was applied, in the order it was applied.
    """
    result: Dict[str, Any] = _deep_copy(mapping)
    applied: List[Override] = []

    for patch in patches:
        # Copied, or a later --add-disk would append to a list the patch still owns.
        result, changed = merge_tracked(result, _deep_copy(patch))
        applied.extend(
            Override(path=path, value=value, source="--patch", replaced=was)
            for path, value, was in changed
        )

    # The devices appear first so the blanket can see them, but they do not speak
    # until after it has: that is what makes a per-device value outrank it.
    placed: List[Tuple[str, int, Dict[str, Any], str]] = []
    for key, values, source in (("storage", disks, "--add-disk"), ("networks", nics, "--add-nic")):
        for text in values:
            fields = parse_fields(text, source)
            placed.append((key, append_device(result, key, fields, source), fields, source))

    if disk_format:
        applied.extend(blanket_disk_format(result, disk_format))

    for key, position, fields, source in placed:
        applied.extend(device_fields(result, key, position, fields, source))

    for text in sets:
        path, value = parse_assignment(text)
        applied.extend(set_path(result, path, value, "--set"))

    return result, applied


def _deep_copy(data: Any) -> Any:
    """Copy nested mappings and lists, so an override cannot reach the caller's."""
    if isinstance(data, dict):
        return {key: _deep_copy(value) for key, value in data.items()}
    if isinstance(data, list):
        return [_deep_copy(item) for item in data]
    return data


def outranked(applied: Sequence[Override]) -> List[Tuple[Override, Override]]:
    """Return the pairs where a later, narrower flag replaced an earlier one.

    What makes ``--disk-format vdi --add-disk format=qcow2`` answerable: the two
    flags disagreed, and this is the pair to show.
    """
    seen: Dict[str, Override] = {}
    pairs: List[Tuple[Override, Override]] = []
    for override in applied:
        earlier = seen.get(override.path)
        if earlier is not None and earlier.value != override.value:
            pairs.append((earlier, override))
        seen[override.path] = override
    return pairs
