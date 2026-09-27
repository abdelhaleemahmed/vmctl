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


def pytest_generate_tests(metafunc):
    """Run every test that takes `provider_name` once per registered provider."""
    if "provider_name" in metafunc.fixturenames:
        metafunc.parametrize("provider_name", registry.names())


#: Where a provider keeps images. Providers name this differently -- VirtualBox
#: has a machine folder, libvirt an image directory -- which is the gap A-09
#: closes. Until then the suite stubs whichever the provider has, because looking
#: it up means asking the hypervisor and this suite must not need one.
STORAGE_ATTRIBUTES = ("machine_folder", "image_dir")


@pytest.fixture
def backend(provider_name, monkeypatch):
    """An instance of the provider under test, with storage lookup stubbed."""
    instance = registry.create(provider_name)
    for attribute in STORAGE_ATTRIBUTES:
        if hasattr(type(instance), attribute) or hasattr(instance, attribute):
            monkeypatch.setattr(type(instance), attribute, "/conformance/images", raising=False)
    return instance


@pytest.fixture
def caps(backend):
    """The provider's capability declaration."""
    return backend.capabilities
