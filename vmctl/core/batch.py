"""
Batch VM creation engine
"""
import copy
from pathlib import Path
from typing import Callable, Dict, List, Any, Optional
import yaml
import json
from .vmconfig import VMConfig
from .exceptions import ValidationError


class BatchCreator:
    """Batch VM creation engine"""

    def __init__(self, engine, on_warning: Optional[Callable[[str], None]] = None):
        """Initialise the batch creator with an engine instance.

        Args:
            engine: A configured :class:`~vmctl.core.engine.VMCtlEngine` used
                to create, read, and validate individual VMs.
            on_warning: Where to send validation warnings. Defaults to
                discarding them; the CLI routes them to stderr so that stdout
                stays pipeable.
        """
        self.engine = engine
        self.on_warning = on_warning

    def create_from_file(self, batch_file: Path) -> List[VMConfig]:
        """Build every VM configuration described by a batch file.

        Nothing is created here: the whole batch is resolved and validated
        first, so a mistake in the last instance is reported before the first
        VM exists.

        Args:
            batch_file: Path to the batch definition (YAML or JSON).

        Returns:
            list[VMConfig]: One configuration per instance, in file order.

        Raises:
            ValidationError: If the file is malformed, an instance has no name,
                two instances share a name, or any instance fails validation.
        """
        batch_def = self._load_batch_file(batch_file)
        base_vm = self._get_base_vm(batch_def)

        instances = batch_def.get('instances') or []
        if not isinstance(instances, list):
            raise ValidationError(
                "'instances' must be a list of VM definitions",
                field="instances")
        if not instances:
            raise ValidationError(
                "The batch file defines no instances",
                field="instances",
                recovery_hint="Add an 'instances:' list with at least one entry.")

        # Every instance needs its own name. Without this, instances without a
        # name all inherited the base VM's, and `--execute` created the first
        # and then failed on the second with a half-built cluster left behind
        # (F-12).
        seen = {}
        for index, instance_def in enumerate(instances):
            if not isinstance(instance_def, dict):
                raise ValidationError(
                    f"instances[{index}] must be a mapping, got "
                    f"{type(instance_def).__name__}", field=f"instances[{index}]")
            name = instance_def.get('name')
            if not name:
                raise ValidationError(
                    f"instances[{index}] has no 'name'",
                    field=f"instances[{index}].name",
                    recovery_hint="Every instance needs a unique name.")
            if name in seen:
                raise ValidationError(
                    f"instances[{index}] repeats the name {name!r}, already "
                    f"used by instances[{seen[name]}]",
                    field=f"instances[{index}].name", value=name)
            seen[name] = index

        return [self._create_instance(base_vm, d) for d in instances]

    def preflight(self, vms: List[VMConfig]) -> List[str]:
        """Return the names in *vms* that already exist on the provider.

        Checking up front turns "created three of five VMs, then failed" into a
        refusal before anything is created.

        Args:
            vms: Configurations about to be created.

        Returns:
            list[str]: Names that are already registered.
        """
        try:
            existing = set(self.engine.list_vms())
        except Exception:
            return []  # cannot check; the provider will report its own error
        return [vm.name for vm in vms if vm.name in existing]
    
    def _load_batch_file(self, batch_file: Path) -> Dict[str, Any]:
        """Load batch definition file"""
        suffix = batch_file.suffix.lower()
        
        try:
            with open(batch_file, 'r', encoding='utf-8') as f:
                if suffix == '.json':
                    return json.load(f)
                elif suffix in ('.yaml', '.yml'):
                    return yaml.safe_load(f)
                else:
                    raise ValidationError(f"Unsupported batch file format: {suffix}")
        except (IOError, json.JSONDecodeError, yaml.YAMLError) as e:
            raise ValidationError(f"Failed to load batch file: {e}")
    
    def _get_base_vm(self, batch_def: Dict[str, Any]) -> VMConfig:
        """Get base VM configuration"""
        base_vm_ref = batch_def.get('base_vm')
        
        if isinstance(base_vm_ref, dict):
            # Base VM is defined inline
            return VMConfig.from_dict(base_vm_ref)
        elif isinstance(base_vm_ref, str):
            # Base VM is a reference to existing VM or file
            if base_vm_ref.endswith(('.json', '.yaml', '.yml')):
                # Load from file
                return self.engine.import_vm(Path(base_vm_ref))
            else:
                # Read from provider
                return self.engine.read_vm(base_vm_ref)
        else:
            raise ValidationError("base_vm must be either a dictionary configuration or a reference string")
    
    def _create_instance(self, base_vm: VMConfig, instance_def: Dict[str, Any]) -> VMConfig:
        """Create a single VM instance from base with overrides"""
        # Deep copy base VM
        vm = copy.deepcopy(base_vm)
        
        # Apply name override
        if 'name' in instance_def:
            vm.name = instance_def['name']
        
        # Apply CPU overrides
        if 'cpu' in instance_def:
            cpu_overrides = instance_def['cpu']
            if isinstance(cpu_overrides, int):
                vm.cpu.count = cpu_overrides
            elif isinstance(cpu_overrides, dict):
                for key, value in cpu_overrides.items():
                    if hasattr(vm.cpu, key):
                        setattr(vm.cpu, key, value)
        
        # Apply memory overrides
        if 'memory' in instance_def:
            memory_overrides = instance_def['memory']
            if isinstance(memory_overrides, (int, float)):
                vm.memory.mb = int(memory_overrides)
            elif isinstance(memory_overrides, dict):
                for key, value in memory_overrides.items():
                    if hasattr(vm.memory, key):
                        setattr(vm.memory, key, value)
        
        # Apply disk overrides
        if 'disks' in instance_def:
            for i, disk_override in enumerate(instance_def['disks']):
                if i < len(vm.disks):
                    for key, value in disk_override.items():
                        if hasattr(vm.disks[i], key):
                            setattr(vm.disks[i], key, value)
        
        # Apply network overrides
        if 'networks' in instance_def:
            for i, net_override in enumerate(instance_def['networks']):
                if i < len(vm.networks):
                    for key, value in net_override.items():
                        if hasattr(vm.networks[i], key):
                            setattr(vm.networks[i], key, value)
        
        # Apply metadata
        if 'metadata' in instance_def:
            vm.metadata.update(instance_def['metadata'])
        
        # Validate. Errors raise and abort the whole batch before anything is
        # created; warnings are reported and the instance is kept.
        for warning in self.engine.validate_vm(vm):
            if self.on_warning:
                self.on_warning(f"{vm.name}: {warning}")
        
        return vm
    
    def generate_batch_template(self, output_path: Path, format: str = "yaml") -> None:
        """Generate a batch template file"""
        template = {
            "name": "batch-example",
            "description": "Batch VM creation template",
            "base_vm": {
                "name": "ubuntu-base",
                "ostype": "Ubuntu_64",
                "cpu": {"count": 2},
                "memory": {"mb": 2048},
                "firmware": {"type": "bios"},
                "disks": [
                    {
                        "name": "system",
                        "size_mb": 20480,
                        "type": "hdd",
                        "controller": "sata",
                        "bootable": True
                    }
                ],
                "networks": [
                    {
                        "network_type": "nat",
                        "adapter_type": "82540EM"
                    }
                ],
                "boot": {
                    "order": ["disk", "dvd", "none", "none"]
                }
            },
            "instances": [
                {
                    "name": "vm-01",
                    "memory": 4096,
                    "cpu": 4,
                    "metadata": {"role": "web-server"}
                },
                {
                    "name": "vm-02",
                    "memory": 2048,
                    "cpu": 2,
                    "metadata": {"role": "database"}
                },
                {
                    "name": "vm-03",
                    "memory": 8192,
                    "cpu": 8,
                    "disks": [
                        {"size_mb": 40960}
                    ],
                    "metadata": {"role": "file-server"}
                }
            ]
        }
        
        try:
            with open(output_path, 'w', encoding='utf-8') as f:
                if format == 'yaml':
                    yaml.safe_dump(template, f, default_flow_style=False, allow_unicode=True)
                elif format == 'json':
                    json.dump(template, f, indent=2, ensure_ascii=False)
                else:
                    raise ValidationError(f"Unsupported format: {format}")
        except IOError as e:
            raise ValidationError(f"Failed to write template: {e}")
