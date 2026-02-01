# serializers/json_serializer.py
"""
JSON serializer for VM configurations
"""
import json
from pathlib import Path
from typing import Any
from .base import VMConfigSerializer
from ..core.vmconfig import VMConfig
from ..core.exceptions import SerializationError


class JSONSerializer(VMConfigSerializer):
    """JSON serializer for VMConfig objects"""
    
    def save(self, vm: VMConfig, path: Path) -> None:
        """Save VM configuration as JSON"""
        try:
            data = self.to_dict(vm)
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except (IOError, TypeError) as e:
            raise SerializationError(f"Failed to save JSON: {e}")
    
    def load(self, path: Path) -> VMConfig:
        """Load VM configuration from JSON"""
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return self.from_dict(data)
        except (IOError, json.JSONDecodeError) as e:
            raise SerializationError(f"Failed to load JSON: {e}")
    
    def to_string(self, vm: VMConfig) -> str:
        """Convert VMConfig to JSON string"""
        data = self.to_dict(vm)
        return json.dumps(data, indent=2, ensure_ascii=False)
