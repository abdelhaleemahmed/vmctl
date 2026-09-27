"""What every provider must be, regardless of which hypervisor it drives (A-07).

These are the rules the four-file contract rests on. They exist because the
contract was previously a convention: a third provider could satisfy the type
checker while getting the shape wrong in ways that only showed up in production.
A provider that fails any of these is not finished.
"""

import copy
import inspect

import pytest

from vmctl.core.capabilities import Capabilities, Support
from vmctl.core.plan import Plan, StepKind
from vmctl.core.vmconfig import DeviceKind
from vmctl.providers.base import BaseProvider


# ---------------------------------------------------------------------------
# Shape
# ---------------------------------------------------------------------------


def test_the_provider_subclasses_the_base(backend):
    assert isinstance(backend, BaseProvider)


def test_the_provider_knows_its_own_name(backend, provider_name):
    assert backend.name == provider_name


def test_every_lifecycle_operation_exists(backend):
    for operation in (
        "list_vms",
        "read_vm",
        "create_vm",
        "edit_vm",
        "delete_vm",
        "start_vm",
        "stop_vm",
        "get_vm_status",
        "run_plan",
        "version",
        "converter",
    ):
        assert callable(getattr(backend, operation, None)), operation


def test_availability_can_be_asked_without_a_hypervisor(backend):
    """is_available chooses a provider, so it must answer rather than raise."""
    assert isinstance(type(backend).is_available(), bool)


def test_editing_never_destroys_the_vm(backend):
    """F-11: the default edit_vm was delete + create, which lost the disks.

    A provider that cannot edit in place must say so, not fall back to something
    destructive.
    """
    source = inspect.getsource(type(backend).edit_vm)
    assert "delete_vm" not in source


def test_editing_never_re_creates_a_disk_the_vm_already_has(backend, vm_minimal, caps, monkeypatch):
    """F-39: the same trap one level down, and this one destroyed data.

    Three of the four providers keep a VM's configuration *in a file*, so changing one
    means writing that file again -- and the shortest way to write it is the code that
    creates a VM, whose plan also **makes the disks**. ``vmctl edit --memory`` on a QEMU
    VM therefore ran ``qemu-img create`` over the VM's own image. Measured, not
    theorised: a pattern written into the image did not survive the edit.

    The rule is checked without knowing any provider's path conventions. Emitting the
    same VM twice, once with its image already there and once without, isolates exactly
    the work a provider does to *make* a disk; none of it may appear in a plan that
    changes a VM which already has one.
    """
    vm_minimal.storage[0].bus = caps.buses_for(DeviceKind.DISK)[0]
    vm_minimal.storage[0].format = caps.native_format

    def words(plan):
        found = set()
        for step in plan:
            found |= set(step.argv or [])
            if step.path is not None:
                found.add(str(step.path))
        return found

    attached = copy.deepcopy(vm_minimal)
    attached.storage[0].source = "/conformance/images/the-only-copy.img"
    making_the_disk = words(backend.create_vm(vm_minimal, execute=False)) - words(
        backend.create_vm(attached, execute=False)
    )

    current = copy.deepcopy(vm_minimal)
    current.storage[0].disk_path = "/conformance/images/the-only-copy.img"
    monkeypatch.setattr(type(backend), "vm_exists", lambda self, name: True)
    monkeypatch.setattr(type(backend), "read_vm", lambda self, name: current)
    monkeypatch.setattr(type(backend), "get_vm_status", lambda self, name: "poweroff")
    desired = copy.deepcopy(current)
    desired.memory.mb += 128

    try:
        plan = backend.edit_vm(current.name, desired, execute=False)
    except NotImplementedError:
        pytest.skip(f"{backend.name} does not edit in place")

    overlap = making_the_disk & words(plan)
    assert not overlap, f"{backend.name} would make the disk again: {sorted(overlap)}"


# ---------------------------------------------------------------------------
# The capability declaration
# ---------------------------------------------------------------------------


def test_the_declaration_is_typed(caps):
    assert isinstance(caps, Capabilities)


def test_the_declaration_names_its_provider(caps, provider_name):
    assert caps.provider == provider_name


