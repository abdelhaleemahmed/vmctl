"""
Does the hypervisor agree? (E-19)

The conformance suite (A-07) proves the *translation* is right without a hypervisor
installed: the declaration is coherent, the emitter produces a plan, the parser reads it
back. What it cannot prove is that the product on this machine accepts any of it -- and
every finding in PLAN.md that cost real time was of that kind. `F-31`: libvirt *defines*
a domain with a VMDK disk and then will not start it. `F-27`: a controller flag that was
never parsed, so every re-created VM had an unbootable disk. `F-39`: editing a VM
re-created its disks. None of those is visible offline.

So this does by command what has been done by hand for every provider in this plan:
create a tiny VM, read it back and compare, snapshot it if the provider says it can,
start it, stop it, delete it -- and assert each step rather than trusting the exit code.
It is deliberately the same shape as the manual checks, because those are what found the
bugs.

Three rules make it safe to run on a machine with real VMs on it:

* **Small and disposable.** 128 MB, one small disk, a name of its own. Nothing is
  attached, nothing is shared, and the name is checked to be free before anything runs.
* **It cleans up even when it fails**, because a failed selftest that leaves a VM behind
  is a selftest people stop running.
* **What the provider says it cannot do is skipped, not failed.** VMware cannot forward
  a port; plain QEMU has no guest-OS field. A selftest that reported those as failures
  would be reporting the capability declaration back at itself.
"""

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .diff import diff, stated_paths
from .plan import Plan
from .translate import Policy
from .vmconfig import (
    BootConfig,
    CPUConfig,
    FirmwareConfig,
    MemoryConfig,
    NetworkConfig,
    StorageDevice,
    VMConfig,
)

#: Small enough to run anywhere, and never large enough to matter on a machine that is
#: already running something. A selftest is not a benchmark.
MEMORY_MB = 128
DISK_MB = 64

#: What the VM is called. Distinctive on purpose: it says what it is to anyone who finds
#: it in a hypervisor's list, and it will not collide with anything a person named.
NAME = "vmctl-selftest"


@dataclass
class Outcome:
    """What happened in one step.

    Attributes:
        name: What was tried.
        state: ``pass``, ``fail`` or ``skip``.
        detail: What was found -- the reason for a failure, the evidence for a pass.
        seconds: How long it took, which is often the interesting part: a create that
            takes 40 seconds is telling you something about the host.
    """

    name: str
    state: str
    detail: str = ""
    seconds: float = 0.0

    @property
    def ok(self) -> bool:
        """True when this step did not fail (a skip is not a failure)."""
        return self.state != "fail"

    def render(self) -> str:
        """Return the line for this step."""
        mark = {"pass": "ok  ", "fail": "FAIL", "skip": "skip"}[self.state]
        timing = f" ({self.seconds:.1f}s)" if self.seconds >= 0.1 else ""
        return f"[{mark}] {self.name}{timing}" + (f": {self.detail}" if self.detail else "")

    def as_dict(self) -> Dict[str, Any]:
        """Return this step as JSON-safe data (E-07)."""
        return {
            "step": self.name,
            "state": self.state,
            "detail": self.detail,
            "seconds": round(self.seconds, 3),
        }


