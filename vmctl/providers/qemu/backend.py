"""
QEMU backend: a VM is a directory, and running it is a process.

This is the provider where "the hypervisor remembers things for you" turns out to be
an assumption. VirtualBox has a registry and libvirt has a daemon; QEMU has neither,
so three questions have to be answered differently:

* **What is a VM?** A directory containing the run script and the disks. Listing
  VMs is listing directories that have a script in them, and reading one is parsing
  that script -- not a vmctl-invented index, because an index would be a second
  source of truth that could disagree with the thing that actually starts the VM.
* **Is it running?** The script writes a pidfile and daemonizes, so the answer is
  whether that pid is alive. There is nothing else to ask.
* **How is it stopped?** A signal. A clean shutdown needs the guest's cooperation
  through a monitor socket, so ``stop`` without ``--force`` sends SIGTERM, which
  QEMU turns into a power button press when the guest supports ACPI.

The state directory follows the same rule as libvirt's image directory (A-09): it is
per-user, because a user-run QEMU cannot write to a system-wide one.
"""

import os
import re
import shutil
import signal
import subprocess
import time
from dataclasses import replace
from typing import Any, Callable, Dict, List, Optional, Tuple

from ...core.capabilities import Capabilities
from ...core.hostinfo import host_bridges
from ...core.exceptions import (
    DependencyError,
    ProviderError,
    VMNotFoundError,
    VMStateError,
)
from ...core.plan import Plan, StepKind
from ...core.storage import StorageLocation, directory
from ...core.translate import Policy
from ...core.vmconfig import VMConfig
from ..base import BaseProvider
from .capabilities import QemuCapabilities
from .convert import QemuImgConverter
from .emitter import QemuEmitter
from .parser import QemuParser

#: The QEMU binaries to look for, most specific first.
BINARIES = ("/usr/libexec/qemu-kvm", "qemu-system-x86_64", "qemu-kvm")

#: The file that makes a directory a VM.
SCRIPT_NAME = "run.sh"


