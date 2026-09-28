# serializers/yaml_serializer.py
"""
YAML serializer for VM configurations
"""
from pathlib import Path
import yaml
from .base import VMConfigSerializer
from ..core.vmconfig import VMConfig
from ..core.exceptions import SerializationError  # noqa: F401  (save still raises it)
from ..core.include import resolve


class YAMLSerializer(VMConfigSerializer):
    """YAML serializer for VMConfig objects"""

    def save(self, vm: VMConfig, path: Path) -> None:
        """Save VM configuration as YAML"""
        try:
            data = self.to_dict(vm)
            with open(path, "w", encoding="utf-8") as f:
                yaml.safe_dump(data, f, default_flow_style=False, allow_unicode=True)
        except (IOError, yaml.YAMLError) as e:
            raise SerializationError(f"Failed to save YAML: {e}")

    def load(self, path: Path) -> VMConfig:
        """Load VM configuration from YAML, resolving ``extends:`` (E-11).

        The reading and merging live in :mod:`vmctl.core.include` so that the two
        serializers and the commands that need the *raw* mapping -- ``diff`` and
        ``apply``, which have to know which fields a file actually states -- all see
        the same file. A YAML config may extend a JSON base, or the other way round.
        """
        return self.from_dict(resolve(path))

    def to_string(self, vm: VMConfig) -> str:
        """Convert VMConfig to YAML string"""
        data = self.to_dict(vm)
        text: str = yaml.safe_dump(data, default_flow_style=False, allow_unicode=True)
        return text