@dataclass
class Report:
    """Every step, in order."""

    provider: str
    vm_name: str
    outcomes: List[Outcome] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when nothing failed."""
        return all(outcome.ok for outcome in self.outcomes)

    def summary(self) -> str:
        """Return one line: how many of each."""
        counts = {state: 0 for state in ("pass", "fail", "skip")}
        for outcome in self.outcomes:
            counts[outcome.state] += 1
        return (
            f"{counts['pass']} passed, {counts['fail']} failed, {counts['skip']} skipped"
            f" on {self.provider}"
        )

    def as_dict(self) -> Dict[str, Any]:
        """Return the whole report as JSON-safe data."""
        return {
            "provider": self.provider,
            "vm": self.vm_name,
            "ok": self.ok,
            "summary": self.summary(),
            "steps": [outcome.as_dict() for outcome in self.outcomes],
        }


def sample_vm(name: str, capabilities) -> VMConfig:
    """Return the VM the selftest creates: the smallest thing worth checking.

    Shaped by the provider's own declaration rather than by a guess -- its native disk
    format and the bus it would idiomatically put a disk on -- because a selftest that
    asks for something the hypervisor does not do is testing the wrong thing.
    """
    from .devices import DeviceKind

    bus = capabilities.native_buses.get(DeviceKind.DISK)
    if bus is None:
        buses = capabilities.buses_for(DeviceKind.DISK)
        bus = buses[0] if buses else None
    device = StorageDevice(
        name="root",
        size_mb=DISK_MB,
        format=capabilities.native_format,
        bootable=True,
    )
    if bus is not None:
        device.bus = bus
    return VMConfig(
        name=name,
        cpu=CPUConfig(count=1),
        memory=MemoryConfig(mb=MEMORY_MB),
        firmware=FirmwareConfig(),
        storage=[device],
        networks=[NetworkConfig()],
        boot=BootConfig(),
        storage_controllers=[],
    )


def run(
    backend,
    name: str = NAME,
    start: bool = True,
    keep: bool = False,
    on_step: Optional[Callable[[Outcome], None]] = None,
) -> Report:
    """Create, check, start, stop and delete a throwaway VM on this host.

    Args:
        backend: The provider to exercise.
        name: What to call the VM. Must not already exist.
        start: Whether to power it on. The most intrusive step, and the one that proves
            the most: a definition a hypervisor accepts is not a VM it will run (F-31).
        keep: Leave the VM behind. For looking at what went wrong.
        on_step: Called with each outcome as it happens, so a slow create is visible
            while it is happening rather than afterwards.

    Returns:
        Report: every step, in order.
    """
    report = Report(provider=backend.name, vm_name=name)
    capabilities = backend.probe()
    vm = sample_vm(name, capabilities)

    def record(outcome: Outcome) -> Outcome:
        report.outcomes.append(outcome)
        if on_step is not None:
            on_step(outcome)
        return outcome

    def step(label: str, work: Callable[[], str]) -> bool:
        started = time.monotonic()
        try:
            detail = work()
        except Exception as exc:
            record(Outcome(label, "fail", str(exc), time.monotonic() - started))
            return False
        record(Outcome(label, "pass", detail, time.monotonic() - started))
        return True

    if name in _existing(backend):
        record(
            Outcome(
                "the name is free",
                "fail",
                f"{name} already exists; delete it or pass --name",
            )
        )
        return report
    record(Outcome("the name is free", "pass", name))

    explained: set = set()
    created = step("create", lambda: _created(backend, vm, explained))
    if not created:
        return report

    try:
        step("it exists", lambda: _exists(backend, name))
        step("read it back", lambda: _round_trip(backend, vm, explained))
        _snapshot_steps(backend, capabilities, name, step)
        if start:
            if step("start", lambda: _started(backend, name)):
                step("stop", lambda: _stopped(backend, name))
        else:
            record(Outcome("start", "skip", "not asked for"))
    finally:
        if keep:
            record(Outcome("delete", "skip", f"kept as asked: {name}"))
        else:
            # In a `finally`, because a selftest that leaves a VM behind when it fails
            # is a selftest people stop running.
            step("delete", lambda: _deleted(backend, name))
    return report


def _existing(backend) -> List[str]:
    """Return the VMs there are, or nothing when the provider cannot say."""
    try:
        return list(backend.list_vms())
    except Exception:
        return []


def _created(backend, vm: VMConfig, explained: set) -> str:
    """Create the VM, and note which fields the provider said it could not express.

    ``explained`` is filled in here rather than guessed later: the plan carries the
    translation report (E-02), so the round-trip check that follows knows which
    differences were announced in advance.
    """
    plan = backend.create_vm(vm, execute=True, policy=Policy.NEAREST)
    steps = len(plan) if isinstance(plan, Plan) else 0
    warnings: List[str] = list(plan.warnings) if isinstance(plan, Plan) else []
    if isinstance(plan, Plan) and plan.report is not None:
        explained.update(plan.report.paths())
    detail = f"{steps} step(s)"
    if warnings:
        # Not a failure: `nearest` is deliberate, and what a hypervisor cannot express
        # exactly is the translator's business to report, not this command's to refuse.
        detail += f", {len(warnings)} translation warning(s)"
    return detail


def _exists(backend, name: str) -> str:
    """Assert the hypervisor lists the VM."""
    if not backend.vm_exists(name):
        raise AssertionError("the provider created it and then said it does not exist")
    if name not in _existing(backend):
        raise AssertionError("it exists but is not in the list of VMs")
    return "listed by the hypervisor"


def _round_trip(backend, vm: VMConfig, explained: Optional[set] = None) -> str:
    """Read the VM back and compare it to what was asked for.

    The check that has found the most: a hypervisor accepting a setting is not the same
    as keeping it, and only asking again shows the difference.

    Every field is compared, not only the ones a file would state -- that is the point
    of a round trip -- except the ones the provider *said* it could not express when it
    created the VM. Those are the capability declaration working, not the hypervisor
    disagreeing, and counting them as failures would make the command report the
    declaration back at itself.
    """
    live = backend.read_vm(vm.name)
    stated = stated_paths(vm.to_dict())
    explained = explained or set()
    changes = [
        change
        for change in diff(live, vm, stated)
        if change.path not in explained and change.path.split(".")[0] not in explained
    ]
    if changes:
        raise AssertionError(
            "what came back differs: " + "; ".join(change.render().strip() for change in changes)
        )
    kept = "identical to what was asked for"
    return kept if not explained else f"{kept}, besides {len(explained)} reported difference(s)"


def _snapshot_steps(backend, capabilities, name: str, step) -> None:
    """Take, list, restore and delete a snapshot, when the provider says it can."""
    if not capabilities.snapshots.usable:
        step_name = "snapshot"
        backend_name = backend.name
        step(step_name, lambda: f"{backend_name} does not support snapshots")
        return

    def take() -> str:
        backend.take_snapshot(name, "selftest", description="taken by vmctl selftest")
        found = [item.name for item in backend.snapshots(name)]
        if "selftest" not in found:
            raise AssertionError(f"took a snapshot and it is not listed: {found}")
        return "taken and listed"

    if step("snapshot", take):
        step("restore snapshot", lambda: _restored(backend, name))
        step("delete snapshot", lambda: _snapshot_deleted(backend, name))


def _restored(backend, name: str) -> str:
    backend.restore_snapshot(name, "selftest")
    return "restored"


def _snapshot_deleted(backend, name: str) -> str:
    backend.delete_snapshot(name, "selftest")
    if "selftest" in [item.name for item in backend.snapshots(name)]:
        raise AssertionError("deleted the snapshot and it is still listed")
    return "deleted"


def _started(backend, name: str) -> str:
    """Start the VM and wait for the hypervisor to agree that it is running.

    There is nothing to boot -- the disk is empty -- which is the point: a VM that
    reaches "running" has had its definition accepted by the thing that runs VMs, and
    that is what a definition being *valid* does not tell you.
    """
    backend.start_vm(name)
    for _ in range(20):
        if backend.get_vm_status(name) == "running":
            return "running"
        time.sleep(0.5)
    raise AssertionError(f"started it and the status is {backend.get_vm_status(name)!r}")


def _stopped(backend, name: str) -> str:
    """Stop it, and wait for the hypervisor to agree."""
    backend.stop_vm(name, force=True)
    for _ in range(30):
        status = str(backend.get_vm_status(name))
        if status != "running":
            return status
        time.sleep(0.5)
    raise AssertionError("stopped it and it is still running")


def _deleted(backend, name: str) -> str:
    """Delete it, and assert it is gone -- which F-28 is the reason for checking."""
    backend.delete_vm(name)
    if backend.vm_exists(name) or name in _existing(backend):
        raise AssertionError("deleted it and the hypervisor still has it")
    return "gone"
