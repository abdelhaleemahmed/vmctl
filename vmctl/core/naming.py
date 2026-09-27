"""
Whether a VM name is safe to use.

A name is not only a label: it is interpolated into file paths, and for some
providers into a document. A path separator in one therefore redirects where files
are written, and a quote or angle bracket has to survive being embedded in XML.

This check lives here, and is called **by the emitters** rather than only by the
validator, because an emitter that writes files cannot depend on a check that may
not have run -- a provider's ``create_vm`` is reachable directly, without the
engine. The conformance suite found exactly that gap (A-07/A-08).
"""

import re
from typing import Optional

from .capabilities import Capabilities
from .exceptions import ValidationError


def check_name(name: str, caps: Capabilities, field: str = "name") -> None:
    """Raise unless *name* is usable with this provider.

    Args:
        name: The VM name to check.
        caps: The provider's declaration, which states the allowed shape.
        field: Field path for the error message.

    Raises:
        ValidationError: If the name is empty, too long, or contains something the
            provider does not allow.
    """
    if not name or not isinstance(name, str):
        raise ValidationError("VM name must be a non-empty string", field=field, value=name)

    if caps.name_max_length and len(name) > caps.name_max_length:
        raise ValidationError(
            f"VM name is {len(name)} characters; this provider allows " f"{caps.name_max_length}",
            field=field,
            value=name,
            expected=f"at most {caps.name_max_length} characters",
        )

    if caps.name_pattern and not re.match(caps.name_pattern, name):
        raise ValidationError(
            f"VM name {name!r} contains characters this provider does not allow",
            field=field,
            value=name,
            recovery_hint="A name cannot contain a path separator or a control "
            "character, or be '.' or '..'.",
        )


def safe_filename(name: str, fallback: str = "disk") -> str:
    """Return *name* reduced to something safe to put in a filename.

    For the parts of a path that come from data vmctl did not choose -- a device
    name taken from a controller, say -- where refusing would be unhelpful.

    Args:
        name: The text to reduce.
        fallback: What to return when nothing usable is left.

    Returns:
        A string containing only letters, digits, dot, dash and underscore.
    """
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._-")
    return cleaned or fallback


def describe_pattern(caps: Capabilities) -> Optional[str]:
    """Return the provider's name pattern, for documentation and messages."""
    return caps.name_pattern or None
