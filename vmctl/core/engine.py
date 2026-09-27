"""
Core engine coordinating all components
"""
from pathlib import Path
from typing import Callable, Optional, List, Dict, Any
from .vmconfig import VMConfig
from .exceptions import ValidationError, SerializationError, ProviderError
from ..validators.vm_validator import VMValidator
from ..serializers.base import VMConfigSerializer
from ..serializers.json_serializer import JSONSerializer
from ..serializers.yaml_serializer import YAMLSerializer
from ..providers.virtualbox.backend import VirtualBoxBackend


class VMCtlEngine:
    """Main engine coordinating all VM operations"""

    def __init__(self, provider: str = "virtualbox"):
        """Initialise the engine with the specified hypervisor provider.

        Args:
            provider: Hypervisor backend to use. Currently only ``"virtualbox"``
                is supported.

        Raises:
            ValueError: If ``provider`` is not a recognised backend name.
        """
        if provider == "virtualbox":
            self.backend = VirtualBoxBackend()
            self.validator = VMValidator(self.backend.capabilities)
        else:
            raise ValueError(f"Unsupported provider: {provider}")

        self.serializers = {
            'json': JSONSerializer(),
            'yaml': YAMLSerializer()
        }

    def read_vm(self, vm_name: str) -> VMConfig:
        """Read VM configuration from provider"""
        return self.backend.read_vm(vm_name)

    def create_vm(
        self,
        vm: VMConfig,
        validate: bool = True,
        execute: bool = True,
        on_warning: Optional[Callable[[str], None]] = None,
    ) -> List[List[str]]:
        """Create a new VM.

        Args:
            vm: Configuration to create.
            validate: Run the validator first. Errors raise; warnings are
                reported through ``on_warning``.
            execute: Actually create it. False returns the commands only.
            on_warning: Where to send warnings. The engine used to ``print()``
                them, which mixed them into stdout ahead of dry-run output that
                a caller may be piping; the CLI now routes them to stderr.

        Returns:
            list: The commands that were, or would be, executed.
        """
        if validate:
            for warning in self.validator.validate(vm):
                if on_warning:
                    on_warning(warning)

        return self.backend.create_vm(vm, execute=execute)

    def edit_vm(
        self,
        vm_name: str,
        new_config: VMConfig,
        execute: bool = True,
        on_warning: Optional[Callable[[str], None]] = None,
    ) -> List[List[str]]:
        """Edit an existing VM.

        Args:
            vm_name: VM to change.
            new_config: Configuration to apply.
            execute: Apply the change. False returns the commands only.
            on_warning: Where to send validation warnings.

        Returns:
            list: The commands that were executed.
        """
        for warning in self.validator.validate(new_config):
            if on_warning:
                on_warning(warning)

        return self.backend.edit_vm(
            vm_name, new_config, execute=execute, on_warning=on_warning
        )

    def delete_vm(self, vm_name: str) -> bool:
        """Delete a VM"""
        return self.backend.delete_vm(vm_name)

    def list_vms(self) -> List[str]:
        """List all VMs"""
        return self.backend.list_vms()

    def start_vm(self, vm_name: str) -> bool:
        """Start a VM"""
        return self.backend.start_vm(vm_name)

    def stop_vm(self, vm_name: str, force: bool = False, wait: int = 0) -> bool:
        """Stop a VM.

        Args:
            vm_name: VM to stop.
            force: Power off instead of requesting a clean shutdown.
            wait: Seconds to wait for the VM to actually stop (0 = no wait).
        """
        return self.backend.stop_vm(vm_name, force=force, wait=wait)

    def get_vm_status(self, vm_name: str) -> str:
        """Get VM status"""
        return self.backend.get_vm_status(vm_name)

    def export_vm(self, vm_name: str, output_path: Path, format: str = "yaml") -> None:
        """Export VM configuration to file"""
        if format not in self.serializers:
            raise SerializationError(f"Unsupported format: {format}")

        vm = self.read_vm(vm_name)
        serializer = self.serializers[format]
        serializer.save(vm, output_path)

    def import_vm(self, config_path: Path, new_name: Optional[str] = None) -> VMConfig:
        """Import VM configuration from file"""
        # Detect format from extension
        suffix = config_path.suffix.lower()
        if suffix == '.json':
            serializer = self.serializers['json']
        elif suffix in ('.yaml', '.yml'):
            serializer = self.serializers['yaml']
        else:
            raise SerializationError(f"Unsupported file format: {suffix}")

        # Load configuration
        vm = serializer.load(config_path)

        # Apply new name if provided
        if new_name:
            vm.name = new_name

        return vm

    def validate_vm(self, vm: VMConfig) -> List[str]:
        """Validate VM configuration"""
        try:
            return self.validator.validate(vm)
        except ValidationError as e:
            raise e

    def get_serializer(self, format: str) -> VMConfigSerializer:
        """Get serializer for specified format"""
        if format not in self.serializers:
            raise SerializationError(f"Unsupported format: {format}")
        return self.serializers[format]
