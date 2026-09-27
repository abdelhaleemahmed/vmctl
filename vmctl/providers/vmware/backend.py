"""
VMware backend: the ``.vmx`` is the VM, and ``vmrun`` is the only verb.

Like QEMU and unlike VirtualBox or libvirt, VMware Workstation keeps no registry of
VMs: a VM is a directory with a ``.vmx`` in it, and the product's inventory is
whatever the GUI happens to have opened. So the same answers as the QEMU provider
apply -- listing VMs is listing directories, and reading one is parsing its file --
with one difference: ``vmrun list`` *does* report which VMs are running, so the
running state comes from the product rather than from a pidfile.

On Windows the tools are not on PATH, so the install directory is found rather than
assumed, and the path separator comes from the storage location (A-09) instead of
from the machine vmctl happens to run on.
"""

import os
import re
import shutil
import subprocess
import time
from typing import Any, Callable, Dict, List, Optional

from ...core.capabilities import Capabilities
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
from .capabilities import VMwareCapabilities
from .convert import VDiskManagerConverter
from .emitter import VMwareEmitter
from .parser import VMwareParser

#: Where Workstation installs its tools. The Windows path is checked first because
#: that is where vmrun is *not* on PATH.
TOOL_DIRS = (
    r"C:\Program Files\VMware\VMware Workstation",
    r"C:\Program Files (x86)\VMware\VMware Workstation",
    "/usr/bin",
    "/Applications/VMware Fusion.app/Contents/Public",
)

#: The extension that makes a directory a VM.
VMX_SUFFIX = ".vmx"


