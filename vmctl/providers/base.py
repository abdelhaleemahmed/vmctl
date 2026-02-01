"""
Base provider interface for hypervisor backends.

All hypervisor providers (VirtualBox, libvirt, QEMU, etc.) must implement
this abstract base class to ensure consistent API across providers.
"""
from abc import ABC, abstractmethod
from typing import List, Optional, Dict, Any
from ..core.vmconfig import VMConfig


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
    def stop_vm(self, vm_name: str, force: bool = False) -> bool:
        """
        Stop a running VM.

        Args:
            vm_name: Name of the VM to stop
            force: If True, force power off. If False, attempt graceful shutdown

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

    def edit_vm(self, vm_name: str, new_config: VMConfig) -> List[List[str]]:
        """
        Edit an existing VM's configuration.

        Default implementation deletes and recreates the VM.
        Providers can override this with more efficient implementations.

        Args:
            vm_name: Name of the VM to edit
            new_config: New configuration to apply

        Returns:
            List of commands that were executed
        """
        self.delete_vm(vm_name)
        return self.create_vm(new_config)
