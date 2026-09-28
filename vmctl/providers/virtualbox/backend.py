# providers/virtualbox/backend.py
"""
VirtualBox backend implementation
"""
import os
import re
import subprocess
import time
from dataclasses import replace
from typing import Callable, Dict, List, Optional, Tuple, cast
from ...core.capabilities import Capabilities
from ...core.plan import Plan, Step
from ...core.storage import StorageLocation, directory
from ...core.translate import Policy
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
from .convert import CloneMediumConverter


class VirtualBoxBackend(BaseProvider):
    """VirtualBox provider backend"""

    REQUIRED_BINARY = "VBoxManage"

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

    def converter(self) -> CloneMediumConverter:
        """Return the ``VBoxManage clonemedium`` converter."""
        return CloneMediumConverter()

    def diagnostics(self):
        """Add the machine folder and, on Linux, the kernel module.

        ``vboxdrv`` not being loaded is the classic VirtualBox failure: every
        ``VBoxManage`` command that only reads still works, and starting a VM fails
        with a driver error -- which reads like a vmctl bug.
        """
        from ...core.doctor import Check

        checks = super().diagnostics()
        checks.append(
            Check(
                "machine folder",
                self.machine_folder or VirtualBoxEmitter.FALLBACK_MACHINE_FOLDER,
            )
        )
        if os.path.isdir("/sys/module"):
            loaded = os.path.isdir("/sys/module/vboxdrv")
            checks.append(
                Check(
                    "vboxdrv module",
                    "loaded" if loaded else "not loaded",
                    loaded,
                    None if loaded else "run 'sudo /sbin/vboxconfig', or modprobe vboxdrv",
                )
            )
        return checks

    def probe(self) -> Capabilities:
        """Refine the declaration by asking this VirtualBox (E-05).

        Two questions only this host can answer:

        * **Which guest OS ids does it know?** The static table is generated from a
          capture of one version (F-29); a host running another may know more or fewer,
          and ``createvm`` refuses an id it does not have.
        * **Which bridged and host-only interfaces exist here?** A config naming
          ``eth0`` is valid everywhere and correct almost nowhere, and the failure
          currently arrives partway through a create.

        Nothing raises: an unanswerable question leaves the static value alone.
        """
        caps = self.capabilities
        ostypes = self._probe_ostypes()
        interfaces = self._probe_interfaces()
        if not ostypes and not interfaces:
            return caps
        asked = []
        if ostypes:
            asked.append(f"{len(ostypes)} guest OS ids")
        if interfaces:
            asked.append(
                ", ".join(f"{len(names)} {mode}" for mode, names in sorted(interfaces.items()))
            )
        return replace(
            caps,
            supported_os_types=ostypes or caps.supported_os_types,
            host_interfaces=interfaces or caps.host_interfaces,
            evidence=f"{caps.evidence} Asked this host directly (E-05): " + "; ".join(asked) + ".",
        )

    def _list(self, what: str) -> str:
        """Return ``VBoxManage list <what>``, or empty when it cannot be run."""
        try:
            result = subprocess.run(
                ["VBoxManage", "list", what], capture_output=True, text=True, check=False
            )
        except (FileNotFoundError, OSError):
            return ""
        return result.stdout if result.returncode == 0 else ""

    def _probe_ostypes(self) -> Tuple[str, ...]:
        """Return every guest OS id and description this host knows."""
        return parse_ostypes(self._list("ostypes"))

    def _probe_interfaces(self) -> Dict[str, Tuple[str, ...]]:
        """Return the bridged and host-only interfaces this host has."""
        found: Dict[str, Tuple[str, ...]] = {}
        bridged = parse_interface_names(self._list("bridgedifs"))
        if bridged:
            found["bridged"] = bridged
        hostonly = parse_interface_names(self._list("hostonlyifs"))
        if hostonly:
            found["hostonly"] = hostonly
        natnets = parse_interface_names(self._list("natnets"))
        if natnets:
            found["natnetwork"] = natnets
        return found

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
                    "VBoxManage is not on PATH",
                    dependency="VirtualBox",
                    install_command="Install VirtualBox and make sure VBoxManage is on "
                    "your PATH",
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

    def storage_location(self) -> StorageLocation:
        """Return VirtualBox's machine folder.

        VirtualBox gives each VM its own subdirectory inside it, so the location
        says so rather than every caller remembering to.
        """
        return directory(
            self.machine_folder or VirtualBoxEmitter.FALLBACK_MACHINE_FOLDER,
            nest_per_vm=True,
        )

    def create_vm(self, vm: VMConfig, execute: bool = True, policy: Policy = Policy.STRICT) -> Plan:
        """Create a new VM from VMConfig.

        Args:
            vm: Configuration to create.
            execute: Run the plan. False returns it unexecuted (dry-run).
            policy: What to do about values VirtualBox does not support.

        Returns:
            Plan: The steps that were, or would be, run. Anything that did not
            translate exactly is in its warnings.
        """
        if execute:
            self.check_supported()
        emitter = VirtualBoxEmitter(
            vm.name,
            location=self.storage_location(),
            capabilities=self._capabilities,
            policy=policy,
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
            on_warning: Where the engine reports validation warnings. Requested
                changes that cannot be applied in place (storage and network
                layout) are on the returned plan's ``warnings``.

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
            location=self.storage_location(),
            capabilities=self._capabilities,
        )
        plan = emitter.emit_modify_vm(current, new_config)

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

    def run_argv(self, step: Step) -> None:
        """Run one VBoxManage command.

        Raises:
            ProviderError: If it fails, naming the command -- VBoxManage's own
                message is usually the useful part, and it goes to stderr.
        """
        self._run_command(self.resolve_argv(list(step.argv or [])))

    def _run_command(self, command: List[str]) -> str:
        """Run a VBoxManage command."""
        try:
            result = subprocess.run(command, capture_output=True, text=True, check=True)
            return result.stdout
        except subprocess.CalledProcessError as e:
            raise ProviderError(f"Command failed: {' '.join(command)}\nError: {e.stderr}")


# ---------------------------------------------------------------------------
# Reading `VBoxManage list` output
#
# Separate functions, and not methods, because they are pure text -> data: the tests
# feed them captured output instead of needing VirtualBox, which is the same shape as
# the parser's MediumProbe (T-04).
# ---------------------------------------------------------------------------


def parse_ostypes(text: str) -> Tuple[str, ...]:
    """Return every guest OS id and description in a ``list ostypes`` listing.

    Both spellings, because ``createvm`` takes the id and ``showvminfo`` reports the
    description, and a config may legitimately hold either (A-05).
    """
    found: List[str] = []
    for line in text.splitlines():
        match = re.match(r"^ID / Description:\s*(\S+)\s*--\s*(.+)$", line.strip())
        if match:
            found.extend([match.group(1), match.group(2).strip()])
    return tuple(found)


def parse_interface_names(text: str) -> Tuple[str, ...]:
    """Return the names in a ``list bridgedifs``, ``hostonlyifs`` or ``natnets`` listing.

    The listing is stanzas of ``Key: value``, and the name is the one that matters:
    VirtualBox's own ``--bridgeadapter`` takes exactly that string.

    All three listings spell it ``Name:`` on 7.1.18 -- checked against captures, after
    writing ``NetworkName:`` for NAT networks from memory and finding it wrong.
    """
    found: List[str] = []
    for line in text.splitlines():
        if line.lower().startswith("name:"):
            name = line.split(":", 1)[1].strip()
            if name and name not in found:
                found.append(name)
    return tuple(found)
