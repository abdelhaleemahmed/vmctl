"""Live capability probing (E-05).

Some of a declaration cannot be written down in advance, because the answer belongs to
the machine rather than to the product: which bridged interfaces exist, which machine
types this QEMU build offers, which guest OS ids this VirtualBox knows. For libvirt --
whose matrix depends on the QEMU underneath it -- the plan calls this a requirement
rather than a refinement.

The text-to-data half is tested against captured output, so none of this needs a
hypervisor; the ``probe()`` half is tested for the property that matters most, which is
that it never makes things worse.
"""

from pathlib import Path

import pytest

from vmctl.providers.virtualbox.backend import (
    VirtualBoxBackend,
    parse_interface_names,
    parse_ostypes,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name):
    return (FIXTURES / name).read_text()


# ---------------------------------------------------------------------------
# Reading what VBoxManage says
# ---------------------------------------------------------------------------


def test_bridged_interfaces_are_read_by_name():
    """`--bridgeadapter` takes exactly this string, spaces and all."""
    names = parse_interface_names(_fixture("list_bridgedifs.txt"))
    assert "Intel(R) Dual Band Wireless-AC 7265" in names
    assert all(name.strip() == name for name in names)


def test_host_only_interfaces_are_read_by_name():
    names = parse_interface_names(_fixture("list_hostonlyifs.txt"))
    assert "VirtualBox Host-Only Ethernet Adapter #4" in names
    assert len(names) == len(set(names)), "a name should not be reported twice"


def test_nat_networks_are_read_the_same_way():
    """All three listings spell it `Name:` on 7.1.18 -- which is worth a test, because
    `NetworkName:` is what I wrote from memory first, and it was wrong."""
    assert parse_interface_names(_fixture("list_natnets.txt")) == ("LocalNetwork",)


def test_guest_os_ids_and_descriptions_are_both_read():
    """createvm takes the id and showvminfo reports the description, so a config may
    hold either (A-05)."""
    found = parse_ostypes(_fixture("vbox_ostypes.txt"))
    assert "Ubuntu22_LTS_64" in found
    assert "Ubuntu 22.04 LTS (Jammy Jellyfish) (64-bit)" in found


def test_unreadable_output_yields_nothing_rather_than_raising():
    assert parse_interface_names("") == ()
    assert parse_ostypes("VBoxManage: error: something went wrong") == ()


# ---------------------------------------------------------------------------
# probe() itself
# ---------------------------------------------------------------------------


def test_a_provider_that_cannot_ask_keeps_its_static_declaration(monkeypatch):
    """The property that matters: probing may improve a declaration and must never
    break one. A machine without the tooling still works."""
    backend = VirtualBoxBackend()
    monkeypatch.setattr(VirtualBoxBackend, "_list", lambda self, what: "")
    probed = backend.probe()
    assert probed is backend.capabilities or probed == backend.capabilities


def test_probing_replaces_the_captured_guest_os_list(monkeypatch):
    """The static list comes from a capture of one version; another host may know
    different ids, and `createvm` refuses one it has not got (F-29)."""
    backend = VirtualBoxBackend()
    monkeypatch.setattr(
        VirtualBoxBackend,
        "_list",
        lambda self, what: _fixture("vbox_ostypes.txt") if what == "ostypes" else "",
    )
    probed = backend.probe()
    assert "Ubuntu22_LTS_64" in probed.supported_os_types
    assert "E-05" in probed.evidence, "the declaration should say it was asked"


def test_probing_records_what_this_host_has(monkeypatch):
    backend = VirtualBoxBackend()
    listings = {
        "bridgedifs": _fixture("list_bridgedifs.txt"),
        "hostonlyifs": _fixture("list_hostonlyifs.txt"),
        "natnets": _fixture("list_natnets.txt"),
    }
    monkeypatch.setattr(VirtualBoxBackend, "_list", lambda self, what: listings.get(what, ""))
    probed = backend.probe()
    assert "Intel(R) Dual Band Wireless-AC 7265" in probed.interfaces_for("bridged")
    assert probed.interfaces_for("natnetwork") == ("LocalNetwork",)
    assert probed.interfaces_for("internal") == (), "not probed means not validated"


