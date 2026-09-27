# providers/virtualbox/backend.py
"""
VirtualBox backend implementation
"""
import re
import subprocess
import time
from typing import Callable, List, Optional, cast
from ...core.capabilities import Capabilities
from ...core.plan import Plan, StepKind
from ...core.vmconfig import VMConfig
from ...core.exceptions import (
    DependencyError,
    ProviderError,
    VMNotFoundError,
    VMStateError,
)
from ..base import BaseProvider
from .parser import VirtualBoxParser
from .emitter import VirtualBoxEmitter
from .capabilities import VirtualBoxCapabilities


class VirtualBoxBackend(BaseProvider):
    """VirtualBox provider backend"""

    def __init__(self) -> None:
        """Initialise the VirtualBox backend.

        Creates a :class:`VirtualBoxParser` instance for reading VM
        configurations and loads the static provider capabilities dict. The
        default machine folder is queried lazily on first use.

        Raises:
            ProviderError: If ``VBoxManage`` is not found during the first
                operation (deferred until actual use).
        """
        self.parser: VirtualBoxParser = VirtualBoxParser()
        self._capabilities: Capabilities = VirtualBoxCapabilities.get()
        self._machine_folder: Optional[str] = None
        self._version: Optional[str] = None

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
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                return ""
            for line in result.stdout.splitlines():
                if line.lower().startswith("default machine folder:"):
                    return line.split(":", 1)[1].strip()
        except (FileNotFoundError, OSError):
            pass
        return ""

    def version(self) -> str:
        """Return the VirtualBox version, e.g. ``"7.1.18"``.

        Cached after the first call.

        Raises:
            DependencyError: If ``VBoxManage`` cannot be run.
        """
        if self._version is None:
            try:
                result = subprocess.run(
                    ["VBoxManage", "--version"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
            except FileNotFoundError:
                raise DependencyError(
                    "VBoxManage",
                    reason="not found on PATH",
                    install_hint="Install VirtualBox and make sure VBoxManage is " "on your PATH.",
                )
            if result.returncode != 0:
                raise DependencyError(
                    f"VBoxManage --version failed with exit code " f"{result.returncode}",
                    dependency="VBoxManage",
                    context={"stderr": result.stderr.strip()},
                )
            # "7.1.18r173720" -> "7.1.18"
            match = re.match(r"(\d+\.\d+\.\d+)", result.stdout.strip())
            self._version = match.group(1) if match else result.stdout.strip()
        return cast(str, self._version)

    def check_supported(self) -> None:
        """Verify the installed VirtualBox meets this provider's floor.

        Raises:
            DependencyError: If VBoxManage is missing or older than the declared
                ``min_version``. Failing here, with the version in the message,
                beats failing later on an option that release does not have
                (H-07).
        """
        floor = self._capabilities.min_version
        if not floor:
            return
        found = self.version()

        def parts(v):
            return tuple(int(x) for x in re.findall(r"\d+", v)[:3])

        if not parts(found):
            return  # unrecognised version string; let the provider speak for itself

        if parts(found) < parts(floor):
            raise DependencyError(
                f"VirtualBox {found} is older than the required {floor}",
                dependency="VirtualBox",
                version_required=floor,
                version_found=found,
                install_command=f"Upgrade VirtualBox to {floor} or later",
            )

    @property
    def name(self) -> str:
        """Return provider name."""
        return "virtualbox"

    @property
    def capabilities(self) -> Capabilities:
        """Return provider capabilities."""
        return self._capabilities

    def list_vms(self) -> List[str]:
        """List all VMs managed by VirtualBox."""
        try:
            result = subprocess.run(
                ["VBoxManage", "list", "vms"], capture_output=True, text=True, check=True
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
            raise DependencyError(
                "VBoxManage is not available",
                dependency="VBoxManage",
                install_command="Install VirtualBox and ensure VBoxManage is on your PATH",
            )

    def read_vm(self, vm_name: str) -> VMConfig:
        """Read VM configuration from VirtualBox."""
        vm: VMConfig = self.parser.parse_vm(vm_name)
        return vm

    def create_vm(self, vm: VMConfig, execute: bool = True) -> Plan:
        """Create a new VM from VMConfig.

        Args:
            vm: Configuration to create.
            execute: Run the plan. False returns it unexecuted (dry-run).

        Returns:
            Plan: The steps that were, or would be, run.
        """
        if execute:
            self.check_supported()
        emitter = VirtualBoxEmitter(
            vm.name,
            machine_folder=self.machine_folder,
            capabilities=self._capabilities,
        )
        plan = emitter.emit_create_vm(vm)

        if execute:
            self.run_plan(plan)

        return plan

    def edit_vm(
        self,
        vm_name: str,
        new_config: VMConfig,
        execute: bool = True,
        on_warning: Optional[Callable[[str], None]] = None,
    ) -> Plan:
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
            Plan: The steps that were, or would be, run.

        Raises:
            ProviderError: If the VM does not exist, is running, or a command
                fails.
        """
        if execute:
            self.check_supported()

        if not self.vm_exists(vm_name):
            raise VMNotFoundError(vm_name)

        # VirtualBox refuses most modifyvm settings on a running VM, and the
        # ones it accepts are silently deferred. Refuse rather than half-apply.
        status = self.get_vm_status(vm_name)
        if status == "running":
            raise VMStateError(
                f"VM '{vm_name}' is running; it must be stopped before editing",
                vm_name=vm_name,
                current_state="running",
                required_state="stopped",
            )

        current = self.read_vm(vm_name)
        emitter = VirtualBoxEmitter(
            vm_name,
            machine_folder=self.machine_folder,
            capabilities=self._capabilities,
        )
        plan = emitter.emit_modify_vm(current, new_config)

        if on_warning:
            for message in plan.warnings:
                on_warning(message)

        if execute:
            self.run_plan(plan)

        return plan

    def delete_vm(self, vm_name: str) -> bool:
        """Delete a VM and its associated files."""
        try:
            # Check if VM exists
            if not self.vm_exists(vm_name):
                raise VMNotFoundError(vm_name)

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
            raise DependencyError(
                "VBoxManage is not available",
                dependency="VBoxManage",
                install_command="Install VirtualBox and ensure VBoxManage is on your PATH",
            )

    def start_vm(self, vm_name: str) -> bool:
        """Start a VM."""
        try:
            if not self.vm_exists(vm_name):
                raise VMNotFoundError(vm_name)

            status = self.get_vm_status(vm_name)
            if status == "running":
                return True  # Already running

            cmd = ["VBoxManage", "startvm", vm_name, "--type", "headless"]
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)

            if result.returncode != 0:
                raise ProviderError(f"Failed to start VM: {result.stderr}")

            return True

        except FileNotFoundError:
            raise DependencyError(
                "VBoxManage is not available",
                dependency="VBoxManage",
                install_command="Install VirtualBox and ensure VBoxManage is on your PATH",
            )

    def stop_vm(self, vm_name: str, force: bool = False, wait: int = 0) -> bool:
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
                raise VMNotFoundError(vm_name)

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
            raise DependencyError(
                "VBoxManage is not available",
                dependency="VBoxManage",
                install_command="Install VirtualBox and ensure VBoxManage is on your PATH",
            )

    def get_vm_status(self, vm_name: str) -> str:
        """Get current VM status."""
        try:
            result = subprocess.run(
                ["VBoxManage", "showvminfo", vm_name, "--machinereadable"],
                capture_output=True,
                text=True,
                check=False,
            )

            if result.returncode != 0:
                raise VMNotFoundError(vm_name)

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
            raise DependencyError(
                "VBoxManage is not available",
                dependency="VBoxManage",
                install_command="Install VirtualBox and ensure VBoxManage is on your PATH",
            )

    def run_plan(self, plan: Plan) -> None:
        """Execute every step in a plan, in order.

        Args:
            plan: The plan to run.

        Raises:
            ProviderError: If a step fails, or the plan contains a kind this
                provider cannot run.
        """
        for step in plan:
            if step.kind is StepKind.EXEC and step.argv:
                self._run_command(step.argv)
            elif step.kind is StepKind.WRITE_FILE and step.path is not None:
                step.path.parent.mkdir(parents=True, exist_ok=True)
                step.path.write_text(step.content or "")
            else:
                raise ProviderError(
                    f"the virtualbox provider cannot run a "
                    f"{step.kind.value} step ({step.description})"
                )

    def _run_command(self, command: List[str]) -> str:
        """Run a VBoxManage command."""
        try:
            result = subprocess.run(command, capture_output=True, text=True, check=True)
            return result.stdout
        except subprocess.CalledProcessError as e:
            raise ProviderError(f"Command failed: {' '.join(command)}\nError: {e.stderr}")
