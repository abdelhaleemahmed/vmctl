"""NAT port forwarding across the four providers (E-10).

`NetworkConfig` had no word for it, so "ssh to localhost:2222 reaches the guest" --
the single most common thing a lab NAT adapter is for -- was lost on every round trip.

Each provider's spelling here was measured against the running product: VirtualBox's
`--natpf1 "ssh,tcp,,2222,,22"`, QEMU's `hostfwd=tcp::2222-:22` (confirmed by finding
the socket listening), libvirt's `<portForward>` (which needs the passt backend, and
was confirmed the same way), and VMware's absence of any per-VM setting at all.
"""

import pytest

from vmctl.core.capabilities import Support
from vmctl.core.devices import DiskFormat
from vmctl.core.storage import directory
from vmctl.core.translate import Policy, Translator
from vmctl.core.vmconfig import (
    BootConfig,
    CPUConfig,
    FirmwareConfig,
    MemoryConfig,
    NetworkConfig,
    NetworkType,
    PortForward,
    StorageDevice,
    VMConfig,
)


def _vm(*rules, network_type=NetworkType.NAT, fmt=DiskFormat.QCOW2):
    return VMConfig(
        name="pf",
        cpu=CPUConfig(count=1),
        memory=MemoryConfig(mb=128),
        firmware=FirmwareConfig(),
        storage=[StorageDevice(name="root", size_mb=64, format=fmt)],
        networks=[NetworkConfig(network_type=network_type, port_forwards=list(rules))],
        boot=BootConfig(),
        storage_controllers=[],
    )


# ---------------------------------------------------------------------------
# The rule itself
# ---------------------------------------------------------------------------


def test_a_rule_names_itself_when_nothing_else_does():
    """Only VirtualBox has rule names, and it requires one -- so a config written for
    any other provider still has to produce a usable one."""
    assert PortForward(host_port=2222, guest_port=22).name == "tcp-2222"


def test_a_stated_name_is_kept():
    assert PortForward(host_port=2222, guest_port=22, name="ssh").name == "ssh"


def test_the_protocol_is_normalised():
    assert PortForward(host_port=53, guest_port=53, protocol="UDP").protocol == "udp"


def test_a_rule_reads_as_what_it_does():
    assert PortForward(host_port=2222, guest_port=22).label == "tcp *:2222 -> guest:22"
    assert (
        PortForward(host_port=80, guest_port=8080, host_ip="127.0.0.1", guest_ip="10.0.2.15").label
        == "tcp 127.0.0.1:80 -> 10.0.2.15:8080"
    )


def test_an_unset_address_is_not_written_out():
    """An empty host_ip means "all addresses", which is an absence, not a value: writing
    it down would turn a default into a request for `diff` and `apply` (E-01, E-02)."""
    data = PortForward(host_port=2222, guest_port=22).to_dict()
    assert "host_ip" not in data and "guest_ip" not in data


def test_a_config_round_trips_through_a_dictionary():
    vm = _vm(PortForward(host_port=2222, guest_port=22, host_ip="127.0.0.1"))
    again = VMConfig.from_dict(vm.to_dict())
    assert again.networks[0].port_forwards[0].host_ip == "127.0.0.1"
    assert again.networks[0].port_forwards[0].host_port == 2222


# ---------------------------------------------------------------------------
# QEMU: hostfwd=
# ---------------------------------------------------------------------------


def test_qemu_writes_one_hostfwd_per_rule():
    from vmctl.providers.qemu.emitter import QemuEmitter

    emitter = QemuEmitter(
        "pf", location=directory("/vms", nest_per_vm=True), binary="/q", accel="tcg"
    )
    argv = emitter.build_argv(
        _vm(
            PortForward(host_port=2222, guest_port=22),
            PortForward(host_port=8080, guest_port=80, host_ip="127.0.0.1", protocol="udp"),
        )
    )

    netdev = argv[argv.index("-netdev") + 1]
    assert "hostfwd=tcp::2222-:22" in netdev
    assert "hostfwd=udp:127.0.0.1:8080-:80" in netdev


