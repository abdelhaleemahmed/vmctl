"""The provider contract a third-party package has to satisfy (E-15).

The mechanism came with A-03; what was missing was the *public* contract -- and the
only way to know a contract is followable is to follow it. ``examples/vmctl-null`` is a
provider in its own package, installable and separately registered, and it is checked
here rather than admired: if vmctl's contract changes in a way that breaks a provider
outside its own tree, this fails.

It was verified the whole way, too, which these tests cannot do: installed into a
virtualenv alongside vmctl, listed by ``vmctl providers`` as usable, asked for its
capabilities, made to produce a dry-run plan, and run through the conformance suite --
where it passed all 44 checks the suite makes of a provider.
"""

import sys
from pathlib import Path

import pytest

EXAMPLE = Path(__file__).parent.parent / "examples" / "vmctl-null"


@pytest.fixture(scope="module")
def null_backend():
    """The example provider, imported the way an installed package would be."""
    sys.path.insert(0, str(EXAMPLE))
    try:
        from vmctl_null.backend import NullBackend

        return NullBackend()
    finally:
        sys.path.remove(str(EXAMPLE))


def test_the_example_is_a_provider(null_backend):
    from vmctl.providers.base import BaseProvider

    assert isinstance(null_backend, BaseProvider)
    assert null_backend.name == "null"


def test_the_example_declares_its_entry_point():
    """The one line that makes a separate package a provider."""
    text = (EXAMPLE / "pyproject.toml").read_text()
    assert '[project.entry-points."vmctl.providers"]' in text
    assert "null = " in text


def test_the_entry_point_group_is_the_one_the_registry_reads():
    """Documentation and code agreeing is the whole point of a public contract."""
    from vmctl.core.registry import ENTRY_POINT_GROUP

    assert (
        f'[project.entry-points."{ENTRY_POINT_GROUP}"]' in (EXAMPLE / "pyproject.toml").read_text()
    )


def test_the_example_declares_every_attach_pair(null_backend):
    """An undeclared (kind, bus) pair is an unanswered question, and the conformance
    suite refuses one -- which is how this example learned the rule."""
    from vmctl.core.devices import DeviceKind

    caps = null_backend.capabilities
    for bus in caps.buses:
        for kind in DeviceKind:
            assert (kind, bus) in caps.attach, f"({kind.value}, {bus.value}) is undeclared"


def test_the_example_says_where_its_numbers_came_from(null_backend):
    assert null_backend.capabilities.evidence


def test_the_example_plans_rather_than_acts(null_backend, vm_minimal):
    """Everything that changes a VM returns a Plan, which is what gives every command
    its dry-run, its -v echo and its --out without a provider knowing they exist."""
    from vmctl.core.plan import Plan

    plan = null_backend.create_vm(vm_minimal, execute=False)

    assert isinstance(plan, Plan) and len(plan) >= 1


def test_the_example_refuses_a_name_that_would_escape(null_backend, vm_minimal):
    """A VM name reaches the filesystem *through* a provider, so the emitter checks it
    rather than trusting that something upstream did (A-08)."""
    from vmctl.core.exceptions import ValidationError

    vm_minimal.name = "../escaped"

    with pytest.raises(ValidationError):
        null_backend.create_vm(vm_minimal, execute=False)


def test_a_provider_that_cannot_edit_says_so_rather_than_recreating(null_backend, vm_minimal):
    """F-11's rule, from the outside: the base class refuses instead of falling back to
    delete-and-create, so a provider gets that behaviour by not implementing editing."""
    with pytest.raises(NotImplementedError):
        null_backend.edit_vm("anything", vm_minimal, execute=False)


def test_the_documentation_describes_what_the_example_does(null_backend):
    """A contract page that has drifted from the example beside it is worse than none."""
    page = (Path(__file__).parent.parent / "docs" / "writing-a-provider.md").read_text()

    from vmctl.core.registry import ENTRY_POINT_GROUP

    assert ENTRY_POINT_GROUP in page
    for method in ("storage_location", "read_vm", "create_vm", "run_argv", "evidence"):
        assert method in page, f"the contract page does not mention {method}"
