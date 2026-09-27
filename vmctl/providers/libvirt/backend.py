"""
libvirt backend: acquire text, run plans, manage domain state.

Talks to libvirt through ``virsh``, which keeps the provider dependency-free --
the Python bindings are optional and only worth requiring once vmctl needs
something ``virsh`` cannot express.

Connection: whatever ``virsh`` itself would use, unless ``--connect``/
``LIBVIRT_DEFAULT_URI`` says otherwise. As a non-root user that is
``qemu:///session``, whose images live under the user's own data directory rather
than ``/var/lib/libvirt/images`` -- so the image directory follows the
connection, the same problem VirtualBox's machine folder posed (F-13, A-09).
"""

import json
import os
import re
import shutil
import subprocess
from typing import Any, Callable, Dict, List, Optional

from ...core.capabilities import Capabilities
from ...core.exceptions import (
    DependencyError,
    ProviderError,
    VMNotFoundError,
    VMStateError,
)
from ...core.plan import Plan, StepKind
from ...core.translate import Policy
from ...core.vmconfig import VMConfig
from ..base import BaseProvider
from .capabilities import LibvirtCapabilities
from .emitter import LibvirtEmitter
from .parser import LibvirtParser

#: libvirt domain states mapped onto vmctl's vocabulary.
STATE_MAP = {
    "running": "running",
    "idle": "running",
    "paused": "paused",
    "in shutdown": "stopping",
    "shut off": "stopped",
    "crashed": "aborted",
    "pmsuspended": "saved",
}


