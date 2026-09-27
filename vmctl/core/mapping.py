"""
A bidirectional field table, interpreted once.

A provider used to describe each scalar setting twice: once in its parser, to
read the value, and once in its emitter, to write it. The two copies drifted --
ten fields were parsed and never emitted (F-05), and three were emitted and
never parsed (F-23), because nothing tied the directions together.

A :class:`Field` states the correspondence once. :func:`read_into` applies a
table in the reading direction and :func:`emit_flags` in the writing direction,
so a field is supported in one place and is automatically symmetric.

Scope: scalar and enum settings, which is where the duplication lived.
Collections -- storage devices, controllers, network adapters -- need structural
assembly and stay in provider code, per the stop line in PLAN.md (A-11).
"""

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence

from .codecs import Codec


@dataclass(frozen=True)
class Field:
    """One setting, described once for both directions.

    Attributes:
        path: Dotted path in the configuration model, e.g. ``"cpu.count"``.
        native: The key the hypervisor reports this setting under.
        codec: How to read and write the value.
        flag: The command-line flag that sets it, or None when the provider
            cannot set it directly.
        emit_when: Optional predicate; the flag is only emitted when it returns
            True for the value. Used for settings whose default is better left
            unstated, such as an unrestricted CPU execution cap.
        note: Why this field is unusual, if it is.
    """

    path: str
    native: str
    codec: Codec
    flag: Optional[str] = None
    emit_when: Optional[Callable[[Any], bool]] = None
    note: str = ""

    @property
    def readable(self) -> bool:
        """True when this field can be read back from the hypervisor."""
        return bool(self.native)

    @property
    def writable(self) -> bool:
        """True when this field can be set."""
        return bool(self.flag)


def get_path(obj: Any, dotted: str) -> Any:
    """Read a dotted attribute path.

    Args:
        obj: Root object.
        dotted: Path such as ``"memory.vram_mb"``.

    Returns:
        The value at that path.
    """
    for part in dotted.split("."):
        obj = getattr(obj, part)
    return obj


def set_path(obj: Any, dotted: str, value: Any) -> None:
    """Write a dotted attribute path.

    Args:
        obj: Root object.
        dotted: Path such as ``"memory.vram_mb"``.
        value: Value to set.
    """
    parts = dotted.split(".")
    for part in parts[:-1]:
        obj = getattr(obj, part)
    setattr(obj, parts[-1], value)


def read_into(
    target: Any,
    native: Dict[str, str],
    fields: Sequence[Field],
    on_warning: Optional[Callable[[str], None]] = None,
) -> None:
    """Apply native values to a configuration object.

    Absent keys are left at their existing value, so a hypervisor that does not
    report a setting simply leaves the default in place. A value that cannot be
    interpreted is reported rather than silently dropped.

    Args:
        target: Configuration object to populate, already constructed with
            defaults.
        native: Decoded ``key -> value`` pairs from the hypervisor.
        fields: The provider's field table.
        on_warning: Where to report values that could not be read.
    """
    for entry in fields:
        if not entry.readable or entry.native not in native:
            continue
        raw = native[entry.native]
        try:
            set_path(target, entry.path, entry.codec.load(raw))
        except (ValueError, TypeError) as exc:
            if on_warning:
                on_warning(f"could not read {entry.native}={raw!r} into {entry.path}: " f"{exc}")


def emit_flags(source: Any, fields: Sequence[Field]) -> List[str]:
    """Render the flags that set every writable field.

    Args:
        source: Configuration object to read from.
        fields: The provider's field table.

    Returns:
        A flat list of flag/value pairs, in table order.
    """
    flags: List[str] = []
    for entry in fields:
        if not entry.writable:
            continue
        value = get_path(source, entry.path)
        if value is None:
            continue
        if entry.emit_when is not None and not entry.emit_when(value):
            continue
        assert entry.flag is not None
        flags += [entry.flag, entry.codec.dump(value)]
    return flags


def changed_flags(current: Any, desired: Any, fields: Sequence[Field]) -> List[str]:
    """Render the flags for fields that differ between two configurations.

    Args:
        current: The configuration as it is now.
        desired: The configuration as it should be.
        fields: The provider's field table.

    Returns:
        A flat list of flag/value pairs for the differing fields only, so
        changing one setting produces one flag.
    """
    flags: List[str] = []
    for entry in fields:
        if not entry.writable:
            continue
        new = get_path(desired, entry.path)
        if new is None or new == get_path(current, entry.path):
            continue
        assert entry.flag is not None
        flags += [entry.flag, entry.codec.dump(new)]
    return flags


def check_table(fields: Sequence[Field]) -> List[str]:
    """Return problems with a field table itself.

    Used by the provider conformance tests: a table that names the same setting
    twice, or a field with neither a native key nor a flag, is a mistake in the
    declaration rather than in the code that reads it.

    Args:
        fields: The table to check.

    Returns:
        A list of human-readable problems, empty when the table is sound.
    """
    problems = []
    seen_paths: Dict[str, int] = {}
    seen_flags: Dict[str, int] = {}
    for i, entry in enumerate(fields):
        if not entry.readable and not entry.writable:
            problems.append(f"fields[{i}] ({entry.path}) has neither a native key nor a flag")
        if entry.path in seen_paths:
            problems.append(
                f"fields[{i}] repeats path {entry.path!r} from " f"fields[{seen_paths[entry.path]}]"
            )
        seen_paths[entry.path] = i
        if entry.flag:
            if entry.flag in seen_flags:
                problems.append(
                    f"fields[{i}] repeats flag {entry.flag!r} from "
                    f"fields[{seen_flags[entry.flag]}]"
                )
            seen_flags[entry.flag] = i
    return problems