class QemuBackend(BaseProvider):
    """Plain QEMU provider backend."""

    REQUIRED_BINARY = "qemu-img"

    def __init__(self, state_dir: Optional[str] = None, binary: Optional[str] = None) -> None:
        """Initialise the backend.

        Args:
            state_dir: Where VMs live. Defaults to ``$XDG_DATA_HOME/vmctl/qemu``.
            binary: The QEMU binary to run. Defaults to the first one found.
        """
        self.parser = QemuParser()
        self._capabilities: Capabilities = QemuCapabilities.get()
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
        self.state_dir = state_dir or os.path.join(base, "vmctl", "qemu")
        self._binary = binary
        self._version: Optional[str] = None

    # -- identity ------------------------------------------------------------

    @property
    def name(self) -> str:
        """Return provider name."""
        return "qemu"

    @property
    def capabilities(self) -> Capabilities:
        """Return provider capabilities."""
        return self._capabilities

    def converter(self) -> QemuImgConverter:
        """Return the ``qemu-img`` converter."""
        return QemuImgConverter()

    @property
    def binary(self) -> str:
        """Return the QEMU binary to run.

        Raises:
            DependencyError: If none of the usual names is present.
        """
        if self._binary:
            return self._binary
        for candidate in BINARIES:
            if candidate.startswith("/") and os.path.exists(candidate):
                self._binary = candidate
                return candidate
            found = shutil.which(candidate)
            if found:
                self._binary = found
                return found
        raise DependencyError(
            "no QEMU binary found",
            dependency="qemu-kvm",
            install_command="Install qemu-kvm (or qemu-system-x86_64)",
        )

    def version(self) -> str:
        """Return the QEMU version, e.g. ``"10.1.0"``.

        Raises:
            DependencyError: If QEMU cannot be run.
        """
        if self._version is None:
            import re

            out = subprocess.run([self.binary, "--version"], capture_output=True, text=True).stdout
            match = re.search(r"(\d+\.\d+\.\d+)", out)
            self._version = match.group(1) if match else out.strip().splitlines()[0]
        return self._version

    @property
    def accel(self) -> str:
        """``kvm`` when ``/dev/kvm`` is there, otherwise ``tcg``.

        The same question the libvirt provider asks to choose a domain type. Asking
        for kvm without it makes QEMU print two errors and fall back, which is a
        worse answer than saying tcg in the first place.
        """
        return "kvm" if os.path.exists("/dev/kvm") else "tcg"

    def probe(self) -> Capabilities:
        """Refine the declaration by asking this QEMU binary (E-05).

        A QEMU build is a build-time selection of devices, so the questions worth
        asking are which machine types and which network devices it has -- both of
        which vmctl otherwise has to declare from a capture. The attach matrix is
        deliberately *not* re-derived from ``-device help``: a device existing is not
        the same as QEMU accepting it on a bus, which is what the recorded matrix
        measured by starting the machine.

        Nothing raises: an unanswerable question leaves the static value alone.
        """
        caps = self.capabilities
        machines = self._probe_machines()
        nics = self._probe_nic_models()
        # `-netdev bridge,br=NAME` takes a host bridge, so the same list libvirt needs
        # applies here -- which is why the answer lives in core rather than in either
        # provider.
        bridges = host_bridges()
        if not machines and not nics and not bridges:
            return caps
        asked = []
        if machines:
            asked.append(f"{len(machines)} machine types")
        if nics:
            asked.append(f"{len(nics)} network devices")
        if bridges:
            asked.append(f"{len(bridges)} host bridges")
        return replace(
            caps,
            machine_types=machines or caps.machine_types,
            nic_models=nics or caps.nic_models,
            host_interfaces={"bridged": bridges} if bridges else caps.host_interfaces,
            evidence=f"{caps.evidence} Asked this binary directly (E-05): "
            + "; ".join(asked)
            + ".",
        )

    def _help(self, *args: str) -> str:
        """Return the output of a ``-... help`` query, or empty when it cannot run."""
        try:
            result = subprocess.run(
                [self.binary, *args], capture_output=True, text=True, check=False
            )
        except (OSError, DependencyError):
            return ""
        return (result.stdout or "") + (result.stderr or "")

    def _probe_machines(self) -> Tuple[str, ...]:
        """Return the machine types this binary offers."""
        found: List[str] = []
        for line in self._help("-machine", "help").splitlines()[1:]:
            name = line.split()[0] if line.strip() else ""
            # `none` is a machine with no hardware at all; it cannot run a VM.
            if name and name != "none" and name not in found:
                found.append(name)
        return tuple(found)

    def _probe_nic_models(self) -> Dict[Any, str]:
        """Return the NIC models this binary actually has.

        F-37 is the reason this exists: the static table was written by reading
        libvirt's and claimed three devices this build does not have.
        """
        from .tables import NIC_MODEL_TO_QEMU

        available = {
            match.group(1)
            for match in re.finditer(r'^name "([^"]+)"', self._help("-device", "help"), re.M)
        }
        if not available:
            return {}
        return {model: native for model, native in NIC_MODEL_TO_QEMU.items() if native in available}

    def storage_location(self) -> StorageLocation:
        """Return the state directory, one subdirectory per VM.

        A VM here *is* its directory, so the per-VM nesting is not a convention --
        it is what makes listing and deleting possible at all.
        """
        return directory(self.state_dir, nest_per_vm=True)

    def _emitter(self, vm_name: str, policy: Policy = Policy.STRICT) -> QemuEmitter:
        return QemuEmitter(
            vm_name,
            location=self.storage_location(),
            binary=self.binary,
            accel=self.accel,
            capabilities=self._capabilities,
            policy=policy,
        )

    def probe_medium(self, path: str) -> Dict[str, Any]:
        """Return a disk image's properties, read with ``qemu-img info``.

        Args:
            path: Path to the image.

        Returns:
            dict: ``size_mb``, ``format`` and ``variant``. Zero and the native
            format when the image cannot be read, which is what happens for one
            that does not exist yet.
        """
        import json

        from ...core.devices import Allocation

        fallback = {
            "size_mb": 0,
            "format": self._capabilities.native_format,
            "variant": Allocation.THIN,
        }
        try:
            result = subprocess.run(
                # -U reads an image another process has open. Without it,
                # inspecting a *running* VM's disk fails with "Failed to get
                # shared write lock", the size came back 0, and the model then
                # invented a 20 GB default in its place (F-32).
                ["qemu-img", "info", "-U", "--output=json", path],
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError:
            return fallback
        if result.returncode != 0:
            return fallback
        try:
            info = json.loads(result.stdout)
        except ValueError:
            return fallback

        from .tables import DRIVER_TO_FORMAT

        virtual = int(info.get("virtual-size") or 0)
        actual = int(info.get("actual-size") or 0)
        return {
            "size_mb": max(0, virtual // (1024 * 1024)),
            "format": DRIVER_TO_FORMAT.get(info.get("format", ""), fallback["format"]),
            "variant": (
                Allocation.THICK if virtual and actual >= virtual * 0.9 else Allocation.THIN
            ),
        }

    # -- the directory -------------------------------------------------------

    def vm_dir(self, vm_name: str) -> str:
        """Return a VM's directory."""
        return self.storage_location().directory_for(vm_name)

    def script_path(self, vm_name: str) -> str:
        """Return a VM's run script, which is its configuration."""
        return os.path.join(self.vm_dir(vm_name), SCRIPT_NAME)

    def pidfile_path(self, vm_name: str) -> str:
        """Return a VM's pidfile, which is how "running" is answered."""
        return os.path.join(self.vm_dir(vm_name), "qemu.pid")

    def _pid(self, vm_name: str) -> Optional[int]:
        """Return the pid of a running VM, or None.

        A pidfile left behind by a process that has gone counts as not running,
        which is the common case after a host reboot.
        """
        try:
            with open(self.pidfile_path(vm_name)) as handle:
                pid = int(handle.read().strip())
        except (OSError, ValueError):
            return None
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return None
        except PermissionError:
            return pid  # alive and someone else's
        return pid

    # -- transport -----------------------------------------------------------

    def run_plan(self, plan: Plan) -> None:
        """Execute every step in a plan, in order.

        Raises:
            ProviderError: If a step fails or is a kind this provider cannot run.
        """
        for step in plan:
            if step.kind is StepKind.WRITE_FILE and step.path is not None:
                step.path.parent.mkdir(parents=True, exist_ok=True)
                step.path.write_text(step.content or "")
            elif step.kind is StepKind.EXEC and step.argv:
                if step.argv[0] == "mkdir":
                    # A plan reads as a shell script; it does not have to be run as
                    # one, and `mkdir -p` is not portable.
                    os.makedirs(step.argv[-1], exist_ok=True)
                    continue
                result = subprocess.run(step.argv, capture_output=True, text=True)
                if result.returncode != 0:
                    raise ProviderError(
                        f"{step.description} failed: "
                        f"{result.stderr.strip() or result.stdout.strip()}"
                    )
            else:
                raise ProviderError(
                    f"the qemu provider cannot run a {step.kind.value} step "
                    f"({step.description})"
                )

    # -- lifecycle -----------------------------------------------------------

    def list_vms(self) -> List[str]:
        """List every VM in the state directory.

        A directory with a run script in it is a VM; anything else there is not.
        """
        try:
            entries = sorted(os.listdir(self.state_dir))
        except FileNotFoundError:
            return []
        return [
            name
            for name in entries
            if os.path.isfile(os.path.join(self.state_dir, name, SCRIPT_NAME))
        ]

    def vm_exists(self, vm_name: str) -> bool:
        """Whether this VM's script exists."""
        return os.path.isfile(self.script_path(vm_name))

    def read_vm(self, vm_name: str) -> VMConfig:
        """Read a VM's command line back into a configuration."""
        if not self.vm_exists(vm_name):
            raise VMNotFoundError(vm_name)
        with open(self.script_path(vm_name)) as handle:
            text = handle.read()
        return self.parser.parse_script(vm_name, text, probe=self.probe_medium)

    def create_vm(self, vm: VMConfig, execute: bool = True, policy: Policy = Policy.STRICT) -> Plan:
        """Create a VM: its directory, its disks and its command line.

        Nothing is started, which matches the other providers -- and here it is
        also the only distinction there is between an existing VM and a running one.
        """
        plan = self._emitter(vm.name, policy).emit_create_vm(vm)
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
        """Rewrite a VM's command line.

        There is nothing to change in place: the script *is* the configuration. A
        running VM keeps running on the old one, so the plan says so.
        """
        if not self.vm_exists(vm_name):
            raise VMNotFoundError(vm_name)
        current = self.read_vm(vm_name)
        plan = self._emitter(vm_name).emit_modify_vm(current, new_config)
        if self._pid(vm_name) is not None:
            plan.warn(
                f"{vm_name} is running on the previous command line; the changes "
                f"take effect when it is stopped and started again"
            )
        if execute:
            self.run_plan(plan)
        return plan

    def delete_vm(self, vm_name: str) -> bool:
        """Stop the VM if it is running and remove its directory.

        Everything a QEMU VM owns is in that directory -- script, disks, pidfile --
        so deleting it is complete by construction. An image the user attached from
        elsewhere was never inside it, and is left alone (the lesson of F-28).
        """
        if not self.vm_exists(vm_name):
            raise VMNotFoundError(vm_name)
        if self._pid(vm_name) is not None:
            self.stop_vm(vm_name, force=True, wait=10)
        shutil.rmtree(self.vm_dir(vm_name), ignore_errors=True)
        if self.vm_exists(vm_name):
            raise ProviderError(f"failed to remove {self.vm_dir(vm_name)}")
        return True

    def start_vm(self, vm_name: str) -> bool:
        """Run the VM's script, which daemonizes and writes its pidfile."""
        if not self.vm_exists(vm_name):
            raise VMNotFoundError(vm_name)
        if self._pid(vm_name) is not None:
            return True
        script = self.script_path(vm_name)
        result = subprocess.run(["sh", script], capture_output=True, text=True)
        if result.returncode != 0:
            raise ProviderError(
                f"QEMU refused to start {vm_name!r}: "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        return True

    def stop_vm(self, vm_name: str, force: bool = False, wait: int = 0) -> bool:
        """Stop a running VM with a signal.

        SIGTERM is QEMU's power button: with an ACPI-aware guest it is a clean
        shutdown request, and the process exits when the guest does. ``force``
        sends SIGKILL, which is pulling the plug.
        """
        if not self.vm_exists(vm_name):
            raise VMNotFoundError(vm_name)
        pid = self._pid(vm_name)
        if pid is None:
            return True
        os.kill(pid, signal.SIGKILL if force else signal.SIGTERM)

        if wait:
            deadline = time.monotonic() + wait
            while time.monotonic() < deadline:
                if self._pid(vm_name) is None:
                    return True
                time.sleep(0.2)
            raise VMStateError(
                f"{vm_name!r} was still running {wait}s after the stop request",
                vm_name=vm_name,
                current_state="running",
                required_state="stopped",
            )
        return True

    def get_vm_status(self, vm_name: str) -> str:
        """Return ``running`` or ``stopped``.

        QEMU has paused and saved states too, but reading them needs a monitor
        socket; claiming to know would be worse than the two states a pidfile can
        actually tell apart.
        """
        if not self.vm_exists(vm_name):
            raise VMNotFoundError(vm_name)
        return "running" if self._pid(vm_name) is not None else "stopped"
