"""Shared test fixtures for the vmctl suite.

Design notes
------------
The suite must run on a machine with **no hypervisor installed**. Two mechanisms
enforce that:

* every parser test goes through a pure entry point (``parse_text``) and is fed
  captured command output from ``tests/fixtures/``;
* the :func:`no_hypervisor` autouse fixture makes any unmocked
  ``subprocess.run`` call fail loudly, so a test can never silently start
  depending on a local VirtualBox.

The fake :class:`~vmctl.providers.base.MediumProbe` decodes its fixture text
with the *production* decoder (``VirtualBoxParser.parse_medium_info``) rather
than a reimplementation, so the tests cannot drift from the real behaviour.
"""

import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, List

import pytest

from vmctl.core.vmconfig import (
    BootConfig,
    CPUConfig,
    DiskConfig,
    DiskFormat,
    DiskType,
    DiskVariant,
    FirmwareConfig,
    FirmwareType,
    MemoryConfig,
    NetworkConfig,
    NetworkType,
    StorageControllerConfig,
    StorageControllerType,
    VMConfig,
)
from vmctl.providers.virtualbox.parser import VirtualBoxParser

FIXTURES = Path(__file__).parent / "fixtures"
GOLDEN = Path(__file__).parent / "golden"

#: Labels of the captured VMs. Each needs ``showvminfo_<label>.txt``; media are
#: picked up from ``showmediuminfo_<label>_<n>.txt`` + ``.path`` sidecars.
VM_LABELS = [
    "bios_minimal",
    "efi_secureboot",
    "iso_attached",
    "multidisk",
    "multinic",
    # A VM whose floppy controller has a *lower* index than its SATA controller.
    # Pins F-15 independently of iteration-order luck: the floppy controller is
    # mis-typed as SATA, so the emitter's "SATA" fallback resolves the system
    # disk onto it.
    "floppy_first",
]


# ---------------------------------------------------------------------------
# Fixture-file access
# ---------------------------------------------------------------------------


def read_fixture(name: str) -> str:
    """Return the contents of ``tests/fixtures/<name>``."""
    path = FIXTURES / name
    if not path.exists():
        raise AssertionError(
            f"Missing fixture {path}. Capture it with "
            f"./scripts/capture-fixtures.sh (see tests/fixtures/README.md)."
        )
    return path.read_text()


def make_fixture_probe(label: str, strict: bool = True) -> Callable[[str], Dict[str, Any]]:
    """Build a :class:`MediumProbe` backed by captured ``showmediuminfo`` text.

    Args:
        label: VM label whose media should be loaded.
        strict: If True, an unknown medium path is an error — it means the
            fixture set is incomplete. If False, fall back to the same defaults
            production uses when ``showmediuminfo`` fails.

    Returns:
        A callable mapping a medium path to its decoded properties.
    """
    parser = VirtualBoxParser()
    table: Dict[str, str] = {}
    for sidecar in sorted(FIXTURES.glob(f"showmediuminfo_{label}_*.path")):
        # The sidecar holds the path exactly as the machine-readable fixture
        # spells it, i.e. still escaped. Normalise it with the parser's own
        # unescaper so the table is keyed the way the parser will look it up.
        medium_path = parser._unescape(sidecar.read_text().strip())
        table[medium_path] = sidecar.with_suffix(".txt").read_text()

    def probe(path: str) -> Dict[str, Any]:
        default_format = parser.default_format_for(path)
        if path not in table:
            if strict:
                raise AssertionError(
                    f"No showmediuminfo fixture for {path!r} (label {label!r}). "
                    f"Known: {sorted(table)}"
                )
            return {"size_mb": 20480, "format": default_format, "variant": DiskVariant.THIN}
        return parser.parse_medium_info(table[path], default_format)

    return probe


# ---------------------------------------------------------------------------
# Hermeticity guard
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def no_hypervisor(request, monkeypatch):
    """Fail any test that reaches for a real hypervisor.

    Opt out with ``@pytest.mark.allow_subprocess`` when a test mocks
    ``subprocess.run`` itself.
    """
    if request.node.get_closest_marker("allow_subprocess"):
        return

    def _forbidden(*args, **kwargs):
        raise AssertionError(
            f"Test called subprocess.run({args!r}) — the suite must not need a "
            f"hypervisor. Feed it a fixture, inject a MediumProbe, or mark the "
            f"test with @pytest.mark.allow_subprocess."
        )

    monkeypatch.setattr(subprocess, "run", _forbidden)


# ---------------------------------------------------------------------------
# Parsed-fixture fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def parser() -> VirtualBoxParser:
    return VirtualBoxParser()


@pytest.fixture(params=VM_LABELS)
def vbox_capture(request, parser):
    """Parametrised over every captured VM: (label, raw_text, probe)."""
    label = request.param
    return label, read_fixture(f"showvminfo_{label}.txt"), make_fixture_probe(label)


