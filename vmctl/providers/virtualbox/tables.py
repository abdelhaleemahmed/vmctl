"""
VirtualBox translation tables: data, not logic.

Everything here states a correspondence between a field in vmctl's model and the
way VirtualBox spells it. The engine in :mod:`vmctl.core.mapping` interprets
these in both directions, so a setting is declared once and can never be
readable but not writable (A-11 in PLAN.md).

Native spellings were taken from ``VBoxManage showvminfo --machinereadable`` and
``VBoxManage modifyvm --help`` on VirtualBox 7.1.18, on a real host.
"""

from typing import Dict

from ...core.codecs import Codec, EnumCodec, Int, OnOff, Str
from ...core.mapping import Field
from ...core.platform import NicModel
from ...core.vmconfig import FirmwareType, BusType
from .ostypes import BY_DESCRIPTION, GENERIC_BY_FAMILY, OSTYPES

#: Neutral NIC model -> what ``--nictype`` accepts. Measured on 7.1.18 by setting
#: each one on a scratch VM: Am79C970A, Am79C973, Am79C960, 82540EM, 82543GC,
#: 82545EM and virtio are accepted, and anything else answers "Invalid NIC type
#: 'x' specified for NIC 1" -- so e1000e, rtl8139, ne2k and vmxnet3 are genuinely
#: absent here, not merely unmapped (A-10).
NIC_MODEL_TO_VBOX = {
    NicModel.VIRTIO: "virtio",
    NicModel.E1000: "82540EM",
    NicModel.PCNET: "Am79C973",
}

#: Reported chipset -> neutral model. Several VirtualBox ids are the same card to
#: a guest: 82540EM, 82543GC and 82545EM are all Intel PRO/1000 variants.
NIC_MODEL_FROM_VBOX = {
    "virtio": NicModel.VIRTIO,
    "82540em": NicModel.E1000,
    "82543gc": NicModel.E1000,
    "82545em": NicModel.E1000,
    "am79c970a": NicModel.PCNET,
    "am79c973": NicModel.PCNET,
    "am79c960": NicModel.PCNET,
}

#: Neutral guest id -> the id ``createvm --ostype`` accepts. Only the ids vmctl
#: names neutrally are here; everything else passes through, and a test checks
#: every value below against the generated ``OSTYPES`` list so a typo cannot ship.
GUEST_OS_TO_VBOX = {
    "linux": "Linux_64",
    "ubuntu": "Ubuntu_64",
    "ubuntu20.04": "Ubuntu20_LTS_64",
    "ubuntu22.04": "Ubuntu22_LTS_64",
    "ubuntu24.04": "Ubuntu24_LTS_64",
    "debian": "Debian_64",
    "debian11": "Debian11_64",
    "debian12": "Debian12_64",
    "rhel": "RedHat_64",
    "rhel8": "RedHat8_64",
    "rhel9": "RedHat9_64",
    "centos7": "RedHat7_64",
    "fedora": "Fedora_64",
    "opensuse": "OpenSUSE_64",
    "oracle9": "Oracle9_64",
    "archlinux": "ArchLinux_64",
    "alpine": "Linux_64",
    "win10": "Windows10_64",
    "win11": "Windows11_64",
    "win2019": "Windows2019_64",
    "win2022": "Windows2022_64",
    "freebsd": "FreeBSD_64",
    "openbsd": "OpenBSD_64",
    "macos": "MacOS_64",
    "solaris11": "Solaris11_64",
    "other": "Other_64",
}

#: The same, reversed. Where two neutral ids share a VirtualBox id -- ``alpine``
#: has none of its own, so it uses the generic Linux one -- the first wins, which
#: keeps the reverse mapping the one a reader would expect.
GUEST_OS_FROM_VBOX: Dict[str, str] = {}
for _neutral, _native in GUEST_OS_TO_VBOX.items():
    GUEST_OS_FROM_VBOX.setdefault(_native, _neutral)


class GuestOSCodec(Codec):
    """Reads what VirtualBox *reports* and writes what it *accepts*.

    Those are two different strings: ``showvminfo`` gives a description ("Ubuntu
    (64-bit)") and ``createvm --ostype`` takes an id ("Ubuntu_64"), answering
    "Unknown or invalid guest OS type given" to anything else. Feeding a
    description straight back is what made any guest outside a forty-entry literal
    impossible to re-import (F-29).

    Reading prefers vmctl's neutral id, so an exported config is portable; a guest
    vmctl does not name neutrally keeps VirtualBox's own id, which is a documented
    passthrough rather than a loss.
    """

    def load(self, raw: str) -> str:
        """Return a neutral id, or VirtualBox's id when there is no neutral one."""
        text = raw.strip().strip('"')
        native = BY_DESCRIPTION.get(text, text if text in OSTYPES else "")
        if not native:
            # Neither a description nor an id this build knows. Keep it verbatim:
            # it came from somewhere, and refusing to read a VM because of its OS
            # label would be worse than carrying a string vmctl cannot place.
            return text
        return GUEST_OS_FROM_VBOX.get(native, native)

    def dump(self, value: str) -> str:
        """Return the id ``--ostype`` takes.

        Args:
            value: A neutral id, a VirtualBox id, or a VirtualBox description.

        Raises:
            ValueError: If it is none of those, naming what would be accepted.
        """
        text = (value or "").strip()
        native = (
            GUEST_OS_TO_VBOX.get(text.lower())
            or (text if text in OSTYPES else None)
            or BY_DESCRIPTION.get(text)
        )
        if native is None:
            raise ValueError(
                f"{value!r} is not a guest OS type VirtualBox knows; expected one "
                f"of vmctl's ids ({', '.join(sorted(GUEST_OS_TO_VBOX))}) or one of "
                f"the {len(OSTYPES)} ids from `VBoxManage list ostypes`"
            )
        return native

    def describe(self) -> str:
        return "a guest OS id"


