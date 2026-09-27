"""
Base provider interface for hypervisor backends.

All hypervisor providers (VirtualBox, libvirt, QEMU, etc.) must implement
this abstract base class to ensure consistent API across providers.
"""

import shutil
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional

try:  # pragma: no cover - typing_extensions fallback for older interpreters
    from typing import Protocol, runtime_checkable
except ImportError:  # pragma: no cover
    from typing_extensions import Protocol, runtime_checkable  # type: ignore

from ..core.capabilities import Capabilities
from ..core.convert import MediumConverter
from ..core.plan import Plan
from ..core.storage import StorageLocation
from ..core.translate import Policy
from ..core.vmconfig import VMConfig


@runtime_checkable
class MediumProbe(Protocol):
    """Looks up the properties of a storage medium by path.

    Parsers need a medium's size, format and allocation variant, but acquiring
    that information is *transport*, not parsing: it means shelling out to the
    hypervisor or reading the host filesystem. Taking it as a collaborator keeps
    every parser a pure function of its input text, which is what makes parsers
    testable without the hypervisor installed.

    Implementations return a dict with at least ``size_mb``, ``format`` and
    ``variant`` keys.
    """

    def __call__(self, path: str) -> Dict[str, Any]:  # pragma: no cover - protocol
        ...


class BaseProvider(ABC):
    """Abstract base class for hypervisor providers."""

    #: Executable this provider needs on PATH. Used by the default
    #: :meth:`is_available`; override that method for anything more involved.
    REQUIRED_BINARY: Optional[str] = None

    @abstractmethod
    def storage_location(self) -> StorageLocation:
        """Return where this provider creates new disk images.

        Providers spell this differently -- a machine folder, an image directory,
        a pool, a datastore -- so the neutral answer lives in
        :class:`~vmctl.core.storage.StorageLocation` and everything that needs a
        path asks for it here rather than guessing an attribute name (A-09).
        """

    def converter(self) -> Optional[MediumConverter]:
        """Return this provider's image converter, if it has one.

        Deciding *whether* an image needs converting is provider-neutral and
        lives in :mod:`vmctl.core.convert`; this supplies the mechanism.

        Returns:
            A converter, or None when the provider cannot convert images.
        """
        return None

    def version(self) -> str:
        """Return the hypervisor's version, e.g. ``"7.1.18"``.

        Returns:
            The version string, or ``""`` when the provider cannot determine it.
        """
        return ""

    def probe(self) -> Capabilities:
        """Return this provider's capabilities, refined by asking the host (E-05).

        Some of a declaration cannot be written down in advance, because the answer
        belongs to the machine rather than to the product: which bridged interfaces
        exist, which host-only networks are defined, which machine types this QEMU
        build offers, which guest OS ids this VirtualBox knows. A static table can
        only be a conservative default, and for libvirt -- whose matrix depends on the
        QEMU build underneath it -- that is a real gap rather than a nicety.

        The default returns the static declaration unchanged, so a provider that
        cannot ask, or a machine where the tooling is absent, still works. A provider
        that overrides this must **never raise**: an unanswerable question leaves the
        static value in place, because refusing to work at all is a worse answer than
        a conservative one.

        Returns:
            The declaration to validate against, cached by the caller.
        """
        return self.capabilities

    @classmethod
    def is_available(cls) -> bool:
        """Whether this provider can be used on this machine.

        Used to choose a provider, so it must never raise: anything unexpected
        counts as unavailable.
        """
        if cls.REQUIRED_BINARY is None:
            return True
        return shutil.which(cls.REQUIRED_BINARY) is not None

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the provider name (e.g., 'virtualbox', 'libvirt')."""
        pass

    @property
    @abstractmethod
    def capabilities(self) -> Capabilities:
        """Return this provider's capability declaration.

        Every limit vmctl enforces is read from here, so a provider states its
        rules once instead of having them restated as literals in the validator.
        """
        pass

    @abstractmethod
    def list_vms(self) -> List[str]:
        """List all VM names managed by this provider."""
        pass

    @abstractmethod
    def read_vm(self, vm_name: str) -> VMConfig:
        """
        Read VM configuration from the provider.

        Args:
            vm_name: Name of the VM to read

        Returns:
            VMConfig object with the VM's configuration

        Raises:
            ProviderError: If VM doesn't exist or cannot be read
        """
        pass

    @abstractmethod
    def create_vm(self, vm: VMConfig, execute: bool = True, policy: Policy = Policy.STRICT) -> Plan:
        """
        Create a new VM from configuration.

        Args:
            vm: VMConfig object defining the VM
            execute: If True, actually create the VM. If False, return the plan
                without running it (dry-run)
            policy: What to do about values this provider does not support --
                refuse (strict), substitute and report (nearest), or convert

        Returns:
            Plan: the steps that were, or would be, run

        Raises:
            ProviderError: If VM creation fails
        """
        pass

    @abstractmethod
    def delete_vm(self, vm_name: str) -> bool:
        """
        Delete a VM and its associated resources.

        Args:
            vm_name: Name of the VM to delete

        Returns:
            True if deletion was successful

        Raises:
            ProviderError: If VM doesn't exist or deletion fails
        """
        pass

    @abstractmethod
    def start_vm(self, vm_name: str) -> bool:
        """
        Start a VM.

        Args:
            vm_name: Name of the VM to start

        Returns:
            True if VM was started successfully

        Raises:
            ProviderError: If VM doesn't exist or cannot be started
        """
        pass

    @abstractmethod
    def stop_vm(self, vm_name: str, force: bool = False, wait: int = 0) -> bool:
        """
        Stop a running VM.

        Args:
            vm_name: Name of the VM to stop
            force: If True, force power off. If False, attempt graceful shutdown
            wait: Seconds to wait for the VM to actually stop (0 = no wait)

        Returns:
            True if VM was stopped successfully

        Raises:
            ProviderError: If VM doesn't exist or cannot be stopped
        """
        pass

    @abstractmethod
    def get_vm_status(self, vm_name: str) -> str:
        """
        Get the current status of a VM.

        Args:
            vm_name: Name of the VM

        Returns:
            Status string (e.g., 'running', 'stopped', 'paused')

        Raises:
            ProviderError: If VM doesn't exist
        """
        pass

    def vm_exists(self, vm_name: str) -> bool:
        """
        Check if a VM exists.

        Args:
            vm_name: Name of the VM to check

        Returns:
            True if VM exists, False otherwise
        """
        return vm_name in self.list_vms()

    def edit_vm(
        self,
        vm_name: str,
        new_config: VMConfig,
        execute: bool = True,
        on_warning: Optional[Callable[[str], None]] = None,
    ) -> Plan:
        """
        Edit an existing VM's configuration.

        Args:
            vm_name: Name of the VM to edit
            new_config: New configuration to apply
            execute: If True, apply the change. If False, return the plan
                without running it (dry-run).
            on_warning: Where the engine reports *validation* warnings. What could
                not be applied in place rides on the returned plan's
                ``warnings``, which is the single carrier -- forwarding those here
                as well printed every one of them twice.

        Returns:
            Plan: the steps that were, or would be, run

        Raises:
            NotImplementedError: If the provider does not implement editing.

        Note:
            This used to default to ``delete_vm()`` followed by ``create_vm()``.
            Since ``delete_vm`` passes ``--delete``, that destroyed the VM's
            disks -- a data-loss trap for any provider that simply did not
            override it (F-11). Providers must implement editing explicitly.
        """
        raise NotImplementedError(f"{self.name} does not support editing VMs in place")
