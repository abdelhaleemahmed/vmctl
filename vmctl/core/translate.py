"""
Resolving a configuration against what a provider can actually do, and saying so.

A configuration describes a VM in neutral terms. A provider supports some of
those terms exactly, some approximately, and some not at all. Deciding which is
the same job for every provider, so it lives here rather than being repeated --
and, more importantly, **the decisions are recorded**.

Silent lossy translation is the failure mode that makes a multi-hypervisor tool
untrustworthy: a VM that comes out subtly different from the one asked for, with
nothing said. Every substitution, drop and conversion therefore lands in a
:class:`TranslationReport`, which the CLI prints before anything runs (A-04 in
PLAN.md).

The policy decides what happens when a value is unsupported:

``strict``
    Refuse. Correct when the user asked for something specific and getting
    something else would be a surprise -- the default.
``nearest``
    Substitute the closest supported value and say so. Correct when the goal is
    "make this work here", which is what moving a VM between hypervisors is.
``convert``
    As ``nearest``, and may also insert conversion steps -- converting a disk
    image rather than substituting its format.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, List, Optional

from .capabilities import Capabilities
from .exceptions import ValidationError
from .vmconfig import DiskConfig, DiskFormat, DiskVariant, FirmwareType, VMConfig


class Policy(Enum):
    """What to do about a value the target provider does not support."""

    STRICT = "strict"
    NEAREST = "nearest"
    CONVERT = "convert"

    @property
    def may_substitute(self) -> bool:
        """Whether an unsupported value may be replaced with a supported one."""
        return self is not Policy.STRICT

    @property
    def may_convert(self) -> bool:
        """Whether conversion steps may be inserted."""
        return self is Policy.CONVERT


@dataclass(frozen=True)
class Substitution:
    """A value that was replaced with one the provider supports."""

    field: str
    requested: Any
    used: Any
    reason: str

    def render(self) -> str:
        return (
            f"{self.field}: {self.requested} is not supported, used "
            f"{self.used} instead ({self.reason})"
        )


@dataclass(frozen=True)
class Drop:
    """A setting the provider has no equivalent for, so it was not applied."""

    field: str
    requested: Any
    reason: str

    def render(self) -> str:
        return f"{self.field}: {self.requested} was not applied ({self.reason})"


@dataclass(frozen=True)
class Conversion:
    """A medium that has to be converted before the provider can use it."""

    field: str
    source: Any
    target: Any
    reason: str

    def render(self) -> str:
        return f"{self.field}: {self.source} will be converted to " f"{self.target} ({self.reason})"


@dataclass
class TranslationReport:
    """Everything that did not carry over exactly.

    Attributes:
        provider: The provider being translated for.
        policy: The policy in force.
        substitutions: Values replaced with supported ones.
        drops: Settings that could not be applied.
        conversions: Media that need converting.
    """

    provider: str
    policy: Policy
    substitutions: List[Substitution] = field(default_factory=list)
    drops: List[Drop] = field(default_factory=list)
    conversions: List[Conversion] = field(default_factory=list)

    def __bool__(self) -> bool:
        """True when anything did not carry over exactly."""
        return bool(self.substitutions or self.drops or self.conversions)

    @property
    def lossy(self) -> bool:
        """True when something was changed or left out."""
        return bool(self.substitutions or self.drops)

    def lines(self) -> List[str]:
        """Return one line per finding, for ``Plan.warnings``."""
        return [
            item.render()
            for group in (self.conversions, self.substitutions, self.drops)
            for item in group
        ]

    def render(self) -> str:
        """Return the report as a block of text for a user to read."""
        if not self:
            return f"Nothing to report: the configuration maps exactly onto {self.provider}."
        parts = [f"Translating for {self.provider} (policy: {self.policy.value}):"]
        for heading, group in (
            ("will be converted", self.conversions),
            ("substituted", self.substitutions),
            ("not applied", self.drops),
        ):
            if group:
                parts.append(f"  {heading}:")
                parts.extend(f"    - {item.render()}" for item in group)
        return "\n".join(parts)


class Translator:
    """Resolves a configuration against a provider, recording what it changed."""

    def __init__(
        self,
        capabilities: Capabilities,
        policy: Policy = Policy.STRICT,
        report: Optional[TranslationReport] = None,
    ):
        """Initialise the translator.

        Args:
            capabilities: What the target provider supports.
            policy: What to do about unsupported values.
            report: Report to accumulate into. A fresh one is made if omitted.
        """
        self.capabilities = capabilities
        self.policy = policy
        self.report = report or TranslationReport(provider=capabilities.provider, policy=policy)

    # -- helpers -------------------------------------------------------------

    def _refuse(self, where: str, requested: Any, reason: str, options: Any) -> None:
        raise ValidationError(
            f"{where}: {requested} is not supported by {self.capabilities.provider} " f"({reason})",
            field=where,
            value=requested,
            expected=options,
            recovery_hint="Pass --policy nearest to let vmctl substitute a "
            "supported value, and it will tell you what it changed.",
        )

    def drop(self, where: str, requested: Any, reason: str) -> None:
        """Record a setting that cannot be applied at all.

        Under ``strict`` this is still only recorded, not refused: a setting the
        provider simply has no concept of is not a reason to refuse the whole
        VM, it is a reason to say so.
        """
        self.report.drops.append(Drop(where, requested, reason))

    # -- resolution ----------------------------------------------------------

    def format_for(self, disk: DiskConfig, where: str) -> DiskFormat:
        """Return the image format to use for a device.

        Args:
            disk: The device.
            where: Field path for messages, e.g. ``"disks[1].format"``.

        Returns:
            The format to create the medium in.

        Raises:
            ValidationError: Under ``strict``, if the provider cannot create the
                requested format.
        """
        requested = disk.format
        spec = self.capabilities.format_spec(requested)
        if spec.support.creatable:
            return requested

        creatable = sorted(f.value for f in self.capabilities.creatable_formats())
        native = self.capabilities.native_format

        if not self.policy.may_substitute:
            self._refuse(
                where,
                requested.value,
                f"this format {spec.support.describe()}",
                " | ".join(creatable),
            )

        if self.policy.may_convert and spec.support.usable:
            # The medium exists and can be read, so converting it keeps the data.
            self.report.conversions.append(
                Conversion(
                    where,
                    requested.value,
                    native.value,
                    f"{self.capabilities.provider} cannot create " f"{requested.value}",
                )
            )
        else:
            self.report.substitutions.append(
                Substitution(
                    where,
                    requested.value,
                    native.value,
                    f"{self.capabilities.provider} cannot create " f"{requested.value}",
                )
            )
        return native

    def allocation_for(self, disk: DiskConfig, fmt: DiskFormat, where: str) -> DiskVariant:
        """Return the allocation to create a medium with.

        A format may be creatable in only one allocation -- VirtualBox can make a
        dynamic qcow2 but not a fixed one, and a fixed RAW but not a dynamic one.
        Refusing would be unhelpful when there is exactly one possible answer, so
        this always adjusts and records it.

        Args:
            disk: The device.
            fmt: The format actually being used.
            where: Field path for messages.

        Returns:
            The allocation to use.
        """
        wanted = "thick" if disk.variant == DiskVariant.THICK else "thin"
        allowed = self.capabilities.format_spec(fmt).allocations
        if not allowed or wanted in allowed:
            return disk.variant
        used = allowed[0]
        self.report.substitutions.append(
            Substitution(
                f"{where}.variant",
                wanted,
                used,
                f"{fmt.value} media can only be created {used}",
            )
        )
        return DiskVariant.THICK if used == "thick" else DiskVariant.THIN

    def bus_for(self, disk: DiskConfig, where: str):
        """Return the bus to attach a device to.

        Args:
            disk: The device.
            where: Field path for messages.

        Returns:
            The bus to use.

        Raises:
            ValidationError: Under ``strict``, if the provider will not carry
                this device kind on the requested bus.
        """
        requested = disk.controller
        if self.capabilities.can_attach(disk.type, requested):
            return requested

        options = self.capabilities.buses_for(disk.type)
        readable = " | ".join(sorted(b.value for b in options)) or "(none)"

        if not options:
            self._refuse(
                where,
                requested.value,
                f"{self.capabilities.provider} has no bus that carries a "
                f"{disk.type.value} device",
                readable,
            )
        if not self.policy.may_substitute:
            self._refuse(
                where,
                requested.value,
                f"a {disk.type.value} device cannot go on that bus",
                readable,
            )

        # Prefer the provider's own idiomatic bus, so a substitution lands
        # somewhere a user of that hypervisor would expect rather than merely
        # somewhere valid.
        preferred = self.capabilities.native_bus(disk.type)
        used = preferred or sorted(options, key=lambda b: b.value)[0]
        self.report.substitutions.append(
            Substitution(
                where,
                requested.value,
                used.value,
                f"a {disk.type.value} device cannot go on {requested.value}",
            )
        )
        return used

    def firmware_for(self, vm: VMConfig, where: str = "firmware.type") -> FirmwareType:
        """Return the firmware type to use.

        Args:
            vm: The configuration.
            where: Field path for messages.

        Returns:
            The firmware type to use.

        Raises:
            ValidationError: Under ``strict``, if the provider does not support it.
        """
        requested = vm.firmware.type
        support = self.capabilities.firmware.get(requested)
        if support is None or support.usable:
            return requested

        usable = [f for f, s in self.capabilities.firmware.items() if s.usable]
        readable = " | ".join(sorted(f.value for f in usable)) or "(none)"
        if not self.policy.may_substitute or not usable:
            self._refuse(where, requested.value, "unsupported firmware", readable)

        # An EFI variant should land on another EFI variant rather than BIOS,
        # which would change how the guest boots.
        family = [f for f in usable if f is not FirmwareType.BIOS]
        used = (
            sorted(family, key=lambda f: f.value)[0]
            if requested is not FirmwareType.BIOS and family
            else sorted(usable, key=lambda f: f.value)[0]
        )
        self.report.substitutions.append(
            Substitution(where, requested.value, used.value, "unsupported firmware")
        )
        return used