def test_qemu_reads_every_hostfwd_back():
    """A mapping keeps only the last value for a repeated key, which silently dropped
    every forward but one -- the reason the raw option list is parsed instead."""
    from vmctl.providers.qemu.parser import QemuParser

    vm = QemuParser().parse_argv(
        "pf",
        [
            "qemu-system-x86_64",
            "-netdev",
            "user,id=net0,hostfwd=tcp::2222-:22,hostfwd=udp:127.0.0.1:5353-10.0.2.15:53",
            "-device",
            "e1000,netdev=net0",
        ],
    )

    rules = vm.networks[0].port_forwards
    assert [rule.label for rule in rules] == [
        "tcp *:2222 -> guest:22",
        "udp 127.0.0.1:5353 -> 10.0.2.15:53",
    ]


def test_qemu_round_trips_its_own_command_line():
    from vmctl.providers.qemu.emitter import QemuEmitter
    from vmctl.providers.qemu.parser import QemuParser

    emitter = QemuEmitter(
        "pf", location=directory("/vms", nest_per_vm=True), binary="/q", accel="tcg"
    )
    original = _vm(PortForward(host_port=2222, guest_port=22, host_ip="127.0.0.1"))

    again = QemuParser().parse_argv("pf", emitter.build_argv(original))

    assert [r.label for r in again.networks[0].port_forwards] == [
        r.label for r in original.networks[0].port_forwards
    ]


def test_qemu_reports_a_forward_on_a_bridged_adapter():
    """A bridged guest is on the network already, so the rule is a mistake, not a loss."""
    from vmctl.providers.qemu.emitter import QemuEmitter

    emitter = QemuEmitter(
        "pf", location=directory("/vms", nest_per_vm=True), binary="/q", accel="tcg"
    )
    translator = Translator(emitter.capabilities, Policy.NEAREST)

    vm = _vm(PortForward(host_port=2222, guest_port=22), network_type=NetworkType.BRIDGED)
    vm.networks[0].adapter_name = "br0"
    argv = emitter.build_argv(vm, translator)

    assert "hostfwd" not in " ".join(argv)
    assert any("port_forwards" in drop.field for drop in translator.report.drops)


# ---------------------------------------------------------------------------
# libvirt: <portForward>, and only with passt
# ---------------------------------------------------------------------------


def test_libvirt_writes_port_forwards_with_the_passt_backend():
    """Measured: libvirt refuses the element otherwise -- "The <portForward> element
    can only be used with the 'passt' backend of interface type='user'"."""
    import xml.etree.ElementTree as ET

    from vmctl.providers.libvirt.emitter import LibvirtEmitter

    emitter = LibvirtEmitter("pf", location=directory("/images"), definition_dir="/tmp")
    xml = emitter.build_domain_xml(_vm(PortForward(host_port=2222, guest_port=22)))

    iface = ET.fromstring(xml).find("devices/interface")
    assert iface.find("backend").get("type") == "passt"
    forward = iface.find("portForward")
    assert forward.get("proto") == "tcp"
    assert forward.find("range").get("start") == "2222"
    assert forward.find("range").get("to") == "22"


def test_libvirt_reports_them_when_passt_is_missing(monkeypatch):
    """The declaration is what says so, and `probe()` turns it off when passt is not
    installed -- so this reports rather than emitting a domain libvirt will reject."""
    from dataclasses import replace

    from vmctl.providers.libvirt.capabilities import LibvirtCapabilities
    from vmctl.providers.libvirt.emitter import LibvirtEmitter

    caps = replace(LibvirtCapabilities.get(), port_forwards=Support.UNSUPPORTED)
    emitter = LibvirtEmitter(
        "pf", location=directory("/images"), definition_dir="/tmp", capabilities=caps
    )
    translator = Translator(caps, Policy.NEAREST)

    xml = emitter.build_domain_xml(_vm(PortForward(host_port=2222, guest_port=22)), translator)

    assert "portForward" not in xml
    assert any("passt is not installed" in drop.reason for drop in translator.report.drops)


