"""Parametrise the conformance suite over every registered provider.

The suite runs against a provider's *declaration and translation* -- its
capabilities, tables, parser and emitter -- and never against a hypervisor. A
provider whose tooling is not installed is still fully checked, which is what
makes this runnable in CI and by someone adding a third provider on a machine
that has neither of the first two.
"""

import pytest

import vmctl.providers  # noqa: F401  (registers the built-in providers)
from vmctl.core import registry
from vmctl.core.storage import directory


def pytest_generate_tests(metafunc):
    """Run every test that takes `provider_name` once per registered provider."""
    if "provider_name" in metafunc.fixturenames:
        metafunc.parametrize("provider_name", registry.names())


@pytest.fixture
def backend(provider_name, monkeypatch):
    """An instance of the provider under test, with storage lookup stubbed.

    Asking a provider where it keeps images means asking the hypervisor, and this
    suite must not need one. Before A-09 that meant stubbing two differently-named
    attributes and hoping a third provider used one of them; now there is a single
    contract method to stub.
    """
    instance = registry.create(provider_name)
    monkeypatch.setattr(
        type(instance),
        "storage_location",
        lambda self: directory("/conformance/images"),
    )
    return instance


@pytest.fixture
def caps(backend):
    """The provider's capability declaration."""
    return backend.capabilities
