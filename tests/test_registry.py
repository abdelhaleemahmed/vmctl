"""Provider registry tests (A-03)."""

import subprocess
import sys

import pytest

from vmctl.core import registry
from vmctl.core.exceptions import ValidationError


@pytest.fixture(autouse=True)
def no_env(monkeypatch):
    """The registry reads VMCTL_PROVIDER; these tests set it explicitly."""
    monkeypatch.delenv(registry.ENV_VAR, raising=False)


def test_built_in_providers_are_registered():
    """This file used to import ``vmctl.providers`` itself, with a comment saying it
    registered the built-ins -- so the test was doing the thing it was testing, and it
    passed for a year while the registry depended on some *other* module happening to
    import a provider package. It does its own loading now, and this reads it cold."""
    assert sorted(registry.names()) == ["libvirt", "qemu", "virtualbox", "vmware"]


@pytest.mark.allow_subprocess
def test_the_providers_command_lists_them_in_a_fresh_interpreter():
    """The one that would have caught it. In-process this cannot fail: by the time any
    test runs, some earlier module has imported a provider package and populated the
    registry. A user gets a fresh interpreter, and there `vmctl providers` printed a
    table with no rows in it -- in 4.0.1 and 4.0.2, because the CLI had stopped
    importing `VirtualBoxCapabilities` for the `--disk-format` list and that import was
    accidentally what registered all four."""
    result = subprocess.run(
        [sys.executable, "-m", "vmctl.cli.main", "providers"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    for name in ("virtualbox", "libvirt", "qemu", "vmware"):
        assert name in result.stdout, result.stdout


def test_entries_carry_a_description():
    assert all(e.description for e in registry.entries())


def test_a_provider_class_can_be_loaded():
    from vmctl.providers.virtualbox.backend import VirtualBoxBackend

    assert registry.provider_class("virtualbox") is VirtualBoxBackend


def test_an_unknown_provider_names_the_ones_that_exist():
    with pytest.raises(ValidationError) as excinfo:
        registry.provider_class("hyperv")
    assert "virtualbox" in str(excinfo.value.expected)


def test_registration_is_lazy(monkeypatch):
    """A provider whose import fails must not break the registry.

    This is what keeps optional dependencies optional: vmctl imports cleanly on a
    machine with no hypervisor tooling at all.
    """
    calls = []

    def exploding_loader():
        calls.append(1)
        raise ImportError("no bindings here")

    registry.register("explodes", exploding_loader, "a provider that cannot load")
    try:
        assert calls == [], "the loader must not run at registration time"
        assert "explodes" in registry.names()
        assert registry.is_available("explodes") is False
        from vmctl.core.exceptions import DependencyError

        with pytest.raises(DependencyError):
            registry.provider_class("explodes")
    finally:
        registry._REGISTRY.pop("explodes", None)


def test_availability_is_reported_not_raised(monkeypatch):
    """is_available chooses a provider, so it must never raise."""

    class Hostile:
        @classmethod
        def is_available(cls):
            raise RuntimeError("boom")

    registry.register("hostile", lambda: Hostile)
    try:
        assert registry.is_available("hostile") is False
    finally:
        registry._REGISTRY.pop("hostile", None)


def test_an_explicit_name_wins():
    assert registry.resolve("libvirt") == "libvirt"


def test_the_environment_is_used_when_no_name_is_given(monkeypatch):
    monkeypatch.setenv(registry.ENV_VAR, "libvirt")
    assert registry.resolve(None) == "libvirt"


def test_an_unknown_name_in_the_environment_is_an_error(monkeypatch):
    monkeypatch.setenv(registry.ENV_VAR, "nope")
    with pytest.raises(ValidationError):
        registry.resolve(None)


def test_detection_prefers_a_provider_that_is_usable(monkeypatch):
    monkeypatch.setattr(registry, "is_available", lambda name: name == "libvirt")
    assert registry.detect() == "libvirt"


def test_detection_returns_none_when_nothing_is_usable(monkeypatch):
    monkeypatch.setattr(registry, "is_available", lambda name: False)
    assert registry.detect() is None


def test_resolve_falls_back_to_virtualbox_when_nothing_is_detected(monkeypatch):
    """Historical default: behaviour does not change on a bare machine."""
    monkeypatch.setattr(registry, "detect", lambda preferred=None: None)
    assert registry.resolve(None) == "virtualbox"


def test_virtualbox_is_preferred_when_both_are_usable(monkeypatch):
    monkeypatch.setattr(registry, "is_available", lambda name: True)
    assert registry.detect() == "virtualbox"


def test_every_registered_provider_satisfies_the_contract():
    """A-07 in embryo: the shape every provider must have."""
    from vmctl.core.capabilities import Capabilities
    from vmctl.providers.base import BaseProvider

    for name in registry.names():
        cls = registry.provider_class(name)
        assert issubclass(cls, BaseProvider), name
        backend = cls()
        assert backend.name == name
        assert isinstance(backend.capabilities, Capabilities)
        assert backend.capabilities.provider == name
        assert backend.capabilities.evidence, f"{name} does not say where its limits came from"
        for required in (
            "list_vms",
            "read_vm",
            "create_vm",
            "delete_vm",
            "start_vm",
            "stop_vm",
            "get_vm_status",
            "run_plan",
        ):
            assert callable(getattr(backend, required)), f"{name}.{required}"
