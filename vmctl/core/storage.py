"""
Where a provider keeps disk images.

Every hypervisor answers this differently: VirtualBox has a *machine folder* and
gives each VM its own subdirectory inside it, libvirt has an image directory (and,
for other setups, storage *pools*), Proxmox has storage ids, VMware has datastores.
The question is the same though -- "where does a new image go, and how is its path
spelled?" -- so it is asked once here (A-09 in PLAN.md).

Before this, the name differed per provider (``machine_folder`` versus
``image_dir``) and every caller that needed it tried both: ``migrate`` had a helper
that guessed, and the conformance suite had to stub two attributes to avoid asking
a hypervisor. Both of those were symptoms of a missing concept.
"""

import os
from dataclasses import dataclass
from enum import Enum


class LocationKind(Enum):
    """How a provider addresses its storage."""

    #: A filesystem path. VirtualBox and libvirt both work this way.
    DIRECTORY = "directory"
    #: A named store the hypervisor resolves itself -- a libvirt storage pool, a
    #: Proxmox storage id, a VMware datastore. Declared so a provider can say this
    #: is what it uses; building paths inside one needs that provider's own API.
    POOL = "pool"


@dataclass(frozen=True)
class StorageLocation:
    """Where new disk images are created, and how their paths are spelled.

    Attributes:
        kind: Whether this is a filesystem path or a named store.
        value: The directory, or the store's name.
        separator: Path separator to build with. Not always this machine's: vmctl
            can emit commands for a host whose separator differs from the one it
            is running on, which is how a plan built on Linux can target a
            Windows VirtualBox.
        nest_per_vm: Whether each VM gets its own subdirectory. VirtualBox does;
            libvirt keeps images flat.
    """

    kind: LocationKind
    value: str
    separator: str = os.sep
    nest_per_vm: bool = False

    def image_path(self, vm_name: str, filename: str) -> str:
        """Return the full path for one VM's image.

        Args:
            vm_name: The VM the image belongs to.
            filename: The image's file name, already safe to use in a path.

        Returns:
            The path to create the image at.

        Raises:
            NotImplementedError: For a pool, whose contents are addressed through
                the hypervisor rather than by path.
        """
        if self.kind is LocationKind.POOL:
            raise NotImplementedError(
                f"{self.value!r} is a storage pool; its volumes are addressed "
                f"through the hypervisor, not by path"
            )
        parts = [self.value.rstrip("/\\")]
        if self.nest_per_vm:
            parts.append(vm_name)
        parts.append(filename)
        return self.separator.join(parts)

    def directory_for(self, vm_name: str) -> str:
        """Return the directory a VM's images live in.

        Args:
            vm_name: The VM.

        Returns:
            The directory path, which is the location itself unless the provider
            nests each VM separately.
        """
        base = self.value.rstrip("/\\")
        return self.separator.join([base, vm_name]) if self.nest_per_vm else base

    def describe(self) -> str:
        """Return a short description, for messages and ``vmctl providers``."""
        return f"{self.kind.value} {self.value}"

    def with_value(self, value: str) -> "StorageLocation":
        """Return a copy pointing somewhere else, keeping the other settings."""
        return StorageLocation(
            kind=self.kind,
            value=value,
            separator=self.separator,
            nest_per_vm=self.nest_per_vm,
        )


def directory(path: str, separator: str = os.sep, nest_per_vm: bool = False) -> StorageLocation:
    """Return a directory location.

    Args:
        path: The directory.
        separator: Separator for the target host.
        nest_per_vm: Whether each VM gets its own subdirectory.
    """
    return StorageLocation(
        kind=LocationKind.DIRECTORY,
        value=path,
        separator=separator,
        nest_per_vm=nest_per_vm,
    )
