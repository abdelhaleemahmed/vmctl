# serializers/json_serializer.py
"""
JSON serializer for VM configurations
"""

import json
from pathlib import Path
from .base import VMConfigSerializer
from ..core.vmconfig import VMConfig
from ..core.exceptions import SerializationError
from ..core.include import resolve


class JSONSerializer(VMConfigSerializer):
    """JSON serializer for VMConfig objects"""

    def save(self, vm: VMConfig, path: Path) -> None:
        """Save VM configuration as JSON"""
        try:
            data = self.to_dict(vm)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except (IOError, TypeError) as e:
            raise SerializationError(f"Failed to save JSON: {e}")

    def load(self, path: Path) -> VMConfig:
        """Load VM configuration from JSON, resolving ``extends:`` (E-11).

        The same loader the YAML serializer uses, so a base may be written in either
        format -- see :mod:`vmctl.core.include`.
        """
        return self.from_dict(resolve(path))

    def to_string(self, vm: VMConfig) -> str:
        """Convert VMConfig to JSON string"""
        data = self.to_dict(vm)
        return json.dumps(data, indent=2, ensure_ascii=False)
