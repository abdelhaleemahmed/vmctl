"""
Base provider interface for hypervisor backends.

All hypervisor providers (VirtualBox, libvirt, QEMU, etc.) must implement
this abstract base class to ensure consistent API across providers.
"""

import os
import shutil
import subprocess
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

try:  # pragma: no cover - typing_extensions fallback for older interpreters
    from typing import Protocol, runtime_checkable
except ImportError:  # pragma: no cover
    from typing_extensions import Protocol, runtime_checkable  # type: ignore

from ..core.capabilities import Capabilities
from ..core.convert import MediumConverter
from ..core.exceptions import DependencyError, ProviderError
from ..core.plan import Plan, Step, StepKind
from ..core.storage import StorageLocation
from ..core.translate import Policy
from ..core.vmconfig import VMConfig

if TYPE_CHECKING:  # pragma: no cover - these import providers, not the other way
    from ..core.doctor import Check
    from ..core.snapshots import Snapshot


@runtime_checkable
class MediumProbe(Protocol):
    """Looks up the properties of a storage medium by path.

    Parsers need a medium's size, format and allocation variant, but acquiring
    that information is *transport*, not parsing: it means shelling out to the
    hypervisor or reading the host filesystem. Taking it as a collaborator keeps
    every parser a pure function of its input text, which is what makes parsers
    testable without the hypervisor installed.

    Implementations return a dict with at least ``size_mb``, ``format`` and
    ``variant`` keys.
    """

    def __call__(self, path: str) -> Dict[str, Any]:  # pragma: no cover - protocol
        ...


