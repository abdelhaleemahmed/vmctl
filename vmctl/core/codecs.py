"""
Value coercions, written once.

Reading a boolean, coercing an integer or matching an enum used to be decided
separately at every field: the VirtualBox parser alone contained 28 hand-written
lookups and comparisons. Two shipped bugs were direct consequences rather than
accidents -- ``firmware`` compared VirtualBox's ``"EFI"`` against lowercase
``'efi'`` (F-02), and boolean flags each re-decided what counted as true.

A codec knows how to read one native value and how to write it back. Because the
same object does both, a field cannot be readable but not writable, which is the
other half of what went wrong (F-05, F-23).
"""

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Generic, Mapping, Optional, Type, TypeVar

T = TypeVar("T")


class Codec(ABC, Generic[T]):
    """Converts one value between its native text form and the model's type."""

    @abstractmethod
    def load(self, raw: str) -> T:
        """Parse a native value.

        Args:
            raw: The value as the hypervisor reported it.

        Returns:
            The value in the model's type.

        Raises:
            ValueError: If the text cannot be interpreted.
        """

    @abstractmethod
    def dump(self, value: T) -> str:
        """Render a value in the form the hypervisor accepts."""

    def describe(self) -> str:
        """Return a short description of the accepted values, for messages."""
        return self.__class__.__name__.lower()


class OnOff(Codec[bool]):
    """A boolean flag.

    Accepts every spelling VirtualBox and its config files use, in any case, so
    no individual field has to decide what "true" looks like.
    """

    TRUTHY = frozenset({"on", "true", "yes", "1", "enabled"})
    FALSY = frozenset({"off", "false", "no", "0", "disabled", "none"})

    def load(self, raw: str) -> bool:
        text = raw.strip().lower()
        if text in self.TRUTHY:
            return True
        if text in self.FALSY:
            return False
        raise ValueError(f"{raw!r} is not a boolean")

    def dump(self, value: bool) -> str:
        return "on" if value else "off"

    def describe(self) -> str:
        return "on | off"


class Int(Codec[int]):
    """A whole number, optionally bounded."""

    def __init__(self, minimum: Optional[int] = None, maximum: Optional[int] = None):
        self.minimum = minimum
        self.maximum = maximum

    def load(self, raw: str) -> int:
        value = int(raw.strip().strip('"'))
        if self.minimum is not None and value < self.minimum:
            raise ValueError(f"{value} is below the minimum {self.minimum}")
        if self.maximum is not None and value > self.maximum:
            raise ValueError(f"{value} is above the maximum {self.maximum}")
        return value

    def dump(self, value: int) -> str:
        return str(value)

    def describe(self) -> str:
        if self.minimum is not None or self.maximum is not None:
            return (
                f"{self.minimum if self.minimum is not None else ''}-"
                f"{self.maximum if self.maximum is not None else ''}"
            )
        return "a whole number"


class Str(Codec[str]):
    """Text passed through unchanged."""

    def load(self, raw: str) -> str:
        return raw.strip().strip('"')

    def dump(self, value: str) -> str:
        return value

    def describe(self) -> str:
        return "text"


class EnumCodec(Codec[Any]):
    """An enum matched against a table of native spellings.

    Args:
        enum: The model's enum type.
        table: Native value (lower case) to enum member. When omitted, each
            member's own ``value`` is used.
        default: Member to fall back to when the native value is unknown. None
            makes an unknown value an error, which is the right default for
            anything vmctl will then act on.
    """

    def __init__(
        self,
        enum: Type[Enum],
        table: Optional[Mapping[str, Enum]] = None,
        default: Optional[Enum] = None,
    ):
        self.enum = enum
        self.table = {k.lower(): v for k, v in (table or {}).items()} or {
            member.value.lower(): member for member in enum
        }
        self.default = default

    def load(self, raw: str) -> Any:
        text = raw.strip().strip('"').lower()
        if text in self.table:
            return self.table[text]
        if self.default is not None:
            return self.default
        raise ValueError(f"{raw!r} is not one of: {', '.join(sorted(self.table))}")

    def dump(self, value: Any) -> str:
        return str(value.value)

    def describe(self) -> str:
        return " | ".join(sorted(self.table))
