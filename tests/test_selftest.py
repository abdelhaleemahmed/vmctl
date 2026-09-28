"""``vmctl selftest`` -- does the hypervisor agree? (E-19)

The command itself needs a hypervisor; its *orchestration* does not, and that is what
these check: the order of the steps, that a failure still cleans up, that a capability a
provider declines is skipped rather than failed, and that a difference the provider
announced in advance is not counted against it.

The command has been run for real against all four providers, which is the point of it:
on VirtualBox it found F-48 within minutes of existing.
"""

import copy

import pytest

from vmctl.core import selftest
from vmctl.core.capabilities import BusSpec, Capabilities, FormatSpec, Support
from vmctl.core.devices import BusType, DeviceKind, DiskFormat
from vmctl.core.plan import Plan
from vmctl.core.translate import Policy, Substitution, TranslationReport


def _caps(snapshots=Support.NATIVE):
    return Capabilities(
        provider="fake",
        buses={BusType.SATA: BusSpec("sata", "ahci", 1, 4, default_ports=4, controller_name="s0")},
        attach={(kind, BusType.SATA): kind is not DeviceKind.FLOPPY for kind in DeviceKind},
        formats={DiskFormat.QCOW2: FormatSpec(Support.NATIVE, ("qcow2",), "qcow2", ("thin",))},
        native_format=DiskFormat.QCOW2,
        native_buses={DeviceKind.DISK: BusType.SATA},
        snapshots=snapshots,
        evidence="fake",
    )


class FakeBackend:
    """A hypervisor that works, and can be told to misbehave in one specific way."""

    def __init__(self, caps=None, fail_at=None, drift=None, report=None, decides=()):
        self.name = "fake"
        self.decides = decides
        self.capabilities = caps or _caps()
        self.fail_at = fail_at
        self.drift = drift or {}
        self.report = report
        self.vms = {}
        self.snaps = {}
        self.running = set()
        self.calls = []

    # -- the contract --------------------------------------------------------

    def probe(self):
        return self.capabilities

    def unexpressible_fields(self, vm=None):
        """What this provider decides for itself, whatever a config says (E-19)."""
        return self.decides

    def list_vms(self):
        return sorted(self.vms)

    def vm_exists(self, name):
        return name in self.vms

    def create_vm(self, vm, execute=True, policy=Policy.STRICT):
        self.calls.append("create")
        if self.fail_at == "create":
            raise RuntimeError("the hypervisor said no")
        stored = copy.deepcopy(vm)
        for field, value in self.drift.items():
            setattr(stored, field, value)
        self.vms[vm.name] = stored
        plan = Plan("fake")
        plan.exec(["fake", "create", vm.name], "create it")
        plan.report = self.report
        return plan

    def read_vm(self, name):
        self.calls.append("read")
        return copy.deepcopy(self.vms[name])

    def start_vm(self, name):
        self.calls.append("start")
        if self.fail_at == "start":
            raise RuntimeError("it would not start")
        self.running.add(name)
        return True

    def stop_vm(self, name, force=False, wait=0):
        self.calls.append("stop")
        self.running.discard(name)
        return True

    def get_vm_status(self, name):
        return "running" if name in self.running else "stopped"

    def delete_vm(self, name):
        self.calls.append("delete")
        self.vms.pop(name, None)
        self.snaps.pop(name, None)
        return True

    # -- snapshots -----------------------------------------------------------

    def take_snapshot(self, name, snapshot, description=None, execute=True):
        self.calls.append("take_snapshot")
        if self.fail_at == "snapshot":
            raise RuntimeError("no snapshots today")
        self.snaps.setdefault(name, []).append(snapshot)
        return Plan("fake")

    def snapshots(self, name):
        from vmctl.core.snapshots import Snapshot

        return [Snapshot(name=item) for item in self.snaps.get(name, [])]

    def restore_snapshot(self, name, snapshot, execute=True):
        self.calls.append("restore_snapshot")
        return Plan("fake")

    def delete_snapshot(self, name, snapshot, execute=True):
        self.calls.append("delete_snapshot")
        self.snaps.get(name, []).remove(snapshot)
        return Plan("fake")


def _states(report):
    return {outcome.name: outcome.state for outcome in report.outcomes}


# ---------------------------------------------------------------------------
# When everything works
# ---------------------------------------------------------------------------


