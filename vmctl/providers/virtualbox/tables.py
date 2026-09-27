"""
VirtualBox translation tables: data, not logic.

Everything here states a correspondence between a field in vmctl's model and the
way VirtualBox spells it. The engine in :mod:`vmctl.core.mapping` interprets
these in both directions, so a setting is declared once and can never be
readable but not writable (A-11 in PLAN.md).

Native spellings were taken from ``VBoxManage showvminfo --machinereadable`` and
``VBoxManage modifyvm --help`` on VirtualBox 7.1.18, on a real host.
"""

from ...core.codecs import EnumCodec, Int, OnOff, Str
from ...core.mapping import Field
from ...core.vmconfig import FirmwareType, StorageControllerType

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
    "piix3": StorageControllerType.IDE,
    "piix4": StorageControllerType.IDE,
    "ich6": StorageControllerType.IDE,
    "intelahci": StorageControllerType.SATA,
    "lsilogic": StorageControllerType.SCSI,
    "buslogic": StorageControllerType.SCSI,
    "lsilogicsas": StorageControllerType.SAS,
    "nvme": StorageControllerType.NVME,
    "i82078": StorageControllerType.FLOPPY,
    "usb": StorageControllerType.USB,
    "virtioscsi": StorageControllerType.VIRTIO_SCSI,
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
    # Read-only here: `ostype` is set by `createvm`, not `modifyvm`, and needs
    # the display-name mapping in the emitter. A-05 moves that to a shared
    # catalogue.
    Field("ostype", "ostype", Str()),
    Field("description", "description", Str(), "--description"),
)

#: Fields that `modifyvm` can change on an existing VM. `ostype` is excluded
#: because `createvm` owns it.
MODIFIABLE = tuple(f for f in FIELDS if f.writable)