def test_the_declaration_says_where_its_numbers_came_from(caps):
    """A capability table written from memory is worse than none, because
    validation then confidently rejects configurations that would have worked.
    Every provider has to state its evidence."""
    assert caps.evidence, "no evidence recorded for these limits"


def test_the_declaration_has_at_least_one_bus(caps):
    assert caps.buses


def test_every_attach_entry_refers_to_a_declared_bus(caps):
    for kind, bus in caps.attach:
        assert bus in caps.buses, f"{kind.value} refers to undeclared bus {bus.value}"


def test_every_bus_says_whether_it_carries_each_device_kind(caps):
    """An unstated combination is treated as unsupported, so leaving one out is a
    silent refusal rather than a declaration."""
    missing = [
        (kind.value, bus.value)
        for bus in caps.buses
        for kind in (DeviceKind.DISK, DeviceKind.CDROM, DeviceKind.FLOPPY)
        if (kind, bus) not in caps.attach
    ]
    assert not missing, f"undeclared combinations: {missing}"


def test_at_least_one_bus_carries_a_disk(caps):
    assert caps.buses_for(DeviceKind.DISK), "no bus carries a hard disk"


def test_port_ranges_are_coherent(caps):
    for bus, spec in caps.buses.items():
        assert spec.min_ports >= 1, bus
        assert spec.max_ports >= spec.min_ports, bus
        assert spec.units_per_port >= 1, bus
        assert spec.min_ports <= spec.default_ports <= spec.max_ports, bus


def test_clamping_a_port_count_always_produces_an_allowed_value(caps):
    for bus, spec in caps.buses.items():
        for requested in (None, 0, 1, spec.max_ports, spec.max_ports + 100):
            got = spec.clamp_ports(requested)
            assert spec.min_ports <= got <= spec.max_ports, (bus, requested, got)


def test_the_native_format_is_one_the_provider_can_create(caps):
    spec = caps.format_spec(caps.native_format)
    assert spec.support.creatable, (
        f"{caps.provider} names {caps.native_format.value} as its native format "
        f"but cannot create it"
    )


def test_every_format_declares_at_least_one_extension(caps):
    for fmt, spec in caps.formats.items():
        assert spec.extensions, fmt


def test_a_creatable_format_says_which_allocations_it_supports(caps):
    for fmt, spec in caps.formats.items():
        if spec.support.creatable:
            assert spec.allocations, f"{fmt.value} is creatable but allocates nothing"
        if spec.support is Support.READ_ONLY:
            assert not spec.allocations, f"{fmt.value} is read-only but claims it can be allocated"


def test_an_idiomatic_bus_is_one_that_works(caps):
    """native_buses steers substitutions, so a wrong entry sends devices somewhere
    the provider will refuse."""
    for kind, bus in caps.native_buses.items():
        assert bus in caps.buses, (kind, bus)
        assert caps.can_attach(kind, bus), (
            f"{caps.provider} names {bus.value} as idiomatic for "
            f"{kind.value} but will not attach one there"
        )


def test_removable_extensions_do_not_collide_with_disk_formats(caps):
    """An extension cannot mean both "a disk in this format" and "removable"."""
    disk_extensions = {ext for spec in caps.formats.values() for ext in spec.extensions}
    overlap = disk_extensions & set(caps.removable_extensions)
    # `img` legitimately means both a raw disk and a floppy image, so it is the
    # one allowed overlap.
    assert overlap <= {"img"}, overlap


# ---------------------------------------------------------------------------
# Emission
# ---------------------------------------------------------------------------


def test_creating_a_minimal_vm_produces_a_plan(backend, vm_minimal, caps):
    """Whatever the hypervisor, the answer is a Plan."""
    vm_minimal.storage[0].bus = caps.buses_for(DeviceKind.DISK)[0]
    vm_minimal.storage[0].format = caps.native_format
    plan = backend.create_vm(vm_minimal, execute=False)
    assert isinstance(plan, Plan)
    assert plan.provider == backend.name
    assert len(plan) >= 1