def test_every_step_runs_in_order():
    backend = FakeBackend()

    report = selftest.run(backend)

    assert report.ok
    assert [outcome.name for outcome in report.outcomes] == [
        "the name is free",
        "create",
        "it exists",
        "read it back",
        "snapshot",
        "restore snapshot",
        "delete snapshot",
        "start",
        "stop",
        "delete",
    ]
    assert backend.calls.index("create") < backend.calls.index("start")
    assert backend.calls[-1] == "delete"


def test_the_vm_it_makes_is_small_and_disposable():
    """It runs on a machine with real VMs on it, so it is never large enough to matter."""
    vm = selftest.sample_vm("x", _caps())

    assert vm.memory.mb == 128
    assert vm.storage[0].size_mb == 64
    assert len(vm.storage) == 1 and vm.storage[0].source is None


def test_the_disk_uses_what_the_provider_would_choose():
    """Asking for something the hypervisor does not do would test the wrong thing."""
    vm = selftest.sample_vm("x", _caps())

    assert vm.storage[0].format is DiskFormat.QCOW2
    assert vm.storage[0].bus is BusType.SATA


def test_the_summary_counts_each_state():
    report = selftest.run(FakeBackend(), start=False)
    assert "passed" in report.summary() and "skipped" in report.summary()


def test_the_report_is_json_safe():
    data = selftest.run(FakeBackend()).as_dict()

    assert data["ok"] is True
    assert data["provider"] == "fake"
    assert all({"step", "state", "detail", "seconds"} == set(step) for step in data["steps"])


# ---------------------------------------------------------------------------
# When something is wrong
# ---------------------------------------------------------------------------


def test_a_name_in_use_stops_before_anything_is_created():
    backend = FakeBackend()
    backend.vms["vmctl-selftest"] = object()

    report = selftest.run(backend)

    assert not report.ok
    assert backend.calls == []
    assert "already exists" in report.outcomes[0].detail


def test_a_failed_create_stops_there():
    backend = FakeBackend(fail_at="create")

    report = selftest.run(backend)

    assert not report.ok
    assert _states(report)["create"] == "fail"
    assert "start" not in _states(report)


def test_a_failure_still_deletes_the_vm():
    """A selftest that leaves a VM behind when it fails is one people stop running."""
    backend = FakeBackend(fail_at="start")

    report = selftest.run(backend)

    assert not report.ok
    assert _states(report)["delete"] == "pass"
    assert backend.vms == {}


def test_keep_leaves_it_behind_on_purpose():
    backend = FakeBackend()

    report = selftest.run(backend, keep=True)

    assert _states(report)["delete"] == "skip"
    assert "vmctl-selftest" in backend.vms


def test_a_round_trip_difference_is_a_failure_that_names_the_field():
    """The check that finds the most: a hypervisor accepting a setting is not the same
    as keeping it. F-48 was exactly this -- VirtualBox kept its own default in a boot
    slot vmctl did not set."""
    backend = FakeBackend(drift={"rtc_utc": False})

    report = selftest.run(backend)

    assert not report.ok
    assert "rtc_utc" in dict((o.name, o.detail) for o in report.outcomes)["read it back"]


def test_a_difference_the_provider_announced_is_not_counted_against_it():
    """libvirt gives every domain a USB controller and says so when it creates one.
    Failing on that would report the capability declaration back at itself."""
    announced = TranslationReport(provider="fake", policy=Policy.NEAREST)
    announced.substitutions.append(Substitution("rtc_utc", False, True, "always on here"))
    backend = FakeBackend(drift={"rtc_utc": False}, report=announced)

    report = selftest.run(backend)

    assert report.ok
    assert (
        "reported difference" in dict((o.name, o.detail) for o in report.outcomes)["read it back"]
    )


def test_a_field_the_provider_decides_for_itself_is_not_a_failure():
    """libvirt gives every domain a USB controller, so `usb_enabled: false` cannot be
    asked for -- and since that is also the model's *default*, warning about it on every
    create would put a line in every report. It is declared instead, and the selftest
    reads the declaration (E-19)."""
    backend = FakeBackend(drift={"rtc_utc": False}, decides=("rtc_utc",))

    report = selftest.run(backend)

    assert report.ok
    assert (
        "reported difference" in dict((o.name, o.detail) for o in report.outcomes)["read it back"]
    )


def test_a_provider_without_snapshots_skips_them_rather_than_failing():
    backend = FakeBackend(caps=_caps(snapshots=Support.UNSUPPORTED))

    report = selftest.run(backend)

    assert report.ok
    assert _states(report)["snapshot"] == "pass"  # reported as "does not support"
    assert "restore snapshot" not in _states(report)


