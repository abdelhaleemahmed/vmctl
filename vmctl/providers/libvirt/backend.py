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
import xml.etree.ElementTree as ET
import shutil
import subprocess
from dataclasses import replace
from typing import Any, Callable, Dict, List, Optional, Tuple

from ...core.capabilities import Capabilities, Support
from ...core.hostinfo import host_bridges
from ...core.exceptions import (
    DependencyError,
    ProviderError,
    VMNotFoundError,
    VMStateError,
)
from ...core.plan import Plan
from ...core.storage import StorageLocation, directory
from ...core.translate import Policy
from ...core.vmconfig import VMConfig
from ..base import BaseProvider
from .capabilities import LibvirtCapabilities
from .convert import QemuImgConverter
from .emitter import LibvirtEmitter
from ...core.snapshots import Snapshot
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

    def converter(self) -> QemuImgConverter:
        """Return the ``qemu-img`` converter."""
        return QemuImgConverter()

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

    def storage_location(self) -> StorageLocation:
        """Return the image directory for this connection.

        A session connection cannot write to the system image store, so the
        location follows the connection rather than being fixed. libvirt keeps
        images flat, unlike VirtualBox's per-VM folders.
        """
        return directory(self.image_dir, nest_per_vm=False)

    def diagnostics(self):
        """Add what is specific to libvirt: which libvirt, and whether it answers.

        The connection is what goes wrong here. ``virsh`` being installed says
        nothing about whether the daemon is running or whether this user may talk to
        it, and "qemu:///session vs qemu:///system" is why a user's VMs seem to have
        vanished -- they are in the other one.
        """
        from ...core.doctor import Check

        checks = super().diagnostics()
        checks.append(Check("connection", self.connect or "virsh's own default"))
        try:
            domains = self.list_vms()
        except Exception as exc:
            checks.append(
                Check(
                    "connection works",
                    f"no: {exc}",
                    False,
                    "check that libvirtd (or the session daemon) is running and that "
                    "this user may connect",
                )
            )
        else:
            checks.append(Check("connection works", f"yes, {len(domains)} domain(s)", True))
            checks.append(
                Check(
                    "domain type",
                    f"{self.domain_type}"
                    + (" (accelerated)" if self.domain_type == "kvm" else " (emulated)"),
                )
            )
        return checks

    def probe(self) -> Capabilities:
        """Refine the declaration by asking libvirt about this host (E-05).

        For libvirt this is not a refinement but the point: the static table describes
        *a* QEMU build on *a* machine type, and the real one is whatever
        ``virsh domcapabilities`` says. Two things are asked for here --

        * the machine types this emulator actually offers, so a config naming one is
          checked rather than substituted on a guess;
        * the networks and bridges this host has, so ``adapter_name`` is validated at
          validate time instead of failing when the domain starts;
        * whether ``passt`` is installed, because it is the only backend libvirt will
          accept ``<portForward>`` with -- so without it, port forwarding is not a
          capability of this host however new the libvirt is (E-10).

        Nothing raises: an unanswerable question leaves the static value alone.
        """
        caps = self.capabilities
        machines = self._probe_machines()
        interfaces = self._probe_interfaces()
        forwarding = caps.port_forwards if shutil.which("passt") else Support.UNSUPPORTED
        if not machines and not interfaces and forwarding is caps.port_forwards:
            return caps
        evidence = caps.evidence
        asked = []
        if machines:
            asked.append(f"{len(machines)} machine types")
        if interfaces:
            asked.append(
                ", ".join(f"{len(names)} {mode}" for mode, names in sorted(interfaces.items()))
            )
        asked.append("passt " + ("present" if forwarding.usable else "absent"))
        return replace(
            caps,
            machine_types=machines or caps.machine_types,
            host_interfaces=interfaces or caps.host_interfaces,
            port_forwards=forwarding,
            evidence=(f"{evidence} Asked this host directly (E-05): " + "; ".join(asked) + "."),
        )

    def _probe_machines(self) -> Tuple[str, ...]:
        """Return the machine types this emulator offers, or empty if it cannot say."""
        out = self._virsh("capabilities", check=False)
        if not out:
            return ()
        found: List[str] = []
        for match in re.finditer(r"<machine[^>]*>([^<]+)</machine>", out):
            name = match.group(1).strip()
            if name and name not in found:
                found.append(name)
        # The aliases are what a person writes, and `capabilities` reports them as
        # canonical= attributes rather than as text, so they are kept from the static
        # declaration instead of being dropped.
        for alias in self.capabilities.machine_types:
            if alias not in found:
                found.append(alias)
        return tuple(found)

    def _probe_interfaces(self) -> Dict[str, Tuple[str, ...]]:
        """Return what this host offers per network mode.

        libvirt's networks serve the modes vmctl calls hostonly and natnetwork; a
        bridged adapter is a host bridge, which libvirt does not own -- so that comes
        from the host itself.
        """
        found: Dict[str, Tuple[str, ...]] = {}
        networks = [
            line.split()[0]
            for line in self._virsh("net-list", "--all", "--name", check=False).splitlines()
            if line.strip()
        ]
        if networks:
            found["hostonly"] = tuple(networks)
            found["natnetwork"] = tuple(networks)
            found["internal"] = tuple(networks)
        bridges = host_bridges()
        if bridges:
            found["bridged"] = bridges
        return found

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
            location=self.storage_location(),
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
        from ...core.vmconfig import Allocation

        fallback = {
            "size_mb": 0,
            "format": self.parser.capabilities.native_format,
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

        virtual = int(info.get("virtual-size") or 0)
        fmt = self.parser.extension_to_format(info.get("format", ""))
        # A qcow2 image reports its allocated size; a fully allocated one is
        # effectively preallocated.
        actual = int(info.get("actual-size") or 0)
        variant = Allocation.THICK if virtual and actual >= virtual * 0.9 else Allocation.THIN
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

    def resolve_argv(self, argv: List[str]) -> List[str]:
        """Add our connection to a ``virsh`` command.

        A plan is emitted without a connection URI so that it reads cleanly and can
        be handed to someone else; running it here has to say which libvirt.
        """
        command = list(argv)
        if command and command[0] == "virsh" and self.connect:
            return [command[0], "--connect", self.connect] + command[1:]
        return command

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
        if execute:
            self.run_plan(plan)
        return plan

    # -- snapshots (E-09) ----------------------------------------------------

    def snapshots(self, vm_name: str) -> List[Snapshot]:
        """Return a domain's snapshots.

        Three questions, because libvirt keeps the answers in three places: the table
        from ``snapshot-list --parent``, which snapshot is current from
        ``snapshot-current``, and the *description* only inside each snapshot's own
        XML. The last one costs a call per snapshot, which is the price of showing the
        text the user typed when they took it.
        """
        if not self.vm_exists(vm_name):
            raise VMNotFoundError(vm_name)
        found = parse_snapshots(self._virsh("snapshot-list", vm_name, "--parent"))
        current = self._virsh("snapshot-current", vm_name, "--name").strip()
        for snapshot in found:
            snapshot.current = snapshot.name == current
            snapshot.description = _description_in(
                self._virsh("snapshot-dumpxml", vm_name, snapshot.name)
            )
        return found

    def take_snapshot(
        self,
        vm_name: str,
        snapshot: str,
        description: Optional[str] = None,
        execute: bool = True,
    ) -> Plan:
        """Take an internal snapshot with ``virsh snapshot-create-as``."""
        self.refuse_unsnapshottable(vm_name)
        argv = ["virsh", "snapshot-create-as", vm_name, snapshot]
        if description:
            argv += ["--description", description]
        return self._snapshot_plan(
            vm_name, argv, f"take snapshot {snapshot!r} of {vm_name}", execute
        )

    def restore_snapshot(self, vm_name: str, snapshot: str, execute: bool = True) -> Plan:
        """Revert to a snapshot with ``virsh snapshot-revert``."""
        return self._snapshot_plan(
            vm_name,
            ["virsh", "snapshot-revert", vm_name, snapshot],
            f"revert {vm_name} to snapshot {snapshot!r}",
            execute,
        )

    def delete_snapshot(self, vm_name: str, snapshot: str, execute: bool = True) -> Plan:
        """Delete a snapshot with ``virsh snapshot-delete``."""
        return self._snapshot_plan(
            vm_name,
            ["virsh", "snapshot-delete", vm_name, snapshot],
            f"delete snapshot {snapshot!r} of {vm_name}",
            execute,
        )

    def _snapshot_plan(
        self, vm_name: str, argv: List[str], description: str, execute: bool
    ) -> Plan:
        """Return (and optionally run) a one-step snapshot plan.

        The connection URI is added by ``resolve_argv`` when it runs, so the plan a
        user reads or writes out with ``--out`` stays the command they would type.
        """
        if not self.vm_exists(vm_name):
            raise VMNotFoundError(vm_name)
        plan = Plan("libvirt")
        plan.exec(argv, description)
        if execute:
            self.run_plan(plan)
        return plan

    def delete_vm(self, vm_name: str) -> bool:
        """Undefine a domain and remove the images vmctl created for it.

        ``virsh undefine --remove-all-storage`` only removes volumes libvirt can
        resolve inside a storage *pool*, and vmctl writes images into a plain
        directory. So it silently left every image behind while reporting success
        (F-28), which `vmctl delete` promises not to do.

        Only images inside this connection's own image directory are removed.
        An image the user attached from somewhere else was not vmctl's to create,
        so it is not vmctl's to delete either.

        ``--snapshots-metadata`` because libvirt refuses otherwise: *"cannot delete
        inactive domain with 3 snapshots"*. Found the moment vmctl could take snapshots
        (E-09) -- a VM vmctl had snapshotted could not then be deleted by vmctl, which
        made the feature a trap rather than a convenience. Deleting a VM means deleting
        what belonged to it, snapshots included.

        Returns:
            True when the domain is gone.

        Raises:
            VMNotFoundError: If no such domain exists.
            ProviderError: If the domain could not be undefined.
        """
        if not self.vm_exists(vm_name):
            raise VMNotFoundError(vm_name)
        ours = self._images_under_our_directory(vm_name)
        if self.get_vm_status(vm_name) == "running":
            self._virsh("destroy", vm_name)
        out = self._virsh(
            "undefine", vm_name, "--remove-all-storage", "--nvram", "--snapshots-metadata"
        )
        if vm_name in self.list_vms():
            raise ProviderError(f"failed to undefine {vm_name!r}: {out.strip()}")
        for path in ours:
            try:
                os.remove(path)
            except FileNotFoundError:
                pass  # --remove-all-storage got there first, which is fine
            except OSError as exc:
                raise ProviderError(
                    f"domain {vm_name!r} was removed, but its image {path} "
                    f"could not be deleted: {exc}"
                )
        return True

    def _images_under_our_directory(self, vm_name: str) -> List[str]:
        """Return the domain's disk images that live in our image directory.

        Read before the domain is undefined, since afterwards there is nothing
        left to ask.
        """
        directory_ = os.path.realpath(self.storage_location().directory_for(vm_name))
        found = []
        for device in self.read_vm(vm_name).storage:
            path = device.disk_path or device.source
            if not path or device.is_removable:
                continue
            if os.path.dirname(os.path.realpath(path)) == directory_:
                found.append(path)
        return found

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


def parse_snapshots(text: str) -> List[Snapshot]:
    """Return the snapshots in a ``virsh snapshot-list --parent`` table.

    Split on runs of two or more spaces rather than on whitespace: the creation time
    is ``2026-09-28 00:17:19 +0000``, which contains two single spaces of its own, and
    a per-column split would turn one snapshot into three fields.
    """
    found: List[Snapshot] = []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("Name") or set(line.strip()) <= {"-"}:
            continue
        columns = [part.strip() for part in re.split(r"\s{2,}", line.strip())]
        if not columns or not columns[0]:
            continue
        found.append(
            Snapshot(
                name=columns[0],
                created=columns[1] if len(columns) > 1 else None,
                state=columns[2] if len(columns) > 2 else None,
                parent=columns[3] if len(columns) > 3 and columns[3] else None,
            )
        )
    return found


def _description_in(xml: str) -> Optional[str]:
    """Return a snapshot's description from its XML, or None when it has none."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return None
    element = root.find("description")
    return element.text if element is not None and element.text else None
