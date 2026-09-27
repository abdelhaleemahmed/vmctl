"""
Moving a VM from one hypervisor to another.

This is what the neutral model was for. Everything it needs already exists:
reading a VM is the source provider's parser, expressing it is the target's
emitter, deciding what does not carry over is :mod:`vmctl.core.translate`,
converting the disks is :mod:`vmctl.core.convert`, and placing the devices is
:mod:`vmctl.core.slots`. Migration is the orchestration, not new machinery --
which is the strongest evidence the abstraction is real (P-06 in PLAN.md).

Two things need care, and both are the kind of detail only a real migration
surfaces:

* **Native hints must not cross.** A libvirt domain's UUID is carried in
  ``metadata`` so ``edit`` can redefine it; carrying that into a *different*
  hypervisor would claim an identity that means nothing there.
* **Disk contents are a separate question from disk configuration.** The image
  files live on the source hypervisor's host, which may not be the machine
  running vmctl. So the configuration moves by default and the data only when
  asked for and reachable, rather than pretending otherwise.
"""

import os
from dataclasses import dataclass, field
from typing import List, Optional

from .convert import ConversionRequest, plan_conversions
from .exceptions import ProviderError, ValidationError
from .plan import Plan
from .translate import Policy
from .vmconfig import VMConfig


@dataclass
class Migration:
    """A planned migration.

    Attributes:
        vm: The configuration as it will be created on the target.
        plan: Everything to run: conversions first, then creation.
        source_provider: Where it came from.
        target_provider: Where it is going.
        disks_included: Whether disk contents come too.
        unreachable: Source images that could not be found from here, when disk
            contents were asked for.
    """

    vm: VMConfig
    plan: Plan
    source_provider: str
    target_provider: str
    disks_included: bool = False
    unreachable: List[str] = field(default_factory=list)


def strip_native_hints(vm: VMConfig, keep_provider: str) -> VMConfig:
    """Remove provider-specific hints that belong to another hypervisor.

    Hints are stored in ``metadata`` under a ``<provider>_`` prefix. A libvirt
    domain's UUID is the live example: it lets ``edit`` redefine that domain, and
    means nothing anywhere else.

    Args:
        vm: Configuration to clean. Modified in place.
        keep_provider: The provider whose hints may stay.

    Returns:
        The same configuration, for chaining.
    """
    for key in list(vm.metadata):
        if "_" not in key:
            continue
        owner = key.split("_", 1)[0]
        if owner != keep_provider and owner in _KNOWN_PROVIDERS():
            del vm.metadata[key]
    return vm


def _KNOWN_PROVIDERS() -> List[str]:
    from . import registry

    return registry.names()


def plan_migration(
    source,
    target,
    vm_name: str,
    new_name: Optional[str] = None,
    policy: Policy = Policy.CONVERT,
    with_disks: bool = False,
    image_dir: Optional[str] = None,
) -> Migration:
    """Work out how to recreate a VM on another hypervisor.

    Nothing is executed: the whole migration is resolved first, so the
    translation report can be read before anything happens.

    Args:
        source: Provider to read from.
        target: Provider to create on.
        vm_name: The VM to move.
        new_name: Name on the target. Defaults to the source name, which is
            usually what is wanted -- the two hypervisors have separate
            namespaces.
        policy: How to handle settings the target cannot express. Defaults to
            ``convert``, because making the VM work on the target is the point;
            the report says what was changed, and nothing runs until
            ``--execute``.
        with_disks: Also convert and attach the source's disk images. They must
            be readable from this machine.
        image_dir: Where converted images are written. Defaults to the target
            provider's own image location.

    Returns:
        Migration: the resolved configuration and plan.

    Raises:
        ProviderError: If the VM cannot be read, or the target cannot create it.
        ValidationError: If the resolved configuration is not valid for the
            target under the chosen policy.
    """
    if source.name == target.name:
        raise ValidationError(
            f"source and target are both {source.name}; nothing to migrate",
            field="to",
            value=target.name,
            recovery_hint="Use 'vmctl create' to copy a VM within one hypervisor.",
        )

    vm = source.read_vm(vm_name)
    vm.name = new_name or vm_name
    strip_native_hints(vm, target.name)

    conversions: List[ConversionRequest] = []
    unreachable: List[str] = []

    if with_disks:
        destination = image_dir or _target_image_dir(target)
        for disk in vm.disks:
            if disk.is_removable:
                # An ISO is host-specific; carrying the path over would point at
                # a file the target cannot see.
                disk.source = None
                continue
            origin = disk.disk_path
            if not origin:
                continue
            if not os.path.exists(origin):
                unreachable.append(origin)
                continue
            spec = target.capabilities.format_spec(disk.format)
            target_format = (
                disk.format if spec.support.creatable else target.capabilities.native_format
            )
            extension = target.capabilities.format_spec(target_format).extension
            copy_to = os.path.join(destination, f"{vm.name}_{disk.name}.{extension}")
            conversions.append(
                ConversionRequest(
                    source=origin,
                    target=copy_to,
                    target_format=target_format,
                    source_format=disk.format,
                    label=disk.name,
                )
            )
            # The target attaches the converted copy instead of creating a blank.
            disk.source = copy_to
            disk.format = target_format

    plan = plan_conversions(conversions, target.converter(), target.name)
    creation = target.create_vm(vm, execute=False, policy=policy)
    for step in creation:
        plan.add(step)
    for warning in creation.warnings:
        plan.warn(warning)

    if unreachable:
        plan.warn(
            f"{len(unreachable)} disk image(s) could not be read from this "
            f"machine, so blank disks will be created instead: "
            f"{', '.join(unreachable)}"
        )

    return Migration(
        vm=vm,
        plan=plan,
        source_provider=source.name,
        target_provider=target.name,
        disks_included=bool(conversions),
        unreachable=unreachable,
    )


def _target_image_dir(target) -> str:
    """Return where the target provider keeps its images.

    Providers name this differently -- VirtualBox has a machine folder, libvirt an
    image directory -- which is what ``A-09`` unifies. Until then, ask for
    whichever the provider has.

    Raises:
        ProviderError: If the provider does not say where images go.
    """
    for attribute in ("image_dir", "machine_folder"):
        value = getattr(target, attribute, None)
        if value:
            return str(value)
    raise ProviderError(
        f"cannot tell where {target.name} keeps disk images; pass an explicit " f"destination"
    )