class VMwareBackend(BaseProvider):
    """VMware Workstation / Fusion provider backend."""

    REQUIRED_BINARY = "vmrun"

    def __init__(
        self,
        state_dir: Optional[str] = None,
        tools_dir: Optional[str] = None,
        host_type: str = "ws",
    ) -> None:
        """Initialise the backend.

        Args:
            state_dir: Where VMs live. Defaults to the documents folder Workstation
                itself uses.
            tools_dir: Where ``vmrun`` and ``vmware-vdiskmanager`` are.
            host_type: ``ws`` for Workstation, ``fusion`` for Fusion, ``player``.
        """
        self.parser = VMwareParser()
        self._capabilities: Capabilities = VMwareCapabilities.get()
        self.host_type = host_type
        self._tools_dir = tools_dir
        # An explicitly passed directory (including "" for "on PATH") is not
        # searched for again.
        self._searched = tools_dir is not None
        self.state_dir = state_dir or os.path.expanduser(
            os.path.join("~", "Documents", "Virtual Machines")
        )
        self._version: Optional[str] = None

    # -- identity ------------------------------------------------------------

    @property
    def name(self) -> str:
        """Return provider name."""
        return "vmware"

    @property
    def capabilities(self) -> Capabilities:
        """Return provider capabilities."""
        return self._capabilities

    @property
    def tools_dir(self) -> Optional[str]:
        """Return the directory VMware's tools are in, if one can be found.

        Three answers, not two: a directory, ``""`` for "on PATH", and None for "not
        installed". Collapsing the first two -- an empty string is falsy -- made a
        provider that had found its tools on PATH report itself missing.
        """
        if self._searched:
            return self._tools_dir
        self._searched = True
        for candidate in TOOL_DIRS:
            if os.path.isfile(os.path.join(candidate, "vmrun")) or os.path.isfile(
                os.path.join(candidate, "vmrun.exe")
            ):
                self._tools_dir = candidate
                return candidate
        self._tools_dir = "" if shutil.which("vmrun") else None
        return self._tools_dir

    def tool(self, name: str) -> str:
        """Return how to invoke one of VMware's tools.

        Raises:
            DependencyError: If VMware Workstation cannot be found.
        """
        found = self.tools_dir
        if found is None:
            raise DependencyError(
                "VMware Workstation was not found",
                dependency="VMware Workstation 17+",
                install_command="Install VMware Workstation (it provides vmrun)",
            )
        return os.path.join(found, name) if found else name  # "" means on PATH

    def converter(self) -> VDiskManagerConverter:
        """Return the ``vmware-vdiskmanager`` converter."""
        return VDiskManagerConverter(self.tool("vmware-vdiskmanager"))

    def version(self) -> str:
        """Return vmrun's version, e.g. ``"1.17.0"``.

        Raises:
            DependencyError: If vmrun cannot be run.
        """
        if self._version is None:
            out = self._vmrun_raw([])
            match = re.search(r"vmrun version ([\d.]+)", out)
            self._version = match.group(1) if match else "unknown"
        return self._version

    def storage_location(self) -> StorageLocation:
        """Return the VM directory, one subdirectory per VM.

        Per-VM directories are VMware's own convention -- the ``.vmx``, the disks,
        the NVRAM and the logs all sit together -- which also makes ``delete``
        complete by construction.
        """
        return directory(self.state_dir, nest_per_vm=True)

    def _emitter(self, vm_name: str, policy: Policy = Policy.STRICT) -> VMwareEmitter:
        return VMwareEmitter(
            vm_name,
            location=self.storage_location(),
            tools_dir=self.tools_dir or None,
            capabilities=self._capabilities,
            policy=policy,
        )

    def probe_medium(self, path: str) -> Dict[str, Any]:
        """Return a disk's properties by reading the VMDK descriptor.

        A sparse VMDK starts with a plain-text descriptor naming its extents, and
        the extent sizes are in 512-byte sectors -- which is the only way to learn a
        disk's capacity without VMware's own libraries. ``vmware-vdiskmanager`` has
        no "info" mode.

        Args:
            path: Path to the ``.vmdk``.

        Returns:
            dict: ``size_mb``, ``format`` and ``variant``.
        """
        from ...core.devices import Allocation, DiskFormat

        info = {"size_mb": 0, "format": DiskFormat.VMDK, "variant": Allocation.THIN}
        try:
            with open(path, "rb") as handle:
                head = handle.read(64 * 1024).decode("latin-1")
        except OSError:
            return info
        sectors = 0
        for line in head.splitlines():
            # RW 131072 SPARSE "disk-s001.vmdk"
            match = re.match(r"^\s*RW\s+(\d+)\s+(\w+)", line)
            if match:
                sectors += int(match.group(1))
                if match.group(2).upper() == "FLAT":
                    info["variant"] = Allocation.THICK
        info["size_mb"] = sectors * 512 // (1024 * 1024)
        return info

    # -- transport -----------------------------------------------------------

    def _vmrun_raw(self, args: List[str]) -> str:
        """Run vmrun and return its output, without checking the result."""
        argv = [self.tool("vmrun")]
        if self.host_type:
            argv += ["-T", self.host_type]
        argv += args
        try:
            result = subprocess.run(argv, capture_output=True, text=True, check=False)
        except FileNotFoundError:
            raise DependencyError(
                "vmrun is not available",
                dependency="VMware Workstation 17+",
                install_command="Install VMware Workstation (it provides vmrun)",
            )
        return (result.stdout or "") + (result.stderr or "")

    def _vmrun(self, *args: str, check: bool = True) -> str:
        """Run a vmrun command.

        vmrun reports failure in its *output* rather than only by exit code, so the
        text is what gets checked.

        Raises:
            ProviderError: If vmrun reported an error and ``check`` is set.
        """
        out = self._vmrun_raw(list(args))
        if check and re.search(r"^Error:", out, re.MULTILINE):
            raise ProviderError(f"vmrun {' '.join(args)} failed: {out.strip()}")
        return out

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
                argv = list(step.argv)
                if argv[0] == "mkdir":
                    # The emitted plan reads as a shell script for a reader; making a
                    # directory does not need one.
                    os.makedirs(argv[-1], exist_ok=True)
                    continue
                result = subprocess.run(argv, capture_output=True, text=True)
                output = (result.stdout or "") + (result.stderr or "")
                if result.returncode != 0 or re.search(r"^Error:", output, re.MULTILINE):
                    raise ProviderError(f"{step.description} failed: {output.strip()}")
            else:
                raise ProviderError(
                    f"the vmware provider cannot run a {step.kind.value} step "
                    f"({step.description})"
                )

    # -- the directory -------------------------------------------------------

    def vm_dir(self, vm_name: str) -> str:
        """Return a VM's directory."""
        return self.storage_location().directory_for(vm_name)

    def vmx_path(self, vm_name: str) -> str:
        """Return a VM's ``.vmx`` -- which is the VM, as far as VMware is concerned."""
        return os.path.join(self.vm_dir(vm_name), f"{vm_name}{VMX_SUFFIX}")

    def _running(self) -> List[str]:
        """Return the ``.vmx`` paths vmrun reports as running."""
        out = self._vmrun("list", check=False)
        return [
            line.strip() for line in out.splitlines() if line.strip().lower().endswith(VMX_SUFFIX)
        ]

    # -- lifecycle -----------------------------------------------------------

    def list_vms(self) -> List[str]:
        """List every VM in the state directory.

        A directory with a matching ``.vmx`` in it is a VM. VMware's own inventory is
        whatever its GUI has opened, which is not a fact about the host.
        """
        try:
            entries = sorted(os.listdir(self.state_dir))
        except FileNotFoundError:
            return []
        return [name for name in entries if os.path.isfile(self.vmx_path(name))]

    def vm_exists(self, vm_name: str) -> bool:
        """Whether this VM's ``.vmx`` exists."""
        return os.path.isfile(self.vmx_path(vm_name))

    def read_vm(self, vm_name: str) -> VMConfig:
        """Read a VM's ``.vmx`` into a configuration."""
        if not self.vm_exists(vm_name):
            raise VMNotFoundError(vm_name)
        with open(self.vmx_path(vm_name)) as handle:
            text = handle.read()
        return self.parser.parse_text(vm_name, text, probe=self.probe_medium)

    def create_vm(self, vm: VMConfig, execute: bool = True, policy: Policy = Policy.STRICT) -> Plan:
        """Create a VM: its directory, its disks and its ``.vmx``."""
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
        """Rewrite a VM's ``.vmx``.

        The file is the configuration, so there is nothing to change in place. A
        running VM keeps what it was powered on with.
        """
        if not self.vm_exists(vm_name):
            raise VMNotFoundError(vm_name)
        current = self.read_vm(vm_name)
        plan = self._emitter(vm_name).emit_modify_vm(current, new_config)
        if self.get_vm_status(vm_name) == "running":
            plan.warn(
                f"{vm_name} is running; VMware reads the .vmx at power-on, so the "
                f"changes take effect when it is restarted"
            )
        if on_warning:
            for message in plan.warnings:
                on_warning(message)
        if execute:
            self.run_plan(plan)
        return plan

    def delete_vm(self, vm_name: str) -> bool:
        """Stop the VM if it is running, then delete it.

        ``vmrun deleteVM`` removes the VM and the files it owns; the directory is
        removed afterwards because VMware leaves logs and lock directories behind,
        and "deleted" should not mean "mostly deleted" (the lesson of F-28).
        """
        if not self.vm_exists(vm_name):
            raise VMNotFoundError(vm_name)
        if self.get_vm_status(vm_name) == "running":
            self.stop_vm(vm_name, force=True, wait=30)
        self._vmrun("deleteVM", self.vmx_path(vm_name), check=False)
        shutil.rmtree(self.vm_dir(vm_name), ignore_errors=True)
        if self.vm_exists(vm_name):
            raise ProviderError(f"failed to remove {self.vm_dir(vm_name)}")
        return True

    def start_vm(self, vm_name: str) -> bool:
        """Power the VM on, with no window.

        ``nogui`` matters more here than elsewhere: Workstation is a desktop product,
        and a VM started with a window belongs to whoever's session ran the command.
        """
        if not self.vm_exists(vm_name):
            raise VMNotFoundError(vm_name)
        if self.get_vm_status(vm_name) == "running":
            return True
        self._vmrun("start", self.vmx_path(vm_name), "nogui")
        return True

    def stop_vm(self, vm_name: str, force: bool = False, wait: int = 0) -> bool:
        """Stop the VM.

        Without ``force`` this is ``stop soft``, which asks VMware Tools in the guest
        to shut down -- and does nothing at all in a guest without them, which is why
        ``--wait`` reporting a timeout is useful rather than pedantic.
        """
        if not self.vm_exists(vm_name):
            raise VMNotFoundError(vm_name)
        if self.get_vm_status(vm_name) != "running":
            return True
        self._vmrun("stop", self.vmx_path(vm_name), "hard" if force else "soft")

        if wait:
            deadline = time.monotonic() + wait
            while time.monotonic() < deadline:
                if self.get_vm_status(vm_name) != "running":
                    return True
                time.sleep(0.5)
            raise VMStateError(
                f"{vm_name!r} was still running {wait}s after the stop request",
                vm_name=vm_name,
                current_state="running",
                required_state="stopped",
            )
        return True

    def get_vm_status(self, vm_name: str) -> str:
        """Return ``running`` or ``stopped``.

        ``vmrun list`` names the running VMs by ``.vmx`` path, which is the product's
        own answer rather than an inference from a lock file.
        """
        if not self.vm_exists(vm_name):
            raise VMNotFoundError(vm_name)
        wanted = os.path.normcase(os.path.normpath(self.vmx_path(vm_name)))
        for path in self._running():
            if os.path.normcase(os.path.normpath(path)) == wanted:
                return "running"
        return "stopped"
