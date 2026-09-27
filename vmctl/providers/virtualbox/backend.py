# providers/virtualbox/backend.py
"""
VirtualBox backend implementation
"""
import subprocess
import time
from pathlib import Path
from typing import Callable, List, Optional, Dict, Any
from ...core.vmconfig import VMConfig
from ...core.exceptions import ProviderError
from ..base import BaseProvider
from .parser import VirtualBoxParser
from .emitter import VirtualBoxEmitter
from .capabilities import VirtualBoxCapabilities


class VirtualBoxBackend(BaseProvider):
    """VirtualBox provider backend"""

    def __init__(self):
        """Initialise the VirtualBox backend.

        Creates a :class:`VirtualBoxParser` instance for reading VM
        configurations and loads the static provider capabilities dict. The
        default machine folder is queried lazily on first use.

        Raises:
            ProviderError: If ``VBoxManage`` is not found during the first
                operation (deferred until actual use).
        """
        self.parser = VirtualBoxParser()
        self._capabilities = VirtualBoxCapabilities.get_capabilities()
        self._machine_folder = None

    @property
    def machine_folder(self) -> Optional[str]:
        """VirtualBox's configured default machine folder, or None.

        Queried once from ``VBoxManage list systemproperties`` and cached.
        Hardcoding ``~/VirtualBox VMs`` put new media outside the VM's own
        directory on any installation with a customised folder (F-13).
        """
        if self._machine_folder is None:
            self._machine_folder = self._query_machine_folder()
        return self._machine_folder or None

    def _query_machine_folder(self) -> str:
        """Read the default machine folder from VirtualBox.

        Returns:
            str: The configured folder, or ``""`` when it cannot be determined
            (the emitter then falls back to VirtualBox's documented default).
        """
        try:
            result = subprocess.run(
                ["VBoxManage", "list", "systemproperties"],
                capture_output=True, text=True, check=False
            )
            if result.returncode != 0:
                return ""
            for line in result.stdout.splitlines():
                if line.lower().startswith("default machine folder:"):
                    return line.split(":", 1)[1].strip()
        except (FileNotFoundError, OSError):
            pass
        return ""

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
        emitter = VirtualBoxEmitter(vm.name, machine_folder=self.machine_folder)
        commands = emitter.emit_create_vm(vm)

        if execute:
            for cmd in commands:
                self._run_command(cmd)

        return commands

    def edit_vm(
        self,
        vm_name: str,
        new_config: VMConfig,
        execute: bool = True,
        on_warning: Optional[Callable[[str], None]] = None,
    ) -> List[List[str]]:
        """Apply a configuration to an existing VM with `modifyvm`.

        Reads the VM's current state and emits only the flags that differ, so
        editing one setting runs one command.

        Args:
            vm_name: The VM to change.
            new_config: Desired configuration.
            execute: Apply the change. False returns the commands only.
            on_warning: Where to report requested changes that cannot be applied
                in place (storage and network layout).

        Returns:
            list: The commands that were, or would be, executed.

        Raises:
            ProviderError: If the VM does not exist, is running, or a command
                fails.
        """
        if not self.vm_exists(vm_name):
            raise ProviderError(f"VM '{vm_name}' does not exist")

        # VirtualBox refuses most modifyvm settings on a running VM, and the
        # ones it accepts are silently deferred. Refuse rather than half-apply.
        status = self.get_vm_status(vm_name)
        if status == "running":
            raise ProviderError(
                f"VM '{vm_name}' is running; stop it before editing "
                f"(vmctl stop {vm_name} --wait 60)"
            )

        current = self.read_vm(vm_name)
        emitter = VirtualBoxEmitter(vm_name, machine_folder=self.machine_folder)
        commands, unsupported = emitter.emit_modify_vm(current, new_config)

        if unsupported and on_warning:
            for item in unsupported:
                on_warning(
                    f"{item} differs from the VM but cannot be changed in "
                    f"place; it was left alone"
                )

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

    def stop_vm(self, vm_name: str, force: bool = False,
                wait: int = 0) -> bool:
        """Stop a running VM.

        Args:
            vm_name: VM to stop.
            force: Power off immediately instead of asking the guest.
            wait: Seconds to wait for the VM to actually stop. A graceful stop
                only *asks* the guest, so without waiting the command returns
                while the VM is still running (L-06). 0 means do not wait.

        Returns:
            True if the VM is stopped, or the request was sent and ``wait`` is 0.

        Raises:
            ProviderError: If the VM does not exist, the request fails, or the
                VM is still running after ``wait`` seconds.
        """
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

            if wait:
                deadline = time.monotonic() + wait
                while time.monotonic() < deadline:
                    if self.get_vm_status(vm_name) != "running":
                        return True
                    time.sleep(1)
                raise ProviderError(
                    f"VM '{vm_name}' was still running {wait}s after the stop "
                    f"request. Use --force to power it off."
                )

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