def test_every_step_carries_a_description(backend, vm_minimal, caps):
    """Dry-run output and progress reporting both rely on it."""
    vm_minimal.storage[0].bus = caps.buses_for(DeviceKind.DISK)[0]
    vm_minimal.storage[0].format = caps.native_format
    for step in backend.create_vm(vm_minimal, execute=False):
        assert step.description, step


def test_every_step_is_a_kind_the_provider_can_run(backend, vm_minimal, caps):
    """A plan a provider cannot execute is a plan it should not emit."""
    vm_minimal.storage[0].bus = caps.buses_for(DeviceKind.DISK)[0]
    vm_minimal.storage[0].format = caps.native_format
    for step in backend.create_vm(vm_minimal, execute=False):
        assert step.kind in (StepKind.EXEC, StepKind.WRITE_FILE), step.kind
        if step.kind is StepKind.EXEC:
            assert step.argv, step
        else:
            assert step.path is not None, step


def test_creating_a_vm_is_not_marked_destructive(backend, vm_minimal, caps):
    vm_minimal.storage[0].bus = caps.buses_for(DeviceKind.DISK)[0]
    vm_minimal.storage[0].format = caps.native_format
    assert not backend.create_vm(vm_minimal, execute=False).destructive


def test_emission_is_deterministic(backend, vm_minimal, caps):
    """The same configuration must produce the same plan every time, or `diff`
    and `apply` report changes that are only re-derivation."""
    vm_minimal.storage[0].bus = caps.buses_for(DeviceKind.DISK)[0]
    vm_minimal.storage[0].format = caps.native_format
    first = backend.create_vm(vm_minimal, execute=False).render()
    for _ in range(5):
        assert backend.create_vm(vm_minimal, execute=False).render() == first


def test_a_device_kind_the_provider_supports_can_actually_be_emitted(backend, vm_minimal, caps):
    """Every claim in the attach matrix has to survive being acted on."""
    from vmctl.core.vmconfig import StorageDevice

    for kind in (DeviceKind.DISK, DeviceKind.CDROM, DeviceKind.FLOPPY):
        buses = caps.buses_for(kind)
        if not buses:
            continue
        removable = kind in (DeviceKind.CDROM, DeviceKind.FLOPPY)
        vm_minimal.storage = [
            StorageDevice(
                name="d",
                kind=kind,
                size_mb=None if removable else 1024,
                format=caps.native_format,
                bus=buses[0],
            )
        ]
        plan = backend.create_vm(vm_minimal, execute=False)
        assert len(plan) >= 1, (kind, buses[0])


# ---------------------------------------------------------------------------
# Escaping (A-08)
# ---------------------------------------------------------------------------

HOSTILE_NAMES = [
    'quote"inside',
    "single'quote",
    "<angle>brackets</angle>",
    "amp&ersand",
    "dollar$sign",
    "back`tick`",
    "semi;colon",
    "pipe|char",
    "new\nline",
]


@pytest.mark.parametrize("hostile", HOSTILE_NAMES, ids=lambda s: repr(s)[:18])
def test_a_hostile_vm_name_stays_data(backend, vm_minimal, caps, hostile):
    """A-08: once a provider emits a document or a script, a name is injectable.

    A name may be rejected, but it must never become *structure*: it has to
    survive as data through argv, through shell rendering, and through whatever
    document the provider writes.
    """
    import shlex
    import xml.etree.ElementTree as ET

    from vmctl.core.exceptions import VMToolError

    vm_minimal.name = f"vm{hostile}"
    vm_minimal.storage[0].bus = caps.buses_for(DeviceKind.DISK)[0]
    vm_minimal.storage[0].format = caps.native_format
    try:
        plan = backend.create_vm(vm_minimal, execute=False)
    except VMToolError:
        return  # refusing is a valid answer

    for step in plan:
        if step.kind is StepKind.EXEC:
            # Rendered as a shell command, the arguments must come back exactly:
            # anything else means a quote, semicolon or newline changed the
            # structure.
            assert shlex.split(step.shell()) == list(step.argv), step.shell()
        elif step.kind is StepKind.WRITE_FILE:
            content = step.content or ""
            if content.lstrip().startswith("<"):
                root = ET.fromstring(content)  # must not be malformed
                assert root.findtext("name") == vm_minimal.name