def test_an_unprobed_mode_is_not_validated(vm_minimal):
    """An empty inventory has to mean "no opinion", not "nothing exists" -- otherwise
    probing a host would invalidate every config on it."""
    from vmctl.core.vmconfig import NetworkConfig, NetworkType
    from vmctl.validators.vm_validator import VMValidator
    from vmctl.providers.virtualbox.capabilities import VirtualBoxCapabilities

    vm_minimal.networks = [
        NetworkConfig(network_type=NetworkType.BRIDGED, adapter_name="whatever0")
    ]
    warnings = VMValidator(VirtualBoxCapabilities.get()).validate(vm_minimal)
    assert not [w for w in warnings if "does not have" in w]


def test_an_interface_this_host_does_not_have_is_reported(vm_minimal):
    """`adapter_name: eth0` is valid everywhere and correct almost nowhere. Without
    this the mistake surfaces partway through a create."""
    from dataclasses import replace

    from vmctl.core.vmconfig import NetworkConfig, NetworkType
    from vmctl.validators.vm_validator import VMValidator
    from vmctl.providers.virtualbox.capabilities import VirtualBoxCapabilities

    caps = replace(VirtualBoxCapabilities.get(), host_interfaces={"bridged": ("wlan0", "eth1")})
    vm_minimal.networks = [NetworkConfig(network_type=NetworkType.BRIDGED, adapter_name="eth0")]
    warnings = VMValidator(caps).validate(vm_minimal)
    assert any("eth0" in w and "wlan0" in w for w in warnings)


def test_an_interface_this_host_does_have_is_not_reported(vm_minimal):
    from dataclasses import replace

    from vmctl.core.vmconfig import NetworkConfig, NetworkType
    from vmctl.validators.vm_validator import VMValidator
    from vmctl.providers.virtualbox.capabilities import VirtualBoxCapabilities

    caps = replace(VirtualBoxCapabilities.get(), host_interfaces={"bridged": ("eth0",)})
    vm_minimal.networks = [NetworkConfig(network_type=NetworkType.BRIDGED, adapter_name="eth0")]
    assert not [w for w in VMValidator(caps).validate(vm_minimal) if "does not have" in w]


# ---------------------------------------------------------------------------
# The two providers that can be asked here
# ---------------------------------------------------------------------------


@pytest.mark.allow_subprocess
def test_qemu_reports_the_machine_types_it_really_has():
    """A QEMU build is a build-time selection, so this is the honest source."""
    from vmctl.providers.qemu.backend import QemuBackend

    backend = QemuBackend()
    if not backend.is_available():  # pragma: no cover - depends on the machine
        pytest.skip("qemu is not installed here")
    probed = backend.probe()
    assert "q35" in probed.machine_types
    assert len(probed.machine_types) > len(("q35", "pc")), "versioned types too"


@pytest.mark.allow_subprocess
def test_qemu_does_not_claim_nics_it_lacks():
    """F-37 in reverse: the static table was wrong once, and asking is what settles
    it."""
    from vmctl.core.platform import NicModel
    from vmctl.providers.qemu.backend import QemuBackend

    backend = QemuBackend()
    if not backend.is_available():  # pragma: no cover
        pytest.skip("qemu is not installed here")
    models = backend.probe().nic_models
    assert NicModel.VIRTIO in models
    assert NicModel.VMXNET3 not in models


@pytest.mark.allow_subprocess
def test_the_attach_matrix_is_not_re_derived_from_a_device_list():
    """A device existing is not the same as the hypervisor accepting it on a bus, which
    is what the recorded matrix measured by starting the machine. Probing must not
    quietly widen it."""
    from vmctl.core.devices import BusType, DeviceKind
    from vmctl.providers.qemu.backend import QemuBackend

    backend = QemuBackend()
    if not backend.is_available():  # pragma: no cover
        pytest.skip("qemu is not installed here")
    probed = backend.probe()
    assert probed.attach == backend.capabilities.attach
    assert not probed.can_attach(DeviceKind.FLOPPY, BusType.FLOPPY)
