"""
One configuration built out of several files (E-11).

A lab is mostly one machine repeated: the same guest OS, the same memory, the same
NAT adapter, and a different name and disk size each time. Without a way to say
"like this, but", every file carries every field -- and the fields that matter get
lost among the ones that are only there because YAML has no inheritance.

``extends:`` is that way to say it::

    # base.yaml
    guest_os: ubuntu22.04
    memory: {mb: 2048}
    networks: [{network_type: nat}]

    # web-01.yaml
    extends: base.yaml
    name: web-01
    storage: [{name: system, size_mb: 40960}]

The rules are the ones the rest of vmctl already uses, so there is nothing new to
learn:

* **A mapping merges, a list replaces.** ``memory: {mb: 4096}`` in the child changes
  the memory and keeps the base's ``vram_mb``; a child that writes ``storage:`` is
  stating the whole list. That is exactly how ``apply`` treats a file against a live
  VM (E-02), and for the same reason: a list has no key to merge on.
* **The child wins**, and with several bases the last one wins -- ``extends`` reads
  left to right, so each file overrides what came before it.
* **Paths are relative to the file that names them**, not to the working directory,
  because a directory of configs has to keep working wherever it is checked out.

Formats may mix: a YAML file can extend a JSON base. What is loaded is a *mapping*,
before any of it becomes a :class:`~vmctl.core.vmconfig.VMConfig`, which is what lets
``diff`` and ``apply`` see the merged file the way they see any other -- including
which fields it actually states (E-01).
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .exceptions import SerializationError

#: The key a file uses to name what it is built on.
EXTENDS = "extends"

#: How deep a chain may go. Not a real limit -- a lab with ten levels of inheritance
#: has a worse problem than this error -- but a cycle through a symlink would
#: otherwise be an infinite loop rather than a message.
MAX_DEPTH = 16


#: Which serializer an extension names. The one place that decides, so the engine and
#: this module cannot come to disagree about what ``.yml`` is.
SUFFIXES = {".json": "json", ".yaml": "yaml", ".yml": "yaml"}


def format_for(path: Path) -> str:
    """Return the serializer name a file's extension asks for.

    Raises:
        SerializationError: If the extension names nothing vmctl can read.
    """
    suffix = Path(path).suffix.lower()
    if suffix not in SUFFIXES:
        raise SerializationError(
            f"Unsupported file format: {suffix}",
            recovery_hint="name the file .yaml, .yml or .json",
        )
    return SUFFIXES[suffix]


def load_mapping(path: Path) -> Dict[str, Any]:
    """Load one file as the plain mapping it is, without resolving ``extends``.

    Args:
        path: The file. ``.json`` is read as JSON, anything else as YAML -- which is
            also how the engine chooses a serializer.

    Returns:
        dict: the file's contents, empty for an empty file.

    Raises:
        SerializationError: If the file cannot be read or is not a mapping.
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise SerializationError(f"cannot read {path}: {exc}")
    try:
        if str(path).lower().endswith(".json"):
            data = json.loads(text) if text.strip() else {}
        else:
            import yaml

            data = yaml.safe_load(text)
    except (ValueError, Exception) as exc:  # yaml.YAMLError is an Exception subclass
        if isinstance(exc, SerializationError):
            raise
        raise SerializationError(f"cannot parse {path}: {exc}")
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise SerializationError(
            f"{path} does not describe a VM: its top level is a "
            f"{type(data).__name__}, not a mapping"
        )
    return data


def resolve(path: Path) -> Dict[str, Any]:
    """Load a configuration file with every ``extends:`` resolved into one mapping.

    Args:
        path: The file to load.

    Returns:
        dict: the merged configuration, with ``extends`` removed.

    Raises:
        SerializationError: If a file cannot be read, a base does not exist, or the
            chain has a cycle.
    """
    return _resolve(Path(path), seen=set(), depth=0)


def _resolve(path: Path, seen: Set[str], depth: int) -> Dict[str, Any]:
    """Resolve one file, guarding against a chain that never ends."""
    real = str(path.resolve() if path.exists() else path)
    if real in seen:
        raise SerializationError(
            f"{path} extends itself, directly or through another file",
            recovery_hint="a base must not, in turn, extend the file that names it",
        )
    if depth > MAX_DEPTH:
        raise SerializationError(f"'{EXTENDS}' is more than {MAX_DEPTH} files deep at {path}")

    data = load_mapping(path)
    bases = data.pop(EXTENDS, None)
    if bases is None:
        return data

    merged: Dict[str, Any] = {}
    for name in _base_list(bases, path):
        base_path = Path(name)
        if not base_path.is_absolute():
            # Relative to the file that named it: a directory of configs has to work
            # wherever it is checked out, not only from the directory vmctl was run in.
            base_path = path.parent / base_path
        if not base_path.exists():
            raise SerializationError(
                f"{path} extends {name!r}, which does not exist",
                recovery_hint=f"looked for {base_path}",
            )
        merged = merge(merged, _resolve(base_path, seen | {real}, depth + 1))
    return merge(merged, data)


def _base_list(bases: Any, path: Path) -> List[str]:
    """Return ``extends`` as a list of names, whichever way it was written."""
    if isinstance(bases, str):
        return [bases]
    if isinstance(bases, list) and all(isinstance(item, str) for item in bases):
        return list(bases)
    raise SerializationError(
        f"{path}: '{EXTENDS}' must be a file name or a list of them",
        recovery_hint="extends: base.yaml   # or: extends: [common.yaml, lab.yaml]",
    )


def merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Return *base* with *override* laid over it.

    Mappings merge recursively; everything else, lists included, is replaced. Neither
    argument is modified.

    Args:
        base: What to start from.
        override: What wins.
    """
    return merge_tracked(base, override)[0]


def merge_tracked(
    base: Dict[str, Any], override: Dict[str, Any], prefix: str = ""
) -> Tuple[Dict[str, Any], List[Tuple[str, Any, Any]]]:
    """Merge, and also say which values changed and what they were.

    The same walk as :func:`merge` -- there is only one, so ``extends:`` and a
    command-line override cannot come to differ about what merging means. The record
    is what lets an override be *reported*: a fragment that quietly halved the memory
    is the kind of silent disagreement vmctl exists to refuse (E-20).

    Returns:
        The merged mapping, and ``(path, new, old)`` for each value replaced, with
        paths spelled the way ``diff`` spells a field.
    """
    result = dict(base)
    changed: List[Tuple[str, Any, Any]] = []
    for key, value in override.items():
        path = f"{prefix}.{key}" if prefix else key
        current = result.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            result[key], nested = merge_tracked(current, value, path)
            changed.extend(nested)
        else:
            result[key] = value
            changed.append((path, value, current))
    return result, changed


def bases_of(path: Path) -> List[str]:
    """Return the files one file extends, as written. For reporting, not loading."""
    data = load_mapping(path)
    bases = data.get(EXTENDS)
    return _base_list(bases, Path(path)) if bases is not None else []


def stated_in(path: Path) -> Optional[Dict[str, Any]]:
    """Return the merged mapping, or None when the file has no ``extends``.

    Lets a caller avoid re-reading a file it has already loaded the plain way.
    """
    data = load_mapping(path)
    return resolve(path) if EXTENDS in data else None
