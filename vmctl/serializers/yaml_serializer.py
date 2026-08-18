# serializers/yaml_serializer.py
"""
YAML serializer for VM configurations
"""
from pathlib import Path
import yaml
from .base import VMConfigSerializer
from ..core.vmconfig import VMConfig
from ..core.exceptions import SerializationError


class YAMLSerializer(VMConfigSerializer):
    """YAML serializer for VMConfig objects"""
    
    def save(self, vm: VMConfig, path: Path) -> None:
        """Save VM configuration as YAML"""
        try:
            data = self.to_dict(vm)
            with open(path, 'w', encoding='utf-8') as f:
                yaml.safe_dump(data, f, default_flow_style=False, allow_unicode=True)
        except (IOError, yaml.YAMLError) as e:
            raise SerializationError(f"Failed to save YAML: {e}")
    
    def load(self, path: Path) -> VMConfig:
        """Load VM configuration from YAML"""
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f)
            return self.from_dict(data)
        except (IOError, yaml.YAMLError) as e:
            raise SerializationError(f"Failed to load YAML: {e}")
    
    def to_string(self, vm: VMConfig) -> str:
        """Convert VMConfig to YAML string"""
        data = self.to_dict(vm)
        return yaml.safe_dump(data, default_flow_style=False, allow_unicode=True)