class BaseProvider(ABC):
    """Abstract base class for hypervisor providers."""

    #: Executable this provider needs on PATH. Used by the default
    #: :meth:`is_available`; override that method for anything more involved.
    REQUIRED_BINARY: Optional[str] = None

    #: Called with each step just before it runs. What ``vmctl -v`` prints, and the
    #: only way to see which command a failure happened *on* rather than after
    #: (E-08). Set by the engine; None means say nothing.
    on_step: Optional[Callable[[Step], None]] = None

    # -- running a plan ------------------------------------------------------
    #
    # One loop, here, rather than one per provider. Four copies of "write a file,
    # run an argv, refuse anything else" had already drifted: `mkdir` interception
    # was added to three of them separately (F-36), and only VMware knew that its
    # tool reports failure in its *output* rather than its exit status. What is
    # genuinely per-provider is one command, so that is the only thing a provider
    # overrides.

    def run_plan(self, plan: Plan) -> None:
        """Execute every step in a plan, in order.

        Args:
            plan: The plan to run.

        Raises:
            ProviderError: If a step fails, or is a kind this provider cannot run.
        """
        for step in plan:
            if self.on_step is not None:
                self.on_step(step)
            self.run_step(step)

    def run_step(self, step: Step) -> None:
        """Execute one step.

        Raises:
            ProviderError: If the step fails, or is a kind this provider cannot run.
        """
        if step.kind is StepKind.WRITE_FILE and step.path is not None:
            step.path.parent.mkdir(parents=True, exist_ok=True)
            step.path.write_text(step.content or "")
            return
        if step.kind is StepKind.EXEC and step.argv:
            if step.argv[0] == "mkdir":
                # A plan reads as a shell script; it does not have to be run as one,
                # and `mkdir -p` is neither portable nor a hypervisor's business.
                os.makedirs(step.argv[-1], exist_ok=True)
                return
            self.run_argv(step)
            return
        raise ProviderError(
            f"the {self.name} provider cannot run a {step.kind.value} step " f"({step.description})"
        )

    def resolve_argv(self, argv: List[str]) -> List[str]:
        """Return the command to actually run for an emitted one.

        A plan is emitted to be *read*, so it leaves out anything that is about this
        machine rather than about the VM -- libvirt's connection URI, for one. This
        is where that is put back.
        """
        return list(argv)

    def run_argv(self, step: Step) -> None:
        """Run one command, treating a non-zero exit as a failure.

        Args:
            step: The step, whose description is what a failure is reported as --
                "create the root image failed" tells a user more than the argv does.

        Raises:
            ProviderError: If the command fails.
        """
        argv = self.resolve_argv(list(step.argv or []))
        try:
            result = subprocess.run(argv, capture_output=True, text=True)
        except OSError as exc:
            # The tool is missing or not executable. F-42's rule: that is the
            # "install it" answer, not a traceback from inside vmctl.
            raise DependencyError(
                f"{argv[0]} could not be run",
                dependency=self.name,
                install_command=f"install {self.name} and make sure {argv[0]} is available",
                original_exception=exc,
            )
        if result.returncode != 0:
            raise ProviderError(
                f"{step.description} failed: " f"{result.stderr.strip() or result.stdout.strip()}"
            )

    @abstractmethod
    def storage_location(self) -> StorageLocation:
        """Return where this provider creates new disk images.

        Providers spell this differently -- a machine folder, an image directory,
        a pool, a datastore -- so the neutral answer lives in
        :class:`~vmctl.core.storage.StorageLocation` and everything that needs a
        path asks for it here rather than guessing an attribute name (A-09).
        """

    def converter(self) -> Optional[MediumConverter]:
        """Return this provider's image converter, if it has one.

        Deciding *whether* an image needs converting is provider-neutral and
        lives in :mod:`vmctl.core.convert`; this supplies the mechanism.

        Returns:
            A converter, or None when the provider cannot convert images.
        """
        return None

    def version(self) -> str:
        """Return the hypervisor's version, e.g. ``"7.1.18"``.

        Returns:
            The version string, or ``""`` when the provider cannot determine it.
        """
        return ""

    def diagnostics(self) -> List["Check"]:
        """Return what this provider can say about its own health (E-12).

        The default covers what every provider has: a tool that must be on PATH, a
        version, and somewhere images go with room for one. A provider that knows
        more -- a connection URI, an acceleration mode, a second tool -- extends
        this rather than the command doing it, so ``vmctl doctor`` stays a printer
        and a fifth provider brings its own answers.

        Never raises: a diagnostic that fails when something is wrong is useless
        exactly when it is needed.
        """
        from ..core.doctor import Check
        from ..core.hostinfo import free_space_mb

        checks: List["Check"] = []
        binary = self.REQUIRED_BINARY
        if binary:
            found = shutil.which(binary)
            checks.append(
                Check(
                    binary,
                    found or "not found on PATH",
                    found is not None,
                    None if found else f"install {self.name} and make sure {binary} is on PATH",
                )
            )
        try:
            version = self.version()
        except Exception as exc:  # a tool that is present but will not answer
            checks.append(Check(f"{self.name} version", f"could not be read: {exc}", False))
        else:
            checks.append(Check(f"{self.name} version", version or "unknown", bool(version)))

        try:
            location = self.storage_location()
        except Exception as exc:
            checks.append(Check("image location", f"could not be established: {exc}", False))
            return checks
        checks.append(Check("image location", location.describe()))
        path = getattr(location, "value", "") or ""
        free = free_space_mb(path) if path else None
        if free is not None:
            # Informational rather than a pass: how much is enough depends on the VM
            # being created, and vmctl is not going to guess a threshold.
            checks.append(Check("free space there", f"{free} MB"))
        return checks

    def probe(self) -> Capabilities:
        """Return this provider's capabilities, refined by asking the host (E-05).

        Some of a declaration cannot be written down in advance, because the answer
        belongs to the machine rather than to the product: which bridged interfaces
        exist, which host-only networks are defined, which machine types this QEMU
        build offers, which guest OS ids this VirtualBox knows. A static table can
        only be a conservative default, and for libvirt -- whose matrix depends on the
        QEMU build underneath it -- that is a real gap rather than a nicety.

        The default returns the static declaration unchanged, so a provider that
        cannot ask, or a machine where the tooling is absent, still works. A provider
        that overrides this must **never raise**: an unanswerable question leaves the
        static value in place, because refusing to work at all is a worse answer than
        a conservative one.

        Returns:
            The declaration to validate against, cached by the caller.
        """
        return self.capabilities

    @classmethod
    def is_available(cls) -> bool:
        """Whether this provider can be used on this machine.

        Used to choose a provider, so it must never raise: anything unexpected
        counts as unavailable.
        """
        if cls.REQUIRED_BINARY is None:
            return True
        return shutil.which(cls.REQUIRED_BINARY) is not None

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the provider name (e.g., 'virtualbox', 'libvirt')."""
        pass

    @property
    @abstractmethod
    def capabilities(self) -> Capabilities:
        """Return this provider's capability declaration.

        Every limit vmctl enforces is read from here, so a provider states its
        rules once instead of having them restated as literals in the validator.
        """
        pass

    @abstractmethod
    def list_vms(self) -> List[str]:
        """List all VM names managed by this provider."""
        pass

    @abstractmethod
    def read_vm(self, vm_name: str) -> VMConfig:
        """
        Read VM configuration from the provider.

        Args:
            vm_name: Name of the VM to read

        Returns:
            VMConfig object with the VM's configuration

        Raises:
            ProviderError: If VM doesn't exist or cannot be read
        """
        pass

    @abstractmethod
    def create_vm(self, vm: VMConfig, execute: bool = True, policy: Policy = Policy.STRICT) -> Plan:
        """
        Create a new VM from configuration.

        Args:
            vm: VMConfig object defining the VM
            execute: If True, actually create the VM. If False, return the plan
                without running it (dry-run)
            policy: What to do about values this provider does not support --
                refuse (strict), substitute and report (nearest), or convert

        Returns:
            Plan: the steps that were, or would be, run

        Raises:
            ProviderError: If VM creation fails
        """
        pass

    @abstractmethod
    def delete_vm(self, vm_name: str) -> bool:
        """
        Delete a VM and its associated resources.

        Args:
            vm_name: Name of the VM to delete

        Returns:
            True if deletion was successful

        Raises:
            ProviderError: If VM doesn't exist or deletion fails
        """
        pass

    @abstractmethod
    def start_vm(self, vm_name: str) -> bool:
        """
        Start a VM.

        Args:
            vm_name: Name of the VM to start

        Returns:
            True if VM was started successfully

        Raises:
            ProviderError: If VM doesn't exist or cannot be started
        """
        pass

    @abstractmethod
    def stop_vm(self, vm_name: str, force: bool = False, wait: int = 0) -> bool:
        """
        Stop a running VM.

        Args:
            vm_name: Name of the VM to stop
            force: If True, force power off. If False, attempt graceful shutdown
            wait: Seconds to wait for the VM to actually stop (0 = no wait)

        Returns:
            True if VM was stopped successfully

        Raises:
            ProviderError: If VM doesn't exist or cannot be stopped
        """
        pass

    @abstractmethod
    def get_vm_status(self, vm_name: str) -> str:
        """
        Get the current status of a VM.

        Args:
            vm_name: Name of the VM

        Returns:
            Status string (e.g., 'running', 'stopped', 'paused')

        Raises:
            ProviderError: If VM doesn't exist
        """
        pass

    def vm_exists(self, vm_name: str) -> bool:
        """
        Check if a VM exists.

        Args:
            vm_name: Name of the VM to check

        Returns:
            True if VM exists, False otherwise
        """
        return vm_name in self.list_vms()

    def edit_vm(
        self,
        vm_name: str,
        new_config: VMConfig,
        execute: bool = True,
        on_warning: Optional[Callable[[str], None]] = None,
    ) -> Plan:
        """
        Edit an existing VM's configuration.

        Args:
            vm_name: Name of the VM to edit
            new_config: New configuration to apply
            execute: If True, apply the change. If False, return the plan
                without running it (dry-run).
            on_warning: Where the engine reports *validation* warnings. What could
                not be applied in place rides on the returned plan's
                ``warnings``, which is the single carrier -- forwarding those here
                as well printed every one of them twice.

        Returns:
            Plan: the steps that were, or would be, run

        Raises:
            NotImplementedError: If the provider does not implement editing.

        Note:
            This used to default to ``delete_vm()`` followed by ``create_vm()``.
            Since ``delete_vm`` passes ``--delete``, that destroyed the VM's
            disks -- a data-loss trap for any provider that simply did not
            override it (F-11). Providers must implement editing explicitly.
        """
        raise NotImplementedError(f"{self.name} does not support editing VMs in place")

    # -- snapshots (E-09) ----------------------------------------------------
    #
    # Four operations on four genuinely different mechanisms: a tree of differencing
    # images, internal qcow2 snapshots, `vmrun`. So the commands are each provider's
    # own and what is shared is the vocabulary (`core/snapshots.py`), the capability
    # declaration, and the fact that all four return a Plan -- which is what gives
    # them dry-run, `-v` and `--out` without any of them knowing about those.

    def snapshots(self, vm_name: str) -> List["Snapshot"]:
        """Return this VM's snapshots, newest-known information first.

        Raises:
            NotImplementedError: If this provider cannot take snapshots.
            VMNotFoundError: If there is no such VM.
        """
        raise NotImplementedError(f"{self.name} does not support snapshots")

    def take_snapshot(
        self,
        vm_name: str,
        snapshot: str,
        description: Optional[str] = None,
        execute: bool = True,
    ) -> Plan:
        """Take a snapshot.

        Args:
            vm_name: The VM.
            snapshot: What to call the snapshot.
            description: Why it was taken, for the providers that can keep one.
            execute: Actually take it. False returns the plan only.

        Raises:
            NotImplementedError: If this provider cannot take snapshots.
        """
        raise NotImplementedError(f"{self.name} does not support snapshots")

    def restore_snapshot(self, vm_name: str, snapshot: str, execute: bool = True) -> Plan:
        """Put the VM back to a snapshot, discarding the state since.

        Raises:
            NotImplementedError: If this provider cannot take snapshots.
        """
        raise NotImplementedError(f"{self.name} does not support snapshots")

    def delete_snapshot(self, vm_name: str, snapshot: str, execute: bool = True) -> Plan:
        """Delete a snapshot, keeping the VM's current state.

        Raises:
            NotImplementedError: If this provider cannot take snapshots.
        """
        raise NotImplementedError(f"{self.name} does not support snapshots")

    def refuse_unsnapshottable(self, vm_name: str) -> None:
        """Raise if this VM's disks cannot hold a snapshot on this provider.

        Asked before the command runs, because the hypervisors answer it half way
        through: libvirt fails with a sentence about storage types and ``qemu-img``
        refuses per image -- after vmctl has already snapshotted the first one.

        Raises:
            ProviderError: With one line per disk that cannot.
        """
        from ..core.snapshots import unsupported_disks

        if not self.capabilities.snapshot_formats:
            return
        problems = unsupported_disks(self.read_vm(vm_name), self.capabilities)
        if problems:
            raise ProviderError(
                f"{vm_name} cannot be snapshotted: " + "; ".join(problems),
                context={"vm_name": vm_name},
                recovery_hint="convert the disk with 'vmctl convert', or use a "
                "provider whose snapshots do not live inside the image",
            )
