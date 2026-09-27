"""Migration between hypervisors (P-06).

Migration is orchestration, not new machinery: reading is the source's parser,
expressing is the target's emitter, and the pieces in between already existed.
These tests use two fake providers so the orchestration can be checked without a
hypervisor -- the real VirtualBox-to-libvirt migration is exercised separately.
"""

import copy

import pytest

from vmctl.core.capabilities import BusSpec, Capabilities, FormatSpec, Support
from vmctl.core.exceptions import ValidationError
from vmctl.core.migrate import plan_migration, strip_native_hints
from vmctl.core.plan import Plan, Step, StepKind
from vmctl.core.storage import directory
from vmctl.core.translate import Policy
from vmctl.core.vmconfig import (
    BootConfig,
    CPUConfig,
    DiskConfig,
    DiskFormat,
    DiskType,
    FirmwareConfig,
    MemoryConfig,
    NetworkConfig,
    StorageControllerType,
    VMConfig,
)


def _caps(provider, native=DiskFormat.QCOW2, buses=(StorageControllerType.SATA,)):
    return Capabilities(
        provider=provider,
        buses={
            b: BusSpec("bus", "model", 1, 8, default_ports=8, controller_name=f"{b.value}0")
            for b in buses
        },
        attach={
            (kind, b): kind is not DiskType.FLOPPY
            for b in buses
            for kind in (DiskType.HDD, DiskType.DVD, DiskType.FLOPPY)
        },
        formats={
            native: FormatSpec(Support.NATIVE, (native.value,), native.value),
            DiskFormat.VDI: FormatSpec(Support.READ_ONLY, ("vdi",), "vdi", ()),
        },
        native_format=native,
        native_buses={DiskType.HDD: buses[0]},
        evidence="fake",
    )


class FakeConverter:
    def can_convert(self, source, target):
        return True

    def steps(self, request):
        return [
            Step(
                kind=StepKind.EXEC,
                description=f"convert {request.label}",
                argv=["fake-convert", request.source, request.target],
            )
        ]


class FakeProvider:
    """Enough of a provider to orchestrate against."""

    def __init__(self, provider, vm=None, native=DiskFormat.QCOW2, has_converter=True):
        self._name = provider
        self._vm = vm
        self.capabilities = _caps(provider, native)
        self.image_dir = f"/{provider}/images"
        self._has_converter = has_converter
        self.created = None

    @property
    def name(self):
        return self._name

    def read_vm(self, vm_name):
        vm = copy.deepcopy(self._vm)
        vm.name = vm_name
        return vm

    def storage_location(self):
        return directory(self.image_dir)

    def converter(self):
        return FakeConverter() if self._has_converter else None

    def create_vm(self, vm, execute=True, policy=Policy.STRICT):
        self.created = copy.deepcopy(vm)
        plan = Plan(self._name)
        plan.exec(["fake-create", vm.name], f"create {vm.name}")
        return plan

    def list_vms(self):
        return []


@pytest.fixture
def source_vm(tmp_path):
    image = tmp_path / "root.vdi"
    image.write_bytes(b"")
    return VMConfig(
        name="src",
        cpu=CPUConfig(count=2),
        memory=MemoryConfig(mb=512),
        firmware=FirmwareConfig(),
        disks=[
            DiskConfig(
                name="root",
                size_mb=1024,
                format=DiskFormat.VDI,
                controller=StorageControllerType.SATA,
                disk_path=str(image),
            )
        ],
        networks=[NetworkConfig()],
        boot=BootConfig(),
        storage_controllers=[],
        metadata={"libvirt_uuid": "abc-123", "role": "web"},
    )


# ---------------------------------------------------------------------------
# The configuration moves
# ---------------------------------------------------------------------------


def test_a_migration_is_planned_without_running_anything(source_vm):
    source = FakeProvider("virtualbox", source_vm)
    target = FakeProvider("libvirt")
    migration = plan_migration(source, target, "src", new_name="moved")
    assert migration.vm.name == "moved"
    assert migration.source_provider == "virtualbox"
    assert migration.target_provider == "libvirt"
    assert len(migration.plan) >= 1


def test_the_name_defaults_to_the_source_name(source_vm):
    """The two hypervisors have separate namespaces, so keeping the name is fine."""
    migration = plan_migration(
        FakeProvider("virtualbox", source_vm), FakeProvider("libvirt"), "src"
    )
    assert migration.vm.name == "src"


def test_migrating_to_the_same_provider_is_refused(source_vm):
    source = FakeProvider("libvirt", source_vm)
    with pytest.raises(ValidationError, match="nothing to migrate"):
        plan_migration(source, FakeProvider("libvirt"), "src")


# ---------------------------------------------------------------------------
# Native hints must not cross
# ---------------------------------------------------------------------------


def test_another_providers_native_hint_is_dropped(source_vm):
    """A libvirt domain's UUID lets `edit` redefine it, and means nothing to
    VirtualBox. Carrying it across would claim an identity that does not exist."""
    migration = plan_migration(
        FakeProvider("libvirt", source_vm), FakeProvider("virtualbox"), "src"
    )
    assert "libvirt_uuid" not in migration.vm.metadata


def test_the_targets_own_hints_are_kept(source_vm):
    migration = plan_migration(
        FakeProvider("virtualbox", source_vm), FakeProvider("libvirt"), "src"
    )
    assert migration.vm.metadata["libvirt_uuid"] == "abc-123"


def test_ordinary_metadata_survives(source_vm):
    """Only provider-prefixed keys are hints; a user's own metadata is theirs."""
    migration = plan_migration(
        FakeProvider("libvirt", source_vm), FakeProvider("virtualbox"), "src"
    )
    assert migration.vm.metadata["role"] == "web"