def test_libvirt_reads_port_forwards_back():
    from vmctl.providers.libvirt.parser import LibvirtParser

    xml = """<domain type='qemu'><name>pf</name><devices>
      <interface type='user'><backend type='passt'/>
        <portForward proto='udp' address='127.0.0.1'><range start='5353' to='53'/></portForward>
      </interface></devices></domain>"""

    vm = LibvirtParser().parse_text("pf", xml)

    assert [r.label for r in vm.networks[0].port_forwards] == ["udp 127.0.0.1:5353 -> guest:53"]


def test_libvirt_expands_a_range_into_one_rule_per_port():
    """`<range start='80' end='82' to='8080'/>` is three forwards said briefly, and the
    neutral model has no word for a range -- so it says the same thing longhand."""
    from vmctl.providers.libvirt.parser import LibvirtParser

    xml = """<domain type='qemu'><name>pf</name><devices><interface type='user'>
        <portForward proto='tcp'><range start='80' end='82' to='8080'/></portForward>
      </interface></devices></domain>"""

    rules = LibvirtParser().parse_text("pf", xml).networks[0].port_forwards

    assert [(r.host_port, r.guest_port) for r in rules] == [(80, 8080), (81, 8081), (82, 8082)]


# ---------------------------------------------------------------------------
# VirtualBox: --natpf, and never twice
# ---------------------------------------------------------------------------


def test_virtualbox_adds_a_rule_in_its_own_spelling():
    from vmctl.providers.virtualbox.emitter import VirtualBoxEmitter

    emitter = VirtualBoxEmitter("pf", location=directory("/vms", nest_per_vm=True))
    plan = emitter.emit_create_vm(_vm(PortForward(host_port=2222, guest_port=22, name="ssh")))

    rules = [step.argv for step in plan if step.argv and "--natpf1" in step.argv]
    assert rules == [["VBoxManage", "modifyvm", "pf", "--natpf1", "ssh,tcp,,2222,,22"]]


def test_virtualbox_deletes_before_it_adds_when_converging():
    """Measured on 7.1.18: adding a rule whose name *or* host port already exists fails
    with E_INVALIDARG, so re-applying the wanted set is not idempotent -- it is an
    error. What is no longer wanted goes first, by name."""
    import copy

    from vmctl.providers.virtualbox.emitter import VirtualBoxEmitter

    emitter = VirtualBoxEmitter("pf", location=directory("/vms", nest_per_vm=True))
    current = _vm(PortForward(host_port=2222, guest_port=22, name="ssh"))
    desired = copy.deepcopy(current)
    desired.networks[0].port_forwards = [PortForward(host_port=8080, guest_port=80, name="web")]

    commands = [step.argv for step in emitter.emit_modify_vm(current, desired) if step.argv]

    assert commands == [
        ["VBoxManage", "modifyvm", "pf", "--natpf1", "delete", "ssh"],
        ["VBoxManage", "modifyvm", "pf", "--natpf1", "web,tcp,,8080,,80"],
    ]


def test_virtualbox_emits_nothing_for_rules_that_are_already_there():
    import copy

    from vmctl.providers.virtualbox.emitter import VirtualBoxEmitter

    emitter = VirtualBoxEmitter("pf", location=directory("/vms", nest_per_vm=True))
    current = _vm(PortForward(host_port=2222, guest_port=22, name="ssh"))

    plan = emitter.emit_modify_vm(current, copy.deepcopy(current))

    assert [step.argv for step in plan if step.argv and "natpf" in " ".join(step.argv)] == []