class LibvirtBackend(BaseProvider):
    """libvirt / QEMU-KVM provider backend."""

    REQUIRED_BINARY = "virsh"

    def __init__(self, connect: Optional[str] = None) -> None:
        """Initialise the backend.

        Args:
            connect: libvirt connection URI. None uses virsh's own default,
                which is ``qemu:///session`` for a non-root user.
        """
        self.parser = LibvirtParser()
        self._capabilities: Capabilities = LibvirtCapabilities.get()
        self.connect = connect or os.environ.get("LIBVIRT_DEFAULT_URI") or None
        self._version: Optional[str] = None

    # -- identity ------------------------------------------------------------

    @property
    def name(self) -> str:
        """Return provider name."""
        return "libvirt"

    @property
    def capabilities(self) -> Capabilities:
        """Return provider capabilities."""
        return self._capabilities

    def version(self) -> str:
        """Return the libvirt version, e.g. ``"11.10.0"``.

        Raises:
            DependencyError: If ``virsh`` cannot be run.
        """
        if self._version is None:
            out = self._virsh("--version", check=True)
            match = re.match(r"(\d+\.\d+\.\d+)", out.strip())
            self._version = match.group(1) if match else out.strip()
        return self._version

    @property
    def session_scoped(self) -> bool:
        """Whether this connection is a per-user session.

        A session connection cannot write to the system image store, so the
        image directory has to follow it.
        """
        uri = self.connect or ""
        if "session" in uri:
            return True
        if "system" in uri:
            return False
        return os.geteuid() != 0

    @property
    def image_dir(self) -> str:
        """Where new disk images are created for this connection."""
        if self.session_scoped:
            base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
            return os.path.join(base, "libvirt", "images")
        return "/var/lib/libvirt/images"

    @property
    def domain_type(self) -> str:
        """``kvm`` when hardware acceleration is present, otherwise ``qemu``.

        Without ``/dev/kvm`` -- inside a VM whose host has not enabled nested
        virtualisation, for instance -- only TCG emulation is available, and
        asking for ``kvm`` makes libvirt refuse the domain.
        """
        return "kvm" if os.path.exists("/dev/kvm") else "qemu"

    def _emitter(self, vm_name: str, policy: Policy = Policy.STRICT) -> LibvirtEmitter:
        return LibvirtEmitter(
            vm_name,
            image_dir=self.image_dir,
            domain_type=self.domain_type,
            emulator=self._emulator(),
            capabilities=self._capabilities,
            policy=policy,
        )

    @staticmethod
    def _emulator() -> Optional[str]:
        """Return the QEMU binary to name in the domain, if one is needed."""
        for candidate in ("/usr/libexec/qemu-kvm", "/usr/bin/qemu-system-x86_64"):
            if os.path.exists(candidate):
                return candidate
        found = shutil.which("qemu-system-x86_64")
        return found

    def probe_medium(self, path: str) -> Dict[str, Any]:
        """Return a disk image's properties.

        Implements :class:`~vmctl.providers.base.MediumProbe` for libvirt. The
        domain document has no capacity in it, so this is the only way to know a
        disk's size.

        Args:
            path: Path to the image.

        Returns:
            dict: ``size_mb``, ``format`` and ``variant`` keys. Sizes are 0 and
            the format is the provider's native one when the image cannot be
            read, which is what happens for an image that does not exist yet.
        """
        from ...core.vmconfig import DiskVariant

        fallback = {
            "size_mb": 0,
            "format": self.parser.capabilities.native_format,
            "variant": DiskVariant.THIN,
        }
        try:
            result = subprocess.run(
                ["qemu-img", "info", "--output=json", path],
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

        virtual = int(info.get("virtual-size") or 0)
        fmt = self.parser.extension_to_format(info.get("format", ""))
        # A qcow2 image reports its allocated size; a fully allocated one is
        # effectively preallocated.
        actual = int(info.get("actual-size") or 0)
        variant = DiskVariant.THICK if virtual and actual >= virtual * 0.9 else DiskVariant.THIN
        return {
            "size_mb": max(0, virtual // (1024 * 1024)),
            "format": fmt or fallback["format"],
            "variant": variant,
        }

    # -- transport -----------------------------------------------------------

    def _virsh(self, *args: str, check: bool = False) -> str:
        """Run a virsh command and return its output.

        Args:
            *args: Arguments after ``virsh``.
            check: Raise when the command fails.

        Returns:
            The command's stdout.

        Raises:
            DependencyError: If virsh is not installed.
            ProviderError: If the command fails and ``check`` is set.
        """
        argv = ["virsh"]
        if self.connect:
            argv += ["--connect", self.connect]
        argv += list(args)
        try:
            result = subprocess.run(argv, capture_output=True, text=True, check=False)
        except FileNotFoundError:
            raise DependencyError(
                "virsh is not available",
                dependency="libvirt-client",
                install_command="Install libvirt-client (provides virsh)",
            )
        if check and result.returncode != 0:
            raise ProviderError(f"virsh {' '.join(args)} failed: {result.stderr.strip()}")
        return result.stdout

    def run_plan(self, plan: Plan) -> None:
        """Execute every step in a plan, in order.

        Args:
            plan: The plan to run.

        Raises:
            ProviderError: If a step fails or cannot be run here.
        """
        for step in plan:
            if step.kind is StepKind.WRITE_FILE and step.path is not None:
                step.path.parent.mkdir(parents=True, exist_ok=True)
                step.path.write_text(step.content or "")
            elif step.kind is StepKind.EXEC and step.argv:
                argv = list(step.argv)
                # A plan is emitted without a connection URI so it reads cleanly;
                # add ours when actually running it.
                if argv[0] == "virsh" and self.connect:
                    argv = [argv[0], "--connect", self.connect] + argv[1:]
                result = subprocess.run(argv, capture_output=True, text=True)
                if result.returncode != 0:
                    raise ProviderError(
                        f"{step.description} failed: "
                        f"{result.stderr.strip() or result.stdout.strip()}"
                    )
            else:
                raise ProviderError(
                    f"the libvirt provider cannot run a {step.kind.value} step "
                    f"({step.description})"
                )

    # -- lifecycle -----------------------------------------------------------

    def list_vms(self) -> List[str]:
        """List every defined domain."""
        out = self._virsh("list", "--all", "--name", check=True)
        return [line.strip() for line in out.splitlines() if line.strip()]

    def read_vm(self, vm_name: str) -> VMConfig:
        """Read a domain's definition into a VMConfig."""
        if not self.vm_exists(vm_name):
            raise VMNotFoundError(vm_name)
        xml = self._virsh("dumpxml", vm_name, check=True)
        return self.parser.parse_text(vm_name, xml, probe=self.probe_medium)

    def create_vm(self, vm: VMConfig, execute: bool = True, policy: Policy = Policy.STRICT) -> Plan:
        """Create a domain from a VMConfig.

        Args:
            vm: Configuration to create.
            execute: Run the plan. False returns it unexecuted.
            policy: What to do about values libvirt does not support.

        Returns:
            Plan: the steps that were, or would be, run.
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
        """Redefine a domain from a new configuration.

        libvirt has no per-setting edit: the domain is redefined wholesale. That
        leaves its disks untouched, so unlike VirtualBox it can be done while the
        domain is running -- the change takes effect on next boot.
        """
        if not self.vm_exists(vm_name):
            raise VMNotFoundError(vm_name)
        current = self.read_vm(vm_name)
        plan = self._emitter(vm_name).emit_modify_vm(current, new_config)
        if on_warning:
            for message in plan.warnings:
                on_warning(message)
        if execute:
            self.run_plan(plan)
        return plan

    def delete_vm(self, vm_name: str) -> bool:
        """Undefine a domain and remove the storage it owns."""
        if not self.vm_exists(vm_name):
            raise VMNotFoundError(vm_name)
        if self.get_vm_status(vm_name) == "running":
            self._virsh("destroy", vm_name)
        # --remove-all-storage deletes the volumes the domain references, which
        # matches what `unregistervm --delete` does for VirtualBox.
        out = self._virsh("undefine", vm_name, "--remove-all-storage", "--nvram")
        if vm_name in self.list_vms():
            raise ProviderError(f"failed to undefine {vm_name!r}: {out.strip()}")
        return True

    def start_vm(self, vm_name: str) -> bool:
        """Start a domain."""
        if not self.vm_exists(vm_name):
            raise VMNotFoundError(vm_name)
        if self.get_vm_status(vm_name) == "running":
            return True
        self._virsh("start", vm_name, check=True)
        return True

    def stop_vm(self, vm_name: str, force: bool = False, wait: int = 0) -> bool:
        """Stop a domain.

        Args:
            vm_name: Domain to stop.
            force: Destroy it instead of requesting a clean shutdown.
            wait: Seconds to wait for it to actually stop.
        """
        import time

        if not self.vm_exists(vm_name):
            raise VMNotFoundError(vm_name)
        if self.get_vm_status(vm_name) != "running":
            return True
        self._virsh("destroy" if force else "shutdown", vm_name, check=True)

        if wait:
            deadline = time.monotonic() + wait
            while time.monotonic() < deadline:
                if self.get_vm_status(vm_name) != "running":
                    return True
                time.sleep(1)
            raise VMStateError(
                f"domain {vm_name!r} was still running {wait}s after the " f"shutdown request",
                vm_name=vm_name,
                current_state="running",
                required_state="stopped",
            )
        return True

    def get_vm_status(self, vm_name: str) -> str:
        """Return a domain's state in vmctl's vocabulary."""
        out = self._virsh("domstate", vm_name)
        state = out.strip().lower()
        if not state:
            raise VMNotFoundError(vm_name)
        return STATE_MAP.get(state, state)
