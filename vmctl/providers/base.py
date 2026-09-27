"""
Base provider interface for hypervisor backends.

All hypervisor providers (VirtualBox, libvirt, QEMU, etc.) must implement
this abstract base class to ensure consistent API across providers.
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any

try:  # pragma: no cover - typing_extensions fallback for older interpreters
    from typing import Protocol, runtime_checkable
except ImportError:  # pragma: no cover
    from typing_extensions import Protocol, runtime_checkable  # type: ignore

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

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the provider name (e.g., 'virtualbox', 'libvirt')."""
        pass

    @property
    @abstractmethod
    def capabilities(self) -> Dict[str, Any]:
        """Return provider capabilities and limits."""
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
    def create_vm(self, vm: VMConfig, execute: bool = True) -> List[List[str]]:
        """
        Create a new VM from configuration.

        Args:
            vm: VMConfig object defining the VM
            execute: If True, actually create the VM. If False, return commands only (dry-run)

        Returns:
            List of commands that were/would be executed

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

    def edit_vm(self, vm_name: str, new_config: VMConfig, execute: bool = True) -> List[List[str]]:
        """
        Edit an existing VM's configuration.

        Args:
            vm_name: Name of the VM to edit
            new_config: New configuration to apply
            execute: If True, apply the change. If False, return the commands
                that would be run (dry-run).

        Returns:
            List of commands that were, or would be, executed

        Raises:
            NotImplementedError: If the provider does not implement editing.

        Note:
            This used to default to ``delete_vm()`` followed by ``create_vm()``.
            Since ``delete_vm`` passes ``--delete``, that destroyed the VM's
            disks -- a data-loss trap for any provider that simply did not
            override it (F-11). Providers must implement editing explicitly.
        """
        raise NotImplementedError(f"{self.name} does not support editing VMs in place")
