"""
Core engine coordinating all components
"""
from pathlib import Path
from typing import Optional, List, Dict, Any
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
        try:
            return self.backend.read_vm(vm_name)
        except ProviderError as e:
            raise e

    def create_vm(self, vm: VMConfig, validate: bool = True, execute: bool = True) -> List[List[str]]:
        """Create a new VM"""
        if validate:
            warnings = self.validator.validate(vm)
            for warning in warnings:
                print(f"Warning: {warning}")

        try:
            return self.backend.create_vm(vm, execute=execute)
        except ProviderError as e:
            raise e

    def edit_vm(self, vm_name: str, new_config: VMConfig) -> List[List[str]]:
        """Edit existing VM"""
        warnings = self.validator.validate(new_config)
        for warning in warnings:
            print(f"Warning: {warning}")

        try:
            return self.backend.edit_vm(vm_name, new_config)
        except ProviderError as e:
            raise e

    def delete_vm(self, vm_name: str) -> bool:
        """Delete a VM"""
        try:
            return self.backend.delete_vm(vm_name)
        except ProviderError as e:
            raise e

    def list_vms(self) -> List[str]:
        """List all VMs"""
        try:
            return self.backend.list_vms()
        except ProviderError as e:
            raise e

    def start_vm(self, vm_name: str) -> bool:
        """Start a VM"""
        try:
            return self.backend.start_vm(vm_name)
        except ProviderError as e:
            raise e

    def stop_vm(self, vm_name: str, force: bool = False) -> bool:
        """Stop a VM"""
        try:
            return self.backend.stop_vm(vm_name, force=force)
        except ProviderError as e:
            raise e

    def get_vm_status(self, vm_name: str) -> str:
        """Get VM status"""
        try:
            return self.backend.get_vm_status(vm_name)
        except ProviderError as e:
            raise e

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