def test_strip_native_hints_leaves_unprefixed_keys(source_vm):
    source_vm.metadata = {"role": "web", "owner_team": "infra"}
    strip_native_hints(source_vm, "libvirt")
    assert source_vm.metadata == {"role": "web", "owner_team": "infra"}


# ---------------------------------------------------------------------------
# Disks
# ---------------------------------------------------------------------------


def test_by_default_the_data_does_not_move(source_vm):
    """Same as `vmctl import`: the new VM gets blank disks."""
    migration = plan_migration(
        FakeProvider("virtualbox", source_vm), FakeProvider("libvirt"), "src"
    )
    assert migration.disks_included is False
    assert not any("fake-convert" in (s.argv or []) for s in migration.plan)
    assert migration.vm.disks[0].source is None


def test_with_disks_converts_and_attaches_the_copy(source_vm):
    target = FakeProvider("libvirt")
    migration = plan_migration(
        FakeProvider("virtualbox", source_vm),
        target,
        "src",
        new_name="moved",
        with_disks=True,
    )
    assert migration.disks_included is True
    convert_steps = [s for s in migration.plan if s.argv and s.argv[0] == "fake-convert"]
    assert len(convert_steps) == 1
    # The target attaches the converted copy rather than creating a blank disk.
    attached = target.created.disks[0]
    assert attached.source == "/libvirt/images/moved_root.qcow2"
    assert attached.format is DiskFormat.QCOW2


def test_conversions_run_before_the_vm_is_created(source_vm):
    migration = plan_migration(
        FakeProvider("virtualbox", source_vm),
        FakeProvider("libvirt"),
        "src",
        with_disks=True,
    )
    verbs = [s.argv[0] for s in migration.plan if s.argv]
    assert verbs.index("fake-convert") < verbs.index("fake-create")


def test_a_format_the_target_can_create_is_not_converted(source_vm):
    source_vm.disks[0].format = DiskFormat.QCOW2
    target = FakeProvider("libvirt")
    plan_migration(
        FakeProvider("virtualbox", source_vm),
        target,
        "src",
        with_disks=True,
    )
    # Still copied to the target's storage, but not reformatted.
    assert target.created.disks[0].format is DiskFormat.QCOW2


def test_an_unreachable_image_is_reported_not_pretended_away(source_vm):
    """The images live on the source hypervisor's host, which may be elsewhere."""
    source_vm.disks[0].disk_path = r"C:\vms\src\root.vdi"
    migration = plan_migration(
        FakeProvider("virtualbox", source_vm),
        FakeProvider("libvirt"),
        "src",
        with_disks=True,
    )
    assert migration.unreachable == [r"C:\vms\src\root.vdi"]
    assert migration.disks_included is False
    assert any("could not be read" in w for w in migration.plan.warnings)


def test_removable_media_do_not_carry_their_path_over(source_vm):
    """An ISO path on the source host means nothing on the target."""
    source_vm.disks.append(
        DiskConfig(name="cd", type=DiskType.DVD, source="/host/install.iso", port=1)
    )
    migration = plan_migration(
        FakeProvider("virtualbox", source_vm),
        FakeProvider("libvirt"),
        "src",
        with_disks=True,
    )
    cd = [d for d in migration.vm.disks if d.type is DiskType.DVD][0]
    assert cd.source is None


def test_a_target_that_cannot_convert_is_reported(source_vm):
    from vmctl.core.exceptions import ProviderError

    target = FakeProvider("libvirt", has_converter=False)
    with pytest.raises(ProviderError, match="cannot convert"):
        plan_migration(FakeProvider("virtualbox", source_vm), target, "src", with_disks=True)


# ---------------------------------------------------------------------------
# Translation carries through
# ---------------------------------------------------------------------------


def test_the_translation_report_reaches_the_plan(source_vm):
    """Whatever the target's emitter could not express exactly is on the plan."""

    class Reporting(FakeProvider):
        def create_vm(self, vm, execute=True, policy=Policy.STRICT):
            plan = super().create_vm(vm, execute, policy)
            plan.warn("something was substituted")
            return plan

    migration = plan_migration(FakeProvider("virtualbox", source_vm), Reporting("libvirt"), "src")
    assert "something was substituted" in migration.plan.warnings


def test_the_policy_is_passed_to_the_target(source_vm):
    seen = {}

    class Recording(FakeProvider):
        def create_vm(self, vm, execute=True, policy=Policy.STRICT):
            seen["policy"] = policy
            return super().create_vm(vm, execute, policy)

    plan_migration(
        FakeProvider("virtualbox", source_vm),
        Recording("libvirt"),
        "src",
        policy=Policy.NEAREST,
    )
    assert seen["policy"] is Policy.NEAREST


def test_migration_defaults_to_strict(source_vm):
    """A script running `migrate --execute` never reads the dry-run report, so
    applying substitutions has to be something the caller asked for. The refusal
    lists them all at once, so opting in is one step."""
    seen = {}

    class Recording(FakeProvider):
        def create_vm(self, vm, execute=True, policy=Policy.STRICT):
            seen["policy"] = policy
            return super().create_vm(vm, execute, policy)

    plan_migration(FakeProvider("virtualbox", source_vm), Recording("libvirt"), "src")
    assert seen["policy"] is Policy.STRICT


def test_the_source_configuration_is_not_modified(source_vm):
    """read_vm hands back a copy, so the caller's VM is untouched."""
    source = FakeProvider("virtualbox", source_vm)
    before = copy.deepcopy(source_vm)
    plan_migration(source, FakeProvider("libvirt"), "src", with_disks=True)
    assert source_vm == before
