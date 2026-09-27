"""
Converting a disk image from one format to another.

Deciding *whether* a conversion is needed is provider-neutral: compare what the
configuration has against what the target provider can create, which the
capability declaration already answers. Only *how* to convert differs -- libvirt
uses ``qemu-img convert``, VirtualBox uses ``VBoxManage clonemedium``, VMware has
``vmware-vdiskmanager``.

So the decision lives here and the mechanism lives in each provider. A conversion
comes back as :class:`~vmctl.core.plan.Plan` steps like any other work, which
means it inherits dry-run, the undo that makes rollback possible, and a
description a user can read -- rather than being a side effect buried in a
provider (M-05 in PLAN.md).

This is also the one code path ``E-03 --clone-disks`` and ``P-06 migrate`` should
use: copying a disk and converting one differ only in whether the formats match.
"""

from dataclasses import dataclass
from typing import List, Optional, Sequence

try:  # pragma: no cover - typing_extensions fallback
    from typing import Protocol, runtime_checkable
except ImportError:  # pragma: no cover
    from typing_extensions import Protocol, runtime_checkable  # type: ignore

from .capabilities import Capabilities
from .exceptions import ProviderError
from .plan import Plan, Step, StepKind
from .vmconfig import DiskFormat, VMConfig


@dataclass(frozen=True)
class ConversionRequest:
    """One image to convert.

    Attributes:
        source: Path to the existing image.
        source_format: Its format, when known. None lets the tool detect it,
            which is usually better than asserting it wrongly.
        target: Path to write.
        target_format: Format to write.
        label: What this image is, for the step description.
    """

    source: str
    target: str
    target_format: DiskFormat
    source_format: Optional[DiskFormat] = None
    label: str = ""

    @property
    def needed(self) -> bool:
        """Whether anything actually has to change.

        A request whose formats match and whose paths are the same is a no-op;
        one whose paths differ is still a copy, which is what ``--clone-disks``
        wants.
        """
        if self.source == self.target:
            return self.source_format != self.target_format
        return True


@runtime_checkable
class MediumConverter(Protocol):
    """A provider's way of converting an image."""

    def can_convert(
        self, source: Optional[DiskFormat], target: DiskFormat
    ) -> bool:  # pragma: no cover - protocol
        """Whether this converter handles the pair."""
        ...

    def steps(self, request: ConversionRequest) -> List[Step]:  # pragma: no cover - protocol
        """Return the steps that perform the conversion."""
        ...


def conversions_for(vm: VMConfig, caps: Capabilities, image_path) -> List[ConversionRequest]:
    """Work out which of a VM's disks need converting for a target provider.

    Args:
        vm: The configuration being realised.
        caps: What the target provider can create.
        image_path: Callable taking ``(vm, disk, extension)`` and returning where
            the new image should live -- the provider knows its own storage
            layout.

    Returns:
        list[ConversionRequest]: one per disk that has an existing image in a
        format the target cannot create. Disks with nothing to convert from are
        skipped: a configuration that merely *describes* a disk to create has no
        source, so there is nothing to convert and the format is substituted
        instead.
    """
    requests = []
    for disk in vm.storage:
        if disk.is_removable:
            continue
        source = disk.disk_path or disk.source
        if not source:
            continue  # nothing exists yet; this disk will be created
        if disk.format is None:
            continue  # no format asked for, so nothing to convert *to* (M-04)
        spec = caps.format_spec(disk.format)
        if spec.support.creatable:
            continue  # the target can make this format directly
        target_format = caps.native_format
        target = image_path(vm, disk, caps.format_spec(target_format).extension)
        requests.append(
            ConversionRequest(
                source=source,
                target=target,
                target_format=target_format,
                source_format=disk.format,
                label=disk.name,
            )
        )
    return requests


def plan_conversions(
    requests: Sequence[ConversionRequest],
    converter: Optional[MediumConverter],
    provider: str,
) -> Plan:
    """Turn conversion requests into a plan.

    Args:
        requests: What to convert.
        converter: The provider's converter. None means the provider cannot
            convert, which is an error only if there is something to convert.
        provider: Provider name, for the plan.

    Returns:
        Plan: the steps, in request order.

    Raises:
        ProviderError: If a conversion is needed and the provider cannot do it.
    """
    plan = Plan(provider)
    directories: List[str] = []
    for request in requests:
        if not request.needed:
            continue
        if converter is None:
            raise ProviderError(
                f"{provider} cannot convert disk images, so {request.source} "
                f"cannot be turned into {request.target_format.value}"
            )
        if not converter.can_convert(request.source_format, request.target_format):
            if request.source_format is None:
                detail = f"cannot write {request.target_format.value} images"
            else:
                detail = (
                    f"cannot convert {request.source_format.value} to "
                    f"{request.target_format.value}"
                )
            raise ProviderError(f"{provider} {detail}")
        # The target's directory has to exist first. Conversions run *before* the
        # VM is created, and for every provider that keeps a directory per VM the
        # create plan is what makes it -- so a migration with --with-disks failed on
        # "Could not create ...: No such file or directory" (F-36).
        directory = _parent_of(request.target)
        if directory and directory not in directories:
            directories.append(directory)
            plan.add(
                Step(
                    kind=StepKind.EXEC,
                    description=f"ensure {directory} exists",
                    argv=["mkdir", "-p", directory],
                )
            )
        for step in converter.steps(request):
            plan.add(step)
    return plan


def _parent_of(path: str) -> str:
    """Return the directory part of a path, for either kind of separator.

    ``os.path.dirname`` is the host's answer, and a plan may name paths on another
    host -- a Windows ``.vmdk`` target written from Linux, for instance (A-09).
    """
    cut = max(path.rfind("/"), path.rfind("\\"))
    return path[:cut] if cut > 0 else ""


def convert(
    source: str,
    target: str,
    target_format: DiskFormat,
    converter: MediumConverter,
    provider: str,
    source_format: Optional[DiskFormat] = None,
    label: str = "",
) -> Plan:
    """Return the plan that converts one image.

    The single-image entry point, for a standalone conversion.

    Args:
        source: Existing image.
        target: Path to write.
        target_format: Format to write.
        converter: The provider's converter.
        provider: Provider name, for the plan.
        source_format: The source's format, if known.
        label: What the image is, for the step description.

    Returns:
        Plan: the steps that perform the conversion.
    """
    request = ConversionRequest(
        source=source,
        target=target,
        target_format=target_format,
        source_format=source_format,
        label=label or source,
    )
    return plan_conversions([request], converter, provider)


def describe(request: ConversionRequest) -> str:
    """Return a one-line description of a conversion, for a step."""
    what = request.label or request.source
    if request.source_format and request.source_format != request.target_format:
        return (
            f"convert {what} from {request.source_format.value} to "
            f"{request.target_format.value}"
        )
    return f"copy {what} to {request.target_format.value}"


__all__ = [
    "ConversionRequest",
    "MediumConverter",
    "conversions_for",
    "plan_conversions",
    "convert",
    "describe",
]
