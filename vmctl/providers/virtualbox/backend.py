# providers/virtualbox/backend.py
"""
VirtualBox backend implementation
"""
import subprocess
from pathlib import Path
from typing import List, Optional, Dict, Any
from ...core.vmconfig import VMConfig
from ...core.exceptions import ProviderError
from ..base import BaseProvider
from .parser import VirtualBoxParser
from .emitter import VirtualBoxEmitter
from .capabilities import VirtualBoxCapabilities


class VirtualBoxBackend(BaseProvider):
    """VirtualBox provider backend"""

    def __init__(self):
        self.parser = VirtualBoxParser()
        self._capabilities = VirtualBoxCapabilities.get_capabilities()

    @property
    def name(self) -> str:
        """Return provider name."""
        return "virtualbox"

    @property
    def capabilities(self) -> Dict[str, Any]:
        """Return provider capabilities."""
        return self._capabilities

    def list_vms(self) -> List[str]:
        """List all VMs managed by VirtualBox."""
        try:
            result = subprocess.run(
                ["VBoxManage", "list", "vms"],
                capture_output=True,
                text=True,
                check=True
            )
            vms = []
            for line in result.stdout.strip().splitlines():
                if line:
                    # Format: "VM Name" {uuid}
                    # Extract name between quotes
                    if line.startswith('"'):
                        name = line.split('"')[1]
                        vms.append(name)
            return vms
        except subprocess.CalledProcessError as e:
            raise ProviderError(f"Failed to list VMs: {e.stderr}")
        except FileNotFoundError:
            raise ProviderError("VBoxManage not found. Is VirtualBox installed?")

    def read_vm(self, vm_name: str) -> VMConfig:
        """Read VM configuration from VirtualBox."""
        return self.parser.parse_vm(vm_name)

    def create_vm(self, vm: VMConfig, execute: bool = True) -> List[List[str]]:
        """Create a new VM from VMConfig."""
        emitter = VirtualBoxEmitter(vm.name)
        commands = emitter.emit_create_vm(vm)

        if execute:
            for cmd in commands:
                self._run_command(cmd)

        return commands

    def delete_vm(self, vm_name: str) -> bool:
        """Delete a VM and its associated files."""
        try:
            # Check if VM exists
            if not self.vm_exists(vm_name):
                raise ProviderError(f"VM '{vm_name}' does not exist")

            # Stop VM if running
            status = self.get_vm_status(vm_name)
            if status == "running":
                self.stop_vm(vm_name, force=True)

            # Delete VM
            cmd = ["VBoxManage", "unregistervm", vm_name, "--delete"]
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)

            if result.returncode != 0:
                raise ProviderError(f"Failed to delete VM: {result.stderr}")

            return True

        except FileNotFoundError:
            raise ProviderError("VBoxManage not found")

    def start_vm(self, vm_name: str) -> bool:
        """Start a VM."""
        try:
            if not self.vm_exists(vm_name):
                raise ProviderError(f"VM '{vm_name}' does not exist")

            status = self.get_vm_status(vm_name)
            if status == "running":
                return True  # Already running

            cmd = ["VBoxManage", "startvm", vm_name, "--type", "headless"]
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)

            if result.returncode != 0:
                raise ProviderError(f"Failed to start VM: {result.stderr}")

            return True

        except FileNotFoundError:
            raise ProviderError("VBoxManage not found")

    def stop_vm(self, vm_name: str, force: bool = False) -> bool:
        """Stop a running VM."""
        try:
            if not self.vm_exists(vm_name):
                raise ProviderError(f"VM '{vm_name}' does not exist")

            status = self.get_vm_status(vm_name)
            if status != "running":
                return True  # Already stopped

            if force:
                cmd = ["VBoxManage", "controlvm", vm_name, "poweroff"]
            else:
                cmd = ["VBoxManage", "controlvm", vm_name, "acpipowerbutton"]

            result = subprocess.run(cmd, capture_output=True, text=True, check=False)

            if result.returncode != 0:
                raise ProviderError(f"Failed to stop VM: {result.stderr}")

            return True

        except FileNotFoundError:
            raise ProviderError("VBoxManage not found")

    def get_vm_status(self, vm_name: str) -> str:
        """Get current VM status."""
        try:
            result = subprocess.run(
                ["VBoxManage", "showvminfo", vm_name, "--machinereadable"],
                capture_output=True,
                text=True,
                check=False
            )

            if result.returncode != 0:
                raise ProviderError(f"VM '{vm_name}' not found")

            # Parse VMState from output
            for line in result.stdout.splitlines():
                if line.startswith("VMState="):
                    state = line.split("=")[1].strip('"')
                    # Map VirtualBox states to common states
                    state_map = {
                        "running": "running",
                        "poweroff": "stopped",
                        "saved": "saved",
                        "paused": "paused",
                        "aborted": "aborted",
                        "stopping": "stopping",
                        "starting": "starting",
                    }
                    return state_map.get(state, state)

            return "unknown"

        except FileNotFoundError:
            raise ProviderError("VBoxManage not found")

    def _run_command(self, command: List[str]) -> str:
        """Run a VBoxManage command."""
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=True
            )
            return result.stdout
        except subprocess.CalledProcessError as e:
            raise ProviderError(f"Command failed: {' '.join(command)}\nError: {e.stderr}")