PATH_HOSTILE = ["../escaped", "a/b", "..", "with\\backslash"]


@pytest.mark.parametrize("hostile", PATH_HOSTILE, ids=lambda s: repr(s)[:18])
def test_a_vm_name_cannot_escape_the_directory_it_writes_into(backend, vm_minimal, caps, hostile):
    """A name is interpolated into paths, so a separator in it redirects a write.

    Either the provider refuses the name, or every file it writes stays inside the
    directory it chose.
    """
    import copy
    import os

    from vmctl.core.exceptions import VMToolError

    vm_minimal.name = hostile
    vm_minimal.storage[0].bus = caps.buses_for(DeviceKind.DISK)[0]
    vm_minimal.storage[0].format = caps.native_format
    try:
        plan = backend.create_vm(vm_minimal, execute=False)
    except VMToolError:
        return  # refusing is the other valid answer

    # Compare against a benign name: the directory each file is written into
    # must not depend on what the VM is called.
    benign = copy.deepcopy(vm_minimal)
    benign.name = "benign"
    expected = [
        os.path.dirname(os.path.normpath(str(s.path)))
        for s in backend.create_vm(benign, execute=False)
        if s.kind is StepKind.WRITE_FILE and s.path is not None
    ]
    actual = [
        os.path.dirname(os.path.normpath(str(s.path)))
        for s in plan
        if s.kind is StepKind.WRITE_FILE and s.path is not None
    ]
    assert (
        actual == expected
    ), f"the name {hostile!r} redirected a write: {actual} instead of {expected}"


def test_the_declaration_states_what_a_name_may_be(caps):
    """A provider that accepts any name accepts one that redirects its writes."""
    import re

    assert caps.name_pattern, "no name pattern declared"
    assert caps.name_max_length >= 1

    for unsafe in ("../escaped", "a/b", "..", ".", "back\\slash", "null\x00byte"):
        assert not re.match(
            caps.name_pattern, unsafe
        ), f"{caps.provider} would accept the name {unsafe!r}"
    for safe in ("web-01", "ubuntu_server", "VM 1", "a.b.c"):
        assert re.match(
            caps.name_pattern, safe
        ), f"{caps.provider} rejects the ordinary name {safe!r}"


def test_an_unusable_name_is_refused_by_the_emitter_not_only_the_validator(
    backend, vm_minimal, caps
):
    """A provider's create_vm is reachable directly, without the engine, so a
    check that only the validator performs does not protect the files it writes.
    """
    from vmctl.core.exceptions import ValidationError

    vm_minimal.name = "../escaped"
    vm_minimal.storage[0].bus = caps.buses_for(DeviceKind.DISK)[0]
    vm_minimal.storage[0].format = caps.native_format
    with pytest.raises(ValidationError):
        backend.create_vm(vm_minimal, execute=False)


def test_the_provider_says_where_it_keeps_images(backend):
    """A-09: every provider answers this the same way, so nothing has to guess
    which attribute holds it.

    Uses the stubbed backend: asking a provider for real means asking the
    hypervisor, which this suite does not do.
    """
    from vmctl.core.storage import LocationKind, StorageLocation

    location = backend.storage_location()
    assert isinstance(location, StorageLocation)
    assert location.value, "no storage location declared"
    assert isinstance(location.kind, LocationKind)
    if location.kind is LocationKind.DIRECTORY:
        path = location.image_path("vm", "vm_disk.img")
        assert path.endswith("vm_disk.img")
        assert location.directory_for("vm") in path


def test_a_warning_never_names_another_provider(backend, caps, vm_minimal):
    """F-30 -- "VirtualBox requires I/O APIC for SMP" turned up while creating a
    libvirt domain. A provider's name in shared code is a bug whichever way it
    points, and the conformance suite is where it shows up."""
    from vmctl.validators.vm_validator import VMValidator

    vm_minimal.cpu.count = 2
    vm_minimal.boot.ioapic = False
    vm_minimal.memory.mb = 129  # trips the rounding warning too
    others = {name for name in ("virtualbox", "libvirt") if name != caps.provider}
    for warning in VMValidator(caps).validate(vm_minimal):
        for other in others:
            assert other.lower() not in warning.lower(), warning