def parse_label(label: str) -> VMConfig:
    """Parse a captured VM by label, using its fixture media."""
    return VirtualBoxParser().parse_text(
        label.replace("_", "-"),
        read_fixture(f"showvminfo_{label}.txt"),
        probe=make_fixture_probe(label),
    )


@pytest.fixture
def parsed(request) -> Callable[[str], VMConfig]:
    return parse_label


# ---------------------------------------------------------------------------
# Hand-built VMConfig objects (for emitter tests that need a precise shape)
# ---------------------------------------------------------------------------


@pytest.fixture
def vm_minimal() -> VMConfig:
    """The smallest config a user could hand-write (as in the README)."""
    return VMConfig(
        name="minimal-vm",
        cpu=CPUConfig(count=1),
        memory=MemoryConfig(mb=2048),
        firmware=FirmwareConfig(),
        disks=[DiskConfig(name="system", size_mb=20480, bootable=True)],
        networks=[NetworkConfig()],
        boot=BootConfig(),
        storage_controllers=[],
    )


@pytest.fixture
def vm_full() -> VMConfig:
    """A config exercising every field the model has."""
    return VMConfig(
        name="full-vm",
        cpu=CPUConfig(count=8, hotplug=True, execution_cap=90, pae=True, nested_virt=True),
        memory=MemoryConfig(mb=16384, vram_mb=128, page_fusion=True, ballooning=True),
        firmware=FirmwareConfig(type=FirmwareType.EFI64, secure_boot=True, tpm=True),
        disks=[
            DiskConfig(
                name="system",
                size_mb=51200,
                type=DiskType.HDD,
                format=DiskFormat.VDI,
                variant=DiskVariant.THIN,
                controller=StorageControllerType.SATA,
                controller_name="SATA Controller",
                port=0,
                device=0,
                bootable=True,
            ),
            DiskConfig(
                name="data",
                size_mb=102400,
                type=DiskType.SSD,
                format=DiskFormat.VMDK,
                variant=DiskVariant.THICK,
                controller=StorageControllerType.SAS,
                controller_name="SAS Controller",
                port=1,
                device=0,
            ),
            DiskConfig(
                name="cd",
                size_mb=700,
                type=DiskType.DVD,
                controller=StorageControllerType.IDE,
                controller_name="IDE Controller",
                port=1,
                device=0,
            ),
        ],
        networks=[
            NetworkConfig(network_type=NetworkType.NAT),
            NetworkConfig(
                network_type=NetworkType.BRIDGED,
                adapter_name="eth0",
                mac_address="080027AA0001",
                promiscuous_mode=True,
            ),
            NetworkConfig(network_type=NetworkType.INTERNAL, adapter_name="lab-net"),
        ],
        boot=BootConfig(
            order=["disk", "dvd", "network", "none"], acpi=True, ioapic=True, hpet=True
        ),
        storage_controllers=[
            StorageControllerConfig(
                name="SATA Controller",
                controller_type=StorageControllerType.SATA,
                port_count=2,
                bootable=True,
            ),
            StorageControllerConfig(
                name="SAS Controller", controller_type=StorageControllerType.SAS, port_count=16
            ),
            StorageControllerConfig(
                name="IDE Controller", controller_type=StorageControllerType.IDE, port_count=2
            ),
        ],
        ostype="Ubuntu_64",
        description="every field set",
        audio_enabled=True,
        clipboard_mode="bidirectional",
        draganddrop="bidirectional",
        usb_enabled=True,
        rtc_utc=True,
        metadata={"role": "test"},
    )


# ---------------------------------------------------------------------------
# Golden-file helpers
# ---------------------------------------------------------------------------


def render_commands(commands) -> str:
    """Render emitted commands the way a golden file stores them.

    Accepts a Plan or a plain list of argv lists, so the golden files stay
    byte-identical across the Plan migration (A-01) -- which is the evidence
    that the migration changed no behaviour.
    """
    if hasattr(commands, "as_argv_lists"):
        commands = commands.as_argv_lists()
    return "\n".join(" ".join(cmd) for cmd in commands) + "\n"


def assert_golden(name: str, actual: str) -> None:
    """Compare ``actual`` against ``tests/golden/<name>``.

    Regenerate with ``python tests/regenerate_golden.py`` after reviewing the
    change — a golden diff is a behaviour change and must be intentional.
    """
    path = GOLDEN / name
    if not path.exists():
        raise AssertionError(
            f"Missing golden file {path}. Create it with "
            f"`python tests/regenerate_golden.py` and review the contents."
        )
    expected = path.read_text()
    assert actual == expected, (
        f"Output drifted from {path}.\n"
        f"If this change is intended, run `python tests/regenerate_golden.py` "
        f"and review the diff."
    )