def test_virtualbox_reads_the_rules_from_the_output_that_names_the_adapter():
    """`--machinereadable` prints `Forwarding(0)=` per adapter, with the index
    restarting and no adapter number anywhere -- so with two NAT adapters it cannot say
    which rule belongs to which. The human-readable form can."""
    from vmctl.providers.virtualbox.parser import parse_port_forwards

    text = (
        "NIC 1 Rule(0):   name = web, protocol = tcp, host ip = 127.0.0.1, "
        "host port = 8080, guest ip = 10.0.2.15, guest port = 80\n"
        "NIC 2 Rule(0):   name = alt, protocol = udp, host ip = , host port = 5353, "
        "guest ip = , guest port = 53\n"
    )

    found = parse_port_forwards(text)

    assert found[1][0].name == "web"
    assert found[1][0].label == "tcp 127.0.0.1:8080 -> 10.0.2.15:80"
    assert found[2][0].label == "udp *:5353 -> guest:53"


# ---------------------------------------------------------------------------
# VMware: nowhere to put them
# ---------------------------------------------------------------------------


def test_vmware_reports_that_it_cannot_forward_a_port():
    """Workstation configures NAT forwarding host-wide in vmnetnat.conf. vmctl is not
    going to edit a host-wide file behind a user's back, so it says so."""
    from vmctl.providers.vmware.emitter import VMwareEmitter

    emitter = VMwareEmitter("pf", location=directory("/vms", nest_per_vm=True), tools_dir="")
    translator = Translator(emitter.capabilities, Policy.NEAREST)

    keys = emitter.build_vmx(_vm(PortForward(host_port=2222, guest_port=22)), translator)

    assert not any("natpf" in key or "forward" in key.lower() for key in keys)
    assert any("vmnetnat.conf" in drop.reason for drop in translator.report.drops)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validator(provider="qemu"):
    import vmctl.providers  # noqa: F401
    from vmctl.core import registry
    from vmctl.validators.vm_validator import VMValidator

    return VMValidator(registry.create(provider).capabilities)


def test_a_port_outside_the_range_is_an_error():
    from vmctl.core.exceptions import ValidationError

    with pytest.raises(ValidationError, match="not a port"):
        _validator().validate(_vm(PortForward(host_port=70000, guest_port=22)))


def test_a_protocol_that_is_not_tcp_or_udp_is_an_error():
    from vmctl.core.exceptions import ValidationError

    with pytest.raises(ValidationError, match="protocol"):
        _validator().validate(_vm(PortForward(host_port=2222, guest_port=22, protocol="sctp")))


def test_two_rules_claiming_one_host_port_are_an_error():
    """Every provider refuses this in its own words; one message is better."""
    from vmctl.core.exceptions import ValidationError

    with pytest.raises(ValidationError, match="claim"):
        _validator().validate(
            _vm(
                PortForward(host_port=2222, guest_port=22, name="a"),
                PortForward(host_port=2222, guest_port=23, name="b"),
            )
        )


def test_the_same_port_on_different_addresses_is_fine():
    assert isinstance(
        _validator().validate(
            _vm(
                PortForward(host_port=2222, guest_port=22, host_ip="127.0.0.1"),
                PortForward(host_port=2222, guest_port=23, host_ip="192.168.56.1"),
            )
        ),
        list,
    )


def test_a_forward_on_a_provider_that_cannot_do_it_is_a_warning_not_an_error():
    """A config is meant to be portable: the file is not wrong, this hypervisor just
    cannot honour part of it, and the translator says so again when it emits."""
    warnings = _validator("vmware").validate(
        _vm(PortForward(host_port=2222, guest_port=22), fmt=DiskFormat.VMDK)
    )

    assert any("cannot be expressed by vmware" in w for w in warnings)


def test_a_forward_on_a_non_nat_adapter_is_a_warning():
    vm = _vm(PortForward(host_port=2222, guest_port=22), network_type=NetworkType.BRIDGED)
    vm.networks[0].adapter_name = "br0"

    assert any("NAT idea" in w for w in _validator().validate(vm))
