# serializers/base.py
"""
Base serializer interface
"""

from abc import ABC, abstractmethod
from pathlib import Path
from ..core.vmconfig import VMConfig


class VMConfigSerializer(ABC):
    """Abstract base class for serializers"""

    @abstractmethod
    def save(self, vm: VMConfig, path: Path) -> None:
        """Save VM configuration to file"""
        pass

    @abstractmethod
    def load(self, path: Path) -> VMConfig:
        """Load VM configuration from file"""
        pass

    def to_dict(self, vm: VMConfig) -> dict:
        """Convert VMConfig to dictionary"""
        return vm.to_dict()

    def from_dict(self, data: dict) -> VMConfig:
        """Create VMConfig from dictionary"""
        return VMConfig.from_dict(data)