def test_not_starting_it_is_a_skip():
    report = selftest.run(FakeBackend(), start=False)

    assert report.ok
    assert _states(report)["start"] == "skip"
    assert "stop" not in _states(report)


def test_a_vm_the_hypervisor_forgets_is_a_failure():
    """It said it created the VM and then said the VM does not exist."""
    backend = FakeBackend()
    original = backend.create_vm

    def create_and_forget(vm, execute=True, policy=Policy.STRICT):
        plan = original(vm, execute=execute, policy=policy)
        backend.vms.clear()
        return plan

    backend.create_vm = create_and_forget

    report = selftest.run(backend)

    assert not report.ok
    assert _states(report)["it exists"] == "fail"


def test_a_delete_that_does_not_delete_is_a_failure():
    """F-28: `virsh undefine --remove-all-storage` reported success and left every
    image on disk."""
    backend = FakeBackend()
    backend.delete_vm = lambda name: True  # says yes, does nothing

    report = selftest.run(backend)

    assert not report.ok
    assert _states(report)["delete"] == "fail"


# ---------------------------------------------------------------------------
# Through the CLI
# ---------------------------------------------------------------------------


@pytest.mark.allow_subprocess
def test_the_command_reports_each_step_and_exits_on_failure(monkeypatch):
    import json as _json

    from click.testing import CliRunner

    from vmctl.cli.main import cli

    backend = FakeBackend(fail_at="start")
    monkeypatch.setattr("vmctl.core.engine.VMCtlEngine.__init__", lambda self, provider=None: None)
    monkeypatch.setattr(
        "vmctl.core.engine.VMCtlEngine.backend", property(lambda self: backend), raising=False
    )
    monkeypatch.setattr(
        "vmctl.core.engine.VMCtlEngine.provider_name",
        property(lambda self: "fake"),
        raising=False,
    )

    result = CliRunner().invoke(cli, ["selftest", "--format", "json"])

    assert result.exit_code == 1
    data = _json.loads(result.output)
    assert data["ok"] is False
    assert any(step["state"] == "fail" for step in data["steps"])


# ---------------------------------------------------------------------------
# What each real provider says it decides for itself (E-19, F-52)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "provider,expected",
    [
        ("libvirt", {"usb_enabled", "machine"}),
        ("virtualbox", {"firmware.secure_boot", "firmware.tpm", "storage.bootable"}),
        ("vmware", {"storage.bootable"}),
        ("qemu", set()),
    ],
)
def test_the_providers_declare_what_they_decide(provider, expected):
    """Each of these was found by reading a created VM back: libvirt resolves a machine
    alias and always adds USB, VirtualBox reports no TPM state at all, and neither it
    nor VMware has a per-disk boot flag. Declared, so that a round trip does not report
    the hypervisor's own decisions as the hypervisor disagreeing."""
    import vmctl.providers  # noqa: F401
    from vmctl.core import registry

    declared = set(registry.create(provider).unexpressible_fields())

    assert expected <= declared


def test_libvirt_only_claims_a_cpu_model_it_resolves():
    """`host-model` comes back as a concrete CPU, so that difference is libvirt's own
    decision. A model named outright is either honoured or a real disagreement, and
    excluding it would hide one."""
    import vmctl.providers  # noqa: F401
    from vmctl.core import registry
    from vmctl.core.platform import CPU_HOST_MODEL

    backend = registry.create("libvirt")
    keyword = selftest.sample_vm("x", backend.capabilities)
    keyword.cpu.model = CPU_HOST_MODEL
    named = selftest.sample_vm("x", backend.capabilities)
    named.cpu.model = "Nehalem"

    assert "cpu.model" in backend.unexpressible_fields(keyword)
    assert "cpu.model" not in backend.unexpressible_fields(named)


def test_a_field_declared_for_a_device_matches_the_device_it_is_found_on():
    """A provider declares `storage.bootable`; a difference names
    `storage[scsi/0].bootable`. Without removing the address, the declaration matched
    nothing and VMware's selftest failed on a field it had already explained."""
    from vmctl.core.diff import Change, ChangeKind

    change = Change("storage[scsi/0].bootable", ChangeKind.CHANGED, live=False, desired=True)

    assert selftest._is_explained(change, {"storage.bootable"})
    assert not selftest._is_explained(change, {"storage.size_mb"})