def generic_for(native: str) -> str:
    """Return the most generic type in *native*'s family, for a substitution.

    Args:
        native: A VirtualBox OS type id.

    Returns:
        The family's generic id, or ``Other_64`` when the family has none --
        VirtualBox offers no version-less "Windows", and choosing its oldest
        member instead would be a guess dressed as a translation.
    """
    entry = OSTYPES.get(native)
    return GENERIC_BY_FAMILY.get(entry.family, "Other_64") if entry else "Other_64"


#: Native firmware value -> model value. VirtualBox reports these in upper case,
#: which is why a lower-case comparison made every EFI VM look like BIOS (F-02).
FIRMWARE = {
    "bios": FirmwareType.BIOS,
    "efi": FirmwareType.EFI,
    "efi32": FirmwareType.EFI32,
    "efi64": FirmwareType.EFI64,
}

#: Reported controller chipset -> bus type. The full chipset set from
#: ``VBoxManage storagectl --help``: BusLogic, I82078, ICH6, IntelAhci,
#: LSILogic, LSILogicSAS, NVMe, PIIX3, PIIX4, USB, VirtIO.
CONTROLLER_CHIPSETS = {
    "piix3": BusType.IDE,
    "piix4": BusType.IDE,
    "ich6": BusType.IDE,
    "intelahci": BusType.SATA,
    "lsilogic": BusType.SCSI,
    "buslogic": BusType.SCSI,
    "lsilogicsas": BusType.SAS,
    "nvme": BusType.NVME,
    "i82078": BusType.FLOPPY,
    "usb": BusType.USB,
    "virtioscsi": BusType.VIRTIO_SCSI,
}

#: Scalar VM settings.
#:
#: The order is the order flags are emitted in, and is deliberately the order the
#: hand-written code used, so the golden command files are unchanged by moving to
#: this table.
#:
#: Three of these -- ``hpet``, ``cpuexecutioncap`` and ``pagefusion`` -- were
#: emitted but never read before this table existed, so exporting a VM and
#: importing it again silently reset them (F-23).
FIELDS = (
    Field("memory.mb", "memory", Int(minimum=4), "--memory"),
    Field("memory.vram_mb", "vram", Int(minimum=1), "--vram"),
    Field("cpu.count", "cpus", Int(minimum=1), "--cpus"),
    Field("firmware.type", "firmware", EnumCodec(FirmwareType, FIRMWARE), "--firmware"),
    Field("boot.acpi", "acpi", OnOff(), "--acpi"),
    Field("boot.ioapic", "ioapic", OnOff(), "--ioapic"),
    Field("rtc_utc", "rtcuseutc", OnOff(), "--rtcuseutc"),
    Field("cpu.pae", "pae", OnOff(), "--pae"),
    Field("cpu.nested_virt", "nested-hw-virt", OnOff(), "--nested-hw-virt"),
    Field("cpu.hotplug", "hotplug", OnOff(), "--cpuhotplug"),
    Field("memory.page_fusion", "pagefusion", OnOff(), "--pagefusion"),
    Field("boot.hpet", "hpet", OnOff(), "--hpet"),
    # An unrestricted cap is the default, so saying so adds noise to every plan.
    Field(
        "cpu.execution_cap",
        "cpuexecutioncap",
        Int(minimum=1, maximum=100),
        "--cpuexecutioncap",
        emit_when=lambda v: v != 100,
    ),
    Field("clipboard_mode", "clipboard", Str(), "--clipboard-mode"),
    Field("draganddrop", "draganddrop", Str(), "--draganddrop"),
    # No flag: `ostype` is set by `createvm`, not `modifyvm`, so the emitter
    # passes `codec.dump()` to the create command itself. The declaration is
    # still here because the parser reads it through the same codec, which is
    # what keeps reading and writing from disagreeing (A-11).
    Field("guest_os", "ostype", GuestOSCodec()),
    Field("description", "description", Str(), "--description"),
)

#: Fields that `modifyvm` can change on an existing VM. `ostype` is excluded
#: because `createvm` owns it.
MODIFIABLE = tuple(f for f in FIELDS if f.writable)
